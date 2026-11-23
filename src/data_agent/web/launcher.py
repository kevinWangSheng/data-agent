"""Starting and cancelling runs from the web backend (spec "后端接口", Q39-Q40).

One run at a time. Each run gets `<runs_dir>/<id>/` for its event file and
`<runs_dir>/<id>/workspace/` as its working directory: empty for a query run, a copy of the data
source's dbt project for a dbt run. The copy leaves out `target/` and `logs/`, and what holds the
database or its connection (ticket 99, spec "凭据与只读"): the DuckDB file goes to
`<runs_dir>/<id>/db/` instead, and the project's `profiles.yml` and its credential files (`.env*`,
keys, or links to them; ticket 100) stay behind. The run's dbt reaches
the database through dbt_execute, whose `scratch` profile the run writes to
`<runs_dir>/<id>/profiles/` (`data_agent.dbt_execute.DbtRun`); the sandbox mounts only the
workspace, so it sees neither. A query run reads the DuckDB file in place through the read-only
gateway.

Cancelling signals only the process this server started, by its recorded pid (and its own process
group when it leads one). When a run's process exits, whatever it left running in its group is
stopped too, and a run without a `result` event gets status "cancelled" (when this server stopped
it) or "failure" in its `web-run.json` record (data_agent.web.runs), which the run list and the
event stream read.
"""

import contextlib
import os
import queue
import secrets
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from data_agent.run_config import CONFIG_NAMES
from data_agent.sandbox import Backend
from data_agent.web.runs import RunsDir
from data_agent.web.sources import DataSource, check_layout, overlap
from data_agent.workspace_guard import (
    is_credential,
    is_duckdb_name,
    is_profile_name,
    named_or_linked,
)

TASK_TYPES = ("query", "dbt")
# The only sandbox backend the web backend gives a run: `local` would run the agent's commands on
# this machine (ADR-0010).
SANDBOX: Backend = "docker"
# How long a run being stopped has to exit after SIGTERM before it gets SIGKILL.
STOP_GRACE_SECONDS = 10.0
WORKSPACE = "workspace"
# A dbt run's copy of the project leaves out the build output and logs of earlier runs, and
# every dbt profile in it, which holds the connection.
NOT_COPIED = ("target", "logs")
# Where a dbt run's DuckDB file goes, in the run's directory, outside its workspace.
DB_DIR = "db"
# The files DuckDB keeps next to a database file, copied with it.
DUCKDB_SIDE_SUFFIXES = (".wal",)

StartRun = Callable[[Sequence[str], Path], "subprocess.Popen[bytes]"]
"""Starts `data-agent run` with the given arguments (those after `data-agent run`) in the given
working directory and returns the process."""


