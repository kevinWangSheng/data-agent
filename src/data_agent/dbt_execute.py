"""`dbt_execute`: the one way a run's dbt reaches the database (ticket 99, ADR-0010 points 2-3).

Adapted from SignalPilot (Apache-2.0, see NOTICE): the tool's shape and its per-run scratch schema
follow `signalpilot/gateway/gateway/mcp/tools/dbt_execute.py`; the command allowlist, the selector
pattern, the 20,000-character output cap, the 900-second limit, `build_dbt_argv`, the reply
(`exit_code`, `output`, the `run_results` summary) and the Postgres profile (`_url_parts`,
`_emit_postgres`) follow `signalpilot/gateway/gateway/standalone_chat/dbt_executor.py`. Changes:
dbt runs here, on the host, with the workspace's project as `--project-dir` (this machine has the
workspace, so nothing is synced); `threads` outside 1-8 is refused instead of clamped; the
profile is a DuckDB one too, written as JSON (which YAML reads) to a directory outside the
workspace; the run's id names the schema (`scratch_schema`); and a `task` target runs the
project's own profile, as a run did before (Harbor). The run_results summary is read only when
this command wrote the file.

The agent and the sandbox never see the generated profile: it is outside the workspace, and the
sandbox mounts the workspace alone.
"""

import json
import os
import re
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple
from urllib.parse import parse_qs, unquote, urlparse

from data_agent.sandbox import local_env, run_process
from data_agent.verifier_v2.project import PROJECT_FILE, dbt_projects, project_config

type Target = Literal["scratch", "task"]
TARGETS: tuple[Target, ...] = ("scratch", "task")

ALLOWED_COMMANDS = ("run", "test", "build", "seed", "snapshot", "compile", "docs generate")
# Node-selector syntax only. A leading dash would let a value pass for a flag, so it is refused
# even though it gets an argv slot of its own (SignalPilot `_SELECTOR_RE`).
SELECTOR = re.compile(r"^[\w.+:,*@/][\w .+:,*@/-]*$")
MIN_THREADS, MAX_THREADS = 1, 8
MAX_OUTPUT_CHARS = 20_000
TIMEOUT_S = 900
# The output the generated profile defines, named on the command line.
SCRATCH_TARGET = "da_run"
SCHEMA_PREFIX = "da_run_"
# Postgres cuts identifiers at 63 bytes; the schema keeps the run id's end, its random part.
MAX_SCHEMA_CHARS = 63
PROFILES_FILE = "profiles.yml"
RUN_RESULTS = Path("target") / "run_results.json"
MAX_FAILURES = 10


class DbtExecuteError(Exception):
    """A call dbt_execute refuses, or a dbt it cannot start."""


def scratch_schema(run_id: str) -> str:
    """`da_run_<run id>`, lower case, with each character outside [a-z0-9_] as `_`, so the name
    needs no quoting in DuckDB or Postgres. Past 63 characters the middle of the id goes: its end
    is the random part that keeps two runs of the same name apart."""
    name = SCHEMA_PREFIX + re.sub(r"[^a-z0-9_]", "_", run_id.lower())
    if len(name) <= MAX_SCHEMA_CHARS:
        return name
    tail = name[-9:]
    return name[: MAX_SCHEMA_CHARS - len(tail) - 1] + "_" + tail


def dbt_argv(
    command: str,
    select: str = "",
    exclude: str = "",
    full_refresh: bool = False,
    threads: int | None = None,
) -> list[str]:
    """The dbt subcommand and its flags from the structured arguments (SignalPilot
    `build_dbt_argv`); the caller adds where dbt finds the project and the profile."""
    parts = command.strip().lower().split()
    if " ".join(parts) not in ALLOWED_COMMANDS:
        raise DbtExecuteError(
            f"command must be one of: {', '.join(ALLOWED_COMMANDS)}; got {command!r}. Pass "
            "selectors as select/exclude; other dbt commands (parse, ls, deps, debug) run in "
            "sandbox_exec"
        )
    argv = ["dbt", *parts, "--no-use-colors"]
    for flag, value in (("--select", select), ("--exclude", exclude)):
        if value:
            if not SELECTOR.match(value):
                raise DbtExecuteError(
                    f"{flag.lstrip('-')} must be dbt node selector syntax (it may not start "
                    f"with '-'); got {value!r}"
                )
            argv += [flag, value]
    if full_refresh:
        argv.append("--full-refresh")
    if threads is not None:
        if not MIN_THREADS <= threads <= MAX_THREADS:
            raise DbtExecuteError(f"threads must be {MIN_THREADS} to {MAX_THREADS}, got {threads}")
        argv += ["--threads", str(threads)]
    return argv


