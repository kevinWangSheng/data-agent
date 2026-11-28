"""The web server's bearer token and `/healthz` (.scratch/cloud-deploy/spec.md "验收测试接缝" 网页
令牌; .scratch/product-config/decisions.md Q8; ADR-0012 and decisions.md "建 settings 工单时主会话
定的细节" 1: the one token is `DATA_AGENT_ADMIN_TOKEN`, with the rights of an organization admin).

Written by ticket 151. The tests marked TICKET_153 are xfail(strict) until ticket 153 makes them
pass and removes the marker, changing nothing else. The test named `test_guard_...` passes now and
must keep passing: a local server on the loopback address without a token answers its API as
before. Signing in for a session cookie (and the API and event stream with only the cookie) is
settings ticket 157's (`TICKET_159`), not here.

Seams (tests/acceptance/cloud_support.py): the whole `data-agent web --config F` with
`uvicorn.run` stood in for; the tests send their requests with a `TestClient` on the app
`uvicorn.run` was given, so how the token reaches the app is ticket 153's choice.

The contract fixed here (ticket 151 contract item 2; for the human review):

2. A server in cloud mode, or listening on an address other than the loopback one (both need
   `DATA_AGENT_ADMIN_TOKEN` set, test_cloud_startup.py contract item 1), takes on every `/api/*`
   route only `Authorization: Bearer <DATA_AGENT_ADMIN_TOKEN>`, the token compared whole: without
   the header, with another token, another scheme, no scheme or an empty token the answer is 401,
   before anything else is looked at (an unknown run or conversation is 401 too). The JSON reads,
   the event stream (`Accept: text/event-stream`) and the requests that start runs alike. Neither
   a 401's body nor the log holds the token's value. `GET /healthz` needs no credential in either
   mode and answers 200 with the JSON `{"ready": true}`. The UI's page and `/assets/*` need no
   credential either, so a browser can open the sign-in page (settings tickets 159 and 160); not
   asserted here. A local server on the loopback address asks for no credential.
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from cloud_support import (
    ADMIN_TOKEN,
    ANY_ADDRESS,
    FORM,
    Served,
    bearer,
    isolated,
    served,
    server_table,
    set_admin_token,
    shop_source,
    write_file,
    write_run,
)
from fastapi.testclient import TestClient

TICKET_153 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 153: [server].mode is not wired yet"
)

pytestmark = pytest.mark.usefixtures(isolated.__name__)

RUN_ID = "run-done"
UNAUTHORIZED = 401
# Every credential that is not the right one: none, another token, another scheme, no scheme, an
# empty token (written with and without the space).
WRONG: dict[str, dict[str, str]] = {
    "none": {},
    "another token": bearer("adm-" + "Xb1z" + "-not-the-token"),
    "a longer token": bearer(ADMIN_TOKEN + "x"),
    "a prefix of the token": bearer(ADMIN_TOKEN[:-1]),
    "another scheme": {"Authorization": f"Basic {ADMIN_TOKEN}"},
    "no scheme": {"Authorization": ADMIN_TOKEN},
    "an empty token": {"Authorization": "Bearer "},
    "the scheme alone": {"Authorization": "Bearer"},
}


def cloud_config(tmp_path: Path) -> Path:
    text = server_table("cloud", ANY_ADDRESS) + "\n" + shop_source(tmp_path)
    return write_file(tmp_path / "config" / "cloud.toml", text)


@pytest.fixture
def cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Iterator[Served]:
    """A cloud server on every address with the token set, and a finished run in its runs
    directory (so its event stream closes)."""
    set_admin_token(monkeypatch)
    write_run(tmp_path / "server" / "runs", RUN_ID)
    argv = ["--config", str(cloud_config(tmp_path))]
    with served(tmp_path / "server", monkeypatch, capsys, argv) as server:
        yield server


# --- Each `/api/*` request: a status code for the headers given -----------------------------


@dataclass(frozen=True)
class Ids:
    """The run and the conversation a request's path names."""

    run: str
    conversation: str


Request = Callable[[TestClient, dict[str, str], Ids], int]


def call(method: str, template: str, body: dict[str, Any] | None = None) -> Request:
    """A request whose path fills `{run}` and `{conversation}` from the ids given."""

    def send(client: TestClient, headers: dict[str, str], ids: Ids) -> int:
        path = template.format(run=ids.run, conversation=ids.conversation)
        return client.request(method, path, json=body, headers=headers).status_code

    return send


