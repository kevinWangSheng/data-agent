"""`data-agent` command line.

Exit codes of `run`: 0 success, 1 the agent did not finish (a turn, spend or `--agent-timeout-sec`
limit stopped it), 2 config or infrastructure error.
"""

import argparse
import asyncio
import dataclasses
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from data_agent.agent import (
    EFFORTS,
    ConfigError,
    ConversationTurn,
    Effort,
    Outcome,
    Task,
    dictionary_cache_dir,
    run,
    source_gateway,
    stdin_lines,
)
from data_agent.config import (
    ConfigFileError,
    EffectiveConfig,
    Key,
    in_section_order,
    load_effective_config,
    parts_of,
    toml_key,
    toml_text,
    toml_value,
)
from data_agent.context_tools import Dictionary
from data_agent.dbt_execute import TARGETS, DbtExecuteError, DbtExecutor, Target, scratch_schema
from data_agent.dbt_execute import TIMEOUT_S as DBT_TIMEOUT_S
from data_agent.external_mcp import (
    LAUNCH_COMMAND,
    ExternalServers,
    McpConfigError,
    launch,
    load_servers,
)
from data_agent.external_mcp import load as load_mcp_servers
from data_agent.gateway import DuckDBGateway, Gateway, PostgresGateway, SourceLimits
from data_agent.mcp_server import build_multi_server, build_server
from data_agent.run_config import CONFIG_NAMES, RunConfig, load_config
from data_agent.sandbox import (
    BACKENDS,
    CPUS,
    MAX_TIMEOUT_S,
    MEMORY,
    SANDBOX_IMAGE,
    Backend,
    DockerSandbox,
    LocalSandbox,
    Sandbox,
)
from data_agent.tracing import LANGFUSE_ENV, TRACE_TIERS, TraceTier
from data_agent.web.sources import DataSource, config_sources

EXIT_OK, EXIT_UNFINISHED, EXIT_ERROR = 0, 1, 2
LANGFUSE_BASE_URL_ENV = LANGFUSE_ENV[2]
# `--config` (ticket 143, decisions.md Q8): a config file, repeatable. Until ticket 146 moves the
# evaluation entries to config files, `run` and `mcp-server` also take an old run config's name:
# a value that is exactly one of `CONFIG_NAMES` is the name (given alone), anything else a path
# (`./full` for a file named `full`).
CONFIG_HELP = (
    "a config file (TOML); repeat for several, later ones winning. Without it, "
    "~/.data-agent/config.toml and <workspace>/.data-agent/config.toml are merged, or the one "
    "file DATA_AGENT_CONFIG names is read"
)
OLD_CONFIG_HELP = (
    CONFIG_HELP + ". Until the evaluation entries move to config files it also takes an old run "
    "config's name, alone: " + ", ".join(CONFIG_NAMES)
)
# `data-agent web` listens on `[server].host`, this by default; until cloud-deploy ticket 153 asks
# for a token, any other address than this machine's loopback is refused (`web_host`).
WEB_HOST = "127.0.0.1"
WEB_PORT = 8765
# What a run takes when neither the command line nor the config says (ticket 144): today's
# defaults, so an evaluation run (an old run config's name, no config file read) is as it was.
DEFAULT_BACKEND = "docker"
DEFAULT_DBT_TARGET = "scratch"
DEFAULT_TRACE_TIER = "full"
# The command-line options of `run` that set a config key (ticket 144): given, a value wins over
# the config's and is refused against a `[policy]` (`load_effective_config`). `--dbt-target` has
# no key of its own: the policy's is the one a dbt target is set and locked by.
RUN_OVERRIDES = {
    "effort": "limits.effort",
    "agent_timeout_sec": "limits.agent_timeout_sec",
    "sandbox": "sandbox.backend",
    "network": "sandbox.network",
    "dbt_target": "policy.dbt_target",
    "trace_tier": "trace.tier",
}
# The process's stdin, read as a descriptor: `sys.stdin` may be replaced, by pytest for one.
STDIN_FD = 0
# `--sandbox` (ticket 97, spec "`sandbox_exec`").
# `run` gives it to every run (ticket 100): without Bash, sandbox_exec is the agent's shell.
SANDBOX_HELP = (
    "where the agent's sandbox_exec runs shell commands: in a Docker container of the run's own "
    "(docker, the default; needs Docker) or directly on this machine (local). Use local only "
    "inside an isolated container, such as a Harbor task: it runs with this machine's files and "
    "network"
)
SERVER_SANDBOX_HELP = (
    "serve sandbox_exec, which runs shell commands in a Docker container of the run's own "
    "(docker) or directly on this machine (local). Use local only inside an isolated container, "
    "such as a Harbor task. Without this option the server has no sandbox_exec"
)
# `--charts` (ticket 119, .scratch/charts/spec.md "开关"): off on every evaluation.
CHARTS_HELP = (
    "the agent may show charts of its query results in the answer (display_chart and the "
    "charting rule); the web UI's runs pass it"
)
SERVER_CHARTS_HELP = (
    "serve display_chart, and give every query_database result an id it can chart. Without "
    "this option the server has no display_chart"
)

# `--dbt-target` (ticket 99, spec "`dbt_execute`").
DBT_TARGET_HELP = (
    "on a dbt task: where dbt_execute writes. scratch: a schema of the run's own, "
    "through a profile generated outside the workspace; task: the project's own profile, "
    "writing to the database it names (for an evaluation, which grades it there)"
)