class Refused(Exception):
    """A submit or cancel the server will not do; `status` is the HTTP status to answer with."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class StdinWriter:
    """A run's stdin, written from a thread of its own. A conversation's turn reads its stdin only
    once its session has started, so steers sent before then wait in the pipe; a write that has
    to wait for room never holds up the caller, who holds the launcher's lock. Lines after a
    failed write (the run has exited) are dropped."""

    def __init__(self, stdin: IO[bytes]) -> None:
        self.stdin = stdin
        self.lines: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        threading.Thread(target=self.write, name="run-stdin", daemon=True).start()

    def send(self, line: bytes) -> None:
        self.lines.put(line)

    def close(self) -> None:
        """Closes the stdin once the lines sent before are written (or dropped)."""
        self.lines.put(None)

    def write(self) -> None:
        broken = False
        while (line := self.lines.get()) is not None:
            if broken:
                continue
            try:
                self.stdin.write(line)
                self.stdin.flush()
            except OSError:
                broken = True
        with contextlib.suppress(OSError):
            self.stdin.close()


@dataclass
class Running:
    run_id: str
    process: "subprocess.Popen[bytes]"
    # Whether the run leads its own process group, which is then signalled as a whole.
    group: bool
    record: dict[str, Any]
    # Its stdin, through which a conversation's turn is steered; None when it has no pipe.
    stdin: StdinWriter | None = None
    # The conversation the run is a turn of (data_agent.web.conversations); None for a form run.
    conversation: str | None = None
    cancelling: bool = False
    # Set once the server has seen the run exit and cleaned up after it (`finish`).
    done: bool = False


Prepare = Callable[[Path], tuple[list[str], Path]]
"""Fills a new run's directory (given) and returns the run's arguments and working directory."""


class Launcher:
    def __init__(self, runs: RunsDir, sources: Sequence[DataSource], start_run: StartRun) -> None:
        check_layout(runs.path, sources)
        self.runs = runs
        self.sources = {source.name: source for source in sources}
        self.start_run = start_run
        self.current: Running | None = None
        # Held while a run is started, stopped or looked at, by conversations too
        # (data_agent.web.conversations): one run at a time across the run form and every
        # conversation.
        self.lock = threading.Lock()

    def submit(
        self, task: str, task_type: str, source_name: str, config: str, network: bool = True
    ) -> str:
        """Checks the request, prepares the run's directories and starts it; returns its id. The
        run has the web tools and a sandbox with the network (`data-agent run --network`, tickets
        98 and 100) unless `network` is off."""
        source = self.check(task, task_type, source_name, config)

        def prepare(run_dir: Path) -> tuple[list[str], Path]:
            workspace = run_dir / WORKSPACE
            database = prepare_workspace(workspace, source, task_type, self.runs.path)
            args = [
                f"--task={task}",
                "--type",
                task_type,
                "--config",
                config,
                "--events-dir",
                str(run_dir),
                "--workspace",
                str(workspace),
                *database,
                "--data-source-name",
                source.name,
                # The web backend runs only in a container (spec "`sandbox_exec`"), and its dbt
                # writes only to a schema of the run's own.
                *("--sandbox", SANDBOX),
                *(("--dbt-target", "scratch") if task_type == "dbt" else ()),
                *(("--network",) if network else ()),
                # Every run from the form may chart its answer (charts spec "开关"), dbt too.
                "--charts",
            ]
            return args, workspace

        with self.lock:
            return self.launch(task, task_type, config, prepare).run_id

    def active(self) -> Running | None:
        """The run whose process is still going; a run seen to have exited is finished first.
        Called with the lock held."""
        current = self.current
        if current is None or current.done:
            return None
        if current.process.poll() is None:
            return current
        self.finish(current)
        return None

    def launch(
        self,
        task: str,
        task_type: str,
        config: str,
        prepare: Prepare,
        conversation: str | None = None,
    ) -> Running:
        """Starts a run, refused with 409 while another is going. Its directory is made first, so
        its events read as an empty list from the moment it is started; `prepare` fills it, then
        the run is recorded and its process started. Nothing is left of a run that fails to start.
        Called with the lock held."""
        current = self.active()
        if current is not None:
            raise Refused(409, f"run {current.run_id} is still going; one run at a time")
        started_at = datetime.now(UTC)
        run_id = new_run_id(started_at)
        run_dir = self.runs.path / run_id
        run_dir.mkdir()
        record: dict[str, Any] = {
            "id": run_id,
            "task": task,
            "task_type": task_type,
            "config": config,
            "status": "running",
            "started_at": started_at.isoformat(),
            "duration_ms": None,
            "cost_usd": None,
        }
        try:
            args, cwd = prepare(run_dir)
            self.runs.write_record(run_id, record)
            process = self.start_run(args, cwd)
        except Exception:
            shutil.rmtree(run_dir, ignore_errors=True)
            raise
        stdin = StdinWriter(process.stdin) if process.stdin is not None else None
        running = Running(
            run_id, process, leads_its_group(process), record, stdin, conversation=conversation
        )
        self.current = running
        threading.Thread(target=self.watch, args=(running,), daemon=True).start()
        return running

    def check(self, task: str, task_type: str, source_name: str, config: str) -> DataSource:
        self.check_configured()
        source = self.sources.get(source_name)
        if source is None:
            raise Refused(
                400,
                f"unknown data source {source_name!r}; choose from {', '.join(self.sources)}",
            )
        if task_type not in TASK_TYPES:
            raise Refused(
                400, f"unknown task type {task_type!r}; choose from {', '.join(TASK_TYPES)}"
            )
        if config not in CONFIG_NAMES:
            raise Refused(400, f"unknown config {config!r}; choose from {', '.join(CONFIG_NAMES)}")
        if not task.strip():
            raise Refused(400, "the task is empty")
        if task_type == "dbt" and source.dbt_project is None:
            raise Refused(400, f"data source {source.name!r} has no dbt_project for a dbt task")
        check_source(source)
        return source

    def check_configured(self) -> None:
        if not self.sources:
            raise Refused(
                400, "no data source is configured; start the server with --data-sources FILE"
            )

    def cancel(self, run_id: str) -> dict[str, Any]:
        """Stops the run if it is the one this server started and it is still going; returns its
        row as it ended (a run that wrote its `result` just before the signal keeps that)."""
        with self.lock:
            return self.cancel_locked(run_id)

    def cancel_locked(self, run_id: str) -> dict[str, Any]:
        """`cancel`, called with the lock held."""
        current = self.current
        if current is None or current.run_id != run_id or current.process.poll() is not None:
            raise Refused(409, f"run {run_id} is not a run this server is running")
        current.cancelling = True
        self.stop(current)
        self.finish(current)
        summary = self.runs.summary(run_id)
        assert summary is not None, "the run's record is written at submit"
        return summary

    def shutdown(self) -> None:
        """Stops the run if it is still going, and whatever it left running, so the server leaves
        no process behind."""
        with self.lock:
            current = self.current
            if current is None or current.done:
                return
            if current.process.poll() is None:
                current.cancelling = True
                self.stop(current)
            self.finish(current)

    def watch(self, running: Running) -> None:
        """Waits for the run to exit on its own, then finishes it."""
        running.process.wait()
        with self.lock:
            self.finish(running)

    def finish(self, running: Running) -> None:
        """After the run's process exits: stops what it left running in its group, and records
        how it ended when it wrote no `result` event. Runs once per run."""
        if running.done:
            return
        if running.group and group_alive(running.process.pid):
            self.stop(running)
        if not self.runs.has_result(running.run_id):
            status = "cancelled" if running.cancelling else "failure"
            self.runs.write_record(running.run_id, {**running.record, "status": status})
        if running.stdin is not None:
            running.stdin.close()
        running.done = True

    def stop(self, running: Running) -> None:
        """SIGTERM, then SIGKILL for whatever is left after the grace period. A run that leads its
        own process group (`data-agent web` starts runs in a new session) is signalled as that
        group, and is stopped only once every process in it has exited, so the tools it started
        (the model CLI, the MCP server, dbt) stop with it."""
        process, group = running.process, running.group
        deadline = time.monotonic() + STOP_GRACE_SECONDS
        signal_run(process, group, signal.SIGTERM)
        if not wait_stopped(process, group, deadline):
            signal_run(process, group, signal.SIGKILL)
            wait_stopped(process, group, time.monotonic() + STOP_GRACE_SECONDS)


def check_source(source: DataSource) -> None:
    """Refused when a run on this server could not reach the source: its DuckDB file is missing,
    or its Postgres variable is not set."""
    if source.duckdb is not None and not source.duckdb.is_file():
        raise Refused(400, f"data source {source.name!r}: DuckDB file not found")
    if source.postgres_env is not None and not os.environ.get(source.postgres_env):
        raise Refused(
            400,
            f"data source {source.name!r}: environment variable {source.postgres_env} "
            "is not set on the server",
        )


def leads_its_group(process: "subprocess.Popen[bytes]") -> bool:
    try:
        return os.getpgid(process.pid) == process.pid
    except ProcessLookupError:
        return False


def signal_run(process: "subprocess.Popen[bytes]", group: bool, sig: signal.Signals) -> None:
    """The signal goes to the recorded pid, or to the group that pid leads; never further. EPERM
    for a group (some member cannot be signalled, see `group_alive`) still reaches the others."""
    try:
        if group:
            os.killpg(process.pid, sig)
        else:
            process.send_signal(sig)
    except (ProcessLookupError, PermissionError):
        pass


def wait_stopped(process: "subprocess.Popen[bytes]", group: bool, deadline: float) -> bool:
    """Whether the run (and its group) exited before the deadline."""
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        return False
    while group and group_alive(process.pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def group_alive(pgid: int) -> bool:
    """Whether any process is left in the group. macOS answers EPERM when some member cannot be
    signalled (seen with an exited member not yet reaped) even while others still run, so only
    ESRCH counts as gone."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def new_run_id(started_at: datetime) -> str:
    """Start time and a short random string (spec "运行目录")."""
    return f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


