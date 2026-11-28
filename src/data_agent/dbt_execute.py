"""`dbt_execute`: the one way a run's dbt reaches the database (ticket 99, ADR-0010 points 2-3).

Adapted from SignalPilot (Apache-2.0, see NOTICE): the tool's shape and its per-run scratch schema
follow `signalpilot/gateway/gateway/mcp/tools/dbt_execute.py`; the command allowlist, the selector
pattern, the 20,000-character output cap, the 900-second limit, `build_dbt_argv`, the reply
(`exit_code`, `output`, the `run_results` summary) and the Postgres profile (`_emit_postgres`)
follow `signalpilot/gateway/gateway/standalone_chat/dbt_executor.py`. Changes: the connection
string is parsed by libpq, as the gateway's is, not by `_url_parts`; dbt runs here, on the host,
with the workspace's project as `--project-dir` (this machine has the workspace, so nothing is
synced); `threads` outside 1-8 is refused instead of clamped; the profile is a DuckDB one too,
written as JSON (which YAML reads) to a directory outside the workspace; the run's id names the
schema (`scratch_schema`); and a `task` target runs the project's own profile, as a run did
before (Harbor). The run_results summary is read only when this command wrote the file.

The agent and the sandbox never see the generated profile: it is outside the workspace, and the
sandbox mounts the workspace alone.
"""

import contextlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple

import psycopg
from psycopg.conninfo import conninfo_to_dict

from data_agent.sandbox import KILL_GRACE_S, Capture, Execution, local_env
from data_agent.verifier_v2.project import PROJECT_FILE, dbt_projects

type Target = Literal["scratch", "task"]
TARGETS: tuple[Target, ...] = ("scratch", "task")

ALLOWED_COMMANDS = ("run", "test", "build", "seed", "snapshot", "compile", "docs generate")
# Node-selector syntax only. A leading dash would let a value pass for a flag, so it is refused
# even though it gets an argv slot of its own (SignalPilot `_SELECTOR_RE`).
SELECTOR = re.compile(r"^[\w.+:,*@/][\w .+:,*@/-]*$")
MIN_THREADS, MAX_THREADS = 1, 8
MAX_OUTPUT_CHARS = 20_000
TIMEOUT_S = 900
# The profile and the output the generated profiles.yml defines, both named on the command line,
# so dbt never has to match the name dbt_project.yml gives.
SCRATCH_PROFILE = "da_run"
SCRATCH_TARGET = "da_run"
SCHEMA_PREFIX = "da_run_"
# Postgres cuts identifiers at 63 bytes; the schema keeps the run id's end, its random part.
MAX_SCHEMA_CHARS = 63
PROFILES_FILE = "profiles.yml"
RUN_RESULTS = Path("target") / "run_results.json"
MAX_FAILURES = 10
# `env_var('NAME')` or `env_var("NAME", default)` in a profile's Jinja.
ENV_VAR_CALL = re.compile(r"""env_var\s*\(\s*['"]([^'"]+)['"]""")
# The run's model credentials (`ModelSettings.sdk_env`, `tool_env`): a task's profile is in the
# workspace, which the agent edits, so naming them there does not pass them to dbt.
MODEL_ENV_PREFIXES = ("DATA_AGENT_", "ANTHROPIC_", "CLAUDE_")
# The threads of a generated profile's output, which `threads` overrides per command.
PROFILE_THREADS = 4
POSTGRES_PORT = 5432
# libpq keys a dbt-postgres output also has, carried over as given.
TLS_FILES = ("sslrootcert", "sslcert", "sslkey")


class DbtExecuteError(Exception):
    """A call dbt_execute refuses, or a dbt it cannot start."""


def scratch_schema(run_id: str) -> str:
    """`da_run_<run id>`, lower case, with each character outside [a-z0-9_] as `_`, so the name
    needs no quoting in DuckDB or Postgres. Past 63 characters the middle of the id goes: its end
    is the random part that keeps two runs of the same name apart."""
    name = SCHEMA_PREFIX + re.sub(r"[^a-z0-9_]", "_", run_id.lower())
    if len(name) <= MAX_SCHEMA_CHARS:
        return name
    tail = name[-8:]
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


def workspace_project(workspace: Path, project_dir: str = "") -> Path:
    """The dbt project in the workspace. `project_dir` (relative to the workspace, or absolute)
    names it, and must be inside the workspace with symlinks followed; without it, the one
    nearest the workspace's top (packages, build output and hidden directories are not looked
    in). Several at the same depth then are an error naming them."""
    root = workspace.resolve()
    if project_dir:
        project = (workspace / project_dir).resolve()
        if not project.is_relative_to(root):
            raise DbtExecuteError(
                f"project_dir {project_dir!r} is outside the workspace; give the project's "
                "directory relative to the workspace (dbt_execute runs outside the sandbox, so "
                "the sandbox's paths do not apply)"
            )
        if not (project / PROJECT_FILE).is_file():
            raise DbtExecuteError(f"project_dir {project_dir!r} has no {PROJECT_FILE}")
        return project
    found = dbt_projects(workspace)
    if not found:
        raise DbtExecuteError(f"no {PROJECT_FILE} in the workspace")
    depth = min(len(path.parts) for path in found)
    nearest = [path for path in found if len(path.parts) == depth]
    if len(nearest) > 1:
        names = ", ".join(str(path.relative_to(workspace)) for path in nearest)
        raise DbtExecuteError(
            f"the workspace has several dbt projects at one level: {names}; pass project_dir "
            "(one of them, relative to the workspace)"
        )
    [project] = nearest
    if not project.resolve().is_relative_to(root):
        raise DbtExecuteError("the dbt project is outside the workspace")
    return project


