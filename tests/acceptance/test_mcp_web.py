"""External MCP servers through `data-agent web` (.scratch/mcp/spec.md "Testing Decisions": 入口;
"挂到哪些入口", Q2): the run form's runs and every conversation turn get the config file's path on
their command line, as `--network` is passed (`launcher.py`, `conversations.py`).

Written by ticket 115. The tests marked TICKET_116 are xfail(strict) until ticket 116 makes them
pass and removes the marker, changing nothing else. The tests without it hold today and must keep
holding: without a config, no run gets `--mcp-servers`.

The app is built with `create_app` and a `start_run` that records the arguments; the run it starts
is tests/fixtures/chat/fake_chat_run.py (reused read-only), which ignores arguments it does not
know and waits on a gate file, so a test reads the arguments while the run is going.

The contract fixed here (ticket 115 Context items 1 and 2; human-reviewed):

1. `data-agent web --runs-dir D ... --mcp-servers FILE` hands FILE to `create_app`.
2. `create_app(runs_dir, data_sources, start_run, ui_dir=UI_DIR, mcp_servers=None)`. Given a
   file, the run form's runs (`POST /api/runs`) and every conversation turn
   (`POST /api/conversations/{id}/messages`) are started with `--mcp-servers <path>`, the path
   exactly as given to `create_app` (the tests give an absolute one); never an expanded value.
   Without it, neither has `--mcp-servers`.

Not asserted: whether `data-agent web` checks the config when it starts (ticket 115 "spec 里的解读"
6; the spec says nothing).
"""

import inspect
import json
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp_support import flag_value, write_config
from seam_support import FIXTURES

from data_agent import cli
from data_agent.web import app as web_app
from data_agent.web.app import DataSource, create_app

FAKE_CHAT_RUN = FIXTURES / "chat" / "fake_chat_run.py"
FORM = {"task": "Count orders", "task_type": "query", "data_source": "orders", "config": "full"}
# In the server's environment: it must never reach a run's command line.
GITHUB_TOKEN = "tok-" + "Pe40" + "-fake"
TIMEOUT = 60


@dataclass
class Started:
    args: list[str]
    process: "subprocess.Popen[bytes]"


@dataclass
class Web:
    client: TestClient
    # The file given to create_app; None for an app built without one.
    config: Path | None
    started: list[Started]

    def only_args(self) -> list[str]:
        assert len(self.started) == 1, f"expected one run, got {len(self.started)}"
        return self.started[0].args


def make_web(tmp_path: Path, mcp_servers: Path | None) -> Iterator[Web]:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    gates = tmp_path / "gates"
    gates.mkdir()
    (tmp_path / "orders.duckdb").write_bytes(b"")
    sources = [DataSource(name="orders", duckdb=tmp_path / "orders.duckdb")]
    started: list[Started] = []

    def start_run(args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
        process = subprocess.Popen(
            [sys.executable, str(FAKE_CHAT_RUN), "--gate-dir", str(gates), *args],
            cwd=cwd,
            stdin=subprocess.PIPE,
        )
        started.append(Started(list(args), process))
        return process

    # Without a file the app is built as every caller before ticket 115 builds it.
    app = (
        create_app(runs_dir, sources, start_run)
        if mcp_servers is None
        else create_app(runs_dir, sources, start_run, mcp_servers=mcp_servers)
    )
    try:
        with TestClient(app) as client:
            # The app is up and its run API answers: whatever fails below is the MCP wiring's.
            if client.get("/api/runs").status_code != 200:
                raise RuntimeError("the app does not answer GET /api/runs")
            yield Web(client, mcp_servers, started)
    finally:
        # Only the fake runs this test started, by their own process handles.
        for run in started:
            if run.process.poll() is None:
                run.process.terminate()
            run.process.wait(timeout=TIMEOUT)


def mcp_config(tmp_path: Path) -> Path:
    return write_config(
        tmp_path / "config" / "mcp.json",
        {
            "github": {
                "type": "http",
                "url": "https://mcp.example/readonly",
                "headers": {"Authorization": "Bearer ${DATA_AGENT_MCP_GITHUB_TOKEN}"},
                "allowedTools": ["*"],
            }
        },
    ).resolve()


@pytest.fixture
def mounted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Web]:
    monkeypatch.setenv("DATA_AGENT_MCP_GITHUB_TOKEN", GITHUB_TOKEN)
    yield from make_web(tmp_path, mcp_config(tmp_path))


@pytest.fixture
def plain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Web]:
    monkeypatch.setenv("DATA_AGENT_MCP_GITHUB_TOKEN", GITHUB_TOKEN)
    yield from make_web(tmp_path, None)


def submit_form(web: Web) -> list[str]:
    response = web.client.post("/api/runs", json=FORM)
    assert response.status_code == 201, response.text
    return web.only_args()


def start_turn(web: Web) -> list[str]:
    response = web.client.post("/api/conversations", json={})
    assert response.status_code == 201, response.text
    conversation = response.json()["id"]
    response = web.client.post(
        f"/api/conversations/{conversation}/messages", json={"text": "How many orders?"}
    )
    assert response.status_code == 201, response.text
    return web.only_args()


ROUTES = {"form": submit_form, "turn": start_turn}


@pytest.mark.parametrize("route", list(ROUTES))
def test_every_web_run_gets_the_config_path_and_no_expanded_value(mounted: Web, route: str) -> None:
    args = ROUTES[route](mounted)
    assert flag_value(args, "--mcp-servers") == str(mounted.config)
    assert GITHUB_TOKEN not in json.dumps(args)


@pytest.mark.parametrize("route", list(ROUTES))
def test_without_a_config_no_web_run_gets_mcp_servers(plain: Web, route: str) -> None:
    args = ROUTES[route](plain)
    assert "--type" in args
    assert not [arg for arg in args if arg == "--mcp-servers" or arg.startswith("--mcp-servers=")]


# --- `data-agent web --mcp-servers` ----------------------------------------------------------


def serve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: Sequence[str]) -> dict[str, Any]:
    """`data-agent web` with `create_app` and `uvicorn.run` stood in for (`serve_web` imports
    both when it runs); returns `create_app`'s arguments, bound to its real signature so either
    positional or keyword passing counts."""
    import uvicorn

    seen: list[dict[str, Any]] = []
    signature = inspect.signature(create_app)

    def fake_create_app(*args: Any, **kwargs: Any) -> object:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        seen.append(dict(bound.arguments))
        return object()

    # Set, so a `data-agent web` that checks the config when it starts finds it valid.
    monkeypatch.setenv("DATA_AGENT_MCP_GITHUB_TOKEN", GITHUB_TOKEN)
    monkeypatch.setattr(web_app, "create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "run", lambda *_args, **_kwargs: None)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    cli.main(["web", "--runs-dir", str(runs_dir), *extra])
    assert len(seen) == 1, "create_app was not called once"
    return seen[0]


def test_data_agent_web_hands_the_config_file_to_create_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = mcp_config(tmp_path)
    called = serve(tmp_path, monkeypatch, ["--mcp-servers", str(config)])
    given = called["mcp_servers"]
    assert given is not None, "create_app got no mcp_servers"
    assert Path(given).resolve() == config


def test_data_agent_web_without_the_option_gives_create_app_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = serve(tmp_path, monkeypatch, [])
    assert called["mcp_servers"] is None
