"""The web backend's read side (ticket 77) where the acceptance tests in
tests/acceptance/test_web_api.py do not reach: failed runs, a run that has not written its event
file yet, stray entries in the runs directory, ids that point outside it, and keepalives."""

import json
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from data_agent.web import app as web_app
from data_agent.web.app import create_app


def never_start(args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
    raise AssertionError("no run is started here")


def client(runs_dir: Path) -> TestClient:
    return TestClient(create_app(runs_dir, [], never_start))


def event(seq: int, type_: str, ts: str, **fields: Any) -> str:
    return json.dumps({"seq": seq, "type": type_, "ts": ts, **fields}) + "\n"


def test_a_failed_run_lists_as_failure_with_its_cost_and_duration(tmp_path: Path) -> None:
    run = tmp_path / "r-failed"
    run.mkdir()
    (run / "run.jsonl").write_text(
        event(0, "version", "2026-11-15T09:00:00+00:00", config="baseline")
        + event(1, "task", "2026-11-15T09:00:00+00:00", task="t", task_type="query")
        + event(
            2,
            "result",
            "2026-11-15T09:00:02.500000+00:00",
            subtype="error_max_turns",
            is_error=True,
            cost_usd=0.2,
        )
    )
    [listed] = client(tmp_path).get("/api/runs").json()
    assert (listed["status"], listed["cost_usd"], listed["duration_ms"]) == ("failure", 0.2, 2500)


def test_runs_list_newest_first_and_skip_what_is_not_a_run(tmp_path: Path) -> None:
    for run_id, ts in (("a", "2026-11-15T08:00:00+00:00"), ("b", "2026-11-15T10:00:00+00:00")):
        (tmp_path / run_id).mkdir()
        (tmp_path / run_id / "run.jsonl").write_text(event(0, "version", ts, config="full"))
    (tmp_path / "not-yet").mkdir()  # a run that has not written its event file
    (tmp_path / "notes.txt").write_text("not a run")
    web = client(tmp_path)
    assert [run["id"] for run in web.get("/api/runs").json()] == ["b", "a"]
    assert web.get("/api/runs/not-yet/events").json() == {"events": []}


@pytest.mark.parametrize("run_id", ["..", ".hidden", "%2E%2E"])
def test_an_id_outside_the_runs_dir_is_no_run(tmp_path: Path, run_id: str) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / ".hidden").mkdir()
    (tmp_path / "run.jsonl").write_text(event(0, "version", "2026-11-15T09:00:00+00:00"))
    assert client(runs_dir).get(f"/api/runs/{run_id}/events").status_code == 404


def test_an_idle_stream_sends_keepalives_then_the_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web_app, "KEEPALIVE_SECONDS", 0.3)
    (tmp_path / "live").mkdir()  # the event file appears only later

    def write_later() -> None:
        time.sleep(1.0)
        (tmp_path / "live" / "run.jsonl").write_text(
            event(0, "version", "2026-11-15T09:00:00+00:00")
            + event(1, "result", "2026-11-15T09:00:01+00:00", subtype="success", is_error=False)
        )

    writer = threading.Thread(target=write_later)
    writer.start()
    headers = {"Accept": "text/event-stream"}
    with client(tmp_path).stream("GET", "/api/runs/live/events", headers=headers) as response:
        lines = list(response.iter_lines())
    writer.join()
    first_frame = lines.index("id: 0")
    assert ": keepalive" in lines[:first_frame]
    assert "id: 1" in lines[first_frame:]
