"""Submitting and cancelling runs (ticket 78) where tests/acceptance/test_web_api.py does not reach:
the server without data sources, the submit checks, each run's working directory, and leaving no
process behind on cancel or shutdown."""

import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_agent.web import launcher
from data_agent.web.app import create_app
from data_agent.web.sources import DataSource

TIMEOUT = 30
PG_ENV = "DA_TEST_SUBMIT_URL"
PG_URL = "postgresql://agent:pa55word@db.example:5432/w"


@dataclass
class Recorder:
    """A `start_run` that records its call and starts `command` (by default one that exits)."""

    command: list[str] = field(default_factory=lambda: [sys.executable, "-c", "pass"])
    new_session: bool = False
    calls: list[tuple[list[str], Path]] = field(default_factory=list)
    processes: list["subprocess.Popen[bytes]"] = field(default_factory=list)

    def __call__(self, args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
        self.calls.append((list(args), cwd))
        process = subprocess.Popen(self.command, cwd=cwd, start_new_session=self.new_session)
        self.processes.append(process)
        return process


@pytest.fixture
def recorder() -> Iterator[Recorder]:
    recorder = Recorder()
    yield recorder
    for process in recorder.processes:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=TIMEOUT)


def body(**overrides: str) -> dict[str, str]:
    return {"task": "t", "task_type": "query", "data_source": "shop", "config": "full", **overrides}


def flag(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def wait_until(check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + TIMEOUT
    while not check():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.05)


def dbt_project(root: Path, with_db: bool) -> Path:
    project = root / "proj"
    (project / "models").mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: p\n")
    (project / "models" / "a.sql").write_text("select 1\n")
    for skipped in ("target", "logs"):
        (project / skipped).mkdir()
        (project / skipped / "x.txt").write_text("old\n")
    if with_db:
        (project / "shop.duckdb").write_bytes(b"db")
    return project


def test_without_data_sources_a_submit_is_refused_and_starts_nothing(
    tmp_path: Path, recorder: Recorder
) -> None:
    with TestClient(create_app(tmp_path, [], recorder)) as client:
        assert client.get("/api/options").json()["data_sources"] == []
        response = client.post("/api/runs", json=body())
        assert response.status_code == 400
        assert "no data source" in response.json()["detail"]
        assert client.get("/api/runs").json() == []
    assert recorder.calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"task_type": "dbt"}, "dbt_project"),
        ({"task_type": "sql"}, "task type"),
        ({"config": "fastest"}, "config"),
        ({"task": "  "}, "empty"),
        ({"data_source": "pg"}, PG_ENV),
    ],
)
def test_a_bad_submit_is_refused_and_starts_nothing(
    tmp_path: Path,
    recorder: Recorder,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str],
    detail: str,
) -> None:
    monkeypatch.delenv(PG_ENV, raising=False)
    shop = tmp_path / "shop.duckdb"
    shop.write_bytes(b"")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    sources = [DataSource(name="shop", duckdb=shop), DataSource(name="pg", postgres_env=PG_ENV)]
    with TestClient(create_app(runs_dir, sources, recorder)) as client:
        response = client.post("/api/runs", json=body(**overrides))
    assert response.status_code == 400
    assert detail in response.json()["detail"]
    assert recorder.calls == []
    assert list(runs_dir.iterdir()) == []


def test_a_query_run_gets_an_empty_workspace_and_reads_the_duckdb_file_in_place(
    tmp_path: Path, recorder: Recorder
) -> None:
    shop = tmp_path / "shop.duckdb"
    shop.write_bytes(b"")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    with TestClient(create_app(runs_dir, [DataSource(name="shop", duckdb=shop)], recorder)) as c:
        run_id = c.post("/api/runs", json=body()).json()["id"]
    [(args, cwd)] = recorder.calls
    assert cwd.parent == runs_dir / run_id
    assert list(cwd.iterdir()) == []
    assert flag(args, "--workspace") == str(cwd)
    assert flag(args, "--duckdb") == str(shop)
    assert flag(args, "--data-source-name") == "shop"


@pytest.mark.parametrize("db_inside_project", [True, False])
def test_a_dbt_run_works_on_a_copy_of_the_project_and_the_duckdb_file(
    tmp_path: Path, recorder: Recorder, db_inside_project: bool
) -> None:
    project = dbt_project(tmp_path, with_db=db_inside_project)
    shop = project / "shop.duckdb" if db_inside_project else tmp_path / "shop.duckdb"
    shop.write_bytes(b"db")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    source = DataSource(name="shop", duckdb=shop, dbt_project=project)
    with TestClient(create_app(runs_dir, [source], recorder)) as client:
        response = client.post("/api/runs", json=body(task_type="dbt"))
    assert response.status_code == 201, response.text
    [(args, cwd)] = recorder.calls
    assert (cwd / "dbt_project.yml").is_file()
    assert (cwd / "models" / "a.sql").read_text() == "select 1\n"
    assert not (cwd / "target").exists()
    assert not (cwd / "logs").exists()
    copy = Path(flag(args, "--duckdb"))
    assert copy == cwd / "shop.duckdb"
    assert copy.read_bytes() == b"db"
    assert (project / "target" / "x.txt").is_file()  # the original is untouched


