"""The chat backend beyond the contract tests (tests/acceptance/test_chat_api.py), ticket 93."""

import json
import subprocess
import sys
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_agent.config import load_effective_config
from data_agent.web.app import create_app
from data_agent.web.launcher import Launcher
from data_agent.web.runs import RunsDir
from data_agent.web.sources import DataSource, config_sources


@dataclass
class Quiet:
    """An app whose runs write nothing and wait, each with a stdin pipe it never reads."""

    client: TestClient
    runs_dir: Path
    processes: list["subprocess.Popen[bytes]"]
    calls: list[tuple[list[str], Path]]


@pytest.fixture
def quiet(tmp_path: Path) -> Iterator[Quiet]:
    shop = tmp_path / "shop.duckdb"
    shop.write_bytes(b"")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    processes: list[subprocess.Popen[bytes]] = []
    calls: list[tuple[list[str], Path]] = []

    def start_run(args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
        calls.append((list(args), cwd))
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], cwd=cwd, stdin=subprocess.PIPE
        )
        processes.append(process)
        return process

    try:
        app = create_app(runs_dir, [DataSource("shop", duckdb=shop)], start_run)
        with TestClient(app) as client:
            yield Quiet(client, runs_dir, processes, calls)
    finally:
        # Only the runs this test started, by their own handles.
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=30)


def test_a_new_turns_events_read_as_an_empty_list_at_once(quiet: Quiet) -> None:
    """The chat page reads a turn's events as soon as the message is answered; until the run
    writes its event file they are an empty list, not a 404 (ticket 95's review)."""
    conversation = quiet.client.post("/api/conversations", json={}).json()["id"]
    sent = quiet.client.post(f"/api/conversations/{conversation}/messages", json={"text": "Count"})
    assert sent.status_code == 201, sent.text
    events = quiet.client.get(f"/api/runs/{sent.json()['run_id']}/events")
    assert (events.status_code, events.json()) == (200, {"events": []})


def test_a_turn_that_does_not_read_its_stdin_yet_can_still_be_steered_and_cancelled(
    quiet: Quiet,
) -> None:
    """A turn reads stdin only once it has started its session; steers sent before then wait in
    the pipe. More than the pipe holds must neither hold up the reply nor keep the turn from
    being cancelled (Codex review of ticket 93)."""
    conversation = quiet.client.post("/api/conversations", json={}).json()["id"]
    path = f"/api/conversations/{conversation}/messages"
    assert quiet.client.post(path, json={"text": "Count"}).status_code == 201
    replies: list[int] = []

    def steer() -> None:
        for _ in range(4):
            replies.append(quiet.client.post(path, json={"text": "x" * 256 * 1024}).status_code)

    sender = threading.Thread(target=steer, daemon=True)
    sender.start()
    sender.join(timeout=10)
    try:
        assert not sender.is_alive(), "a steer is stuck writing to the turn's stdin"
        assert replies == [202] * 4
        cancelled = quiet.client.post(f"/api/conversations/{conversation}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["turns"][0]["status"] == "cancelled"
    finally:
        # Unblocks a stuck write, so the test fails instead of hanging.
        for process in quiet.processes:
            process.kill()


@pytest.mark.parametrize("index", ["{not json", '{"id": "x"}', "[]"])
def test_an_unreadable_index_leaves_the_other_conversations_listed(
    quiet: Quiet, index: str
) -> None:
    good = quiet.client.post("/api/conversations", json={}).json()["id"]
    broken = quiet.runs_dir / "conversations" / "20261117T000000Z-broken"
    broken.mkdir()
    (broken / "conversation.json").write_text(index)
    listed = quiet.client.get("/api/conversations")
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [good]
    assert json.loads((quiet.runs_dir / "conversations" / good / "conversation.json").read_text())


def test_the_config_file_a_turn_gets_reads_back_as_the_same_sources(tmp_path: Path) -> None:
    """Names and paths that TOML must escape: quotes, backslashes, non-ASCII and characters
    outside the Basic Multilingual Plane, a dot."""
    folder = tmp_path / 'dir "q" \\ 数据 🦆'
    folder.mkdir()
    file = folder / "a.duckdb"
    sources = [
        DataSource('or"ders.v2', duckdb=file),
        DataSource("客户 🦆", postgres_env="CRM_URL"),
    ]
    runs = tmp_path / "runs"
    runs.mkdir()
    launcher = Launcher(RunsDir(runs), sources, lambda *_: None)  # type: ignore[arg-type]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    [_, path] = launcher.config_args(run_dir, True)
    assert config_sources(load_effective_config([Path(path)], {}, {})) == [
        DataSource('or"ders.v2', duckdb=file.resolve()),
        DataSource("客户 🦆", postgres_env="CRM_URL"),
    ]


def test_conversation_message_defaults_network_on_and_forwards_network_false(
    quiet: Quiet,
) -> None:
    """Ticket 103: POST /api/conversations/{id}/messages defaults network=True and forwards
    network=False to the started turn."""
    conversation = quiet.client.post("/api/conversations", json={}).json()["id"]
    path = f"/api/conversations/{conversation}/messages"

    first = quiet.client.post(path, json={"text": "First"})
    assert first.status_code == 201, first.text
    assert quiet.client.post(f"/api/conversations/{conversation}/cancel").status_code == 200

    second = quiet.client.post(path, json={"text": "Second", "network": False})
    assert second.status_code == 201, second.text
    assert quiet.client.post(f"/api/conversations/{conversation}/cancel").status_code == 200

    third = quiet.client.post(path, json={"text": "Third", "network": True})
    assert third.status_code == 201, third.text
    assert quiet.client.post(f"/api/conversations/{conversation}/cancel").status_code == 200

    (default_args, _), (off_args, _), (on_args, _) = quiet.calls
    assert "--network" in default_args
    assert "--network" not in off_args
    assert "--network" in on_args
