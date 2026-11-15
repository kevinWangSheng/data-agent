"""Seam 2 of the web UI (.scratch/web-ui/spec.md "Testing Decisions"): the backend's HTTP
interface, driven through FastAPI's test client. `data-agent run` is replaced by
tests/fixtures/web/fake_run.py, which writes events on a script and waits on a gate file before it
finishes.

Written by ticket 73. Each test is xfail(strict) until the ticket named in its reason makes it pass
and removes the marker, changing nothing else.

External interface fixed here (`data_agent.web.app.create_app(runs_dir, data_sources, start_run)`):

- A run is a subdirectory of `runs_dir` holding one event file (`*.jsonl`); its id is the
  directory name. Runs written by `data-agent run --events-dir <runs_dir>/<id>` list like any other.
- `GET /api/options` -> `{"data_sources": [name, ...], "task_types": [...], "configs": [...]}`.
- `GET /api/runs` -> a list of `{"id", "task", "task_type", "config", "status", "started_at",
  "duration_ms", "cost_usd"}`; status is `running`, `success`, `failure` or `cancelled`;
  `started_at` is the first event's `ts`.
- `GET /api/runs/{id}/events?after=SEQ` -> `{"events": [...]}`, the complete event lines with
  `seq` > SEQ (all without `after`). 404 for an unknown run.
- The same URL with `Accept: text/event-stream` is an SSE stream: one frame per event,
  `id: <seq>` and `data: <event JSON>`; it first sends what is already written after the cursor,
  then each new event, and closes once the run has ended and everything is sent. A reconnect
  resumes after its `Last-Event-ID`. Comment lines (keepalives) carry no data.
- `POST /api/runs` with `{"task", "task_type", "data_source", "config"}` -> 201 `{"id"}`, the
  run started through `start_run` with `--events-dir <runs_dir>/<id>`. 4xx with a `detail` saying
  why when the data source is unknown; 409 while another run is going.
- `POST /api/runs/{id}/cancel` stops a running run this server started; 4xx for any other run.
- `data-agent web --runs-dir DIR --port N` serves the app on the loopback address only; there is
  no option to change the address.
"""

import contextlib
import ipaddress
import json
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from seam_support import DATA_AGENT, FIXTURES

from data_agent.web.app import DataSource, create_app

WEB_FIXTURES = FIXTURES / "web"
FAKE_RUN = WEB_FIXTURES / "fake_run.py"
NEW_FIELDS = WEB_FIXTURES / "new-fields" / "events.jsonl"
QUERY_SHOP = WEB_FIXTURES / "real" / "query-shop" / "events.jsonl"

# The fake run writes 5 events, waits on the gate, then writes 2 more.
FAKE_EVENTS = 7
FAKE_BEFORE_GATE = 5

PG_ENV = "DA_TEST_WAREHOUSE_URL"
# A connection string the server holds in its environment; no byte of it may leave the server.
PG_PASSWORD = "hunter2-" + "x7Qp"
PG_HOST = "warehouse-db.internal.example"
PG_URL = f"postgresql://agent:{PG_PASSWORD}@{PG_HOST}:5432/warehouse"

TIMEOUT = 60
# Talk to the local server directly, whatever proxy the environment names.
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
INET = re.compile(r"\binet (?:addr:)?(\d+\.\d+\.\d+\.\d+)")
# 198.18/15, the benchmarking range proxies give their TUN interface (RFC 2544), as an integer.
TUN_RANGE = ipaddress.ip_network((0xC612_0000, 15))

TICKET_77 = pytest.mark.xfail(strict=True, reason="ticket 77: list runs, read events, SSE")
TICKET_78 = pytest.mark.xfail(strict=True, reason="ticket 78: submit and cancel runs")


@dataclass
class Started:
    args: list[str]
    cwd: Path
    process: "subprocess.Popen[bytes]"


@dataclass
class Web:
    client: TestClient
    runs_dir: Path
    gate: Path
    started: list[Started] = field(default_factory=list)