def test_a_postgres_dbt_run_copies_the_project_and_names_only_the_variable(
    tmp_path: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PG_ENV, PG_URL)
    project = dbt_project(tmp_path, with_db=False)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    source = DataSource(name="pg", postgres_env=PG_ENV, dbt_project=project)
    with TestClient(create_app(runs_dir, [source], recorder)) as client:
        response = client.post("/api/runs", json=body(task_type="dbt", data_source="pg"))
    assert response.status_code == 201, response.text
    [(args, cwd)] = recorder.calls
    assert (cwd / "dbt_project.yml").is_file()
    assert flag(args, "--postgres-env") == PG_ENV
    assert "--duckdb" not in args
    assert "pa55word" not in " ".join(args)


def test_a_run_that_exits_before_writing_events_lists_as_failed(
    tmp_path: Path, recorder: Recorder
) -> None:
    shop = tmp_path / "shop.duckdb"
    shop.write_bytes(b"")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    with TestClient(create_app(runs_dir, [DataSource(name="shop", duckdb=shop)], recorder)) as c:
        run_id = c.post("/api/runs", json=body()).json()["id"]
        recorder.processes[0].wait(timeout=TIMEOUT)
        [listed] = c.get("/api/runs").json()
        assert (listed["id"], listed["status"], listed["task"]) == (run_id, "failure", "t")
        # It no longer blocks the next run.
        assert c.post("/api/runs", json=body()).status_code == 201


# The run starts a tool that ignores SIGTERM and says its pid once it does.
STUBBORN_CHILD = """
import os, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open("child.pid.tmp", "w") as file:
    file.write(str(os.getpid()))
os.rename("child.pid.tmp", "child.pid")
time.sleep(120)
"""
SPAWNS_A_CHILD = f"""
import subprocess, sys, time
subprocess.Popen([sys.executable, "-c", {STUBBORN_CHILD!r}])
time.sleep(120)
"""


def alive(pid: int) -> bool:
    """A zombie (exited, not yet reaped) is not alive; macOS answers EPERM for one."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


@pytest.mark.parametrize("how", ["cancel", "shutdown"])
def test_stopping_a_run_in_its_own_session_stops_the_processes_it_started(
    tmp_path: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    """`data-agent web` starts each run in a new session; stopping it reaches the run's own
    process group, so the tools the run started stop too, one ignoring SIGTERM included, before
    the cancel answers or the server exits; and the run lists cancelled."""
    monkeypatch.setattr(launcher, "STOP_GRACE_SECONDS", 1.0)
    recorder.command = [sys.executable, "-c", SPAWNS_A_CHILD]
    recorder.new_session = True
    shop = tmp_path / "shop.duckdb"
    shop.write_bytes(b"")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    app = create_app(runs_dir, [DataSource(name="shop", duckdb=shop)], recorder)
    with TestClient(app) as client:
        run_id = client.post("/api/runs", json=body()).json()["id"]
        [(_, cwd)] = recorder.calls
        pid_file = cwd / "child.pid"
        wait_until(pid_file.is_file)
        child = int(pid_file.read_text())
        if how == "cancel":
            assert client.post(f"/api/runs/{run_id}/cancel").status_code == 200
            assert not alive(child)
    assert recorder.processes[0].poll() is not None
    assert not alive(child)
    assert (runs_dir / run_id / "cancelled").is_file()


def test_the_web_command_refuses_a_bad_data_source_file(tmp_path: Path) -> None:
    sources = tmp_path / "sources.toml"
    sources.write_text('[sources.s]\nkind = "mysql"\n')
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "data_agent",
            "web",
            "--runs-dir",
            str(tmp_path),
            "--data-sources",
            str(sources),
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    assert done.returncode != 0
    assert "kind" in done.stderr


def test_a_stream_of_a_cancelled_run_closes_once_its_events_are_sent(
    tmp_path: Path, recorder: Recorder
) -> None:
    run = tmp_path / "r1"
    run.mkdir()
    (run / "run.jsonl").write_text(
        '{"seq": 0, "type": "version", "ts": "2026-11-15T09:00:00+00:00", "config": "full"}\n'
        '{"seq": 1, "type": "status", "ts": "2026-11-15T09:00:01+00:00", "state": "started"}\n'
    )
    (run / "cancelled").touch()
    with TestClient(create_app(tmp_path, [], recorder)) as client:
        headers = {"Accept": "text/event-stream"}
        with client.stream("GET", "/api/runs/r1/events", headers=headers) as response:
            frames = [line for line in response.iter_lines() if line.startswith("id:")]
        assert frames == ["id: 0", "id: 1"]
        assert client.get("/api/runs").json()[0]["status"] == "cancelled"