def workspace_project(workspace: Path) -> Path:
    """The dbt project in the workspace: the one nearest its top (packages, build output and
    hidden directories are not looked in). Several at the same depth are an error."""
    found = dbt_projects(workspace)
    if not found:
        raise DbtExecuteError(f"no {PROJECT_FILE} in the workspace")
    depth = min(len(path.parts) for path in found)
    nearest = [path for path in found if len(path.parts) == depth]
    if len(nearest) > 1:
        names = ", ".join(str(path.relative_to(workspace)) for path in nearest)
        raise DbtExecuteError(f"the workspace has several dbt projects at one level: {names}")
    [project] = nearest
    if not project.resolve().is_relative_to(workspace.resolve()):
        raise DbtExecuteError("the dbt project is outside the workspace")
    return project


def postgres_output(url: str, schema: str) -> dict[str, object]:
    """A dbt-postgres output for the connection string (SignalPilot `_url_parts`,
    `_emit_postgres`)."""
    parts = urlparse(url)
    query = {key: values[0] for key, values in parse_qs(parts.query).items()}
    return {
        "type": "postgres",
        "host": parts.hostname or "",
        "port": parts.port or 5432,
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "dbname": (parts.path or "/").lstrip("/"),
        "schema": schema,
        "threads": 4,
        "sslmode": query.get("sslmode", "prefer"),
    }


def scratch_profile(
    profile: str, schema: str, duckdb: Path | None, postgres_url: str | None
) -> dict[str, object]:
    """The profile `scratch` writes: the project's profile name, one output whose default schema
    is the run's own."""
    if duckdb is not None:
        output: dict[str, object] = {
            "type": "duckdb",
            "path": str(duckdb.resolve()),
            "schema": schema,
            "threads": 4,
        }
    else:
        assert postgres_url is not None, "a dbt run has a DuckDB file or a Postgres URL"
        output = postgres_output(postgres_url, schema)
    return {profile: {"target": SCRATCH_TARGET, "outputs": {SCRATCH_TARGET: output}}}


class DbtRun(NamedTuple):
    """A dbt run's `--dbt-target` and, for `scratch`, where its profile goes, as the run passes
    them to its MCP server."""

    target: Target
    profiles_dir: Path | None = None

    @classmethod
    def of_run(cls, target: Target, events_dir: Path) -> "DbtRun":
        """`scratch` writes its profile to `<events dir>/profiles/`: the web UI's run directory,
        which holds the workspace but is not in it."""
        if target == "task":
            return cls("task")
        return cls("scratch", events_dir.resolve() / "profiles")

    def server_args(self) -> tuple[str, ...]:
        profiles = (
            () if self.profiles_dir is None else ("--dbt-profiles-dir", str(self.profiles_dir))
        )
        return ("--dbt-target", self.target, *profiles)


