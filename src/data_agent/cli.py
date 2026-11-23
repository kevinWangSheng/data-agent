"""`data-agent` command line.

Exit codes of `run`: 0 success, 1 the agent did not finish (a turn, spend or `--agent-timeout-sec`
limit stopped it), 2 config or infrastructure error.
"""

import argparse
import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from data_agent.agent import (
    EFFORTS,
    ConfigError,
    ConversationTurn,
    Task,
    run,
    source_gateway,
    stdin_lines,
)
from data_agent.context_tools import Dictionary
from data_agent.dbt_execute import TARGETS, DbtExecuteError, DbtExecutor
from data_agent.gateway import DuckDBGateway, Gateway, PostgresGateway
from data_agent.mcp_server import build_multi_server, build_server
from data_agent.run_config import CONFIG_NAMES, RunConfig, load_config
from data_agent.sandbox import BACKENDS, DockerSandbox, LocalSandbox, Sandbox
from data_agent.web.sources import DataSource, load_data_sources

EXIT_OK, EXIT_UNFINISHED, EXIT_ERROR = 0, 1, 2
# `data-agent web` listens here only; no option changes it (spec "后端接口", user story 35).
WEB_HOST = "127.0.0.1"
WEB_PORT = 8765
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

# `--dbt-target` (ticket 99, spec "`dbt_execute`").
DBT_TARGET_HELP = (
    "on a dbt task: where dbt_execute writes. scratch: a schema of the run's own, "
    "through a profile generated outside the workspace; task (the default for run): the "
    "project's own profile, writing to the database it names"
)


def add_database(parser: argparse.ArgumentParser, server: bool) -> None:
    """The data source: a DuckDB file, or a Postgres connection string read from the environment
    variable `--postgres-env` names, so the string (password included) stays out of argv. Or
    several (ticket 89): `run` reads them from a data-source file, and the MCP server `run`
    starts gets them as `--source` entries."""
    database = parser.add_mutually_exclusive_group(required=True)
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
    else:
        database.add_argument(
            "--data-sources",
            type=Path,
            help="TOML file naming the data sources, as `data-agent web --data-sources` reads; "
            "with several, the agent picks the one each query runs on",
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
    parser: argparse.ArgumentParser, args: argparse.Namespace, config: RunConfig
) -> Sandbox | None:
    """The sandbox `mcp-server --sandbox` serves sandbox_exec on: it works in the workspace and
    sees the config's Skill directory; a container is named and labelled by `--run-id`."""
    if args.sandbox is None:
        if args.run_id is not None:
            parser.error("--run-id goes with --sandbox")
        if args.network:
            parser.error("--network goes with --sandbox: it turns the sandbox's network on")
        return None
    if args.workspace is None:
        parser.error("--sandbox needs --workspace: the sandbox works in it")
    if args.sandbox == "local":
        return LocalSandbox(args.workspace, config.skills_dir)
    if args.run_id is None:
        parser.error("--sandbox docker needs --run-id, the id its container is named after")
    return DockerSandbox(args.workspace, config.skills_dir, args.run_id, network=args.network)


def server_dbt(parser: argparse.ArgumentParser, args: argparse.Namespace) -> DbtExecutor | None:
    """The dbt_execute `mcp-server --dbt-target` serves: on the workspace's project, with the
    server's one database; `scratch` writes its profile to `--dbt-profiles-dir` and names its
    schema after `--run-id`."""
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
        )
    except DbtExecuteError as exc:
        parser.error(str(exc))


def serve_sources(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    config: RunConfig,
    sandbox: Sandbox | None,
) -> None:
    """`mcp-server --source ...`: one server over every database the entries name."""
    sources = given_sources(parser, args.source)
    names = {source.name for source in sources}
    dictionaries: dict[str, Dictionary] = {}
    for name, path in args.source_dictionary or []:
        if name not in names:
            parser.error(f"--source-dictionary {name}: no --source has that name")
        if not Path(path).is_file():
            parser.error(f"dictionary file not found: {path}")
        dictionaries[name] = Dictionary.load(Path(path))
    gateways = {source.name: source_gateway(source, os.environ) for source in sources}
    build_multi_server(gateways, config, args.workspace, dictionaries, sandbox).run("stdio")


