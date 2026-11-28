"""Who may look at a run on the web (.scratch/settings/spec.md "会话查看 (S8)"; "Testing Decisions"
会话查看; decisions.md S8, "建 settings 工单时主会话定的细节" 3, 4, 5, 15, 18).

Written by ticket 157. The tests marked TICKET_162 are xfail(strict) until ticket 162 makes them
pass and removes the marker, changing nothing else.

The runs are made the way the product makes them, on a cloud server with Sign in with Slack
(contract 5): a Slack channel's turn and a Slack direct chat's turn through the IM adapter
(ticket 161 records them in the session index, contract 9), a Telegram group's turn the same
way, a run from the web form by a signed-in person (ticket 162 records its owner), and a run the
index does not know (one from the command line, or from before the index).

Contract 9, viewing (human-reviewed with ticket 157), one rule for a run's events
(`GET /api/runs/{id}/events`, JSON and `text/event-stream`), the run list (`GET /api/runs`) and
the conversation list (`GET /api/conversations`) (detail 4):
- a Slack channel's run: that channel's members (`conversations.members`);
- a direct chat's run and a web run: their owner;
- a Telegram or Discord run, and a run the index does not know: no one else;
- an organisation admin: every run.
A run the person may not see: 403 for its events, left out of both lists. Not signed in: 401;
the link (`/runs/<id>`) grants nothing. A conversation of a chat platform is read only on the
web: `POST /api/conversations/{id}/messages` on it is 403 for everyone (S8; the web's own
conversations go on as they do).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from settings_support import (
    ALICE,
    BOB,
    EVE,
    FORM,
    ROOT,
    SALES,
    TEAM,
    TELEGRAM_BOT,
    TELEGRAM_GROUP,
    TELEGRAM_USER,
    FakePlatform,
    FakeSlack,
    Web,
    cloud_config_text,
    cloud_env,
    finished_run,
    flag_value,
    https_client,
    incoming,
    load_config,
    open_store,
    patch_async_web_client,
    served,
    settings_attr,
    shop_config,
    signed_in,
    start_bot,
    stop_sessions,
)

TICKET_162 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 162: viewing runs is not checked yet"
)

UNKNOWN_RUN = "20261120T090000Z-cli00001"
ALLOWED = (ALICE, BOB, EVE, ROOT, TELEGRAM_USER)


@dataclass
class World:
    app: Any
    slack: FakeSlack
    runs: dict[str, str]
    im_conversation: str

    def client(self, person: str | None) -> Any:
        client = https_client(self.app)
        if person is not None:
            signed_in(client, self.slack, person)
        return client


def last_run(web: Web) -> str:
    return Path(flag_value(web.started[-1].args, "--events-dir")).name


@asynccontextmanager
async def world(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[World]:
    settings_attr("store", "open_store")
    stop_sessions(monkeypatch, tmp_path / "home")
    cloud_env(monkeypatch)
    slack = FakeSlack()
    patch_async_web_client(monkeypatch, slack)
    config_path = shop_config(tmp_path, cloud_config_text())
    root = tmp_path / "srv"
    with served(root, monkeypatch, capsys, ["--config", str(config_path)]) as server:
        web = server.web
        assert web is not None, f"did not start (exit {server.code!r}): {server.stderr}"
        config = load_config([config_path])
        store = open_store(root / "runs" / "settings.sqlite", config)
        conversations = web.app.state.conversations
        runs: dict[str, str] = {}

        slack_bot = await start_bot_on(root, FakePlatform("slack"), conversations, store, config)
        await deliver(slack_bot, incoming("How many orders?", chat=SALES, workspace_id=TEAM))
        runs["channel"] = last_run(web)
        web.finish()
        im_conversation = slack_bot.adapter.threads[("slack", SALES, "T1")]
        await deliver(slack_bot, incoming("Mine?", chat="D0ALICE", thread=None, direct=True))
        runs["direct"] = last_run(web)
        web.finish()

        telegram_bot = await start_bot_on(
            root, FakePlatform("telegram"), conversations, store, config
        )
        await deliver(
            telegram_bot,
            incoming(
                "Orders?",
                person=TELEGRAM_USER,
                chat=TELEGRAM_GROUP,
                thread=None,
                platform="telegram",
                workspace_id=TELEGRAM_BOT,
            ),
        )
        runs["telegram"] = last_run(web)
        web.finish()

        bob = https_client(web.app)
        signed_in(bob, slack, BOB)
        response = bob.post("/api/runs", json=FORM)
        assert response.status_code == 201, response.text
        runs["web"] = response.json()["id"]
        web.finish()

        runs["unknown"] = finished_run(root / "runs", UNKNOWN_RUN)
        yield World(web.app, slack, runs, im_conversation)


async def start_bot_on(
    root: Path, platform: FakePlatform, conversations: Any, store: Any, config: Any
) -> Any:
    return await start_bot(
        root / "runs", platform, conversations, ALLOWED, store=store, config=config
    )


async def deliver(bot: Any, message: Any) -> None:
    await bot.platform.deliver(message)


# Who sees which run (S8): channel members, the owner, the organisation admin.
SEES = {
    ALICE: {"channel", "direct"},
    BOB: {"channel", "web"},
    EVE: set[str](),
    ROOT: {"channel", "direct", "telegram", "web", "unknown"},
}


@TICKET_162
async def test_a_run_s_events_are_shown_only_to_who_may_see_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with world(tmp_path, capsys, monkeypatch) as w:
        nobody = w.client(None)
        for run_id in w.runs.values():
            assert nobody.get(f"/api/runs/{run_id}/events").status_code == 401
        for person, sees in SEES.items():
            client = w.client(person)
            for name, run_id in w.runs.items():
                expected = 200 if name in sees else 403
                for accept in ("application/json", "text/event-stream"):
                    response = client.get(f"/api/runs/{run_id}/events", headers={"Accept": accept})
                    assert response.status_code == expected, (person, name, accept)
                    if expected == 403:
                        assert '"type": "result"' not in response.text
                        assert '"type":"result"' not in response.text


@TICKET_162
async def test_the_lists_hold_only_what_the_person_may_see(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with world(tmp_path, capsys, monkeypatch) as w:
        for person, sees in SEES.items():
            client = w.client(person)
            response = client.get("/api/runs")
            assert response.status_code == 200, response.text
            listed = {row["id"] for row in response.json()}
            assert listed == {w.runs[name] for name in sees}, (person, listed)
            response = client.get("/api/conversations")
            assert response.status_code == 200, response.text
            text = response.text
            for name, run_id in w.runs.items():
                assert (run_id in text) == (name in sees), (person, name)


@TICKET_162
async def test_a_chat_platform_s_conversation_is_read_only_on_the_web(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with world(tmp_path, capsys, monkeypatch) as w:
        path = f"/api/conversations/{w.im_conversation}/messages"
        for person in (ALICE, ROOT):
            client = w.client(person)
            response = client.post(path, json={"text": "Continue here?"})
            assert response.status_code == 403, (person, response.status_code, response.text)
        # The web's own conversations go on.
        client = w.client(ALICE)
        response = client.post("/api/conversations", json={})
        assert response.status_code == 201, response.text
        own = response.json()["id"]
        response = client.post(f"/api/conversations/{own}/messages", json={"text": "Orders?"})
        assert response.status_code == 201, response.text