def add_database(parser: argparse.ArgumentParser, server: bool) -> None:
    """The data source: a DuckDB file, or a Postgres connection string read from the environment
    variable `--postgres-env` names, so the string (password included) stays out of argv. Or
    several (ticket 89): `run` reads them from the config's `[sources.<name>]` tables (then
    neither option is given), and the MCP server `run` starts gets them as `--source` entries."""
    database = parser.add_mutually_exclusive_group(required=server)
    database.add_argument("--duckdb", type=Path, help="DuckDB file to query")
    database.add_argument(
        "--postgres-env", metavar="VAR", help="environment variable holding a Postgres URL"
    )
    if server:
        database.add_argument(
            "--source",
            nargs=3,
            action="append",
            metavar=("NAME", "KIND", "LOCATION"),
            help="one of several databases: its name, duckdb or postgres, and the DuckDB file "
            "or the variable holding the Postgres URL; repeat for each",
        )


def check_database(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.duckdb is not None and not args.duckdb.is_file():
        parser.error(f"DuckDB file not found: {args.duckdb}")
    if args.postgres_env is not None and not os.environ.get(args.postgres_env):
        parser.error(f"environment variable {args.postgres_env} (--postgres-env) is not set")


def check_sources(parser: argparse.ArgumentParser, sources: Sequence[DataSource]) -> None:
    """As check_database, for each source; names are unique."""
    names = [source.name for source in sources]
    if len(set(names)) < len(names):
        parser.error(f"data-source names repeat: {', '.join(names)}")
    for source in sources:
        if source.duckdb is not None and not source.duckdb.is_file():
            parser.error(f"[sources.{source.name}]: DuckDB file not found: {source.duckdb}")
        if source.postgres_env is not None and not os.environ.get(source.postgres_env):
            parser.error(
                f"[sources.{source.name}]: environment variable {source.postgres_env} is not set"
            )


def given_sources(parser: argparse.ArgumentParser, given: list[list[str]]) -> list[DataSource]:
    """The MCP server's `--source NAME KIND LOCATION` entries (written by `Task.database_args`)."""
    sources: list[DataSource] = []
    for name, kind, location in given:
        if kind == "duckdb":
            sources.append(DataSource(name, duckdb=Path(location)))
        elif kind == "postgres":
            sources.append(DataSource(name, postgres_env=location))
        else:
            parser.error(f"--source {name}: kind must be duckdb or postgres, got {kind!r}")
    if len(sources) < 2:
        parser.error(
            "--source names two or more databases; give one with --duckdb or --postgres-env"
        )
    check_sources(parser, sources)
    return sources


def server_sandbox(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    config: RunConfig,
    settings: EffectiveConfig,
) -> Sandbox | None:
    """The sandbox `mcp-server --sandbox` serves sandbox_exec on: it works in the workspace and
    sees the config's Skill directory; a container is named and labelled by `--run-id`. Its
    image, CPUs, memory and longest command are `settings`' `[sandbox]` (ticket 144)."""
    if args.sandbox is None:
        if args.run_id is not None:
            parser.error("--run-id goes with --sandbox")
        if args.network:
            parser.error("--network goes with --sandbox: it turns the sandbox's network on")
        return None
    if args.workspace is None:
        parser.error("--sandbox needs --workspace: the sandbox works in it")
    max_seconds = setting(settings, "sandbox.exec_max_seconds", MAX_TIMEOUT_S)
    if args.sandbox == "local":
        return LocalSandbox(args.workspace, config.skills_dir, max_seconds=max_seconds)
    if args.run_id is None:
        parser.error("--sandbox docker needs --run-id, the id its container is named after")
    cpus = settings.value("sandbox.cpus")
    return DockerSandbox(
        args.workspace,
        config.skills_dir,
        args.run_id,
        network=args.network,
        image=setting(settings, "sandbox.image", SANDBOX_IMAGE),
        cpus=CPUS if cpus is None else f"{cpus:g}",
        memory=setting(settings, "sandbox.memory", MEMORY),
        max_seconds=max_seconds,
    )


def setting(settings: EffectiveConfig, key: str, default: Any) -> Any:
    """The key's effective value, `default` when nothing sets it."""
    found = settings.value(key)
    return default if found is None else found


def source_limits(settings: EffectiveConfig, name: str | None) -> SourceLimits:
    """The limits of the data source `[sources.<name>]` sets (ticket 144); the code's for a key
    it leaves out, and for a name the config has no table of."""
    entry = (settings.value(Key(("sources", name))) or {}) if name is not None else {}
    duckdb, schemas = entry.get("duckdb") or {}, entry.get("schemas") or {}
    code = SourceLimits()
    return SourceLimits(
        query_timeout_seconds=float(entry.get("query_timeout_seconds", code.query_timeout_seconds)),
        max_rows=int(entry.get("max_rows", code.max_rows)),
        memory_limit=duckdb.get("memory_limit", code.memory_limit),
        max_temp_directory_size=duckdb.get("max_temp_directory_size", code.max_temp_directory_size),
        include_schemas=tuple(schemas.get("include", ())),
        exclude_schemas=tuple(schemas.get("exclude", ())),
    )


def same_file(a: str | Path, b: Path) -> bool:
    return Path(a).resolve() == b.resolve()


def dbt_timeout(settings: EffectiveConfig, name: str | None) -> float:
    """`[sources.<name>].dbt_timeout_seconds`, else dbt_execute's own."""
    entry = (settings.value(Key(("sources", name))) or {}) if name is not None else {}
    return float(entry.get("dbt_timeout_seconds", DBT_TIMEOUT_S))


def served_source(settings: EffectiveConfig, args: argparse.Namespace) -> str | None:
    """The `[sources.<name>]` of the MCP server's one database, found by its location: the
    `--duckdb` file or the `--postgres-env` variable (`data-agent run` writes the run's own
    location into the file the server reads, `server_config`). None when no source has it."""
    return located_source(settings, args.duckdb, args.postgres_env)


def located_source(
    settings: EffectiveConfig, duckdb: Path | None, postgres_env: str | None
) -> str | None:
    """The `[sources.<name>]` whose `path` is the `duckdb` file or whose `env` is
    `postgres_env`; None when no source has it."""
    for name in settings.names("sources"):
        entry = settings.value(Key(("sources", name))) or {}
        path = entry.get("path")
        if duckdb is not None and path is not None and same_file(path, duckdb):
            return name
        if postgres_env is not None and entry.get("env") == postgres_env:
            return name
    return None


def run_source_limits(settings: EffectiveConfig, task: Task) -> dict[str, SourceLimits]:
    """The limits of each of the run's databases, by the name the run knows it by: its
    `[sources.<name>]` by that name, else (`--duckdb`/`--postgres-env`) the source at its
    location, as the MCP server finds it (`served_source`), so the run's dictionary and deadline
    and the server's tools take the same limits."""
    limits: dict[str, SourceLimits] = {}
    for source in task.databases:
        name: str | None = source.name
        if settings.value(Key(("sources", source.name))) is None:
            name = located_source(settings, source.duckdb, source.postgres_env)
        limits[source.name] = source_limits(settings, name)
    return limits


def server_dbt(
    parser: argparse.ArgumentParser, args: argparse.Namespace, timeout_seconds: float
) -> DbtExecutor | None:
    """The dbt_execute `mcp-server --dbt-target` serves: on the workspace's project, with the
    server's one database; `scratch` writes its profile to `--dbt-profiles-dir` and names its
    schema after `--run-id`. A dbt command may run for `timeout_seconds`."""
    if args.dbt_target is None:
        if args.dbt_profiles_dir is not None:
            parser.error("--dbt-profiles-dir goes with --dbt-target scratch")
        return None
    if args.sandbox is None:
        parser.error("--dbt-target goes with --sandbox: dbt_execute comes with the sandbox")
    if args.source is not None:
        parser.error("--dbt-target takes one database, not --source")
    if args.dbt_target == "scratch":
        if args.dbt_profiles_dir is None or args.run_id is None:
            parser.error("--dbt-target scratch needs --dbt-profiles-dir and --run-id")
    elif args.dbt_profiles_dir is not None:
        parser.error("--dbt-profiles-dir goes with --dbt-target scratch")
    try:
        return DbtExecutor(
            args.workspace,
            args.dbt_target,
            args.run_id,
            args.dbt_profiles_dir,
            duckdb=args.duckdb,
            postgres_env=args.postgres_env,
            timeout_seconds=timeout_seconds,
        )
    except DbtExecuteError as exc:
        parser.error(str(exc))


def own_schema(args: argparse.Namespace) -> str | None:
    """The scratch schema `mcp-server --run-id` names, as dbt_execute's `scratch` target writes
    it: it stays in the agent's view, and no other run's does (ticket 140). None without a run
    id."""
    return None if args.run_id is None else scratch_schema(args.run_id)


def serve_sources(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    config: RunConfig,
    settings: EffectiveConfig,
    sandbox: Sandbox | None,
    own: str | None,
) -> None:
    """`mcp-server --source ...`: one server over every database the entries name, each with
    its `[sources.<name>]` limits."""
    sources = given_sources(parser, args.source)
    names = {source.name for source in sources}
    dictionaries: dict[str, Dictionary] = {}
    for name, path in args.source_dictionary or []:
        if name not in names:
            parser.error(f"--source-dictionary {name}: no --source has that name")
        if not Path(path).is_file():
            parser.error(f"dictionary file not found: {path}")
        dictionaries[name] = Dictionary.load(Path(path))
    limits = {source.name: source_limits(settings, source.name) for source in sources}
    gateways = {
        source.name: source_gateway(source, os.environ, limits[source.name]) for source in sources
    }
    build_multi_server(
        gateways,
        config,
        args.workspace,
        dictionaries,
        sandbox,
        charts=args.charts,
        own_schema=own,
        limits=limits,
    ).run("stdio")


def sources_task(
    parser: argparse.ArgumentParser, args: argparse.Namespace, text: str, config: EffectiveConfig
) -> Task:
    """The task of a run whose data source is the config's `[sources.<name>]`. One source runs as
    `--duckdb` or `--postgres-env` would, under the source's name; with several, the agent sees
    them all."""
    if args.data_source_name is not None:
        parser.error(
            "--data-source-name goes with --duckdb or --postgres-env; the config names its sources"
        )
    try:
        sources = config_sources(config)
    except ValueError as exc:
        parser.error(str(exc))
    if not sources:
        parser.error(
            "no data source: give --duckdb or --postgres-env, or a [sources.<name>] table in the "
            "config"
        )
    check_sources(parser, sources)
    if len(sources) == 1:
        [only] = sources
        return Task(
            text=text,
            type=args.type,
            workspace=args.workspace,
            duckdb=only.duckdb,
            postgres_env=only.postgres_env,
            data_source=only.name,
        )
    if args.type == "dbt":
        parser.error("a dbt run takes one data source; the config names several")
    return Task(text=text, type=args.type, workspace=args.workspace, sources=tuple(sources))


def effective_config(
    parser: argparse.ArgumentParser,
    given: Sequence[str],
    workspace: Path | None,
    overrides: Mapping[str, Any] | None = None,
) -> EffectiveConfig:
    """The effective config of the `--config` files (without any, the user-level and
    project-level files, or `DATA_AGENT_CONFIG`) under the command line's `overrides` (dotted
    keys); exit 2 naming the file and the key on an error, a command-line value against a
    `[policy]` included."""
    try:
        return load_effective_config(
            [Path(p) for p in given], os.environ, overrides or {}, workspace
        )
    except ConfigFileError as exc:
        parser.error(str(exc))


def check_policy(parser: argparse.ArgumentParser, config: EffectiveConfig) -> None:
    """Exit 2 when a `[roles]` model is one `policy.disabled_models` names (decisions.md Q8:
    refused, never swapped for another)."""
    disabled = config.value("policy.disabled_models") or []
    for role in ("main", "small", "classify", "verifier"):
        model = config.value(f"roles.{role}")
        if model is not None and model in disabled:
            parser.error(
                f"{config.source('policy.disabled_models')}: policy.disabled_models: the model "
                f"{model} (roles.{role}) is disabled"
            )


def given_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """The `RUN_OVERRIDES` options given on the command line, as config keys."""
    overrides: dict[str, Any] = {}
    for name, key in RUN_OVERRIDES.items():
        value = getattr(args, name)
        # `--network` is a switch: left out, the config says.
        if value is not None and value is not False:
            overrides[key] = value
    return overrides


def run_config(
    parser: argparse.ArgumentParser,
    given: Sequence[str],
    workspace: Path | None,
    overrides: Mapping[str, Any] | None = None,
) -> tuple[RunConfig, EffectiveConfig | None, EffectiveConfig]:
    """The run config `--config` asks for, the effective config and the run's settings (the
    config its options are read from, `run_settings`). An old run config's name reads no config
    file, so the evaluation entries' runs stay as they were: its settings are the code defaults
    under `overrides`, and it has no effective config. Else the effective config, whose
    `[limits]` give the run config its turn and spend caps, is the settings too."""
    names = [value for value in given if value in CONFIG_NAMES]
    if names:
        if len(given) > 1:
            parser.error(
                f"--config {names[0]} is an old run config's name, which goes alone; give a "
                f"file of that name as ./{names[0]}"
            )
        try:
            config = load_config(names[0])
            # No environment, no file: what the user keeps never reaches an evaluation.
            settings = load_effective_config((), {}, overrides or {})
        except (ValueError, ConfigFileError) as exc:
            parser.error(str(exc))
        return config, None, settings
    effective = effective_config(parser, given, workspace, overrides)
    check_policy(parser, effective)
    return with_limits(load_config(effective), effective), effective, effective


def with_limits(config: RunConfig, effective: EffectiveConfig) -> RunConfig:
    """`config` with the turn and spend caps of `[limits]`."""
    return dataclasses.replace(
        config,
        max_turns={
            kind: int(setting(effective, f"limits.max_turns.{kind}", config.max_turns[kind]))
            for kind in ("dbt", "query", "large")
        },
        max_budget_usd=float(setting(effective, "limits.max_budget_usd", config.max_budget_usd)),
    )


def server_config(
    config: RunConfig, effective: EffectiveConfig, scratch: Path, task: Task | None = None
) -> RunConfig:
    """`config` naming the file our own MCP server gets as `--config`: the effective config
    written to the run's scratch directory, `${VAR}` references as written, without the
    `[models.*].api_key`s. The server runs under `sdk_env()`, which blanks `ANTHROPIC_API_KEY`,
    and takes its model settings from `tool_env()`. A run of one data source the config names
    (`--data-source-name`) on a DuckDB file of its own (a web dbt run's copy) has that source's
    `path` be the file, so the server finds the source's limits by it (`served_source`)."""
    leaves = {key: value for key, value in effective.raw.items() if not is_model_key(key)}
    if task is not None and not task.several and task.duckdb is not None:
        path = Key(("sources", task.data_source_name, "path"))
        if effective.value(path) is not None:
            leaves[path] = str(task.duckdb.resolve())
    path = scratch / f"{config.name}.toml"
    path.write_text(toml_text(leaves), encoding="utf-8")
    return dataclasses.replace(config, server_config=str(path))


def is_model_key(key: str) -> bool:
    """A `[models.<name>].api_key` leaf."""
    parts = parts_of(key)
    return parts[0] == "models" and parts[-1] == "api_key"


def config_mcp(
    parser: argparse.ArgumentParser, config: EffectiveConfig, scratch: Path | None
) -> ExternalServers | None:
    """The config's `[mcp]` servers by ticket 116's rules, checked before any session starts:
    `[mcp].file` as it is; `[mcp.servers]` written to `scratch` as an `mcpServers` file for the
    stdio wrapper to read (only checked when `scratch` is None). None when it names none."""
    path = config.value("mcp.file")
    servers = config.raw_value("mcp.servers")
    if path is not None and servers:
        parser.error("[mcp]: give the servers inline ([mcp.servers.<name>]) or as a file, not both")
    if path is not None:
        return external_servers(parser, Path(path))
    if not servers:
        return None
    if scratch is None:
        target = Path("[mcp.servers]")
    else:
        target = scratch / "mcp-servers.json"
        target.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    try:
        external = load_servers(servers, target, os.environ)
    except McpConfigError as exc:
        parser.error(f"[mcp]: {exc}")
    for warning in external.warnings:
        print(warning, file=sys.stderr)
    return external


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="data-agent")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("mcp-server", help="serve the data tools over MCP stdio")
    serve.add_argument("--config", required=True, action="append", help=OLD_CONFIG_HELP)
    add_database(serve, server=True)
    serve.add_argument(
        "--workspace", type=Path, help="keep the tools' file reads inside this directory"
    )
    serve.add_argument(
        "--no-verifier-tools",
        action="store_true",
        help="leave out the verifier tools (for a run without the verifier subagents)",
    )
    serve.add_argument(
        "--dictionary",
        type=Path,
        help="value dictionary JSON written by `data-agent run`; built at startup when absent",
    )
    serve.add_argument(
        "--source-dictionary",
        nargs=2,
        action="append",
        metavar=("NAME", "FILE"),
        help="with --source: the value dictionary of the database NAME, as --dictionary",
    )
    serve.add_argument("--sandbox", choices=BACKENDS, help=SERVER_SANDBOX_HELP)
    serve.add_argument(
        "--run-id", help="with --sandbox docker: the run's id, which names its container"
    )
    serve.add_argument(
        "--network",
        action="store_true",
        help="with --sandbox docker: the container may reach the network (none without it)",
    )
    serve.add_argument("--dbt-target", choices=TARGETS, help=DBT_TARGET_HELP)
    serve.add_argument(
        "--dbt-profiles-dir",
        type=Path,
        help="with --dbt-target scratch: the directory outside the workspace its profile goes to",
    )
    serve.add_argument("--charts", action="store_true", help=SERVER_CHARTS_HELP)

    run_cmd = commands.add_parser("run", help="run the agent on one task")
    task_arg = run_cmd.add_mutually_exclusive_group(required=True)
    task_arg.add_argument("--task", help="task text")
    task_arg.add_argument("--task-file", type=Path, help="file holding the task text")
    run_cmd.add_argument("--type", required=True, choices=("dbt", "query"))
    run_cmd.add_argument("--workspace", required=True, type=Path, help="working directory")
    add_database(run_cmd, server=False)
    run_cmd.add_argument("--config", action="append", default=[], help=OLD_CONFIG_HELP)
    run_cmd.add_argument("--events-dir", required=True, type=Path, help="where the event file goes")
    run_cmd.add_argument(
        "--data-source-name",
        help="the data source's name in the event file (default: the --postgres-env variable's "
        "name or the --duckdb file's name)",
    )
    run_cmd.add_argument(
        "--agent-timeout-sec",
        type=float,
        help="the trial's time limit, counted from now; the run stops itself before it "
        "(default: [limits].agent_timeout_sec, else none)",
    )
    # A run-level switch, not a config layer: the configs are shared by the product and the
    # evaluations (ticket 98). Off unless given, so leaving it out only takes tools away.
    run_cmd.add_argument(
        "--network",
        action="store_true",
        help="give the agent WebSearch and WebFetch, and the docker sandbox the network "
        "(never on an evaluation run; default: [sandbox].network, else off)",
    )
    # Run-level too (ticket 104): unset, the CLI's own default holds (`agent.EFFORTS`).
    run_cmd.add_argument(
        "--effort",
        choices=EFFORTS,
        help="the model's reasoning effort (default: [limits].effort, else the Claude Code CLI's)",
    )
    # Run-level too (ticket 112): the export happens only with the three Langfuse variables set;
    # this picks what it carries. The evaluation entries pass it by the task's split.
    run_cmd.add_argument(
        "--trace-tier",
        choices=TRACE_TIERS,
        help="with the Langfuse keys set: full (everything but credentials) or structure "
        "(allowlisted structure only, anonymous ids) (default: [trace].tier, else full)",
    )
    # A turn of a conversation (tests/acceptance/test_chat_api.py, contract item 5); evaluations
    # and single runs never set these (spec decision 19).
    run_cmd.add_argument(
        "--chat",
        action="store_true",
        help="run as a turn of a conversation (query tasks only), steered by JSON lines on stdin",
    )
    run_cmd.add_argument(
        "--conversation-dir",
        type=Path,
        help="the conversation's directory; its claude-config/ keeps the session (needs --chat)",
    )
    run_cmd.add_argument("--turn", type=int, help="the turn's number, from 1 (needs --chat)")
    run_cmd.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="the previous turn's session to go on with (needs --chat)",
    )
    run_cmd.add_argument(
        "--sandbox",
        choices=BACKENDS,
        help=SANDBOX_HELP + " (default: [sandbox].backend, else docker)",
    )
    # `scratch` unless given (ticket 140, ADR-0010): a product run never writes the data source's
    # own schemas. The evaluation entries pass `task`, where their tasks are graded.
    run_cmd.add_argument(
        "--dbt-target",
        choices=TARGETS,
        help=DBT_TARGET_HELP + " (default: [policy].dbt_target, else scratch)",
    )
    run_cmd.add_argument("--charts", action="store_true", help=CHARTS_HELP)
    # Ticket 144: a product run reuses the value dictionary of a catalog it has seen before; an
    # evaluation entry passes this (an old run config's name implies it), so each trial builds
    # its own as before.
    run_cmd.add_argument(
        "--no-dictionary-cache",
        action="store_true",
        help="build the value dictionary afresh, as an evaluation does, rather than reuse one "
        "built before on the same catalog (an old run config's name implies it)",
    )

    check = commands.add_parser(
        "trace-check", help="print the structure of a run's trace as Langfuse holds it"
    )
    check.add_argument(
        "run",
        help="the run's event file, or its run id (looked up as <runs dir>/**/<run id>.jsonl)",
    )
    check.add_argument(
        "--runs-dir",
        type=Path,
        help="the directory a run id is looked up under (default: ./runs)",
    )

    web = commands.add_parser("web", help="serve the web UI on this machine's loopback address")
    web.add_argument(
        "--runs-dir", type=Path, help="one subdirectory per run (default: [server].runs_dir)"
    )
    web.add_argument("--port", type=int, help=f"(default: [server].port, else {WEB_PORT})")
    # The data sources the run form offers, every run's external MCP servers and the IM
    # platforms the bot starts on come from the config: `[sources.*]`, `[mcp]`, `[im.*]`.
    web.add_argument(
        "--config",
        action="append",
        default=[],
        help=CONFIG_HELP + " (the project-level file is looked for in the working directory)",
    )
    # The server's network switch (ticket 140, ADR-0010: on by default, off in the settings).
    web.add_argument(
        "--network",
        action=argparse.BooleanOptionalAction,
        help="whether runs from the web UI, its conversations and the IM bot may have the "
        "network (WebSearch, WebFetch, the sandbox's network). With --no-network none has it, "
        "and a request asking for it is refused (default: [sandbox].network, else on)",
    )

    # The stdio wrapper every external stdio server is started through (ticket 116,
    # `external_mcp.launch`); the CLI starts it, not a person.
    mcp_launch = commands.add_parser(
        LAUNCH_COMMAND,
        help="start an stdio server of an --mcp-servers file in a clean environment (run by "
        "the Claude Code CLI)",
    )
    mcp_launch.add_argument("--mcp-servers", required=True, type=Path)
    mcp_launch.add_argument("--server", required=True, help="the server's name in the file")

    config_cmd = commands.add_parser("config", help="look at the config")
    config_cmds = config_cmd.add_subparsers(dest="config_command", required=True)
    show = config_cmds.add_parser(
        "show", help="print every key of the effective config and where its value comes from"
    )
    show.add_argument("--config", action="append", default=[], help=CONFIG_HELP)
    show.add_argument(
        "--workspace",
        type=Path,
        help="where the project-level .data-agent/config.toml is looked for (default: the "
        "working directory)",
    )

    args = parser.parse_args(argv)
    if args.command == "trace-check":
        # Imported here, like the web stack: no other command needs it.
        from data_agent import trace_check

        sys.exit(trace_check.main(args.run, os.environ, args.runs_dir or trace_check.RUNS_DIR))
    if args.command == LAUNCH_COMMAND:
        launch(args.mcp_servers, args.server, os.environ)
    if args.command == "config":
        show_config(effective_config(show, args.config, args.workspace or Path.cwd()))
        return
    if args.command == "web":
        # `--network`/`--no-network` given is the command line's `sandbox.network`.
        overrides = {} if args.network is None else {"sandbox.network": args.network}
        config = effective_config(web, args.config, Path.cwd(), overrides)
        check_policy(web, config)
        serve_web(web, args.runs_dir, args.port, config)
        return
    # The deadline counts from the earliest point the process can see (ticket 54).
    started = time.monotonic()
    command = serve if args.command == "mcp-server" else run_cmd
    # The MCP server's options are `run`'s choices, already checked against the policy there.
    overrides = given_overrides(args) if args.command == "run" else {}
    config, effective, settings = run_config(command, args.config, args.workspace, overrides)
    check_database(command, args)
    if args.command == "mcp-server":
        if args.workspace is not None and not args.workspace.is_dir():
            serve.error(f"workspace directory not found: {args.workspace}")
        sandbox = server_sandbox(serve, args, config, settings)
        name = None if args.source is not None else served_source(settings, args)
        dbt = server_dbt(serve, args, dbt_timeout(settings, name))
        if args.source is not None:
            if args.dictionary is not None:
                serve.error("with --source, give each database's dictionary as --source-dictionary")
            serve_sources(serve, args, config, settings, sandbox, own_schema(args))
            return
        if args.source_dictionary is not None:
            serve.error("--source-dictionary goes with --source")
        if args.dictionary is not None and not args.dictionary.is_file():
            serve.error(f"dictionary file not found: {args.dictionary}")
        limits = source_limits(settings, name)
        gateway: Gateway = (
            DuckDBGateway(
                args.duckdb,
                limits.query_timeout_seconds,
                limits.memory_limit,
                limits.max_temp_directory_size,
            )
            if args.duckdb is not None
            else PostgresGateway(os.environ[args.postgres_env], limits.query_timeout_seconds)
        )
        # Without the file (the server run on its own) build_server builds the dictionary.
        dictionary = Dictionary.load(args.dictionary) if args.dictionary is not None else None
        build_server(
            gateway,
            config,
            args.workspace,
            not args.no_verifier_tools,
            dictionary,
            sandbox,
            dbt,
            charts=args.charts,
            own_schema=own_schema(args),
            limits=limits,
        ).run("stdio")
        return

    if args.agent_timeout_sec is not None and not args.agent_timeout_sec > 0:
        run_cmd.error(f"--agent-timeout-sec must be positive, got {args.agent_timeout_sec}")
    if not args.workspace.is_dir():
        run_cmd.error(f"workspace directory not found: {args.workspace}")
    text: str = args.task
    if args.task_file is not None:
        if not args.task_file.is_file():
            run_cmd.error(f"task file not found: {args.task_file}")
        text = args.task_file.read_text()
    turn = conversation_turn(run_cmd, args)
    if args.duckdb is None and args.postgres_env is None:
        if effective is None:
            run_cmd.error("an old run config's name takes --duckdb or --postgres-env")
        task = sources_task(run_cmd, args, text, effective)
    else:
        task = Task(
            text=text,
            type=args.type,
            workspace=args.workspace,
            duckdb=args.duckdb,
            postgres_env=args.postgres_env,
            data_source=args.data_source_name,
        )
    # The run's own files from the config, which our MCP server and the stdio wrapper read.
    scratch = None if effective is None else Path(tempfile.mkdtemp(prefix="data-agent-config-"))
    try:
        external = None
        if effective is not None and scratch is not None:
            external = config_mcp(run_cmd, effective, scratch)
            config = server_config(config, effective, scratch, task)
        outcome = run_task(
            run_cmd, args, task, config, settings, effective, started, turn, external
        )
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
    print(outcome.answer)
    for error in outcome.errors:
        print(f"error: {error}", file=sys.stderr)
    print(f"events: {outcome.events_path}", file=sys.stderr)
    if outcome.infra_error:
        sys.exit(EXIT_ERROR)
    sys.exit(EXIT_OK if outcome.finished else EXIT_UNFINISHED)