def file_task(parser: argparse.ArgumentParser, args: argparse.Namespace, text: str) -> Task:
    """The task of `run --data-sources FILE`. A file with one source runs as `--duckdb` or
    `--postgres-env` would, under the source's name; with several, the agent sees them all."""
    if args.data_source_name is not None:
        parser.error(
            "--data-source-name goes with --duckdb or --postgres-env; the file names its sources"
        )
    try:
        sources = load_data_sources(args.data_sources)
    except ValueError as exc:
        parser.error(str(exc))
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
        parser.error("a dbt run takes one data source; --data-sources names several")
    return Task(text=text, type=args.type, workspace=args.workspace, sources=tuple(sources))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="data-agent")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("mcp-server", help="serve the data tools over MCP stdio")
    serve.add_argument("--config", required=True, choices=CONFIG_NAMES)
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

    run_cmd = commands.add_parser("run", help="run the agent on one task")
    task_arg = run_cmd.add_mutually_exclusive_group(required=True)
    task_arg.add_argument("--task", help="task text")
    task_arg.add_argument("--task-file", type=Path, help="file holding the task text")
    run_cmd.add_argument("--type", required=True, choices=("dbt", "query"))
    run_cmd.add_argument("--workspace", required=True, type=Path, help="working directory")
    add_database(run_cmd, server=False)
    run_cmd.add_argument("--config", required=True, choices=CONFIG_NAMES)
    run_cmd.add_argument("--events-dir", required=True, type=Path, help="where the event file goes")
    run_cmd.add_argument(
        "--data-source-name",
        help="the data source's name in the event file (default: the --postgres-env variable's "
        "name or the --duckdb file's name)",
    )
    run_cmd.add_argument(
        "--agent-timeout-sec",
        type=float,
        help="the trial's time limit, counted from now; the run stops itself before it",
    )
    # A run-level switch, not a config layer: the configs are shared by the product and the
    # evaluations (ticket 98). Off unless given, so leaving it out only takes tools away.
    run_cmd.add_argument(
        "--network",
        action="store_true",
        help="give the agent WebSearch and WebFetch, and the docker sandbox the network "
        "(never on an evaluation run)",
    )
    # Run-level too (ticket 104): unset, the CLI's own default holds (`agent.EFFORTS`).
    run_cmd.add_argument(
        "--effort",
        choices=EFFORTS,
        help="the model's reasoning effort (default: the Claude Code CLI's)",
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
    run_cmd.add_argument("--sandbox", choices=BACKENDS, default="docker", help=SANDBOX_HELP)
    run_cmd.add_argument("--dbt-target", choices=TARGETS, default="task", help=DBT_TARGET_HELP)
    run_cmd.add_argument(
        "--mcp-servers",
        type=Path,
        help="JSON file of external MCP servers: Claude Code / Cursor `mcpServers`, plus an "
        "`allowedTools` list per server (never on an evaluation run)",
    )

    web = commands.add_parser("web", help="serve the web UI on this machine's loopback address")
    web.add_argument("--runs-dir", required=True, type=Path, help="one subdirectory per run")
    web.add_argument("--port", type=int, default=WEB_PORT)
    web.add_argument(
        "--data-sources",
        type=Path,
        help="TOML file naming the data sources the run form offers (none without it)",
    )
    web.add_argument(
        "--mcp-servers",
        type=Path,
        help="JSON file of external MCP servers for every run: Claude Code / Cursor "
        "`mcpServers`, plus an `allowedTools` list per server",
    )

    args = parser.parse_args(argv)
    if args.command == "web":
        serve_web(web, args.runs_dir, args.port, args.data_sources)
        return
    # The deadline counts from the earliest point the process can see (ticket 54).
    started = time.monotonic()
    command = serve if args.command == "mcp-server" else run_cmd
    try:
        config = load_config(args.config)
    except ValueError as exc:
        command.error(str(exc))
    check_database(command, args)
    if args.command == "mcp-server":
        if args.workspace is not None and not args.workspace.is_dir():
            serve.error(f"workspace directory not found: {args.workspace}")
        sandbox = server_sandbox(serve, args, config)
        dbt = server_dbt(serve, args)
        if args.source is not None:
            if args.dictionary is not None:
                serve.error("with --source, give each database's dictionary as --source-dictionary")
            serve_sources(serve, args, config, sandbox)
            return
        if args.source_dictionary is not None:
            serve.error("--source-dictionary goes with --source")
        if args.dictionary is not None and not args.dictionary.is_file():
            serve.error(f"dictionary file not found: {args.dictionary}")
        gateway: Gateway = (
            DuckDBGateway(args.duckdb)
            if args.duckdb is not None
            else PostgresGateway(os.environ[args.postgres_env])
        )
        # Without the file (the server run on its own) build_server builds the dictionary.
        dictionary = Dictionary.load(args.dictionary) if args.dictionary is not None else None
        build_server(
            gateway, config, args.workspace, not args.no_verifier_tools, dictionary, sandbox, dbt
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
    if args.data_sources is not None:
        task = file_task(run_cmd, args, text)
    else:
        task = Task(
            text=text,
            type=args.type,
            workspace=args.workspace,
            duckdb=args.duckdb,
            postgres_env=args.postgres_env,
            data_source=args.data_source_name,
        )
    try:
        deadline = None if args.agent_timeout_sec is None else started + args.agent_timeout_sec
        # A turn of a conversation is steered through stdin (contract item 6); a single run never
        # reads it. A cancelled run is stopped by signal, so its stdin is read no more.
        steering = None if turn is None else stdin_lines(STDIN_FD)
        outcome = asyncio.run(
            run(
                task,
                config,
                args.events_dir,
                os.environ,
                deadline,
                turn=turn,
                steering=steering,
                sandbox=args.sandbox,
                network=args.network,
                dbt_target=args.dbt_target,
                effort=args.effort,
            )
        )
    except ConfigError as exc:
        run_cmd.error(str(exc))
    print(outcome.answer)
    for error in outcome.errors:
        print(f"error: {error}", file=sys.stderr)
    print(f"events: {outcome.events_path}", file=sys.stderr)
    if outcome.infra_error:
        sys.exit(EXIT_ERROR)
    sys.exit(EXIT_OK if outcome.finished else EXIT_UNFINISHED)


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


def serve_web(
    parser: argparse.ArgumentParser, runs_dir: Path, port: int, sources_file: Path | None
) -> None:
    if not runs_dir.is_dir():
        parser.error(f"runs directory not found: {runs_dir}")
    # Imported here so `data-agent run` does not load the web stack.
    import uvicorn

    from data_agent.web.app import create_app
    from data_agent.web.sources import load_data_sources

    # Read only when given: there is no default path (spec "本地运行的数据源").
    try:
        sources = load_data_sources(sources_file) if sources_file is not None else []
    except ValueError as exc:
        parser.error(str(exc))

    def start_run(args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
        # Its own session, so cancelling can stop the run with the tools it started. A stdin pipe
        # of its own: a conversation's turn is steered through it (data_agent.web.conversations),
        # and no run reads the server's.
        return subprocess.Popen(
            [sys.executable, "-m", "data_agent", "run", *args],
            cwd=cwd,
            start_new_session=True,
            stdin=subprocess.PIPE,
        )

    try:
        app = create_app(runs_dir.resolve(), sources, start_run)
    except ValueError as exc:
        parser.error(str(exc))
    # On SIGINT or SIGTERM uvicorn first waits for open responses to end, and only then runs the
    # app's lifespan shutdown, which stops the run (`Launcher.shutdown`). An open event stream ends
    # only with its run, so without a limit the run would go on until it finished. Past the limit
    # uvicorn cancels the remaining responses and goes on to the lifespan shutdown (uvicorn
    # `Server.shutdown`). A few seconds. A cancel or submit cut off by the limit goes on in its
    # thread, holding the launcher's lock, so the lifespan shutdown waits for it and then
    # stops whatever run is left.
    uvicorn.run(app, host=WEB_HOST, port=port, timeout_graceful_shutdown=3)
