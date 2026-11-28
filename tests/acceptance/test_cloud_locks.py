"""The network, `dbt_target` and the runs' environment in cloud mode (.scratch/cloud-deploy/spec.md
"验收测试接缝" 锁定; .scratch/product-config/decisions.md Q8 人定 4, "主会话定的实现细节" `[policy]`
and 云端).

Written by ticket 151. The tests marked TICKET_153 are xfail(strict) until ticket 153 makes them
pass and removes the marker, changing nothing else. The tests named `test_guard_...` pass now and
must keep passing: a local server (no `[server]`) and a local `data-agent run` give the network
as they did. Not here: the endpoint (Q8 人定 5: cloud takes `DATA_AGENT_BASE_URL`, and a single
run cannot change it today), the `config` event (product-config 146; only the refusals are
asserted), and the run form's network default in `/api/options` (settings 157, `TICKET_160`).

Seams (tests/acceptance/cloud_support.py): the whole `data-agent run --config F`, its session
stopped at the SDK transport; the whole `data-agent web --config F` with `uvicorn.run` stood in
for, its requests sent with a `TestClient` on the app `uvicorn.run` was given, and the runs it
starts swapped for the fake run at `subprocess.Popen`, whose arguments and environment are
recorded. The run form is sent without `config` (product-config ticket 144 removes the field), so
the guards go through a conversation turn, which takes the same `network` today.

The contract fixed here (for the human review; it extends test_cloud_startup.py contract item 1):

- In cloud mode the network is off unless the config opens it with `[sandbox].network = true`;
  no single run goes past the config. Off: `data-agent run --network` is refused (exit 2, stderr
  naming `network`, no session); a run form or a conversation turn asking for it
  (`"network": true`) gets 409 and no run starts; one with `"network": false`, and an IM turn
  (`Conversations.send` with no `network`), start without `--network`. Opened: a request asking
  for it starts with `--network`.
- In cloud mode `dbt_target` is held at `scratch`: `data-agent run --type dbt --dbt-target task`
  is refused (exit 2, stderr naming `dbt_target`), never quietly changed (Q8).
- No run the server starts has `DATA_AGENT_ADMIN_TOKEN` in its environment (Q8), whatever else it
  is given.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from cloud_support import (
    ADMIN_TOKEN,
    ADMIN_TOKEN_ENV,
    FORM,
    Served,
    assert_run_refused,
    bearer,
    dbt_args,
    isolated,
    query_args,
    run_cli,
    served,
    server_table,
    set_admin_token,
    shop_source,
    stop_sessions,
    write_file,
)
from fastapi.testclient import TestClient

TICKET_153 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 153: [server].mode is not wired yet"
)

pytestmark = pytest.mark.usefixtures(isolated.__name__)

NETWORK = "--network"
WEB_TOOLS = ("WebSearch", "WebFetch")
CLOUD = server_table("cloud")
OPENED = CLOUD + "\n[sandbox]\nnetwork = true\n"


def config(root: Path, text: str) -> Path:
    return write_file(root / "config" / "config.toml", text)


# --- `data-agent run` -----------------------------------------------------------------------


@TICKET_153
def test_cloud_run_refuses_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    set_admin_token(monkeypatch)
    sessions = stop_sessions(monkeypatch)
    run = run_cli(capsys, query_args(tmp_path, config(tmp_path, CLOUD), NETWORK))
    assert_run_refused(run, sessions, "network")


@TICKET_153
def test_cloud_run_refuses_the_task_dbt_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    set_admin_token(monkeypatch)
    sessions = stop_sessions(monkeypatch)
    argv = dbt_args(tmp_path, config(tmp_path, CLOUD), "--dbt-target", "task")
    assert_run_refused(run_cli(capsys, argv), sessions, "dbt_target")


def test_guard_a_local_run_with_network_gets_the_web_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sessions = stop_sessions(monkeypatch)
    argv = query_args(tmp_path, config(tmp_path, "# no [server]\n"), NETWORK, "--sandbox", "local")
    run = run_cli(capsys, argv)
    assert sessions.options, f"no session started (exit {run.code!r}): {run.stderr[-2000:]!r}"
    options = sessions.only()
    for tool in WEB_TOOLS:
        assert tool in options.allowed_tools
        assert tool not in options.disallowed_tools


# --- The web server -------------------------------------------------------------------------


@contextmanager
def web(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    text: str,
    token: bool = True,
) -> Iterator[tuple[Served, TestClient]]:
    """`data-agent web` with the config `text` and a shop source; with `token`, the admin token
    set and sent with every request."""
    if token:
        set_admin_token(monkeypatch)
    path = config(root, text + "\n" + shop_source(root))
    with served(root / "server", monkeypatch, capsys, ["--config", str(path)]) as server:
        headers = bearer() if token else {}
        with TestClient(server.app, headers=headers) as client:
            yield server, client


def ask_form(client: TestClient, network: bool) -> Any:
    return client.post("/api/runs", json={**FORM, "network": network})


def ask_turn(client: TestClient, network: bool) -> Any:
    created = client.post("/api/conversations", json={})
    assert created.status_code == 201, f"{created.status_code}: {created.text}"
    path = f"/api/conversations/{created.json()['id']}/messages"
    return client.post(path, json={"text": "How many orders?", "network": network})


ROUTES = {"form": ask_form, "turn": ask_turn}


def assert_started(response: Any) -> None:
    assert response.status_code == 201, f"{response.status_code}: {response.text}"


@TICKET_153
@pytest.mark.parametrize("route", list(ROUTES))
def test_cloud_web_refuses_the_network_and_runs_without_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    route: str,
) -> None:
    ask = ROUTES[route]
    with web(tmp_path, monkeypatch, capsys, CLOUD) as (server, client):
        refused = ask(client, True)
        assert refused.status_code == 409, f"{refused.status_code}: {refused.text}"
        assert server.started == [], "a run started despite the 409"
        assert_started(ask(client, False))
        assert NETWORK not in server.only().args


@TICKET_153
def test_cloud_im_turn_runs_without_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from data_agent.web.launcher import Refused

    with web(tmp_path, monkeypatch, capsys, CLOUD) as (server, _):
        conversations = server.created.state.conversations
        conversation = conversations.create()
        try:
            sent = conversations.send(conversation, "How many orders?")
        except Refused as exc:
            raise AssertionError(f"the IM turn was refused: {exc.status} {exc.detail}") from exc
        assert sent.get("kind") == "turn", sent
        assert NETWORK not in server.only().args


@TICKET_153
@pytest.mark.parametrize("route", list(ROUTES))
def test_cloud_web_gives_the_network_only_when_the_config_opens_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    route: str,
) -> None:
    ask = ROUTES[route]
    with web(tmp_path / "closed", monkeypatch, capsys, CLOUD) as (_, client):
        closed = ask(client, True)
    with web(tmp_path / "opened", monkeypatch, capsys, OPENED) as (server, client):
        opened = ask(client, True)
        assert_started(opened)
        assert NETWORK in server.only().args
    assert closed.status_code == 409, f"{closed.status_code}: {closed.text}"


@TICKET_153
@pytest.mark.parametrize("route", list(ROUTES))
def test_a_cloud_run_gets_no_admin_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    route: str,
) -> None:
    with web(tmp_path, monkeypatch, capsys, CLOUD) as (server, client):
        assert_started(ROUTES[route](client, False))
        env = server.only().env
    # Booleans first: a failure message never prints the environment.
    named = ADMIN_TOKEN_ENV in env
    held = any(ADMIN_TOKEN in value for value in env.values())
    assert not named, f"the run's environment has {ADMIN_TOKEN_ENV}"
    assert not held, "a variable of the run's environment holds the token's value"
    # Only the token is gone: the rest of the server's environment is there as it was.
    assert env.get("DATA_AGENT_API_KEY"), "the run lost the model key"


def test_guard_a_local_web_turn_with_network_gets_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with web(tmp_path, monkeypatch, capsys, "# no [server]\n", token=False) as (server, client):
        assert_started(ask_turn(client, True))
        assert NETWORK in server.only().args