def postgres_output(url: str, schema: str) -> dict[str, object]:
    """A dbt-postgres output for the connection string (SignalPilot `_emit_postgres`). The
    string is parsed by libpq, as the read-only gateway's connection parses it, so both decode
    it the same way; the TLS files and the connect timeout carry over too. A string dbt-postgres
    cannot hold (several hosts, a bad port, an unknown key) is refused."""
    try:
        params = conninfo_to_dict(url)
    except psycopg.ProgrammingError:
        # libpq's message can quote the password; neither it nor the cause goes to the agent.
        raise DbtExecuteError("the Postgres connection string does not parse") from None
    host = str(params.get("host") or "")
    port = str(params.get("port") or POSTGRES_PORT)
    if "," in host or not port.isdigit():
        raise DbtExecuteError("the Postgres connection string must name one host and one port")
    output: dict[str, object] = {
        "type": "postgres",
        "host": host,
        "port": int(port),
        "user": str(params.get("user") or ""),
        "password": str(params.get("password") or ""),
        "dbname": str(params.get("dbname") or ""),
        "schema": schema,
        "threads": PROFILE_THREADS,
        "sslmode": str(params.get("sslmode") or "prefer"),
    }
    output.update({key: str(params[key]) for key in TLS_FILES if params.get(key)})
    if params.get("connect_timeout"):
        output["connect_timeout"] = int(str(params["connect_timeout"]))
    return output


def scratch_profile(
    schema: str, duckdb: Path | None, postgres_url: str | None
) -> dict[str, object]:
    """The profile `scratch` writes: one output whose default schema is the run's own."""
    if duckdb is not None:
        output: dict[str, object] = {
            "type": "duckdb",
            "path": str(duckdb.resolve()),
            "schema": schema,
            "threads": PROFILE_THREADS,
        }
    else:
        assert postgres_url is not None, "a dbt run has a DuckDB file or a Postgres URL"
        output = postgres_output(postgres_url, schema)
    return {SCRATCH_PROFILE: {"target": SCRATCH_TARGET, "outputs": {SCRATCH_TARGET: output}}}