@dataclass(frozen=True)
class DbtExecutor:
    """Runs dbt on the workspace's project. `scratch` writes, before each command, a profile to
    `profiles_dir` (outside the workspace) whose schema is the run's (`scratch_schema(run_id)`),
    on the run's DuckDB file or the Postgres URL in `postgres_env`; dbt sees only the plain
    variables of this process (`local_env`). `task` uses the project's own profile, with the
    `DBT_*` variables and the Postgres one too."""

    workspace: Path
    target: Target
    run_id: str = ""
    profiles_dir: Path | None = None
    duckdb: Path | None = None
    postgres_env: str | None = None

    def __post_init__(self) -> None:
        if self.target == "scratch":
            if self.profiles_dir is None or not self.run_id:
                raise DbtExecuteError("the scratch target needs a profiles directory and a run id")
            if self.profiles_dir.resolve().is_relative_to(self.workspace.resolve()):
                raise DbtExecuteError(
                    "the scratch profiles directory must be outside the workspace"
                )
        if (self.duckdb is None) == (self.postgres_env is None):
            raise DbtExecuteError("a dbt run has one DuckDB file or one Postgres variable")

    @property
    def schema(self) -> str:
        return scratch_schema(self.run_id)

    def command_line(
        self, argv: list[str], env: Mapping[str, str]
    ) -> tuple[list[str], Path, dict[str, str]]:
        """The full argv, the directory dbt runs in (the project, so that relative paths in a
        task's profile resolve from it) and dbt's environment. `scratch` writes its profile."""
        project = workspace_project(self.workspace)
        argv = [*argv, "--project-dir", str(project.resolve())]
        dbt_env = local_env(env)
        if self.target == "scratch":
            assert self.profiles_dir is not None
            self.write_profile(project, env)
            argv += ["--profiles-dir", str(self.profiles_dir), "--target", SCRATCH_TARGET]
        else:
            if (project / PROFILES_FILE).is_file():
                argv += ["--profiles-dir", str(project.resolve())]
            dbt_env |= {name: value for name, value in env.items() if name.startswith("DBT_")}
            if self.postgres_env is not None and self.postgres_env in env:
                dbt_env[self.postgres_env] = env[self.postgres_env]
        return argv, project, dbt_env

    def write_profile(self, project: Path, env: Mapping[str, str]) -> None:
        assert self.profiles_dir is not None
        config = project_config(project)
        name = str(config.get("profile") or config.get("name") or "default")
        url = None
        if self.postgres_env is not None:
            url = env.get(self.postgres_env)
            if not url:
                raise DbtExecuteError(f"{self.postgres_env} (the Postgres URL) is not set")
        profile = scratch_profile(name, self.schema, self.duckdb, url)
        self.profiles_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.profiles_dir / PROFILES_FILE
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(profile, f, indent=2)

    def execute(
        self,
        command: str,
        select: str = "",
        exclude: str = "",
        full_refresh: bool = False,
        threads: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> str:
        """Run the command and reply as SignalPilot does, `target_schema` first for `scratch`."""
        argv = dbt_argv(command, select, exclude, full_refresh, threads)
        env = os.environ if env is None else env
        full, project, dbt_env = self.command_line(argv, env)
        executable = shutil.which("dbt", path=env.get("PATH"))
        if executable is None:
            raise DbtExecuteError("dbt is not on PATH")
        full[0] = executable
        started = time.time()
        done = run_process(full, TIMEOUT_S, cwd=project, env=dbt_env)
        output = done.stdout + (f"\n{done.stderr}" if done.stderr else "")
        output = output[-MAX_OUTPUT_CHARS:].strip()
        if done.timed_out:
            raise DbtExecuteError(
                f"dbt timed out after {TIMEOUT_S} seconds and was killed. Its output until "
                f"then:\n{output}"
            )
        sections = [f"target_schema: {self.schema}"] if self.target == "scratch" else []
        sections += [f"exit_code: {done.exit_code}", f"output:\n{output}"]
        sections += run_results(project / RUN_RESULTS, started)
        return "\n".join(sections)


def run_results(path: Path, since: float) -> list[str]:
    """The statuses of the nodes in run_results.json and the first failures, when this command
    wrote the file (an earlier one's would describe another command)."""
    try:
        if path.stat().st_mtime < since:
            return []
        parsed = json.loads(path.read_text())
        results = parsed.get("results", [])
    except (OSError, ValueError, AttributeError):
        return []
    statuses: dict[str, int] = {}
    failures: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        status = str(result.get("status"))
        statuses[status] = statuses.get(status, 0) + 1
        if status in ("error", "fail") and len(failures) < MAX_FAILURES:
            failures.append(f"{result.get('unique_id')}: {str(result.get('message'))[:200]}")
    if not statuses:
        return []
    lines = ["run_results: " + ", ".join(f"{k}={v}" for k, v in sorted(statuses.items()))]
    if failures:
        lines.append("failures:\n" + "\n".join(failures))
    return lines


def tool_description(executor: DbtExecutor) -> str:
    if executor.target == "scratch":
        where = (
            f"Models materialize into this run's own schema, `{executor.schema}` (a model with a "
            f"custom schema goes to `{executor.schema}_<custom>`), never the project's "
            "production schemas; sources are read where they are."
        )
    else:
        where = "dbt uses the project's own profile and writes to the database it names."
    return f"""\
Run a dbt command on the workspace's dbt project against the database, and return dbt's exit \
code and output. This is the only way dbt reaches the database: the connection profile is held \
outside the workspace and the sandbox. {where}

`command` is one of: {", ".join(ALLOWED_COMMANDS)}. `select` and `exclude` take dbt node \
selector syntax (no flags); `full_refresh` rebuilds incremental models; `threads` is 1 to \
{MAX_THREADS}. Other dbt commands (parse, ls, deps, debug) run in sandbox_exec with a stub \
profile. The output shows its last {MAX_OUTPUT_CHARS:,} characters, then the node statuses from \
run_results.json. A command is killed after {TIMEOUT_S} seconds."""
