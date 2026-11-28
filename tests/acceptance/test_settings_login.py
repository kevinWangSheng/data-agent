"""Signing in to the web UI (.scratch/settings/spec.md "网页登录 (S5, S6)"; "Testing Decisions"
登录; decisions.md S5, S6, Q8 "网页 Cookie", "建 settings 工单时主会话定的细节" 1, 5, 7, 12, 13).

Written by ticket 157. The tests marked TICKET_159 are xfail(strict) until ticket 159 makes them
pass and removes the marker, changing nothing else. The local-mode guards pass now (today the web
UI has no sign-in at all) and must keep passing.

Contract 5, signing in (human-reviewed with ticket 157):

- Start-up, cloud mode (`[server].mode = "cloud"`): without `DATA_AGENT_SESSION_KEY` (the key
  sessions are signed with) the server refuses to start: exit 2, stderr names the variable,
  `uvicorn.run` not called. A deployment with Slack (`[im.slack]`, its workspace's team id in
  `[im.slack].team_id`, or ticket 161's `[im.slack.workspaces.*]`) signs people in with Slack and
  refuses to start the same way without `DATA_AGENT_SLACK_OIDC_CLIENT_ID` or
  `DATA_AGENT_SLACK_OIDC_CLIENT_SECRET`. A deployment without Slack signs in with
  `DATA_AGENT_ADMIN_TOKEN` only.
- Sign in with Slack (OpenID Connect; https://docs.slack.dev/authentication/sign-in-with-slack):
  `GET /auth/slack/login` redirects to `https://slack.com/openid/connect/authorize` with
  `response_type=code`, `scope` holding `openid`, `client_id`, a fresh `state`, and
  `redirect_uri = <[server].public_url>/auth/slack/callback`.
  `GET /auth/slack/callback?code=&state=` checks `state` (a state the server did not hand out:
  400, no cookie), exchanges the code with
  `openid.connect.token` (same `redirect_uri`) and reads who it is from
  `openid.connect.userInfo` (`https://slack.com/user_id`, `https://slack.com/team_id`; the
  id_token is not read). A team the config does not list: 403, no cookie. Otherwise the session
  cookie and a redirect (302 or 303) to `/`. Slack is called through slack_sdk's clients
  (`AsyncWebClient` or `WebClient`); the tests answer those calls.
- The admin token: `POST /auth/token` with `{"token": "<DATA_AGENT_ADMIN_TOKEN>"}` gives the
  session cookie of an organisation admin (200); a wrong or empty token, or none set on the
  server, 401 and no cookie. `Authorization: Bearer <DATA_AGENT_ADMIN_TOKEN>` works on any
  `/api/*` too (test_settings_permissions.py).
- The session cookie: `data_agent_session`, a value signed with `DATA_AGENT_SESSION_KEY` holding
  who the person is and when it was issued, never a token (neither the admin token nor Slack's);
  `HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=604800` (7 days). A forged, altered or
  expired one: 401 on `/api/*`. Its age is reckoned by `data_agent.settings.login.now()` (seconds
  since the epoch), which the tests replace.
- `GET /api/me`: `{"user": <platform user id or null>, "workspace": <scope id or null>,
  "org_admin": bool}`; 401 without a session.
- Local mode (only this machine's address): no sign-in, no cookie ever, the person is the
  organisation admin (detail 12).

A run from the web by a signed-in person is given org < their workspace < their own layer
(detail 5).
"""

import logging
import time
from pathlib import Path
from typing import Any

import pytest
from settings_support import (
    ADMIN_TOKEN,
    ADMIN_TOKEN_ENV,
    ALICE,
    CALLBACK,
    COOKIE,
    FORM,
    LOGIN,
    OIDC_CLIENT_ID,
    OIDC_CLIENT_ID_ENV,
    OIDC_CLIENT_SECRET_ENV,
    PUBLIC_URL,
    REDIRECTS,
    ROOT,
    SESSION_KEY,
    SESSION_KEY_ENV,
    SLACK_ACCESS_TOKEN,
    TEAM,
    TOKEN_LOGIN,
    UNREGISTERED_TEAM,
    FakeSlack,
    cloud_config_text,
    cloud_env,
    cookie_attributes,
    finished_run,
    https_client,
    key_of,
    load_config,
    me,
    module_of,
    open_store,
    patch_async_web_client,
    patch_attr,
    platform_user,
    run_config_leaves,
    served,
    set_cookies,
    settings_attr,
    shop_config,
    sign_in,
    signed_in,
    start_login,
    stop_sessions,
    user,
    workspace,
)