def check_outside(profiles_dir: Path, workspace: Path) -> None:
    """The scratch profile's directory may not be in the workspace, which the agent and the
    sandbox reach."""
    if profiles_dir.resolve().is_relative_to(workspace.resolve()):
        raise DbtExecuteError(
            f"the scratch profiles directory ({profiles_dir}) must be outside the workspace"
        )


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
    `DBT_*` variables, the Postgres one and those the profile reads (`profile_env_names`)."""

    workspace: Path
    target: Target
    run_id: str | None = None
    profiles_dir: Path | None = None
    duckdb: Path | None = None
    postgres_env: str | None = None
    # How long a dbt command may run (`[sources.<name>].dbt_timeout_seconds`, ticket 144).
    timeout_seconds: float = TIMEOUT_S

    def __post_init__(self) -> None:
        if self.target == "scratch":
            if self.profiles_dir is None or not self.run_id:
                raise DbtExecuteError("the scratch target needs a profiles directory and a run id")
            check_outside(self.profiles_dir, self.workspace)
        if (self.duckdb is None) == (self.postgres_env is None):
            raise DbtExecuteError("a dbt run has one DuckDB file or one Postgres variable")

    @property
    def schema(self) -> str:
        assert self.run_id is not None, "only the scratch target has a schema of its own"
        return scratch_schema(self.run_id)

    def command_line(
        self, argv: list[str], env: Mapping[str, str], project_dir: str = ""
    ) -> tuple[list[str], Path, dict[str, str]]:
        """The full argv, the directory dbt runs in (the project, `workspace_project`, so that
        relative paths in a task's profile resolve from it) and dbt's environment. `scratch`
        writes its profile."""
        project = workspace_project(self.workspace, project_dir)
        argv = [*argv, "--project-dir", str(project.resolve())]
        dbt_env = local_env(env)
        if self.target == "scratch":
            assert self.profiles_dir is not None
            self.write_profile(env)
            # Absolute: dbt runs in the project, the server wrote from its own directory.
            argv += ["--profiles-dir", str(self.profiles_dir.resolve())]
            argv += ["--profile", SCRATCH_PROFILE, "--target", SCRATCH_TARGET]
        else:
            profiles_file = task_profiles_file(project, env)
            if profiles_file == project / PROFILES_FILE:
                argv += ["--profiles-dir", str(project.resolve())]
            dbt_env |= {name: value for name, value in env.items() if name.startswith("DBT_")}
            names = {
                name
                for name in profile_env_names(profiles_file)
                if not name.startswith(MODEL_ENV_PREFIXES)
            }
            if self.postgres_env is not None:
                names.add(self.postgres_env)
            dbt_env |= {name: env[name] for name in names if name in env}
        return argv, project, dbt_env

    def write_profile(self, env: Mapping[str, str]) -> None:
        assert self.profiles_dir is not None
        url = None
        if self.postgres_env is not None:
            url = env.get(self.postgres_env)
            if not url:
                raise DbtExecuteError(f"{self.postgres_env} (the Postgres URL) is not set")
        profile = scratch_profile(self.schema, self.duckdb, url)
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
        project_dir: str = "",
        env: Mapping[str, str] | None = None,
    ) -> str:
        """Run the command and reply as SignalPilot does, `target_schema` first for `scratch`."""
        argv = dbt_argv(command, select, exclude, full_refresh, threads)
        env = os.environ if env is None else env
        dbt_command, project, dbt_env = self.command_line(argv, env, project_dir)
        executable = shutil.which("dbt", path=env.get("PATH"))
        if executable is None:
            raise DbtExecuteError("dbt is not on PATH")
        dbt_command[0] = executable
        started = time.time()
        done = run_dbt(dbt_command, self.timeout_seconds, project, dbt_env)
        output = done.stdout + (f"\n{done.stderr}" if done.stderr else "")
        output = output[-MAX_OUTPUT_CHARS:].strip()
        if done.timed_out:
            raise DbtExecuteError(
                f"dbt timed out after {self.timeout_seconds:g} seconds and was killed. Its "
                f"output until then:\n{output}"
            )
        sections = [f"target_schema: {self.schema}"] if self.target == "scratch" else []
        sections += [f"exit_code: {done.exit_code}", f"output:\n{output}"]
        sections += run_results(project / RUN_RESULTS, started)
        return "\n".join(sections)


def task_profiles_file(project: Path, env: Mapping[str, str]) -> Path | None:
    """The profiles.yml a `task` run's dbt reads, found as dbt finds it: `--profiles-dir` (the
    project's own file, which `command_line` passes), then DBT_PROFILES_DIR (from the project,
    where dbt runs), then ~/.dbt."""
    if (project / PROFILES_FILE).is_file():
        return project / PROFILES_FILE
    if env.get("DBT_PROFILES_DIR"):
        return project / env["DBT_PROFILES_DIR"] / PROFILES_FILE
    if env.get("HOME"):
        return Path(env["HOME"]) / ".dbt" / PROFILES_FILE
    return None


def profile_env_names(path: Path | None) -> set[str]:
    """The literal names a profile reads with `env_var('NAME')`. A task's profile may take its
    connection from any variables (the jaffle evaluation's DA_JAFFLE_*); only those reach dbt,
    less the run's model credentials (`command_line`)."""
    if path is None or not path.is_file():
        return set()
    return set(ENV_VAR_CALL.findall(path.read_text(errors="replace")))


def run_dbt(argv: list[str], timeout: float, cwd: Path, env: Mapping[str, str]) -> Execution:
    """Run dbt to its end, or kill it past `timeout`. Unlike `sandbox.run_process`, dbt stays in
    this process's group: cancelling a run signals the run's group, which then stops dbt too, and
    dbt starts no process of its own to outlive it."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stderr is not None
    out, err = Capture(proc.stdout), Capture(proc.stderr)
    try:
        proc.wait(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        proc.wait()
    for capture in (out, err):
        capture.thread.join(KILL_GRACE_S)
    return Execution(None if timed_out else proc.returncode, out.text(), err.text(), timed_out)


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
            f"The profile's schema is this run's own, `{executor.schema}`: models materialize "
            f"there (a model with a custom schema in `{executor.schema}_<custom>`), not in the "
            "project's production schemas, and sources are read where they are. The schema "
            "starts empty, so a model whose upstream models are not built in it yet fails: "
            "select it with its upstream models (`+<model>`) or run `build`. Write nothing "
            "that names another schema (a snapshot's target_schema, a hook, a schema macro): "
            "dbt would write it there."
        )
    else:
        where = "dbt uses the project's own profile and writes to the database it names."
    return f"""\
Run a dbt command on the workspace's dbt project against the database, and return dbt's exit \
code and output. This is the only way dbt reaches the database: the connection profile is held \
outside the workspace and the sandbox. {where}

`command` is one of: {", ".join(ALLOWED_COMMANDS)}. `select` and `exclude` take dbt node \
selector syntax (no flags); `full_refresh` rebuilds incremental models; `threads` is 1 to \
{MAX_THREADS}. `project_dir` names the dbt project, as a path relative to the workspace; leave \
it empty when the workspace has one project, and pass it when there are several. Other dbt \
commands (parse, ls, deps, debug) run in sandbox_exec with a stub profile. The output shows its \
last {MAX_OUTPUT_CHARS:,} characters, then the node statuses from run_results.json. A command is \
killed after {executor.timeout_seconds:g} seconds."""
