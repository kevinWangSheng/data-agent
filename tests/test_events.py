"""The run's event file: append-only JSONL, consecutive `seq`, secrets masked before writing."""

import json
from pathlib import Path
from typing import Any

import pytest

from data_agent.events import REDACTED, EventLog, known_secrets, url_names, url_secrets


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


def test_database_url_hosts_are_masked_even_as_bare_names(tmp_path: Path) -> None:
    # Docker and Kubernetes hosts are often single-label names (PR #14 review).
    url = "postgresql://u:s3cret-pw@prod-db:5432/shop"
    path = tmp_path / "run.jsonl"
    with EventLog(path, secrets=url_secrets(url), names=url_names(url)) as log:
        log.emit("tool_end", content='could not translate host name "prod-db" to address')
        log.emit("tool_end", content="prod-dbx and prod-db-replica are other words; dbt too")
    events = read(path)
    assert events[0]["content"] == f'could not translate host name "{REDACTED}" to address'
    assert events[1]["content"] == "prod-dbx and prod-db-replica are other words; dbt too"


@pytest.mark.parametrize(
    ("url", "names"),
    [
        ("postgresql://u:pw@postgres:5432/shop", ["postgres"]),
        ("host=db1,db2 dbname=shop", ["db1", "db2"]),
        ("postgresql://u:pw@db.example:5432/shop", []),
        ("postgresql://u:pw@localhost:5432/shop", []),
        ("host=/var/run/postgresql dbname=shop", []),
        ("", []),
    ],
)
def test_url_names_are_the_single_label_hosts(url: str, names: list[str]) -> None:
    assert url_names(url) == names


def test_libpq_server_address_is_masked(tmp_path: Path) -> None:
    # libpq names the resolved address next to the host, and the address is in no URL.
    path = tmp_path / "run.jsonl"
    with EventLog(path, secrets=url_secrets("postgresql://u@db.example/shop")) as log:
        log.emit(
            "tool_end",
            content='connection to server at "db.example" (192.0.2.123), port 5432 failed: refused',
        )
    assert read(path)[0]["content"] == (
        f"connection to server at {REDACTED}, port 5432 failed: refused"
    )
