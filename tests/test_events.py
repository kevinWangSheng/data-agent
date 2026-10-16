"""The run's event file: append-only JSONL, consecutive `seq`, secrets masked before writing."""

import json
from pathlib import Path
from typing import Any

from data_agent.events import REDACTED, EventLog, known_secrets


def read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_events_are_numbered_consecutively_from_zero(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    with EventLog(path, secrets=[]) as log:
        log.emit("version", model="m")
        log.emit("text", text="hello")
        log.emit("result", cost_usd=0.1)
    events = read(path)
    assert [e["seq"] for e in events] == [0, 1, 2]
    assert [e["type"] for e in events] == ["version", "text", "result"]
    assert events[1]["text"] == "hello"


def test_each_event_is_on_disk_once_emitted(tmp_path: Path) -> None:
    """A crash mid-run must still leave every earlier event in the file."""
    path = tmp_path / "run.jsonl"
    with EventLog(path, secrets=[]) as log:
        log.emit("status", state="started")
        assert read(path)[0]["state"] == "started"


def test_secret_values_are_masked_wherever_they_appear(tmp_path: Path) -> None:
    key = 'sk-abc"def\\ghi'  # characters JSON escapes must still be caught
    path = tmp_path / "run.jsonl"
    with EventLog(path, secrets=[key]) as log:
        log.emit("tool_end", content=f"error: bad key {key}", nested={"args": [key]})
    text = path.read_text()
    assert "sk-abc" not in text
    event = read(path)[0]
    assert event["content"] == f"error: bad key {REDACTED}"
    assert event["nested"] == {"args": [REDACTED]}


def test_known_secrets_cover_keys_and_the_endpoint_host() -> None:
    env = {
        "DATA_AGENT_API_KEY": "sk-one",
        "DATA_AGENT_ANTHROPIC_API_KEY": "sk-two",
        "DATA_AGENT_BASE_URL": "https://llm.internal.example:8443/anthropic",
        "HOME": "/home/me",
    }
    secrets = known_secrets(env)
    assert {"sk-one", "sk-two", "https://llm.internal.example:8443/anthropic"} <= set(secrets)
    assert "llm.internal.example" in secrets
    assert "/home/me" not in secrets


def test_endpoint_host_is_masked_on_its_own(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    secrets = known_secrets({"DATA_AGENT_BASE_URL": "https://llm.internal.example/anthropic"})
    with EventLog(path, secrets=secrets) as log:
        log.emit("error", message="connect to llm.internal.example failed")
    assert "llm.internal.example" not in path.read_text()