def show_config(config: EffectiveConfig) -> None:
    """`data-agent config show`: `<key> = <value as TOML>  # <source>` per key, every `${VAR}`
    reference as written."""
    for key in in_section_order(config.raw):
        dotted = ".".join(toml_key(part) for part in parts_of(key))
        print(f"{dotted} = {toml_value(config.raw[key])}  # {config.source(key)}")


def run_task(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    task: Task,
    config: RunConfig,
    settings: EffectiveConfig,
    effective: EffectiveConfig | None,
    started: float,
    turn: ConversationTurn | None,
    external: ExternalServers | None,
) -> Outcome:
    """The run, its options from `settings` (`run_settings`)."""
    options = run_settings(settings)
    try:
        timeout = options.agent_timeout_sec
        deadline = None if timeout is None else started + timeout
        # A turn of a conversation is steered through stdin (contract item 6); a single run never
        # reads it. A cancelled run is stopped by signal, so its stdin is read no more.
        steering = None if turn is None else stdin_lines(STDIN_FD)
        limits = run_source_limits(settings, task)
        # A product run reuses its value dictionaries; an evaluation (an old run config's name, or
        # `--no-dictionary-cache`) builds each afresh.
        cache = (
            None
            if args.no_dictionary_cache or effective is None
            else dictionary_cache_dir(os.environ)
        )
        outcome = asyncio.run(
            run(
                task,
                config,
                args.events_dir,
                trace_env(os.environ, options.trace_base_url),
                deadline,
                turn=turn,
                steering=steering,
                sandbox=options.backend,
                network=options.network,
                dbt_target=options.dbt_target,
                effort=options.effort,
                trace_tier=options.trace_tier,
                external_mcp=external,
                charts=args.charts,
                deadline_margin=deadline_margin(limits),
                source_limits=limits,
                dictionary_cache=cache,
            )
        )
    except ConfigError as exc:
        parser.error(str(exc))
    return outcome


