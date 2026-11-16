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


def test_a_dbt_run_works_on_a_copy_of_the_project_and_the_duckdb_file(
    tmp_path: Path, recorder: Recorder
) -> None:
    project = dbt_project(tmp_path, with_db=True)
    shop = project / "shop.duckdb"
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


def test_a_symlink_in_the_project_is_copied_as_its_target(
    tmp_path: Path, recorder: Recorder
) -> None:
    """A write through the workspace never reaches the original project."""
    project = dbt_project(tmp_path, with_db=True)
    outside = tmp_path / "shared.sql"
    outside.write_text("select 2\n")
    (project / "models" / "linked.sql").symlink_to(outside)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    source = DataSource(name="shop", duckdb=project / "shop.duckdb", dbt_project=project)
    with TestClient(create_app(runs_dir, [source], recorder)) as client:
        assert client.post("/api/runs", json=body(task_type="dbt")).status_code == 201
    [(_, cwd)] = recorder.calls
    linked = cwd / "models" / "linked.sql"
    assert not linked.is_symlink()
    assert linked.read_text() == "select 2\n"


def test_a_duckdb_file_under_target_is_still_copied(tmp_path: Path, recorder: Recorder) -> None:
    project = dbt_project(tmp_path, with_db=False)
    shop = project / "target" / "shop.duckdb"
    shop.write_bytes(b"db")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    source = DataSource(name="shop", duckdb=shop, dbt_project=project)
    with TestClient(create_app(runs_dir, [source], recorder)) as client:
        assert client.post("/api/runs", json=body(task_type="dbt")).status_code == 201
    [(args, cwd)] = recorder.calls
    assert Path(flag(args, "--duckdb")) == cwd / "target" / "shop.duckdb"
    assert (cwd / "target" / "shop.duckdb").read_bytes() == b"db"
    assert not (cwd / "target" / "x.txt").exists()


@pytest.mark.parametrize("where", ["runs_inside_project", "project_inside_runs", "same"])
def test_a_runs_dir_overlapping_a_dbt_project_is_refused(tmp_path: Path, where: str) -> None:
    """A workspace inside the project it copies would be copied into itself."""
    project = dbt_project(tmp_path, with_db=False)
    runs_dir = {
        "runs_inside_project": project / "runs",
        "project_inside_runs": tmp_path,
        "same": project,
    }[where]
    runs_dir.mkdir(exist_ok=True)
    source = DataSource(name="pg", postgres_env=PG_ENV, dbt_project=project)
    with pytest.raises(ValueError, match=r"pg.*dbt_project.*runs"):
        create_app(runs_dir, [source], never_start)


def test_a_duckdb_file_outside_its_dbt_project_is_refused(tmp_path: Path) -> None:
    """dbt finds the file through profiles.yml relative to the project, so a copy of it has to
    stay at the same place in the workspace, which only a file inside the project can."""
    project = dbt_project(tmp_path, with_db=False)
    shop = tmp_path / "shop.duckdb"
    shop.write_bytes(b"db")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    source = DataSource(name="shop", duckdb=shop, dbt_project=project)
    with pytest.raises(ValueError, match=r"shop.*inside.*dbt_project"):
        create_app(runs_dir, [source], never_start)


def test_a_duckdb_link_inside_the_project_counts_as_inside(
    tmp_path: Path, recorder: Recorder
) -> None:
    project = dbt_project(tmp_path, with_db=False)
    real = tmp_path / "data" / "shop.duckdb"
    real.parent.mkdir()
    real.write_bytes(b"db")
    (project / "shop.duckdb").symlink_to(real)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    source = DataSource(name="shop", duckdb=project / "shop.duckdb", dbt_project=project)
    with TestClient(create_app(runs_dir, [source], recorder)) as client:
        assert client.post("/api/runs", json=body(task_type="dbt")).status_code == 201
    [(args, cwd)] = recorder.calls
    copy = Path(flag(args, "--duckdb"))
    assert copy == cwd / "shop.duckdb"
    assert not copy.is_symlink()
    assert copy.read_bytes() == b"db"


