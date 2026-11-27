"""The server's network switch (ticket 140, ADR-0010 "设置里可以关"): `data-agent web --no-network`
starts every run without the network, from the run form, a conversation or the IM bot; a request
asking for it is refused (409), never turned off quietly. Without the switch a run gets the
network unless the request turns it off, as before."""

import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from data_agent import cli
from data_agent.web.app import create_app
from data_agent.web.conversations import Conversations
from data_agent.web.sources import DataSource

TIMEOUT = 30


@dataclass
class Server:
    client: TestClient
    app: FastAPI
    calls: list[list[str]]


def serve(tmp_path: Path, network: bool) -> Iterator[Server]:
    shop = tmp_path / "shop.duckdb"
    shop.write_bytes(b"")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    calls: list[list[str]] = []
    processes: list[subprocess.Popen[bytes]] = []

    def start_run(args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
        calls.append(list(args))
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], cwd=cwd, stdin=subprocess.PIPE
        )
        processes.append(process)
        return process

    app = create_app(runs_dir, [DataSource("shop", duckdb=shop)], start_run, network=network)
    try:
        with TestClient(app) as client:
            yield Server(client, app, calls)
    finally:
        # Only the runs this test started, by their own handles.
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=TIMEOUT)


@pytest.fixture
def offline(tmp_path: Path) -> Iterator[Server]:
    yield from serve(tmp_path, network=False)


@pytest.fixture
def default(tmp_path: Path) -> Iterator[Server]:
    yield from serve(tmp_path, network=True)


def submission(**overrides: Any) -> dict[str, Any]:
    return {"task": "t", "task_type": "query", "data_source": "shop", "config": "full", **overrides}


def cancel_run(server: Server, run_id: str) -> None:
    assert server.client.post(f"/api/runs/{run_id}/cancel").status_code == 200


def new_conversation(server: Server) -> str:
    return server.client.post("/api/conversations", json={}).json()["id"]


def cancel_turn(server: Server, conversation: str) -> None:
    assert server.client.post(f"/api/conversations/{conversation}/cancel").status_code == 200


# --- The run form ------------------------------------------------------------------------------


@pytest.mark.parametrize("asked", [{}, {"network": False}])
def test_offline_a_submit_runs_without_the_network(offline: Server, asked: dict[str, Any]) -> None:
    response = offline.client.post("/api/runs", json=submission(**asked))
    assert response.status_code == 201, response.text
    [args] = offline.calls
    assert "--network" not in args


def test_offline_a_submit_asking_for_the_network_is_refused(offline: Server) -> None:
    response = offline.client.post("/api/runs", json=submission(network=True))
    assert response.status_code == 409
    assert "--no-network" in response.json()["detail"]
    assert offline.calls == []
    assert offline.client.get("/api/runs").json() == []


@pytest.mark.parametrize(("asked", "online"), [({}, True), ({"network": False}, False)])
def test_by_default_a_submit_runs_as_before(
    default: Server, asked: dict[str, Any], online: bool
) -> None:
    response = default.client.post("/api/runs", json=submission(**asked))
    assert response.status_code == 201, response.text
    [args] = default.calls
    assert ("--network" in args) is online


# --- Conversations ------------------------------------------------------------------------------


def test_offline_a_turn_runs_without_the_network(offline: Server) -> None:
    conversation = new_conversation(offline)
    path = f"/api/conversations/{conversation}/messages"
    assert offline.client.post(path, json={"text": "First"}).status_code == 201
    cancel_turn(offline, conversation)
    assert offline.client.post(path, json={"text": "Second", "network": False}).status_code == 201
    assert all("--network" not in args for args in offline.calls)
    assert len(offline.calls) == 2


def test_offline_a_message_asking_for_the_network_is_refused(offline: Server) -> None:
    conversation = new_conversation(offline)
    path = f"/api/conversations/{conversation}/messages"
    refused = offline.client.post(path, json={"text": "First", "network": True})
    assert refused.status_code == 409
    assert "--no-network" in refused.json()["detail"]
    assert offline.calls == []
    # A steer asking for it too: the turn it goes into has no network to give.
    assert offline.client.post(path, json={"text": "First"}).status_code == 201
    steer = offline.client.post(path, json={"text": "More", "network": True})
    assert steer.status_code == 409
    assert offline.client.post(path, json={"text": "More"}).status_code == 202


def test_by_default_a_turn_has_the_network_unless_turned_off(default: Server) -> None:
    conversation = new_conversation(default)
    path = f"/api/conversations/{conversation}/messages"
    assert default.client.post(path, json={"text": "First"}).status_code == 201
    cancel_turn(default, conversation)
    assert default.client.post(path, json={"text": "Second", "network": False}).status_code == 201
    first, second = default.calls
    assert "--network" in first
    assert "--network" not in second


# --- The IM bot: it sends with no network of its own, so its turns follow the server ------------


@pytest.mark.parametrize(("server", "online"), [("offline", False), ("default", True)])
def test_an_im_turn_follows_the_server(
    request: pytest.FixtureRequest, server: str, online: bool
) -> None:
    started: Server = request.getfixturevalue(server)
    conversations: Conversations = started.app.state.conversations
    conversation = conversations.create()
    # As data_agent.im.adapter.Adapter.send_turn calls it.
    assert conversations.send(conversation, "How many orders?")["kind"] == "turn"
    [args] = started.calls
    assert ("--network" in args) is online


# --- The command line ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flags", "status"), [(("--no-network",), 409), ((), 400), (("--network",), 400)]
)
def test_data_agent_web_takes_the_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: tuple[str, ...], status: int
) -> None:
    """Up to `uvicorn.run`, then the app it was given. With no data sources a submit the switch
    lets through is refused for that (400) and starts nothing."""
    served: list[FastAPI] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **_: served.append(app))
    runs = tmp_path / "runs"
    runs.mkdir()
    cli.main(["web", "--runs-dir", str(runs), *flags])
    [app] = served
    with TestClient(app) as client:
        response = client.post("/api/runs", json=submission(network=True))
    assert response.status_code == status
    assert list(runs.iterdir()) == []