@dataclasses.dataclass(frozen=True)
class RunSettings:
    """What a run's options are when neither the command line nor the config says: today's."""

    backend: Backend = DEFAULT_BACKEND
    network: bool = False
    dbt_target: Target = DEFAULT_DBT_TARGET
    effort: Effort | None = None
    agent_timeout_sec: float | None = None
    trace_tier: TraceTier = DEFAULT_TRACE_TIER
    trace_base_url: str | None = None


def run_settings(settings: EffectiveConfig) -> RunSettings:
    """A run's options from its settings, the command line's values already merged in
    (`given_overrides`)."""

    def value(key: str, default: Any) -> Any:
        found = settings.value(key)
        return default if found is None else found

    return RunSettings(
        backend=value("sandbox.backend", DEFAULT_BACKEND),
        network=bool(value("sandbox.network", False)),
        dbt_target=value("policy.dbt_target", DEFAULT_DBT_TARGET),
        # A model's `efforts` checks the value (ticket 145); `--effort` takes `EFFORTS` only.
        effort=cast(Effort | None, settings.value("limits.effort")),
        agent_timeout_sec=settings.value("limits.agent_timeout_sec"),
        trace_tier=value("trace.tier", DEFAULT_TRACE_TIER),
        trace_base_url=settings.value("trace.base_url"),
    )