TICKET_159 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 159: signing in is not built yet"
)

RUN_ID = "20261124T090000Z-seen0001"
WEEK = 7 * 24 * 3600


@pytest.fixture
def slack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeSlack:
    stop_sessions(monkeypatch, tmp_path / "home")
    cloud_env(monkeypatch)
    fake = FakeSlack()
    patch_async_web_client(monkeypatch, fake)
    return fake


def cloud_argv(tmp_path: Path, slack: bool = True) -> list[str]:
    return ["--config", str(shop_config(tmp_path, cloud_config_text(slack=slack)))]


def assert_refused(server: Any, variable: str) -> None:
    assert variable in server.stderr, f"stderr does not name {variable}: {server.stderr!r}"
    assert server.uvicorn is None, "uvicorn.run was called"
    assert server.code == 2, f"exit {server.code!r}"


# --- Start-up ---------------------------------------------------------------------------------


@TICKET_159
@pytest.mark.parametrize("variable", [SESSION_KEY_ENV, OIDC_CLIENT_ID_ENV, OIDC_CLIENT_SECRET_ENV])
def test_a_cloud_server_with_slack_refuses_to_start_without_a_sign_in_variable(
    tmp_path: Path,
    slack: FakeSlack,
    variable: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = cloud_argv(tmp_path)
    with served(tmp_path / "ok", monkeypatch, capsys, argv) as server:
        assert server.uvicorn is not None, f"did not start (exit {server.code!r}): {server.stderr}"
    monkeypatch.delenv(variable)
    with served(tmp_path / "srv", monkeypatch, capsys, argv) as server:
        assert_refused(server, variable)


@TICKET_159
def test_a_cloud_server_without_slack_refuses_to_start_without_the_session_key(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ADMIN_TOKEN_ENV, ADMIN_TOKEN)
    monkeypatch.delenv(SESSION_KEY_ENV)
    with served(tmp_path / "srv", monkeypatch, capsys, cloud_argv(tmp_path, slack=False)) as server:
        assert_refused(server, SESSION_KEY_ENV)


# --- Sign in with Slack ---------------------------------------------------------------------------


@TICKET_159
def test_the_login_redirect_names_the_public_callback(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with served(tmp_path / "srv", monkeypatch, capsys, cloud_argv(tmp_path)) as server:
        client = https_client(server.app())
        query = start_login(client)
        assert key_of(query, "redirect_uri") == [f"{PUBLIC_URL}{CALLBACK}"]
        assert key_of(query, "client_id") == [OIDC_CLIENT_ID]
        assert key_of(query, "response_type") == ["code"]
        assert "openid" in key_of(query, "scope")[0].split()
        assert key_of(query, "state")[0] != start_login(client)["state"][0]


@TICKET_159
def test_a_registered_workspace_signs_in_and_another_does_not(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with served(tmp_path / "srv", monkeypatch, capsys, cloud_argv(tmp_path)) as server:
        client = https_client(server.app())
        response = sign_in(client, slack, ALICE, TEAM)
        assert response.status_code in REDIRECTS, response.text
        assert [cookie_attributes(c)[0] for c in set_cookies(response)] == [COOKIE]
        [(_, exchange)] = [call for call in slack.calls if call[0] == "openid.connect.token"]
        assert exchange.get("redirect_uri") == f"{PUBLIC_URL}{CALLBACK}"
        body = me(client)
        assert body == {"user": platform_user(ALICE), "workspace": workspace(), "org_admin": False}

        outsider = https_client(server.app())
        response = sign_in(outsider, slack, "U0EVIL001", UNREGISTERED_TEAM)
        assert response.status_code == 403, response.text
        assert set_cookies(response) == []
        assert outsider.get("/api/me").status_code == 401


@TICKET_159
def test_a_callback_with_a_state_the_server_did_not_give_sets_no_cookie(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with served(tmp_path / "srv", monkeypatch, capsys, cloud_argv(tmp_path)) as server:
        client = https_client(server.app())
        start_login(client)
        code = slack.will_sign_in(ALICE)
        response = client.get(
            CALLBACK, params={"code": code, "state": "forged"}, follow_redirects=False
        )
        assert response.status_code == 400, response.text
        assert set_cookies(response) == []
        signed_in(client, slack, ALICE)


@TICKET_159
def test_the_session_cookie_is_signed_http_only_and_secure(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with served(tmp_path / "srv", monkeypatch, capsys, cloud_argv(tmp_path)) as server:
        response = sign_in(https_client(server.app()), slack, ALICE)
        [header] = set_cookies(response)
        name, value, attributes = cookie_attributes(header)
        assert name == COOKIE and value
        assert "httponly" in attributes and "secure" in attributes, attributes
        assert attributes.get("samesite", "").lower() == "lax", attributes
        assert attributes.get("path") == "/", attributes
        assert attributes.get("max-age") == str(WEEK), attributes
        for secret in (SLACK_ACCESS_TOKEN, ADMIN_TOKEN, SESSION_KEY):
            assert secret not in header


@TICKET_159
def test_a_forged_altered_or_expired_cookie_is_refused(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with served(tmp_path / "srv", monkeypatch, capsys, cloud_argv(tmp_path)) as server:
        app = server.app()
        response = sign_in(https_client(app), slack, ALICE)
        [header] = set_cookies(response)
        _, value, _ = cookie_attributes(header)

        def runs(cookie: str) -> int:
            client = https_client(app)
            return client.get("/api/runs", headers={"Cookie": f"{COOKIE}={cookie}"}).status_code

        assert runs(value) == 200
        altered = value[:-2] + ("AA" if not value.endswith("AA") else "BB")
        assert runs(altered) == 401
        assert runs("not-a-session") == 401
        assert runs("") == 401
        issued = time.time()
        patch_attr(
            monkeypatch, module_of("data_agent.settings.login"), "now", lambda: issued + WEEK + 60
        )
        assert runs(value) == 401


@TICKET_159
def test_the_cookie_alone_opens_the_api_and_the_event_stream(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moved here from cloud-deploy ticket 151 (its TICKET_156 tests, ADR-0012)."""
    with served(tmp_path / "srv", monkeypatch, capsys, cloud_argv(tmp_path)) as server:
        finished_run(tmp_path / "srv" / "runs", RUN_ID)
        client = https_client(server.app())
        assert client.get("/api/runs").status_code == 401
        signed_in(client, slack, ROOT)
        for path in (
            "/api/runs",
            "/api/options",
            "/api/conversations",
            f"/api/runs/{RUN_ID}/events",
        ):
            response = client.get(path)
            assert response.status_code == 200, f"{path}: {response.status_code} {response.text}"
        response = client.get(f"/api/runs/{RUN_ID}/events", headers={"Accept": "text/event-stream"})
        assert response.status_code == 200, response.text
        assert '"type": "result"' in response.text


# --- The admin token -----------------------------------------------------------------------------


@TICKET_159
def test_the_admin_token_signs_in_an_org_admin_and_nothing_else_does(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv(ADMIN_TOKEN_ENV, ADMIN_TOKEN)
    with served(tmp_path / "srv", monkeypatch, capsys, cloud_argv(tmp_path, slack=False)) as server:
        app = server.app()
        texts: list[str] = []
        for wrong in ("wrong", "", ADMIN_TOKEN + "x", ADMIN_TOKEN[:-1]):
            client = https_client(app)
            response = client.post(TOKEN_LOGIN, json={"token": wrong})
            assert response.status_code == 401, (wrong, response.status_code)
            assert set_cookies(response) == []
            texts.append(response.text)
        client = https_client(app)
        response = client.post(TOKEN_LOGIN, json={"token": ADMIN_TOKEN})
        assert response.status_code == 200, response.text
        [header] = set_cookies(response)
        assert cookie_attributes(header)[0] == COOKIE
        texts += [response.text, header]
        assert me(client).get("org_admin") is True
    for text in [*texts, caplog.text]:
        assert ADMIN_TOKEN not in text


@TICKET_159
def test_an_empty_or_unset_admin_token_lets_no_one_in(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ADMIN_TOKEN_ENV, "")
    with served(tmp_path / "srv", monkeypatch, capsys, cloud_argv(tmp_path)) as server:
        client = https_client(server.app())
        for body in ({"token": ""}, {"token": " "}, {}):
            response = client.post(TOKEN_LOGIN, json=body)
            assert response.status_code in (401, 422), (body, response.status_code)
            assert set_cookies(response) == []
        response = client.get("/api/me", headers={"Authorization": "Bearer "})
        assert response.status_code == 401
        signed_in(client, slack, ALICE)
        assert me(client).get("org_admin") is False


# --- A signed-in person's runs (detail 5) ---------------------------------------------------------


@TICKET_159
def test_a_signed_in_persons_web_run_has_their_workspace_and_personal_settings(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = shop_config(tmp_path, cloud_config_text())
    root = tmp_path / "srv"
    db = root / "runs" / "settings.sqlite"
    db.parent.mkdir(parents=True)
    store = open_store(db, load_config([config], {}))
    by = platform_user(ROOT)
    store.set("org", "limits.max_turns.query", 30, by=by)
    store.set(workspace(), "limits.effort", "high", by=by)
    store.set(workspace("T0GLOBEX2"), "limits.max_turns.query", 99, by=by)
    store.set(user(ALICE), "instructions.text", "ALICE-ONLY", by=platform_user(ALICE))
    store.set(user("U0BOB0002"), "instructions.text", "BOB-ONLY", by=by)
    settings_attr("access", "Access")
    with served(root, monkeypatch, capsys, ["--config", str(config)]) as server:
        web = server.web
        assert web is not None
        client = https_client(web.app)
        signed_in(client, slack, ALICE)
        response = client.post("/api/runs", json=FORM)
        assert response.status_code == 201, response.text
        leaves = run_config_leaves(web.only_args())
    assert leaves.get("limits.max_turns.query") == 30
    assert leaves.get("limits.effort") == "high"
    assert leaves.get("instructions.text") == "ALICE-ONLY"


# --- Local mode: no sign-in (guards, detail 12) ---------------------------------------------------


def local_argv(tmp_path: Path) -> list[str]:
    return ["--config", str(shop_config(tmp_path))]


def test_local_mode_needs_no_credentials_for_the_settings_page_s_options(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stop_sessions(monkeypatch, tmp_path / "home")
    from fastapi.testclient import TestClient

    with served(tmp_path / "srv", monkeypatch, capsys, local_argv(tmp_path)) as server:
        response = TestClient(server.app()).get("/api/options")
        assert response.status_code == 200, response.text


def test_local_mode_sets_no_cookie(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stop_sessions(monkeypatch, tmp_path / "home")
    from fastapi.testclient import TestClient

    with served(tmp_path / "srv", monkeypatch, capsys, local_argv(tmp_path)) as server:
        finished_run(tmp_path / "srv" / "runs", RUN_ID)
        client = TestClient(server.app())
        responses = [
            client.get("/api/options"),
            client.get("/api/runs"),
            client.get(f"/api/runs/{RUN_ID}/events"),
            client.get(f"/api/runs/{RUN_ID}/events", headers={"Accept": "text/event-stream"}),
            client.get("/api/conversations"),
            client.post("/api/conversations", json={}),
            client.get("/"),
            client.get(LOGIN, follow_redirects=False),
            client.post(TOKEN_LOGIN, json={"token": "anything"}),
        ]
        for response in responses:
            assert set_cookies(response) == [], (response.request.url, set_cookies(response))
        assert not client.cookies