def events_stream(client: TestClient, headers: dict[str, str], ids: Ids) -> int:
    headers = {**headers, "Accept": "text/event-stream"}
    with client.stream("GET", f"/api/runs/{ids.run}/events", headers=headers) as response:
        # Read to the end: the run is finished, so a stream that was let through closes.
        for _line in response.iter_lines():
            pass
        return response.status_code


REQUESTS: dict[str, Request] = {
    "GET /api/runs": call("GET", "/api/runs"),
    "GET /api/options": call("GET", "/api/options"),
    "POST /api/runs": call("POST", "/api/runs", FORM),
    "GET /api/runs/{id}/events": call("GET", "/api/runs/{run}/events"),
    "GET /api/runs/{id}/events (SSE)": events_stream,
    "POST /api/runs/{id}/cancel": call("POST", "/api/runs/{run}/cancel"),
    "GET /api/conversations": call("GET", "/api/conversations"),
    "GET /api/conversations/{id}": call("GET", "/api/conversations/{conversation}"),
    "POST /api/conversations": call("POST", "/api/conversations", {}),
    "POST /api/conversations/{id}/messages": call(
        "POST",
        "/api/conversations/{conversation}/messages",
        {"text": "How many orders?", "network": False},
    ),
    "POST /api/conversations/{id}/cancel": call("POST", "/api/conversations/{conversation}/cancel"),
}
UNKNOWN = Ids("no-such-run", "no-such-conversation")


def new_conversation(client: TestClient) -> str:
    """A conversation made with the right token, for the routes that take one."""
    response = client.post("/api/conversations", json={}, headers=bearer())
    assert response.status_code == 201, f"{response.status_code}: {response.text}"
    return str(response.json()["id"])


@TICKET_153
@pytest.mark.parametrize("route", list(REQUESTS))
def test_every_api_route_takes_only_the_right_bearer_token(
    cloud: Served, caplog: pytest.LogCaptureFixture, route: str
) -> None:
    caplog.set_level("DEBUG")
    send = REQUESTS[route]
    with TestClient(cloud.app) as client:
        # A conversation that is there, so a message let through would start a run.
        known = Ids(RUN_ID, new_conversation(client))
        refused: dict[str, int] = {}
        for name, headers in WRONG.items():
            refused[name] = send(client, headers, known)
        if "{id}" in route:
            # 401 before the run or conversation is looked up.
            refused["an unknown id"] = send(client, {}, UNKNOWN)
        assert refused == dict.fromkeys(refused, UNAUTHORIZED)
        assert cloud.started == [], "a request without the right token started a run"
        # The same request with the right token is let through (whatever it then answers).
        assert send(client, bearer(), known) != UNAUTHORIZED
    assert ADMIN_TOKEN not in caplog.text


@TICKET_153
def test_a_401_holds_no_token(cloud: Served, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    with TestClient(cloud.app) as client:
        responses = [client.get("/api/runs", headers=headers) for headers in WRONG.values()]
    assert [response.status_code for response in responses] == [UNAUTHORIZED] * len(WRONG)
    for response in responses:
        assert ADMIN_TOKEN not in response.text
        assert ADMIN_TOKEN not in json.dumps(dict(response.headers))
    assert ADMIN_TOKEN not in caplog.text


# --- `/healthz` -----------------------------------------------------------------------------


@TICKET_153
@pytest.mark.parametrize("mode", ["local", "cloud"])
def test_healthz_answers_ready_without_a_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    if mode == "cloud":
        set_admin_token(monkeypatch)
        path = cloud_config(tmp_path)
    else:
        path = write_file(tmp_path / "config" / "local.toml", shop_source(tmp_path))
    argv = ["--config", str(path)]
    with (
        served(tmp_path / "server", monkeypatch, capsys, argv) as server,
        TestClient(server.app) as client,
    ):
        response = client.get("/healthz")
    assert response.status_code == 200, f"{response.status_code}: {response.text[:200]!r}"
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("application/json"), f"not JSON: {content_type!r}"
    assert response.json() == {"ready": True}


# --- A local server on the loopback address -------------------------------------------------


def test_guard_a_local_loopback_server_without_a_token_answers_without_a_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_run(tmp_path / "server" / "runs", RUN_ID)
    path = write_file(tmp_path / "config" / "local.toml", shop_source(tmp_path))
    argv = ["--config", str(path)]
    with (
        served(tmp_path / "server", monkeypatch, capsys, argv) as server,
        TestClient(server.app) as client,
    ):
        statuses = {
            "runs": client.get("/api/runs").status_code,
            "options": client.get("/api/options").status_code,
            "events": client.get(f"/api/runs/{RUN_ID}/events").status_code,
            "conversations": client.get("/api/conversations").status_code,
        }
    assert statuses == dict.fromkeys(statuses, 200)