@pytest.fixture
def web(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Web]:
    monkeypatch.setenv(PG_ENV, PG_URL)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    shop = tmp_path / "sources" / "shop.duckdb"
    shop.parent.mkdir()
    shop.write_bytes(b"")
    dbt_project = tmp_path / "sources" / "warehouse_dbt"
    dbt_project.mkdir()
    (dbt_project / "dbt_project.yml").write_text("name: warehouse\n")
    sources = [
        DataSource(name="shop", duckdb=shop),
        DataSource(name="warehouse", postgres_env=PG_ENV, dbt_project=dbt_project),
    ]
    gate = tmp_path / "gate"
    started: list[Started] = []

    def start_run(args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
        process = subprocess.Popen(
            [sys.executable, str(FAKE_RUN), "--gate", str(gate), *args], cwd=cwd
        )
        started.append(Started(list(args), cwd, process))
        return process

    try:
        with TestClient(create_app(runs_dir, sources, start_run)) as client:
            yield Web(client, runs_dir, gate, started)
    finally:
        # Only the fake runs this test started, by their own process handles.
        for run in started:
            if run.process.poll() is None:
                run.process.terminate()
            run.process.wait(timeout=TIMEOUT)


# --- Helpers -------------------------------------------------------------------------------


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_run(runs_dir: Path, run_id: str, events: list[dict[str, Any]]) -> Path:
    """A run as `data-agent run --events-dir <runs_dir>/<run_id>` leaves it."""
    path = runs_dir / run_id / "run-20261115T090000Z-cli.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


def append(path: Path, events: list[dict[str, Any]], pause: float = 0.02) -> None:
    with path.open("a") as file:
        for event in events:
            file.write(json.dumps(event) + "\n")
            file.flush()
            time.sleep(pause)


def wait_until(check: Callable[[], bool], timeout: float = TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting")
        time.sleep(0.05)


def runs_by_id(web: Web) -> dict[str, dict[str, Any]]:
    response = web.client.get("/api/runs")
    assert response.status_code == 200, response.text
    return {run["id"]: run for run in response.json()}


def sse(
    web: Web, run_id: str, last_event_id: int | None = None
) -> list[tuple[int, dict[str, Any]]]:
    """Every frame of one SSE connection, until the server closes it: (id, event)."""
    headers = {"Accept": "text/event-stream"}
    if last_event_id is not None:
        headers["Last-Event-ID"] = str(last_event_id)
    frames: list[tuple[int, dict[str, Any]]] = []
    frame_id: int | None = None
    data: list[str] = []
    with web.client.stream("GET", f"/api/runs/{run_id}/events", headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("id:"):
                frame_id = int(line[3:].strip())
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif line == "" and data:
                assert frame_id is not None, "frame without an id"
                frames.append((frame_id, json.loads("\n".join(data))))
                frame_id, data = None, []
    return frames


def sse_in_background(web: Web, run_id: str, last_event_id: int | None = None) -> Future[Any]:
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(sse, web, run_id, last_event_id)
    pool.shutdown(wait=False)
    return future


def submit(web: Web, **overrides: str) -> Any:
    body = {
        "task": "How many orders are there?",
        "task_type": "query",
        "data_source": "shop",
        "config": "full",
        **overrides,
    }
    return web.client.post("/api/runs", json=body)


def submit_ok(web: Web, **overrides: str) -> str:
    response = submit(web, **overrides)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def event_file(web: Web, run_id: str) -> Path:
    [path] = (web.runs_dir / run_id).glob("*.jsonl")
    return path


def event_file_exists(web: Web, run_id: str) -> bool:
    return any((web.runs_dir / run_id).glob("*.jsonl"))


def flag(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


# --- Listing runs --------------------------------------------------------------------------


def test_lists_every_run_in_the_runs_dir(web: Web) -> None:
    """Runs from the command line list too: finished, still going, and an old event file."""
    events = read_events(NEW_FIELDS)
    write_run(web.runs_dir, "20261115T090000Z-done", events)
    write_run(web.runs_dir, "20261115T091500Z-live", events[:20])
    write_run(web.runs_dir, "20261115T143153Z-old", read_events(QUERY_SHOP))

    runs = runs_by_id(web)
    assert set(runs) == {"20261115T090000Z-done", "20261115T091500Z-live", "20261115T143153Z-old"}
    assert runs["20261115T090000Z-done"] == {
        "id": "20261115T090000Z-done",
        "task": "Build the orders mart from raw.orders.",
        "task_type": "dbt",
        "config": "full",
        "status": "success",
        "started_at": "2026-11-15T09:00:00+00:00",
        "duration_ms": 44000,
        "cost_usd": 0.05,
    }
    assert runs["20261115T091500Z-live"]["status"] == "running"
    assert runs["20261115T091500Z-live"]["cost_usd"] is None
    old = runs["20261115T143153Z-old"]
    assert (old["task"], old["config"], old["status"]) == (None, "no-verifiers", "success")
    assert old["cost_usd"] == 0.006241872


# --- Reading events by cursor --------------------------------------------------------------


def test_reads_events_after_a_cursor(web: Web) -> None:
    events = read_events(NEW_FIELDS)
    write_run(web.runs_dir, "r1", events)

    def seqs(query: str) -> list[int]:
        response = web.client.get(f"/api/runs/r1/events{query}")
        assert response.status_code == 200, response.text
        return [event["seq"] for event in response.json()["events"]]

    assert seqs("") == list(range(45))
    assert seqs("?after=40") == [41, 42, 43, 44]
    assert seqs("?after=44") == []
    full = web.client.get("/api/runs/r1/events").json()["events"]
    assert full == events
    assert web.client.get("/api/runs/no-such-run/events").status_code == 404


def test_a_half_written_last_line_is_not_returned(web: Web) -> None:
    """The event file is append-only and may be read while a line is being written."""
    events = read_events(NEW_FIELDS)
    path = write_run(web.runs_dir, "r1", events[:10])
    with path.open("a") as file:
        file.write(json.dumps(events[10])[:25])
    response = web.client.get("/api/runs/r1/events")
    assert [e["seq"] for e in response.json()["events"]] == list(range(10))


# --- SSE -----------------------------------------------------------------------------------


def test_sse_sends_the_backlog_then_new_events_then_closes(web: Web) -> None:
    events = read_events(NEW_FIELDS)
    path = write_run(web.runs_dir, "live", events[:5])
    stream = sse_in_background(web, "live")
    time.sleep(0.5)
    append(path, events[5:])  # ends with the result event: the run is over
    frames = stream.result(timeout=TIMEOUT)
    assert [frame_id for frame_id, _ in frames] == list(range(45))
    assert [event for _, event in frames] == events


def test_sse_resumes_after_last_event_id_without_loss_or_repeat(web: Web) -> None:
    events = read_events(NEW_FIELDS)
    write_run(web.runs_dir, "done", events)
    first = sse(web, "done")
    assert [frame_id for frame_id, _ in first] == list(range(45))
    # The connection dropped after frame 20; the browser reconnects with Last-Event-ID: 20.
    rest = sse(web, "done", last_event_id=20)
    assert [frame_id for frame_id, _ in rest] == list(range(21, 45))
    assert [e for _, e in first[:21]] + [e for _, e in rest] == events

    path = write_run(web.runs_dir, "live", events[:5])
    stream = sse_in_background(web, "live", last_event_id=2)
    time.sleep(0.5)
    append(path, events[5:])
    assert [frame_id for frame_id, _ in stream.result(timeout=TIMEOUT)] == list(range(3, 45))


# --- No connection strings -----------------------------------------------------------------


def assert_no_connection_string(text: str) -> None:
    for secret in (PG_URL, PG_PASSWORD, PG_HOST):
        assert secret not in text


def test_read_endpoints_hold_no_connection_string(web: Web) -> None:
    write_run(web.runs_dir, "r1", read_events(NEW_FIELDS))
    options = web.client.get("/api/options")
    assert options.status_code == 200, options.text
    assert options.json()["data_sources"] == ["shop", "warehouse"]
    assert {"query", "dbt"} <= set(options.json()["task_types"])
    assert "full" in options.json()["configs"]
    for url in ("/api/options", "/api/runs", "/api/runs/r1/events"):
        assert_no_connection_string(web.client.get(url).text)


@TICKET_78
def test_a_run_gets_the_variable_name_never_the_connection_string(web: Web) -> None:
    response = submit(web, data_source="warehouse")
    assert response.status_code == 201, response.text
    assert_no_connection_string(response.text)
    [run] = web.started
    assert flag(run.args, "--postgres-env") == PG_ENV
    assert_no_connection_string(json.dumps(run.args))
    run_id = response.json()["id"]
    wait_until(lambda: event_file_exists(web, run_id))
    web.gate.touch()
    wait_until(lambda: runs_by_id(web)[run_id]["status"] == "success")
    for url in ("/api/runs", f"/api/runs/{run_id}/events"):
        assert_no_connection_string(web.client.get(url).text)


# --- Submitting runs -----------------------------------------------------------------------


@TICKET_78
def test_a_submitted_run_appears_and_streams(web: Web) -> None:
    run_id = submit_ok(web, task="How many orders are there?", config="baseline")
    [run] = web.started
    assert Path(flag(run.args, "--events-dir")) == web.runs_dir / run_id
    assert flag(run.args, "--config") == "baseline"
    assert flag(run.args, "--type") == "query"
    assert run.cwd.is_dir()

    wait_until(lambda: len(web.client.get(f"/api/runs/{run_id}/events").json()["events"]) >= 3)
    listed = runs_by_id(web)[run_id]
    assert (listed["task"], listed["status"]) == ("How many orders are there?", "running")

    stream = sse_in_background(web, run_id)
    time.sleep(0.5)
    web.gate.touch()
    frames = stream.result(timeout=TIMEOUT)
    assert [frame_id for frame_id, _ in frames] == list(range(FAKE_EVENTS))
    assert frames[-1][1]["type"] == "result"
    wait_until(lambda: runs_by_id(web)[run_id]["status"] == "success")


@TICKET_78
def test_a_live_stream_resumes_from_last_event_id(web: Web) -> None:
    run_id = submit_ok(web)
    wait_until(
        lambda: (
            len(web.client.get(f"/api/runs/{run_id}/events").json()["events"]) == FAKE_BEFORE_GATE
        )
    )
    stream = sse_in_background(web, run_id, last_event_id=1)
    time.sleep(0.5)
    web.gate.touch()
    assert [frame_id for frame_id, _ in stream.result(timeout=TIMEOUT)] == list(
        range(2, FAKE_EVENTS)
    )


@TICKET_78
def test_an_unknown_data_source_is_refused(web: Web) -> None:
    response = submit(web, data_source="no-such-source")
    assert 400 <= response.status_code < 500
    assert "no-such-source" in response.json()["detail"]
    assert web.started == []
    assert runs_by_id(web) == {}


@TICKET_78
def test_a_second_run_is_refused_while_one_is_going(web: Web) -> None:
    first = submit_ok(web)
    response = submit(web, task="Another question")
    assert response.status_code == 409
    assert response.json()["detail"]
    assert len(web.started) == 1
    assert set(runs_by_id(web)) == {first}

    web.gate.touch()
    web.started[0].process.wait(timeout=TIMEOUT)
    wait_until(lambda: runs_by_id(web)[first]["status"] == "success")
    second = submit_ok(web, task="Another question")
    assert set(runs_by_id(web)) == {first, second}


@TICKET_78
def test_cancel_stops_only_the_run_this_server_started(web: Web) -> None:
    write_run(web.runs_dir, "cli-run", read_events(NEW_FIELDS)[:20])
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        run_id = submit_ok(web)
        wait_until(lambda: event_file_exists(web, run_id))
        [run] = web.started

        # A run from the command line is not this server's to stop.
        assert 400 <= web.client.post("/api/runs/cli-run/cancel").status_code < 500
        assert run.process.poll() is None

        response = web.client.post(f"/api/runs/{run_id}/cancel")
        assert response.status_code == 200, response.text
        assert run.process.wait(timeout=TIMEOUT) != 0
        assert bystander.poll() is None
        assert runs_by_id(web)[run_id]["status"] == "cancelled"
        assert "result" not in {e["type"] for e in read_events(event_file(web, run_id))}
        # Nothing is left to cancel.
        assert 400 <= web.client.post(f"/api/runs/{run_id}/cancel").status_code < 500
    finally:
        bystander.terminate()
        bystander.wait(timeout=TIMEOUT)


# --- Loopback only -------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def outward_addresses() -> set[str]:
    """This machine's IPv4 addresses other than loopback, from its interface list."""
    found: set[str] = set()
    for command in (["ip", "-4", "-o", "addr"], ["ifconfig", "-a"]):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            listing = subprocess.run(command, capture_output=True, text=True, timeout=10).stdout
            found |= set(INET.findall(listing))
    with contextlib.suppress(OSError):
        found |= {str(info[4][0]) for info in socket.getaddrinfo(socket.gethostname(), None)}
    # A proxy's TUN interface (TUN_RANGE) accepts any connection itself; it says nothing
    # about the server, and the test sends nothing into it.
    return {
        a
        for a in found
        if "." in a
        and not ipaddress.ip_address(a).is_loopback
        and ipaddress.ip_address(a) not in TUN_RANGE
    }


def server_answers(host: str, port: int) -> bool:
    """True when our server, not just any socket, answers on host:port."""
    try:
        with NO_PROXY.open(f"http://{host}:{port}/api/runs", timeout=2) as response:
            return response.status == 200 and isinstance(json.load(response), list)
    except (OSError, ValueError):
        return False


def test_data_agent_web_listens_on_loopback_only(tmp_path: Path) -> None:
    refused = subprocess.run(
        [str(DATA_AGENT), "web", "--runs-dir", str(tmp_path), "--host", "0.0.0.0"],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    assert refused.returncode != 0

    port = free_port()
    server = subprocess.Popen(
        [str(DATA_AGENT), "web", "--runs-dir", str(tmp_path), "--port", str(port)],
    )
    try:

        def up() -> bool:
            assert server.poll() is None, "server exited"
            return server_answers("127.0.0.1", port)

        wait_until(up)
        for address in outward_addresses():
            assert not server_answers(address, port), f"the server answers on {address}"
    finally:
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=TIMEOUT)
