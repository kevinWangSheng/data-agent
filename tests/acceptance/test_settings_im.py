"""Settings in the chat platforms (.scratch/settings/spec.md "IM (S4, S8, S1)", "临时覆盖 (S4)",
"作用域与权限"; "Testing Decisions" IM; decisions.md S1, S2, S4, S5, S6, S8,
"建 settings 工单时主会话定的细节" 3, 6, 8, 9, 10, 14).

Written by ticket 157. The tests marked TICKET_161 are xfail(strict) until ticket 161 makes them
pass and removes the marker, changing nothing else. The guard passes now and must keep passing.

The seam: the IM adapter (`data_agent.im.adapter.Adapter`) on a platform in memory
(tests/acceptance/settings_support.py `FakePlatform`, im_support.py's extended), driven as the
IM acceptance tests drive it. Where what a turn is given matters, the conversations are the real
ones of a `data-agent web` server (its `create_app`, a recording `start_run`, the fake run of
tests/fixtures/chat/fake_chat_run.py), and a turn's settings are read from the config files it is
started with (`--config`, `Launcher.config_args`): what ticket 146's `config` event records
(contract 9). Elsewhere the conversations are a stand-in.

Contract 7, the platform and the adapter (human-reviewed with ticket 157):

- `Incoming.workspace_id: str | None = None`: the Slack team id (`team` of the event), the
  Discord server id, the Telegram bot's id. With it a Slack channel is
  `channel:slack:<team>:<chat>` (contract 3).
- `Platform.send_private(chat_id, thread_id, user_id, message) -> str`: a message only that user
  sees: Slack `chat.postEphemeral` in the chat; Telegram and Discord a private message. It raises
  when the platform cannot reach the user privately (a Telegram user who never wrote to the bot).
- `Adapter(platform, conversations, runs, allowed_users, clock, *, store=None, config=None)`:
  the settings store (contract 1) and the server's effective config (`[server].public_url`, the
  policy, the files' settings). Without them the adapter behaves as it does today.
  `ConversationsLike.send` and `Conversations.send` keep their signatures; anything new is a
  keyword argument with a default.
- `[im.slack.workspaces.<name>]`: `team_id`, `bot_token_env`, `app_token_env` (the variables'
  names, as `[im.slack]` has them); `[im.slack].allowed_users` stays the one list. A workspace's
  variable not set: the server refuses to start naming it.

Contract 4, overrides in a message: words `model=<name>`, `effort=<level>`, `source=<name>`,
`mcp=<name>[,<name>...]` at the start of the message, separated by spaces; the rest is the
question the turn gets. Each holds for the thread's every turn from then on until the thread sets
it again; `<key>=default` drops it. They map to `roles.main`, `limits.effort`, `chat.sources`
(`[<name>]`) and `chat.mcp`, inside `[policy]` (contract 1); a source or server must be one the
channel's `chat.sources` / `chat.mcp` opens (unset: all the deployment has). A value outside
them: the bot replies naming it, no turn starts, and the thread's overrides stay as they were.
A message of overrides only: the bot confirms them in the thread, no turn.

Contract 8, links: a turn's answer ends with `<[server].public_url>/runs/<run_id>` (the run's
page, no credential in it); `!configure` (Slack, the bot mentioned) and `/configure` (Telegram,
Discord) answer, only to the person, `<[server].public_url>/settings?scope=<channel scope id,
percent-encoded>` and send nothing to the channel. Telegram, when the private message fails: one
reply in the group saying to message the bot privately first, without the link (detail 14).

Contract 9, the session index (written by ticket 161, read by 162): `Store.record_run(run_id, *,
thread, channel, owner)` and `Store.run_entry(run_id) -> RunEntry | None` with those fields:
`thread` is `<platform>:<chat id>:<thread id>` (empty after the last colon without threads),
`channel` the channel's scope id (None in a direct chat), `owner` the person's user scope id in a
direct chat or a web run (None in a channel). `im/threads.json` stays as it is.

A channel-layer change (any `Store.set` on a `channel:` scope, through the settings page or not)
is announced in that channel within the adapter's next `poll()`: who (their user id) and which
key. `chat.require_mention = false` on a channel: a top-level message there without the mention
starts a turn too; unset, today's rule (test_im_triggers.py).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
from settings_support import (
    ADMIN,
    ALICE,
    BOB,
    DISCORD_CHANNEL,
    DISCORD_GUILD,
    DISCORD_USER,
    ENG,
    OTHER_TEAM,
    PUBLIC_URL,
    SALES,
    TEAM,
    TELEGRAM_BOT,
    TELEGRAM_GROUP,
    TELEGRAM_USER,
    FakePlatform,
    Web,
    build_shop_duckdb,
    channel,
    finish_im_run,
    has_field,
    incoming,
    load_config,
    open_store,
    platform_user,
    run_config_leaves,
    served,
    settings_attr,
    shop_config,
    start_bot,
    stop_sessions,
    toml_path,
    user,
    workspace,
)

TICKET_161 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 161: settings in IM are not built yet"
)

ALLOWED = (ALICE, BOB, ADMIN, TELEGRAM_USER, DISCORD_USER)
BY = platform_user("U0ROOT001")
SERVER = f'[server]\npublic_url = "{PUBLIC_URL}"\n\n'
MCP = (
    '[mcp.servers.acme]\ntype = "http"\nurl = "https://mcp.example/acme"\nallowedTools = ["*"]\n\n'
    '[mcp.servers.notes]\ntype = "http"\nurl = "https://mcp.example/notes"\n'
    'allowedTools = ["*"]\n\n'
)


@dataclass
class Product:
    web: Web
    config: Any
    store: Any
    root: Path

    def turn(self, index: int) -> dict[str, Any]:
        assert len(self.web.started) > index, f"only {len(self.web.started)} turns started"
        return run_config_leaves(self.web.started[index].args)

    def task(self, index: int) -> str:
        task = [a for a in self.web.started[index].args if a.startswith("--task=")]
        return task[0].removeprefix("--task=") if task else ""


@contextmanager
def product(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra: str = "",
) -> Iterator[Product]:
    """A `data-agent web` server (local mode) with the shop source and a second one, `extra` in
    its config, and the store it opened at `<runs_dir>/settings.sqlite`."""
    settings_attr("store", "open_store")
    stop_sessions(monkeypatch, tmp_path / "home")
    second = build_shop_duckdb(tmp_path / "shop2.duckdb")
    sources = f'[sources.shop2]\nkind = "duckdb"\npath = {toml_path(second)}\n\n'
    config_path = shop_config(tmp_path, SERVER + sources + extra)
    root = tmp_path / "srv"
    with served(root, monkeypatch, capsys, ["--config", str(config_path)]) as server:
        assert server.web is not None, f"did not start (exit {server.code!r}): {server.stderr}"
        config = load_config([config_path])
        store = open_store(root / "runs" / "settings.sqlite", config)
        yield Product(server.web, config, store, root)


async def product_bot(p: Product, platform: FakePlatform) -> Any:
    return await start_bot(
        p.root / "runs",
        platform,
        p.web.app.state.conversations,
        ALLOWED,
        store=p.store,
        config=p.config,
    )


def settings_only(tmp_path: Path, extra: str = "") -> tuple[Any, Any]:
    settings_attr("store", "open_store")
    config = load_config([shop_config(tmp_path, SERVER + extra)])
    return config, open_store(tmp_path / "settings.sqlite", config)


# --- !configure ---------------------------------------------------------------------------------


SETTINGS_LINK = f"{PUBLIC_URL}/settings?scope="


def links_to(text: str, scope: str) -> bool:
    start = text.find(SETTINGS_LINK)
    if start < 0:
        return False
    rest = text[start + len(SETTINGS_LINK) :].split()[0].rstrip(">)")
    return unquote(rest) == scope


@TICKET_161
@pytest.mark.parametrize(
    ("platform", "text", "chat", "team", "person"),
    [
        ("slack", "!configure", SALES, TEAM, ALICE),
        ("telegram", "/configure", TELEGRAM_GROUP, TELEGRAM_BOT, TELEGRAM_USER),
        ("discord", "/configure", DISCORD_CHANNEL, DISCORD_GUILD, DISCORD_USER),
    ],
)
async def test_configure_answers_only_the_person_with_the_channel_s_settings_link(
    tmp_path: Path, platform: str, text: str, chat: str, team: str, person: str
) -> None:
    config, store = settings_only(tmp_path)
    fake = FakePlatform(platform)
    bot = await start_bot(tmp_path / "runs", fake, None, ALLOWED, store=store, config=config)
    thread = "T1" if platform != "telegram" else None
    message = incoming(
        text, person=person, chat=chat, thread=thread, platform=platform, workspace_id=team
    )
    await fake.deliver(message)
    [private] = fake.private(person)
    assert links_to(private.text, channel(chat, team, platform)), private.text
    assert fake.public(chat) == []
    assert bot.conversations.created == []


@TICKET_161
async def test_configure_on_telegram_without_a_private_chat_asks_for_one_first(
    tmp_path: Path,
) -> None:
    config, store = settings_only(tmp_path)
    fake = FakePlatform("telegram")
    fake.unreachable.add(TELEGRAM_USER)
    bot = await start_bot(tmp_path / "runs", fake, None, ALLOWED, store=store, config=config)
    await fake.deliver(
        incoming(
            "/configure",
            person=TELEGRAM_USER,
            chat=TELEGRAM_GROUP,
            thread=None,
            platform="telegram",
            workspace_id=TELEGRAM_BOT,
        )
    )
    [reply] = fake.public(TELEGRAM_GROUP)
    assert "private" in reply.text.lower() or "privately" in reply.text.lower(), reply.text
    assert PUBLIC_URL not in reply.text
    assert bot.conversations.created == []


# --- Channel changes are announced --------------------------------------------------------------


@TICKET_161
async def test_a_channel_change_is_announced_in_that_channel(tmp_path: Path) -> None:
    config, store = settings_only(tmp_path)
    fake = FakePlatform("slack")
    bot = await start_bot(tmp_path / "runs", fake, None, ALLOWED, store=store, config=config)
    store.set(channel(SALES), "instructions.text", "Answer in euros.", by=platform_user(BOB))
    store.set(workspace(), "limits.effort", "high", by=platform_user(ADMIN))
    await bot.adapter.poll()
    [notice] = fake.public(SALES)
    assert BOB in notice.text and "instructions.text" in notice.text, notice.text
    assert fake.public(ENG) == []
    await bot.adapter.poll()
    assert len(fake.public(SALES)) == 1, "the change was announced twice"


# --- Overrides (contract 4) -----------------------------------------------------------------------


@TICKET_161
async def test_overrides_hold_for_the_thread_until_changed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with product(tmp_path, monkeypatch, capsys) as p:
        fake = FakePlatform("slack")
        await product_bot(p, fake)
        await fake.deliver(incoming("model=deepseek-v4-pro effort=high How many orders?"))
        first = p.turn(0)
        assert (first.get("roles.main"), first.get("limits.effort")) == ("deepseek-v4-pro", "high")
        assert p.task(0) == "How many orders?"
        p.web.finish()

        await fake.deliver(incoming("And last month?", mentioned=False))
        second = p.turn(1)
        assert (second.get("roles.main"), second.get("limits.effort")) == (
            "deepseek-v4-pro",
            "high",
        )
        p.web.finish()

        await fake.deliver(incoming("model=deepseek-flash Again, please", mentioned=False))
        third = p.turn(2)
        assert (third.get("roles.main"), third.get("limits.effort")) == ("deepseek-flash", "high")
        p.web.finish()

        await fake.deliver(incoming("effort=default Once more", mentioned=False))
        assert p.turn(3).get("limits.effort") != "high"
        p.web.finish()

        await fake.deliver(incoming("How many customers?", thread="T2"))
        other = p.turn(4)
        assert other.get("roles.main") != "deepseek-v4-pro"
        assert other.get("limits.effort") != "high"


@TICKET_161
async def test_an_override_of_only_settings_starts_no_turn_and_holds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with product(tmp_path, monkeypatch, capsys) as p:
        fake = FakePlatform("slack")
        await product_bot(p, fake)
        await fake.deliver(incoming("model=deepseek-v4-pro"))
        assert p.web.started == []
        assert any("deepseek-v4-pro" in s.text for s in fake.public(SALES)), fake.texts()
        await fake.deliver(incoming("How many orders?", mentioned=False))
        assert p.turn(0).get("roles.main") == "deepseek-v4-pro"


@TICKET_161
@pytest.mark.parametrize(
    "words",
    [
        "model=deepseek-v4-pro",  # disabled by the policy
        "source=shop2",  # not opened to the channel
        "mcp=notes",  # not opened to the channel
    ],
)
async def test_an_override_out_of_bounds_is_refused_and_nothing_runs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    words: str,
) -> None:
    third = build_shop_duckdb(tmp_path / "shop3.duckdb")
    extra = (
        MCP
        + '[mcp.servers.wiki]\ntype = "http"\nurl = "https://mcp.example/wiki"\n'
        + 'allowedTools = ["*"]\n\n'
        + f'[sources.shop3]\nkind = "duckdb"\npath = {toml_path(third)}\n\n'
        + '[policy]\ndisabled_models = ["deepseek-v4-pro"]\n'
    )
    opened_sources, opened_mcp = ["shop", "shop3"], ["acme", "wiki"]
    with product(tmp_path, monkeypatch, capsys, extra) as p:
        p.store.set(channel(SALES), "chat.sources", opened_sources, by=BY)
        p.store.set(channel(SALES), "chat.mcp", opened_mcp, by=BY)
        fake = FakePlatform("slack")
        await product_bot(p, fake)
        # Allowed ones first: one of the channel's sources and servers each, not all it opens.
        await fake.deliver(incoming("source=shop3 mcp=wiki effort=high How many orders?"))
        allowed = p.turn(0)
        assert allowed.get("chat.sources") == ["shop3"] and allowed.get("chat.mcp") == ["wiki"]
        p.web.finish()

        shown = len(fake.shown)
        await fake.deliver(incoming(f"{words} How many customers?", mentioned=False))
        assert len(p.web.started) == 1, "a turn started despite the refused override"
        replies = fake.shown[shown:]
        value = words.split("=", 1)[1]
        assert replies and value in replies[-1].text, [r.text for r in replies]

        await fake.deliver(incoming("And now?", mentioned=False))
        after = p.turn(1)
        assert (after.get("chat.sources"), after.get("chat.mcp")) == (["shop3"], ["wiki"])
        assert after.get("limits.effort") == "high"
        assert after.get("roles.main") != "deepseek-v4-pro"
        p.web.finish()

        await fake.deliver(
            incoming("source=default mcp=default And without them?", mentioned=False)
        )
        reset = p.turn(2)
        assert (reset.get("chat.sources"), reset.get("chat.mcp")) == (opened_sources, opened_mcp)
        assert reset.get("limits.effort") == "high"


@TICKET_161
async def test_instructions_are_pinned_when_the_thread_starts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with product(tmp_path, monkeypatch, capsys) as p:
        p.store.set(channel(SALES), "instructions.text", "OLD", by=BY)
        fake = FakePlatform("slack")
        await product_bot(p, fake)
        await fake.deliver(incoming("How many orders?"))
        assert p.turn(0).get("instructions.text") == "OLD"
        p.web.finish()

        p.store.set(channel(SALES), "instructions.text", "NEW", by=platform_user(BOB))
        p.store.set(channel(SALES), "limits.effort", "high", by=platform_user(BOB))
        await fake.deliver(incoming("And last month?", mentioned=False))
        again = p.turn(1)
        assert again.get("instructions.text") == "OLD"
        assert again.get("limits.effort") == "high"
        p.web.finish()

        await fake.deliver(incoming("How many customers?", thread="T2"))
        assert p.turn(2).get("instructions.text") == "NEW"


# --- Where a message's settings come from (S2, S5, S6) --------------------------------------------


@TICKET_161
async def test_each_platform_s_message_resolves_to_its_own_scopes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with product(tmp_path, monkeypatch, capsys) as p:
        marks = {
            workspace(): "SLACK-WS",
            channel(SALES): "SLACK-CHANNEL",
            user(ALICE): "ALICE-OWN",
            workspace(DISCORD_GUILD, "discord"): "DISCORD-WS",
            channel(DISCORD_CHANNEL, DISCORD_GUILD, "discord"): "DISCORD-CHANNEL",
            workspace(TELEGRAM_BOT, "telegram"): "TELEGRAM-WS",
            user(TELEGRAM_USER, "telegram"): "TELEGRAM-OWN",
        }
        for scope, text in marks.items():
            p.store.set(scope, "instructions.text", text, by=BY)
        p.store.set(workspace(), "limits.effort", "high", by=BY)
        p.store.set(user(ALICE), "limits.max_budget_usd", 0.5, by=BY)
        p.store.set(user(ALICE), "limits.effort", "low", by=platform_user(ALICE))
        slack, discord, telegram = (
            FakePlatform("slack"),
            FakePlatform("discord"),
            FakePlatform("telegram"),
        )
        for fake in (slack, discord, telegram):
            await product_bot(p, fake)

        cases = [
            (slack, incoming("q"), "SLACK-CHANNEL"),
            (slack, incoming("q", chat="D0ALICE", thread=None, direct=True), "ALICE-OWN"),
            (
                discord,
                incoming(
                    "q",
                    person=DISCORD_USER,
                    chat=DISCORD_CHANNEL,
                    platform="discord",
                    workspace_id=DISCORD_GUILD,
                ),
                "DISCORD-CHANNEL",
            ),
            (
                telegram,
                incoming(
                    "q",
                    person=TELEGRAM_USER,
                    chat=TELEGRAM_GROUP,
                    thread=None,
                    platform="telegram",
                    workspace_id=TELEGRAM_BOT,
                ),
                "TELEGRAM-WS",
            ),
            (
                telegram,
                incoming(
                    "q",
                    person=TELEGRAM_USER,
                    chat=TELEGRAM_USER,
                    thread=None,
                    direct=True,
                    platform="telegram",
                    workspace_id=TELEGRAM_BOT,
                ),
                "TELEGRAM-OWN",
            ),
        ]
        for index, (fake, message, expected) in enumerate(cases):
            await fake.deliver(message)
            assert p.turn(index).get("instructions.text") == expected, (index, expected)
            p.web.finish()
        # In the channel the workspace's effort; in Alice's direct chat her own (S2).
        assert p.turn(0).get("limits.effort") == "high"
        assert p.turn(1).get("limits.effort") == "low"
        # A personal layer holds behaviour keys only; access and spend keys are not taken from it.
        assert p.turn(1).get("limits.max_budget_usd") != 0.5


@TICKET_161
async def test_two_slack_workspaces_each_resolve_to_their_own_layer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = {
        "DATA_AGENT_SLACK_ACME_BOT": "acme-bot-" + "Ab1c",
        "DATA_AGENT_SLACK_ACME_APP": "acme-app-" + "Cd2e",
        "DATA_AGENT_SLACK_GLOBEX_BOT": "globex-bot-" + "Ef3g",
        "DATA_AGENT_SLACK_GLOBEX_APP": "globex-app-" + "Gh4i",
    }
    for name, value in tokens.items():
        monkeypatch.setenv(name, value)
    im = (
        f'[im.slack]\nallowed_users = ["{ALICE}", "{BOB}"]\n\n'
        f'[im.slack.workspaces.acme]\nteam_id = "{TEAM}"\n'
        'bot_token_env = "DATA_AGENT_SLACK_ACME_BOT"\n'
        'app_token_env = "DATA_AGENT_SLACK_ACME_APP"\n\n'
        f'[im.slack.workspaces.globex]\nteam_id = "{OTHER_TEAM}"\n'
        'bot_token_env = "DATA_AGENT_SLACK_GLOBEX_BOT"\n'
        'app_token_env = "DATA_AGENT_SLACK_GLOBEX_APP"\n\n'
    )
    with product(tmp_path, monkeypatch, capsys, im) as p:
        p.store.set(workspace(TEAM), "instructions.text", "ACME", by=BY)
        p.store.set(workspace(OTHER_TEAM), "instructions.text", "GLOBEX", by=BY)
        fake = FakePlatform("slack")
        await product_bot(p, fake)
        await fake.deliver(incoming("q", chat="C0ACME", workspace_id=TEAM))
        assert p.turn(0).get("instructions.text") == "ACME"
        p.web.finish()
        await fake.deliver(incoming("q", chat="C0GLOBEX", person=BOB, workspace_id=OTHER_TEAM))
        assert p.turn(1).get("instructions.text") == "GLOBEX"

    monkeypatch.delenv("DATA_AGENT_SLACK_GLOBEX_APP")
    stop_sessions(monkeypatch, tmp_path / "home")
    config_path = shop_config(tmp_path / "missing", SERVER + im)
    with served(
        tmp_path / "missing" / "srv", monkeypatch, capsys, ["--config", str(config_path)]
    ) as s:
        assert "DATA_AGENT_SLACK_GLOBEX_APP" in s.stderr, s.stderr
        assert s.uvicorn is None and s.code == 2


# --- The answer's link and the session index (S8, details 3, 7) -----------------------------------


@TICKET_161
async def test_the_answer_links_the_run_s_page_and_the_index_records_the_turn(
    tmp_path: Path,
) -> None:
    config, store = settings_only(tmp_path)
    fake = FakePlatform("slack")
    bot = await start_bot(tmp_path / "runs", fake, None, ALLOWED, store=store, config=config)
    await fake.deliver(incoming("How many orders?"))
    channel_run = bot.conversations.turns[bot.conversations.created[0]][0]
    finish_im_run(bot.runs, channel_run)
    await fake.deliver(incoming("How many?", chat="D0ALICE", thread=None, direct=True))
    direct_run = bot.conversations.turns[bot.conversations.created[1]][0]
    finish_im_run(bot.runs, direct_run)
    await bot.adapter.poll()

    for chat, thread, run_id in ((SALES, "T1", channel_run), ("D0ALICE", None, direct_run)):
        texts = [s.text for s in fake.shown if s.chat_id == chat and s.thread_id == thread]
        link = f"{PUBLIC_URL}/runs/{run_id}"
        assert any(link in text for text in texts), texts
        for text in texts:
            after = text[text.find(link) + len(link) :] if link in text else ""
            assert not after.startswith(("?", "#")), "the link carries more than the run's id"

    entry = store.run_entry(channel_run)
    assert entry is not None
    assert (entry.thread, entry.channel, entry.owner) == (f"slack:{SALES}:T1", channel(SALES), None)
    entry = store.run_entry(direct_run)
    assert entry is not None
    assert (entry.thread, entry.channel, entry.owner) == ("slack:D0ALICE:", None, user(ALICE))
    assert (tmp_path / "runs" / "im" / "threads.json").is_file()


# --- Whether the mention is needed (detail 8) -----------------------------------------------------


@TICKET_161
async def test_a_channel_can_drop_the_mention(tmp_path: Path) -> None:
    config, store = settings_only(tmp_path)
    store.set(channel(SALES), "chat.require_mention", False, by=platform_user(BOB))
    fake = FakePlatform("slack")
    bot = await start_bot(tmp_path / "runs", fake, None, ALLOWED, store=store, config=config)
    await fake.deliver(incoming("Lunch numbers?", thread="T9", mentioned=False))
    assert len(bot.conversations.created) == 1
    await fake.deliver(incoming("Lunch numbers?", chat=ENG, thread="T8", mentioned=False))
    assert len(bot.conversations.created) == 1


async def test_without_settings_a_channel_message_still_needs_the_mention(tmp_path: Path) -> None:
    """Guard (detail 8): with no setting the trigger rule stays test_im_triggers.py's."""
    from data_agent.im.platform import Incoming

    fake = FakePlatform("slack")
    bot = await start_bot(tmp_path / "runs", fake, None, ALLOWED)
    await fake.deliver(
        Incoming("slack", SALES, "T9", ALICE, direct=False, mentioned=False, text="Lunch?")
    )
    assert bot.conversations.created == []
    assert fake.shown == []
    await fake.deliver(
        Incoming("slack", SALES, "T9", ALICE, direct=False, mentioned=True, text="Orders?")
    )
    assert len(bot.conversations.created) == 1


@TICKET_161
def test_slack_s_message_event_gives_its_team_as_the_workspace_id() -> None:
    from data_agent.im.platform import Incoming
    from data_agent.im.slack import to_incoming

    has_field(Incoming, "workspace_id")
    bot = "U0BOT0001"
    event = {
        "type": "message",
        "channel": SALES,
        "channel_type": "channel",
        "user": ALICE,
        "ts": "1700000000.000100",
        "team": TEAM,
        "text": f"<@{bot}> How many orders?",
    }
    got = to_incoming(event, bot)
    assert got is not None
    assert getattr(got, "workspace_id", None) == TEAM