def prepare_workspace(
    workspace: Path, source: DataSource, task_type: str, runs_dir: Path
) -> list[str]:
    """Creates the run's working directory; returns the database arguments for `data-agent run`.
    A dbt run's DuckDB file is copied to `db/` beside the workspace, not into it, and the
    project's other DuckDB files and `profiles.yml` stay behind. The copy follows links and
    skips dangling ones.
    Refused when the dbt project holds a directory link reaching the runs directory, which the
    copy would copy into itself."""
    if source.postgres_env is not None:
        database = ["--postgres-env", source.postgres_env]
    else:
        assert source.duckdb is not None
        database = ["--duckdb", str(source.duckdb)]
    if task_type == "query" or source.dbt_project is None:
        workspace.mkdir()
        return database
    project = source.dbt_project
    runs = runs_dir.resolve()
    # The DuckDB file and the files beside it, known by identity: a linked directory in the
    # project is another path to the same file.
    database_files = (
        []
        if source.duckdb is None
        else [path for path in (source.duckdb, *side_files(source.duckdb)) if path.is_file()]
    )

    def is_database(entry: Path) -> bool:
        return entry.is_file() and any(os.path.samefile(entry, f) for f in database_files)

    def ignore(directory: str, names: list[str]) -> list[str]:
        skipped: list[str] = list(NOT_COPIED) if Path(directory) == project else []
        # A profile and a credential file by name, or a link to one under any name (the copy
        # follows links): sandbox_exec reads any file in the workspace (ADR-0010).
        skipped += [
            name
            for name in names
            if name not in skipped
            and (
                named_or_linked(Path(directory) / name, is_profile_name)
                or named_or_linked(Path(directory) / name, is_duckdb_name)
                or is_database(Path(directory) / name)
                or named_or_linked(Path(directory) / name, is_credential)
            )
        ]
        for name in set(names) - set(skipped):
            entry = Path(directory) / name
            if not entry.is_symlink():
                continue
            if not entry.exists():
                # Dangling. `Path.exists` resolves a relative link from the directory holding
                # it; `copytree`'s own `ignore_dangling_symlinks` resolves it from the cwd.
                skipped.append(name)
            elif entry.is_dir() and overlap(entry.resolve(), runs):
                raise Refused(
                    400,
                    f"data source {source.name!r}: {entry.relative_to(project)} in dbt_project "
                    "links to the runs directory, which a dbt run's copy of the project "
                    "would copy into itself",
                )
        return skipped

    # Link targets are copied, so no write in the workspace reaches the original project.
    shutil.copytree(project, workspace, ignore=ignore)
    if source.duckdb is None:
        return database
    # Outside the workspace, so neither the agent's file tools nor the sandbox reach it.
    db_dir = workspace.parent / DB_DIR
    db_dir.mkdir()
    copy = db_dir / source.duckdb.name
    shutil.copy2(source.duckdb, copy)
    for side in side_files(source.duckdb):
        if side.is_file():
            shutil.copy2(side, db_dir / side.name)
    return ["--duckdb", str(copy)]


def side_files(duckdb: Path) -> list[Path]:
    """The files DuckDB keeps next to a database file."""
    return [duckdb.with_name(duckdb.name + suffix) for suffix in DUCKDB_SIDE_SUFFIXES]