def trace_env(env: Mapping[str, str], base_url: str | None) -> Mapping[str, str]:
    """The run's environment with `[trace].base_url` as Langfuse's address when the environment
    names none (the environment wins over a config file, spec "优先级"); the keys come from the
    environment only."""
    if base_url is None or env.get(LANGFUSE_BASE_URL_ENV):
        return env
    return {**env, LANGFUSE_BASE_URL_ENV: base_url}


def deadline_margin(limits: Mapping[str, SourceLimits]) -> float:
    """How long before its deadline the run stops itself: the longest statement timeout of its
    data sources (`[sources.<name>].query_timeout_seconds`, else the gateway's), so a query
    started just before still ends in time."""
    return max(limit.query_timeout_seconds for limit in limits.values())


def conversation_turn(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> ConversationTurn | None:
    """The conversation turn `--chat` asks for, None for a single run. A conversation only
    queries (spec decision 3): the verifiers, which check dbt work, never run in one."""
    if not args.chat:
        for name in ("conversation_dir", "turn", "resume"):
            if getattr(args, name) is not None:
                parser.error(f"--{name.replace('_', '-')} needs --chat")
        return None
    if args.type != "query":
        parser.error("--chat runs --type query only")
    if args.conversation_dir is None:
        parser.error("--chat needs --conversation-dir")
    if not args.conversation_dir.is_dir():
        parser.error(f"conversation directory not found: {args.conversation_dir}")
    if args.turn is None or args.turn < 1:
        parser.error("--chat needs --turn, a number from 1")
    return ConversationTurn(args.conversation_dir, args.turn, args.resume)


def external_servers(parser: argparse.ArgumentParser, path: Path | None) -> ExternalServers | None:
    """The servers of the `mcpServers` file `[mcp].file` names, checked before any session starts
    (exit code 2 on an error); its warnings go to stderr. None without a file."""
    if path is None:
        return None
    try:
        external = load_mcp_servers(path, os.environ)
    except McpConfigError as exc:
        parser.error(str(exc))
    for warning in external.warnings:
        print(warning, file=sys.stderr)
    return external


def serve_web(
    parser: argparse.ArgumentParser,
    runs_dir: Path | None,
    port: int | None,
    config: EffectiveConfig,
) -> None:
    """The server's config gives the run form its `[sources.*]`, every run its `[mcp]` servers
    (each run gets a file of the server's config, never a value expanded from it;
    `create_app`) and the bot its `[im.*]` platforms (data_agent.im.service), whose tokens'
    variables no run gets. The config's sources, servers and platforms are checked here first,
    so a bad one stops the server from starting. `[server]` gives the runs directory, port and
    address unless `--runs-dir`/`--port` do (ticket 144); `[sandbox].network` (on unless the
    config turns it off, ADR-0010) is the server's network switch: off, no run has the network
    (`create_app`)."""
    if runs_dir is None:
        runs_dir = config.value("server.runs_dir")
    if runs_dir is None:
        parser.error("no runs directory: give --runs-dir or [server].runs_dir in the config")
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        parser.error(f"runs directory not found: {runs_dir}")
    port = int(setting(config, "server.port", WEB_PORT)) if port is None else port
    host = web_host(parser, str(setting(config, "server.host", WEB_HOST)))
    network = bool(setting(config, "sandbox.network", True))
    config_mcp(parser, config, None)
    # Imported here so `data-agent run` does not load the web stack.
    import uvicorn

    from data_agent.web.app import create_app

    try:
        sources = config_sources(config)
    except ValueError as exc:
        parser.error(str(exc))
    # Imported here too, for the stand-in of the acceptance tests (data_agent.im.service). No
    # platform library is imported before `service.start`.
    from data_agent.im import config as im_config
    from data_agent.im import service

    # Expanded (an `allowed_users` entry may be a `${VAR}`); the token keys hold names only.
    im_section = config.value("im")
    try:
        im_configs = im_config.load_section(im_section, os.environ) if im_section else None
    except im_config.ImConfigError as exc:
        parser.error(str(exc))

    def start_run(args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
        # Its own session, so cancelling can stop the run with the tools it started. A stdin pipe
        # of its own: a conversation's turn is steered through it (data_agent.web.conversations),
        # and no run reads the server's. With `[im.*]`, no IM token.
        if im_configs is None:
            return subprocess.Popen(
                [sys.executable, "-m", "data_agent", "run", *args],
                cwd=cwd,
                start_new_session=True,
                stdin=subprocess.PIPE,
            )
        return subprocess.Popen(
            [sys.executable, "-m", "data_agent", "run", *args],
            cwd=cwd,
            start_new_session=True,
            stdin=subprocess.PIPE,
            env=im_config.run_env(os.environ, im_configs.values()),
        )

    try:
        app = create_app(runs_dir.resolve(), sources, start_run, config=config, network=network)
    except ValueError as exc:
        parser.error(str(exc))
    if im_configs is not None:
        try:
            service.start(app, im_configs)
        except service.ImUnavailable as exc:
            parser.error(str(exc))
    # On SIGINT or SIGTERM uvicorn first waits for open responses to end, and only then runs the
    # app's lifespan shutdown, which stops the run (`Launcher.shutdown`). An open event stream ends
    # only with its run, so without a limit the run would go on until it finished. Past the limit
    # uvicorn cancels the remaining responses and goes on to the lifespan shutdown (uvicorn
    # `Server.shutdown`). A few seconds. A cancel or submit cut off by the limit goes on in its
    # thread, holding the launcher's lock, so the lifespan shutdown waits for it and then
    # stops whatever run is left.
    uvicorn.run(app, host=host, port=port, timeout_graceful_shutdown=3)


def web_host(parser: argparse.ArgumentParser, host: str) -> str:
    """`[server].host`, refused unless it is this machine's loopback address: the web UI has no
    login until cloud-deploy ticket 153 adds a token, so it is never served beyond this
    machine."""
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        parser.error(
            f"[server].host {host}: only a loopback address (127.0.0.1, ::1, localhost) is "
            "served until the web UI takes a token (cloud-deploy ticket 153)"
        )
    return host