def never_start(args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
    raise AssertionError("no run should start")


def statuses(client: TestClient) -> dict[str, str]:
    return {run["id"]: run["status"] for run in client.get("/api/runs").json()}


def test_runs_that_exit_without_a_result_list_as_failed_for_good(
    tmp_path: Path, recorder: Recorder
) -> None:
    """One exits before writing any event (a config error), one after writing some (a crash);
    both stay listed as failures after later runs, and their streams close."""
    shop = tmp_path / "shop.duckdb"
    shop.write_bytes(b"")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    with TestClient(create_app(runs_dir, [DataSource(name="shop", duckdb=shop)], recorder)) as c:
        recorder.command = [sys.executable, "-c", "raise SystemExit(2)"]
        eventless = c.post("/api/runs", json=body()).json()["id"]
        wait_until(lambda: statuses(c).get(eventless) == "failure")
        assert c.get("/api/runs").json()[0]["task"] == "t"

        recorder.command = [sys.executable, "-c", WRITES_EVENTS_THEN_CRASHES]
        crashed = c.post("/api/runs", json=body()).json()["id"]
        wait_until(lambda: statuses(c).get(crashed) == "failure")
        assert statuses(c) == {eventless: "failure", crashed: "failure"}
        headers = {"Accept": "text/event-stream"}
        for run_id, expected in ((crashed, ["id: 0", "id: 1"]), (eventless, [])):
            with c.stream("GET", f"/api/runs/{run_id}/events", headers=headers) as response:
                frames = [line for line in response.iter_lines() if line.startswith("id:")]
            assert frames == expected


def event_lines(*types: str) -> str:
    """Event lines as Python source text; a `result` is a successful one."""
    fields = {"result": ', "subtype": "success", "is_error": false'}
    return "".join(
        f'{{"seq": {seq}, "type": "{type_}", "ts": "2026-11-15T09:00:0{seq}+00:00"'
        f"{fields.get(type_, '')}}}\\n"
        for seq, type_ in enumerate(types)
    )


# Stand-ins for `data-agent run`, started in the run's workspace; the event file goes next to it.
WRITES_EVENTS_THEN_CRASHES = f"""
open("../run.jsonl", "w").write('{event_lines("version", "status")}')
raise SystemExit(1)
"""
FINISHES_THEN_IGNORES_SIGTERM = f"""
import os, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
open("../run.jsonl", "w").write('{event_lines("version", "result")}')
open("ready", "w").close()
time.sleep(120)
"""
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
import os, subprocess, sys, time
subprocess.Popen([sys.executable, "-c", {STUBBORN_CHILD!r}])
time.sleep(120)
"""
SPAWNS_A_CHILD_AND_EXITS = f"""
import os, subprocess, sys, time
subprocess.Popen([sys.executable, "-c", {STUBBORN_CHILD!r}])
while not os.path.exists("child.pid"):
    time.sleep(0.05)
"""


def test_a_run_that_finished_just_before_the_cancel_keeps_its_result(
    tmp_path: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "STOP_GRACE_SECONDS", 1.0)
    recorder.command = [sys.executable, "-c", FINISHES_THEN_IGNORES_SIGTERM]
    shop = tmp_path / "shop.duckdb"
    shop.write_bytes(b"")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    with TestClient(create_app(runs_dir, [DataSource(name="shop", duckdb=shop)], recorder)) as c:
        run_id = c.post("/api/runs", json=body()).json()["id"]
        [(_, cwd)] = recorder.calls
        wait_until((cwd / "ready").is_file)
        response = c.post(f"/api/runs/{run_id}/cancel")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "success"
        assert statuses(c) == {run_id: "success"}


def alive(pid: int) -> bool:
    """A zombie (exited, not yet reaped) is not alive; macOS answers EPERM for one."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


@pytest.mark.parametrize(
    ("how", "status"), [("cancel", "cancelled"), ("shutdown", "cancelled"), ("exit", "failure")]
)
def test_a_run_in_its_own_session_leaves_no_process_behind(
    tmp_path: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch, how: str, status: str
) -> None:
    """`data-agent web` starts each run in a new session. Stopping it (cancel, server shutdown)
    reaches the run's own process group, so the tools it started stop too, one ignoring SIGTERM
    included, before the cancel answers or the server exits; a run that exits on its own has what
    it left running stopped the same way."""
    monkeypatch.setattr(launcher, "STOP_GRACE_SECONDS", 1.0)
    script = SPAWNS_A_CHILD_AND_EXITS if how == "exit" else SPAWNS_A_CHILD
    recorder.command = [sys.executable, "-c", script]
    recorder.new_session = True
    shop = tmp_path / "shop.duckdb"
    shop.write_bytes(b"")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    sources = [DataSource(name="shop", duckdb=shop)]
    with TestClient(create_app(runs_dir, sources, recorder)) as client:
        run_id = client.post("/api/runs", json=body()).json()["id"]
        [(_, cwd)] = recorder.calls
        pid_file = cwd / "child.pid"
        wait_until(pid_file.is_file)
        child = int(pid_file.read_text())
        if how == "cancel":
            response = client.post(f"/api/runs/{run_id}/cancel")
            assert response.status_code == 200
            assert response.json()["status"] == "cancelled"
            assert not alive(child)
        if how == "exit":
            wait_until(lambda: not alive(child))
    assert recorder.processes[0].poll() is not None
    assert not alive(child)
    with TestClient(create_app(runs_dir, sources, recorder)) as client:
        assert statuses(client) == {run_id: status}


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


def test_the_web_command_refuses_a_runs_dir_inside_a_dbt_project(tmp_path: Path) -> None:
    project = dbt_project(tmp_path, with_db=True)
    runs_dir = project / "runs"
    runs_dir.mkdir()
    sources = tmp_path / "sources.toml"
    sources.write_text(
        '[sources.shop]\nkind = "duckdb"\npath = "proj/shop.duckdb"\ndbt_project = "proj"\n'
    )
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "data_agent",
            "web",
            "--runs-dir",
            str(runs_dir),
            "--data-sources",
            str(sources),
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    assert done.returncode != 0
    assert "dbt_project" in done.stderr
