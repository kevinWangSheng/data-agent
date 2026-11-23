"""The web UI's runs and conversation turns export like `data-agent run` (ticket 112; spec
"覆盖范围": the form's runs and the conversations, on the dev tier, once the three variables are
set). The web backend passes no `--trace-tier` and no environment of its own, so each run it
starts reads the variables from the server's environment and takes the default `full`; a turn's
session is its conversation (tests/acceptance/test_observability_switch.py pins the rest).

The runs the app starts are recorded and replayed through `data-agent run`'s argument parsing with
`run` itself stood in for.
"""

import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from data_agent import cli
from data_agent.agent import Outcome
from data_agent.web.app import create_app
from data_agent.web.sources import DataSource

LANGFUSE = {
    "DATA_AGENT_LANGFUSE_PUBLIC_KEY": "pk-lf-web",
    "DATA_AGENT_LANGFUSE_SECRET_KEY": "sk-lf-web",
    "DATA_AGENT_LANGFUSE_BASE_URL": "https://langfuse.example",
}


def started_runs(tmp_path: Path) -> list[list[str]]:
    """The `data-agent run` arguments of one form run and one conversation turn."""
    shop = tmp_path / "shop.duckdb"
    duckdb.connect(str(shop)).close()
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    started: list[list[str]] = []

    def start_run(args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
        started.append(list(args))
        return subprocess.Popen([sys.executable, "-c", "pass"], cwd=cwd, stdin=subprocess.PIPE)

    with TestClient(create_app(runs_dir, [DataSource(name="shop", duckdb=shop)], start_run)) as c:
        body = {"task": "How many?", "task_type": "query", "data_source": "shop", "config": "full"}
        assert c.post("/api/runs", json=body).status_code == 201
        conversation_id = c.post("/api/conversations", json={}).json()["id"]
        # One run at a time: the turn is refused until the form run's process is seen gone.
        deadline = time.monotonic() + 10
        while True:
            sent = c.post(f"/api/conversations/{conversation_id}/messages", json={"text": "Hi"})
            if sent.status_code != 409 or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        assert sent.status_code == 201, sent.text
    return started


@pytest.mark.parametrize("which", ["form run", "conversation turn"])
def test_a_web_run_exports_on_the_dev_tier_from_the_servers_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    started = started_runs(tmp_path)
    assert len(started) == 2
    args = started[0] if which == "form run" else started[1]
    assert not [a for a in args if a.startswith("--trace-tier")]
    for name, value in LANGFUSE.items():
        monkeypatch.setenv(name, value)
    seen: dict[str, Any] = {}

    async def run(task: Any, config: Any, events_dir: Path, env: Any, *a: Any, **kw: Any) -> Any:
        seen.update(env=env, **kw)
        return Outcome("", True, False, [], events_dir / "x.jsonl")

    monkeypatch.setattr(cli, "run", run)
    with pytest.raises(SystemExit):
        cli.main(["run", *args])
    assert seen["trace_tier"] == "full"
    assert {name: seen["env"].get(name) for name in LANGFUSE} == LANGFUSE
    if which == "conversation turn":
        # The turn's session in Langfuse is its conversation (`RunFacts.session`).
        assert seen["turn"] is not None and seen["turn"].conversation


def test_the_web_backend_starts_runs_in_its_own_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    import data_agent.web.app as app

    captured: dict[str, Any] = {}

    def create(runs_dir: Path, sources: Any, start_run: Any) -> object:
        captured["start_run"] = start_run
        return object()

    monkeypatch.setattr(app, "create_app", create)
    monkeypatch.setattr(uvicorn, "run", lambda *_, **__: None)
    cli.main(["web", "--runs-dir", str(tmp_path)])
    popen: dict[str, Any] = {}

    def record(*args: Any, **kwargs: Any) -> None:
        popen.update(kwargs)

    monkeypatch.setattr(cli.subprocess, "Popen", record)
    captured["start_run"](["--task=x"], tmp_path)
    assert "env" not in popen  # inherited: the server's Langfuse variables reach the run
