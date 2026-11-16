"""Stopping `data-agent web` (ticket 85): SIGINT or SIGTERM to the server cancels the run it
started and leaves none of its processes behind, even while a browser holds the run's event stream
open. The server runs as a real `data-agent web` process; only the command it starts for a run is a
stand-in, so the run costs nothing."""

import contextlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_agent.web.app import create_app
from data_agent.web.sources import DataSource

TIMEOUT = 30
# How long the server may take to exit after the signal: the uvicorn grace period for open
# connections, plus stopping a run that exits on SIGTERM.
EXIT_SECONDS = 15

# Stands in for `data-agent run` in the run's workspace: writes its first events next to it,
# starts a tool, says both pids once the tool is up, then keeps going as a long run would.
LONG_RUN = """
import os, subprocess, sys, time
open("../run.jsonl", "w").write(
    '{"seq": 0, "type": "version", "ts": "2026-11-16T09:00:00+00:00"}\\n'
    '{"seq": 1, "type": "status", "ts": "2026-11-16T09:00:01+00:00"}\\n'
)
tool = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
with open("pids.tmp", "w") as file:
    file.write(f"{os.getpid()} {tool.pid}")
os.rename("pids.tmp", "pids")
time.sleep(120)
"""

# `data-agent web` as `python -m data_agent web` runs it, with the run command swapped for
# LONG_RUN; everything else (the session the run gets, uvicorn and its signal handling) is the
# real one.
SERVER = f"""
import subprocess, sys
from data_agent.cli import main

class Popen(subprocess.Popen):
    def __init__(self, args, *rest, **options):
        if list(args[1:4]) == ["-m", "data_agent", "run"]:
            args = [sys.executable, "-c", {LONG_RUN!r}]
        super().__init__(args, *rest, **options)

subprocess.Popen = Popen
main(sys.argv[1:])
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + TIMEOUT
    while not check():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.05)


def alive(pid: int) -> bool:
    """A zombie (exited, not yet reaped) is not alive; macOS answers EPERM for one."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def answers(port: int) -> bool:
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        connection.request("GET", "/api/options")
        return connection.getresponse().status == 200
    except OSError:
        return False


class Server:
    """The `data-agent web` process; stopped, with the run it started, if a test leaves it up."""

    def __init__(self, runs_dir: Path, sources: Path) -> None:
        self.port = free_port()
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                SERVER,
                "web",
                "--runs-dir",
                str(runs_dir),
                "--data-sources",
                str(sources),
                "--port",
                str(self.port),
            ]
        )
        self.run_pid: int | None = None
        wait_until(lambda: answers(self.port) or self.process.poll() is not None)
        assert self.process.poll() is None, "the server did not start"

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=TIMEOUT)
        if self.run_pid is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.run_pid, signal.SIGKILL)


@pytest.fixture
def server(tmp_path: Path) -> Iterator[Server]:
    shop = tmp_path / "shop.duckdb"
    shop.write_bytes(b"")
    sources = tmp_path / "sources.toml"
    sources.write_text('[sources.shop]\nkind = "duckdb"\npath = "shop.duckdb"\n')
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    server = Server(runs_dir, sources)
    yield server
    server.close()


def submit(port: int) -> str:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
    body = {"task": "t", "task_type": "query", "data_source": "shop", "config": "full"}
    connection.request("POST", "/api/runs", json.dumps(body), {"Content-Type": "application/json"})
    response = connection.getresponse()
    assert response.status == 201, response.read()
    return json.loads(response.read())["id"]


def open_stream(port: int, run_id: str) -> http.client.HTTPConnection:
    """The run's event stream, open as a browser keeps it, with its first frame read."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
    connection.request("GET", f"/api/runs/{run_id}/events", headers={"Accept": "text/event-stream"})
    response = connection.getresponse()
    assert response.status == 200
    assert response.readline().startswith(b"id: 0")
    return connection


def never_start(args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
    raise AssertionError("no run is started")


@pytest.mark.parametrize(
    ("sig", "with_stream"),
    [(signal.SIGINT, True), (signal.SIGTERM, True), (signal.SIGINT, False)],
    ids=["sigint-with-stream", "sigterm-with-stream", "sigint-without-stream"],
)
def test_stopping_the_server_cancels_its_run_even_with_a_stream_open(
    tmp_path: Path, server: Server, sig: signal.Signals, with_stream: bool
) -> None:
    run_id = submit(server.port)
    pids = tmp_path / "runs" / run_id / "workspace" / "pids"
    wait_until(pids.is_file)
    server.run_pid, tool = (int(pid) for pid in pids.read_text().split())
    stream = open_stream(server.port, run_id) if with_stream else None

    started = time.monotonic()
    server.process.send_signal(sig)
    try:
        server.process.wait(timeout=EXIT_SECONDS)
    except subprocess.TimeoutExpired:
        pytest.fail(f"the server was still up {EXIT_SECONDS}s after {sig.name}")
    took = time.monotonic() - started
    if stream is not None:
        stream.close()

    assert took < EXIT_SECONDS
    assert not alive(server.run_pid)
    assert not alive(tool)
    with TestClient(
        create_app(
            tmp_path / "runs",
            [DataSource(name="shop", duckdb=tmp_path / "shop.duckdb")],
            never_start,
        )
    ) as client:
        [row] = client.get("/api/runs").json()
    assert (row["id"], row["status"]) == (run_id, "cancelled")
