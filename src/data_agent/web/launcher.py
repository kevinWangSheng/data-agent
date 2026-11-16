"""Starting and cancelling runs from the web backend (spec "后端接口", Q39-Q40).

One run at a time. Each run gets `<runs_dir>/<id>/` for its event file and
`<runs_dir>/<id>/workspace/` as its working directory: empty for a query run, a copy of the data
source's dbt project (without `target/` and `logs/`) for a dbt run, with the DuckDB file copied in
too. A query run reads the DuckDB file in place through the read-only gateway.

Cancelling signals only the process this server started, by its recorded pid (and its own process
group when it leads one). A run that exits without writing its `result` event after that is marked
cancelled with a file next to its event file, which the run list and the event stream read.
"""

import os
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
from typing import Any

from data_agent.run_config import CONFIG_NAMES
from data_agent.web.runs import CANCELLED_MARKER, RunsDir
from data_agent.web.sources import DataSource

TASK_TYPES = ("query", "dbt")
# How long a cancelled run has to exit after SIGTERM before it gets SIGKILL.
STOP_GRACE_SECONDS = 10.0
WORKSPACE = "workspace"

StartRun = Callable[[Sequence[str], Path], "subprocess.Popen[bytes]"]
"""Starts `data-agent run` with the given arguments (those after `data-agent run`) in the given
working directory and returns the process."""


class Refused(Exception):
    """A submit or cancel the server will not do; `status` is the HTTP status to answer with."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class Running:
    run_id: str
    process: "subprocess.Popen[bytes]"
    # What was submitted, for the run list until the run writes its event file.
    summary: dict[str, Any]


class Launcher:
    def __init__(self, runs: RunsDir, sources: Sequence[DataSource], start_run: StartRun) -> None:
        self.runs = runs
        self.sources = {source.name: source for source in sources}
        self.start_run = start_run
        self.current: Running | None = None
        self.lock = threading.Lock()

    def submit(self, task: str, task_type: str, source_name: str, config: str) -> str:
        """Checks the request, prepares the run's directories and starts it; returns its id."""
        source = self.check(task, task_type, source_name, config)
        with self.lock:
            if self.current is not None and self.current.process.poll() is None:
                raise Refused(409, f"run {self.current.run_id} is still going; one run at a time")
            started_at = datetime.now(UTC)
            run_id = new_run_id(started_at)
            run_dir = self.runs.path / run_id
            workspace = run_dir / WORKSPACE
            run_dir.mkdir()
            try:
                database = prepare_workspace(workspace, source, task_type)
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
                ]
                process = self.start_run(args, workspace)
            except Exception:
                shutil.rmtree(run_dir, ignore_errors=True)
                raise
            summary = {
                "id": run_id,
                "task": task,
                "task_type": task_type,
                "config": config,
                "status": "running",
                "started_at": started_at.isoformat(),
                "duration_ms": None,
                "cost_usd": None,
            }
            self.current = Running(run_id, process, summary)
            return run_id

    def unwritten(self) -> list[dict[str, Any]]:
        """The started run's row while it has not written its event file yet (the run list reads
        event files, so it would leave the run out)."""
        current = self.current
        if current is None or self.runs.event_file(current.run_id) is not None:
            return []
        if current.process.poll() is None:
            return [current.summary]
        status = "cancelled" if self.runs.cancelled(current.run_id) else "failure"
        return [{**current.summary, "status": status}]

    def check(self, task: str, task_type: str, source_name: str, config: str) -> DataSource:
        if not self.sources:
            raise Refused(
                400, "no data source is configured; start the server with --data-sources FILE"
            )
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
        if source.duckdb is not None and not source.duckdb.is_file():
            raise Refused(400, f"data source {source.name!r}: DuckDB file not found")
        if source.postgres_env is not None and not os.environ.get(source.postgres_env):
            raise Refused(
                400,
                f"data source {source.name!r}: environment variable {source.postgres_env} "
                "is not set on the server",
            )
        return source

    def cancel(self, run_id: str) -> None:
        """Stops the run if it is the one this server started and it is still going."""
        with self.lock:
            current = self.current
            if current is None or current.run_id != run_id or current.process.poll() is not None:
                raise Refused(409, f"run {run_id} is not a run this server is running")
            self.stop(current)

    def shutdown(self) -> None:
        """Stops the running run, if any, so the server leaves no process behind."""
        with self.lock:
            if self.current is not None and self.current.process.poll() is None:
                self.stop(self.current)

    def stop(self, running: Running) -> None:
        """SIGTERM, then SIGKILL for whatever is left after the grace period. A run that leads its
        own process group (`data-agent web` starts runs in a new session) is signalled as that
        group, and is stopped only once every process in it has exited, so the tools it started
        (the model CLI, the MCP server, dbt) stop with it."""
        process = running.process
        group = leads_its_group(process)
        deadline = time.monotonic() + STOP_GRACE_SECONDS
        signal_run(process, group, signal.SIGTERM)
        if not wait_stopped(process, group, deadline):
            signal_run(process, group, signal.SIGKILL)
            wait_stopped(process, group, time.monotonic() + STOP_GRACE_SECONDS)
        if not self.runs.has_result(running.run_id):
            (self.runs.path / running.run_id / CANCELLED_MARKER).touch()


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


def prepare_workspace(workspace: Path, source: DataSource, task_type: str) -> list[str]:
    """Creates the run's working directory; returns the database arguments for `data-agent run`."""
    if task_type == "query" or source.dbt_project is None:
        workspace.mkdir()
        if source.postgres_env is not None:
            return ["--postgres-env", source.postgres_env]
        assert source.duckdb is not None
        return ["--duckdb", str(source.duckdb)]
    project = source.dbt_project
    shutil.copytree(
        project,
        workspace,
        symlinks=True,
        ignore=lambda directory, _: ["target", "logs"] if Path(directory) == project else [],
    )
    if source.postgres_env is not None:
        return ["--postgres-env", source.postgres_env]
    assert source.duckdb is not None
    # A DuckDB file inside the project was copied with it; one outside goes next to the project.
    try:
        copy = workspace / source.duckdb.resolve().relative_to(project.resolve())
    except ValueError:
        copy = workspace / source.duckdb.name
        shutil.copy2(source.duckdb, copy)
    return ["--duckdb", str(copy)]
