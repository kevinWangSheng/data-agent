"""The Discord platform (data_agent.im.discord_bot): discord.py's messages and interactions <-> the
platform interface, sending into lazily made threads, buttons, the limits, `start`/`stop`, no
token in the logs, and `service` picking it. No Discord: stand-ins that record their calls, or a
real client pointed at an HTTP server on the loopback address."""

import asyncio
import json
import logging
import sys
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest
from aiohttp import web
from discord.http import Route
from fastapi import FastAPI

from data_agent.im import service
from data_agent.im.config import PlatformConfig
from data_agent.im.discord_bot import (
    CUSTOM_ID,
    LIMITS,
    MESSAGE_CHARS,
    THREAD_ARCHIVE_MINUTES,
    THREAD_NAME,
    DiscordPlatform,
    sync_commands,
    thread_name,
)
from data_agent.im.format import format_answer
from data_agent.im.platform import Button, Incoming, Outgoing, Press

BOT = 900000000000000001
ALICE = 100000000000000001
CHANNEL = 200000000000000001
GUILD = 300000000000000001
THREAD = 400000000000000001
DM = 500000000000000001
# Made up, in no real token's shape.
TOKEN = "dc-" + "Zr5m" + "-made-up-bot"
CONFIG = PlatformConfig(
    "discord",
    frozenset({str(ALICE)}),
    {"bot_token_env": "DATA_AGENT_DISCORD_BOT_TOKEN"},
    {"bot_token_env": TOKEN},
)

TEXT = discord.ChannelType.text
PRIVATE = discord.ChannelType.private
PUBLIC_THREAD = discord.ChannelType.public_thread


def not_found() -> discord.NotFound:
    return discord.NotFound(cast(Any, SimpleNamespace(status=404, reason="Not Found")), "Unknown")


def forbidden() -> discord.Forbidden:
    return discord.Forbidden(cast(Any, SimpleNamespace(status=403, reason="Forbidden")), "Missing")


class Sent(SimpleNamespace):
    id: int


class FakeMessage:
    """A message where it is: `create_thread` and `edit` recorded."""

    def __init__(self, channel: "FakeChannel", message_id: int) -> None:
        self.channel = channel
        self.id = message_id

    async def edit(self, **kwargs: Any) -> None:
        self.channel.calls.append(("edit", {"message_id": self.id, **kwargs}))


class FakeChannel:
    def __init__(self, client: "FakeClient", channel_id: int) -> None:
        self.client = client
        self.id = channel_id
        self.calls = client.calls

    async def send(self, **kwargs: Any) -> Sent:
        if self.client.rate_limited:
            self.client.rate_limited -= 1
            raise discord.RateLimited(45.0)
        self.calls.append(("send", {"channel": self.id, **kwargs}))
        return Sent(id=700000000000000000 + len(self.calls))

    def get_partial_message(self, message_id: int) -> FakeMessage:
        return FakeMessage(self, message_id)

    async def fetch_message(self, message_id: int) -> Any:
        return source(message_id, f"<@{BOT}> fetched question", self.client.calls)


class FakeClient:
    """The calls of `discord.Client` the platform makes, recorded."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.user = SimpleNamespace(id=BOT)
        # Channels `fetch_channel` finds.
        self.existing: set[int] = {CHANNEL}
        self.rate_limited = 0

    def get_partial_messageable(self, channel_id: int) -> FakeChannel:
        return FakeChannel(self, channel_id)

    async def fetch_channel(self, channel_id: int) -> FakeChannel:
        self.calls.append(("fetch_channel", {"channel": channel_id}))
        if channel_id not in self.existing:
            raise not_found()
        return FakeChannel(self, channel_id)


def platform(client: FakeClient | None = None) -> DiscordPlatform:
    return DiscordPlatform(CONFIG, cast(discord.Client, client or FakeClient()))


def channel(kind: discord.ChannelType = TEXT, channel_id: int = CHANNEL) -> SimpleNamespace:
    parent = CHANNEL if kind == PUBLIC_THREAD else None
    return SimpleNamespace(type=kind, id=channel_id, parent_id=parent)


def message(
    content: str,
    *,
    where: SimpleNamespace | None = None,
    message_id: int = 600000000000000001,
    author: int = ALICE,
    bot: bool = False,
    mentions: tuple[int, ...] = (),
    kind: discord.MessageType = discord.MessageType.default,
    calls: list[tuple[str, dict[str, Any]]] | None = None,
) -> Any:
    async def create_thread(**kwargs: Any) -> None:
        if calls is not None:
            calls.append(("create_thread", {"message_id": message_id, **kwargs}))

    return SimpleNamespace(
        id=message_id,
        type=kind,
        content=content,
        author=SimpleNamespace(id=author, bot=bot),
        channel=where or channel(),
        mentions=[SimpleNamespace(id=user) for user in mentions],
        create_thread=create_thread,
    )


def source(message_id: int, content: str, calls: list[tuple[str, dict[str, Any]]]) -> Any:
    return message(content, message_id=message_id, mentions=(BOT,), calls=calls)


async def handed(messages: list[Any], into: DiscordPlatform | None = None) -> list[Incoming]:
    bot = into or platform()
    got: list[Incoming] = []

    async def on_message(incoming: Incoming) -> None:
        got.append(incoming)

    bot.on_message = on_message
    for item in messages:
        await bot.handle_message(item)
    return got


# --- messages -> the interface ---------------------------------------------------------------


async def test_a_mention_in_a_channel_is_keyed_by_the_message_its_thread_will_have() -> None:
    got = await handed([message(f"<@{BOT}> how many orders? <@!{BOT}>", mentions=(BOT,))])
    assert got == [
        Incoming(
            platform="discord",
            chat_id=str(CHANNEL),
            thread_id="600000000000000001",
            user_id=str(ALICE),
            direct=False,
            mentioned=True,
            text="how many orders?",
        )
    ]


async def test_a_reply_in_a_thread_is_keyed_by_its_parent_and_the_thread() -> None:
    got = await handed([message("and by month?", where=channel(PUBLIC_THREAD, THREAD))])
    assert len(got) == 1
    assert (got[0].chat_id, got[0].thread_id, got[0].direct) == (str(CHANNEL), str(THREAD), False)
    assert not got[0].mentioned


async def test_a_direct_message_has_no_thread() -> None:
    got = await handed([message("hello", where=channel(PRIVATE, DM))])
    assert len(got) == 1
    assert (got[0].chat_id, got[0].thread_id, got[0].direct) == (str(DM), None, True)


async def test_everyone_is_no_mention_of_the_bot() -> None:
    # Discord leaves `mentions` empty for `@everyone`; `mention_everyone` is set instead.
    got = await handed([message("@everyone lunch?")])
    assert not got[0].mentioned
    assert got[0].text == "@everyone lunch?"


async def test_a_reply_to_the_bot_mentions_it() -> None:
    # Discord puts the replied-to author in `mentions` when the reply pings it.
    got = await handed([message("why?", mentions=(BOT,), kind=discord.MessageType.reply)])
    assert got[0].mentioned
    assert got[0].text == "why?"


async def test_bots_and_system_messages_are_not_handed_on() -> None:
    got = await handed(
        [
            message("from the bot", author=BOT, bot=True, mentions=(BOT,)),
            message("another bot", author=12345, bot=True, mentions=(BOT,)),
            message("", kind=discord.MessageType.pins_add, message_id=2),
            message("", kind=discord.MessageType.thread_starter_message, message_id=3),
            message("/new", kind=discord.MessageType.chat_input_command, message_id=4),
        ]
    )
    assert got == []


async def test_the_same_message_is_handed_on_once() -> None:
    again = message(f"<@{BOT}> hi", mentions=(BOT,))
    assert len(await handed([again, again])) == 1


async def test_every_id_is_a_string() -> None:
    got = await handed([message("x", where=channel(PUBLIC_THREAD, THREAD))])
    assert all(isinstance(value, str) for value in (got[0].chat_id, got[0].thread_id))
    assert isinstance(got[0].user_id, str)


async def test_messages_and_presses_are_handed_on_one_at_a_time() -> None:
    bot = platform()
    order: list[str] = []

    async def on_message(incoming: Incoming) -> None:
        order.append(f"start {incoming.text}")
        await asyncio.sleep(0.01)
        order.append(f"end {incoming.text}")

    async def on_press(press: Press) -> None:
        order.append(f"press {press.value}")

    bot.on_message, bot.on_press = on_message, on_press
    await asyncio.gather(
        bot.handle_message(message("first", message_id=1)),
        bot.handle_interaction(press_interaction(CUSTOM_ID + "stop")),
        bot.handle_message(message("second", message_id=2)),
    )
    assert order == ["start first", "end first", "press stop", "start second", "end second"]


# --- send and edit ---------------------------------------------------------------------------


async def test_a_send_into_a_thread_not_there_yet_makes_it_from_the_message() -> None:
    client = FakeClient()
    bot = platform(client)
    long = "word " * 30
    await handed(
        [message(f"<@{BOT}> {long}", mentions=(BOT,), message_id=THREAD, calls=client.calls)],
        bot,
    )
    sent = await bot.send(str(CHANNEL), str(THREAD), Outgoing("Working..."))
    made = [call for kind, call in client.calls if kind == "create_thread"]
    assert len(made) == 1
    assert made[0]["auto_archive_duration"] == THREAD_ARCHIVE_MINUTES == 1440
    assert made[0]["name"] == " ".join(long.split())[:77] + "..."
    assert len(made[0]["name"]) == 80
    assert f"<@{BOT}>" not in made[0]["name"]
    sends = [call for kind, call in client.calls if kind == "send"]
    assert [call["channel"] for call in sends] == [THREAD]
    # Again: the thread is there, nothing made twice.
    await bot.send(str(CHANNEL), str(THREAD), Outgoing("Done"))
    assert sum(kind == "create_thread" for kind, _ in client.calls) == 1
    # An edit goes to the thread the message is in, not to `chat_id`.
    await bot.edit(str(CHANNEL), sent, Outgoing("Still working"))
    edits = [call for kind, call in client.calls if kind == "edit"]
    assert edits[0]["message_id"] == int(sent)
    assert "fetch_channel" not in [kind for kind, _ in client.calls]


def test_thread_names() -> None:
    assert thread_name("short question") == "short question"
    assert thread_name("a" * 80) == "a" * 80
    assert thread_name("a" * 81) == "a" * 77 + "..."
    assert thread_name("  \n ") == THREAD_NAME
    assert thread_name("two\nlines") == "two lines"


async def test_a_thread_already_there_is_not_made_again() -> None:
    """A key from before a restart: the thread is found, and the message goes into it."""
    client = FakeClient()
    client.existing.add(THREAD)
    bot = platform(client)
    await bot.send(str(CHANNEL), str(THREAD), Outgoing("hello"))
    assert "create_thread" not in [kind for kind, _ in client.calls]
    assert [call["channel"] for kind, call in client.calls if kind == "send"] == [THREAD]


async def test_a_message_from_before_a_restart_gets_its_thread_made() -> None:
    client = FakeClient()
    bot = platform(client)
    await bot.send(str(CHANNEL), str(THREAD), Outgoing("hello"))
    made = [call for kind, call in client.calls if kind == "create_thread"]
    assert made == [
        {"message_id": THREAD, "name": "fetched question", "auto_archive_duration": 1440}
    ]


async def test_a_message_in_a_thread_needs_no_new_thread() -> None:
    client = FakeClient()
    bot = platform(client)
    await handed([message("more", where=channel(PUBLIC_THREAD, THREAD))], bot)
    await bot.send(str(CHANNEL), str(THREAD), Outgoing("ok"))
    assert [kind for kind, _ in client.calls] == ["send"]


async def test_no_thread_sends_into_the_channel_or_direct_message() -> None:
    client = FakeClient()
    bot = platform(client)
    await bot.send(str(DM), None, Outgoing("hi"))
    await bot.send(str(CHANNEL), None, Outgoing("hint"))
    assert [kind for kind, _ in client.calls] == ["send", "send"]
    assert [call["channel"] for _, call in client.calls] == [DM, CHANNEL]
    assert all("reference" not in call for _, call in client.calls)


async def test_a_thread_that_cannot_be_made_is_a_reply_in_the_channel(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeClient()
    bot = platform(client)
    failing = message(f"<@{BOT}> q", mentions=(BOT,), message_id=THREAD)

    async def create_thread(**_: Any) -> None:
        raise forbidden()

    failing.create_thread = create_thread
    await handed([failing], bot)
    with caplog.at_level(logging.WARNING):
        first = await bot.send(str(CHANNEL), str(THREAD), Outgoing("one", (Button("Stop", "s"),)))
        await bot.send(str(CHANNEL), str(THREAD), Outgoing("two"))
    assert "no thread could be made" in caplog.text
    sends = [call for kind, call in client.calls if kind == "send"]
    assert [call["channel"] for call in sends] == [CHANNEL, CHANNEL]
    assert all(call["reference"].message_id == THREAD for call in sends)
    # A press of the reply's buttons has the conversation's key.
    pressed: list[Press] = []

    async def on_press(press: Press) -> None:
        pressed.append(press)

    bot.on_press = on_press
    await bot.handle_interaction(
        press_interaction(CUSTOM_ID + "s", where=channel(), message_id=int(first))
    )
    assert pressed[0].thread_id == str(THREAD)


async def test_messages_mention_nobody_and_carry_their_buttons_in_rows() -> None:
    client = FakeClient()
    bot = platform(client)
    buttons = tuple(Button(str(number), f"answer:{number}") for number in range(1, 8))
    await bot.send(str(DM), None, Outgoing("@everyone pick one", buttons))
    call = client.calls[0][1]
    assert call["content"] == "@everyone pick one"
    mentions: discord.AllowedMentions = call["allowed_mentions"]
    assert mentions.to_dict() == discord.AllowedMentions.none().to_dict()
    rows = call["view"].to_components()
    assert [len(row["components"]) for row in rows] == [5, 2]
    ids = [button["custom_id"] for row in rows for button in row["components"]]
    assert ids == [f"{CUSTOM_ID}answer:{number}" for number in range(1, 8)]
    assert all(len(custom_id) <= 100 for custom_id in ids)
    # Not stored for dispatch: `on_interaction` handles the presses.
    assert not call["view"].is_dispatchable()


async def test_an_edit_replaces_text_and_buttons_and_one_without_takes_them_off() -> None:
    client = FakeClient()
    bot = platform(client)
    await bot.edit(str(DM), "800", Outgoing("status", (Button("Stop", "stop"),)))
    await bot.edit(str(DM), "800", Outgoing("done"))
    first, second = (call for _, call in client.calls)
    assert first["content"] == "status"
    assert len(first["view"].children) == 1
    assert second["content"] == "done"
    assert second["view"] is None
    assert "allowed_mentions" in second


async def test_a_rate_limited_send_waits_and_is_tried_once_more() -> None:
    client = FakeClient()
    client.rate_limited = 1
    bot = platform(client)
    waited: list[float] = []

    async def sleep(seconds: float) -> None:
        waited.append(seconds)

    bot.sleep = sleep
    await bot.send(str(DM), None, Outgoing("hi"))
    assert waited == [30.0]
    assert len(client.calls) == 1
    client.rate_limited = 2
    with pytest.raises(discord.RateLimited):
        await bot.send(str(DM), None, Outgoing("hi"))


async def test_a_rate_limited_edit_raises_for_the_adapter_to_skip() -> None:
    bot = platform()

    class Limited(FakeMessage):
        async def edit(self, **kwargs: Any) -> None:
            raise discord.RateLimited(45.0)

    class LimitedChannel(FakeChannel):
        def get_partial_message(self, message_id: int) -> FakeMessage:
            return Limited(self, message_id)

    client = cast(FakeClient, bot.client)
    client.get_partial_messageable = lambda channel_id: LimitedChannel(client, channel_id)
    with pytest.raises(discord.RateLimited):
        await bot.edit(str(DM), "800", Outgoing("x"))


def test_the_limits() -> None:
    assert LIMITS.edit_interval == 2.0
    assert LIMITS.max_chars <= MESSAGE_CHARS
    answer = "\n\n".join(["Intro " * 50, "```sql\n" + "select 1;\n" * 400 + "```", "x" * 5000])
    parts = format_answer(answer, [], LIMITS.max_chars)
    assert len(parts) > 3
    assert all(len(part) <= MESSAGE_CHARS for part in parts)


# --- interactions ----------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self.calls = calls

    async def defer(self, **kwargs: Any) -> None:
        self.calls.append(("defer", kwargs))


def press_interaction(
    custom_id: str,
    *,
    where: SimpleNamespace | None = None,
    message_id: int = 800000000000000001,
    calls: list[tuple[str, dict[str, Any]]] | None = None,
) -> Any:
    log = calls if calls is not None else []
    return SimpleNamespace(
        type=discord.InteractionType.component,
        data={"custom_id": custom_id, "component_type": 2},
        channel=where or channel(PUBLIC_THREAD, THREAD),
        message=SimpleNamespace(id=message_id),
        user=SimpleNamespace(id=ALICE),
        response=FakeResponse(log),
    )


async def test_a_press_is_deferred_then_handed_on() -> None:
    bot = platform()
    calls: list[tuple[str, dict[str, Any]]] = []

    async def on_press(press: Press) -> None:
        calls.append(("press", {"press": press}))

    bot.on_press = on_press
    await bot.handle_interaction(press_interaction(CUSTOM_ID + "answer:2", calls=calls))
    assert [kind for kind, _ in calls] == ["defer", "press"]
    assert calls[1][1]["press"] == Press(
        platform="discord",
        chat_id=str(CHANNEL),
        thread_id=str(THREAD),
        user_id=str(ALICE),
        direct=False,
        message_id="800000000000000001",
        value="answer:2",
    )
    calls.clear()
    await bot.handle_interaction(press_interaction(CUSTOM_ID + "stop", where=channel(PRIVATE, DM)))
    dm: Press = calls[0][1]["press"]
    assert (dm.chat_id, dm.thread_id, dm.direct) == (str(DM), None, True)


async def test_another_bots_button_is_left_alone() -> None:
    bot = platform()
    calls: list[tuple[str, dict[str, Any]]] = []

    async def on_press(press: Press) -> None:
        calls.append(("press", {}))

    bot.on_press = on_press
    await bot.handle_interaction(press_interaction("someone_else:1", calls=calls))
    command = press_interaction(CUSTOM_ID + "x", calls=calls)
    command.type = discord.InteractionType.application_command
    await bot.handle_interaction(command)
    assert calls == []


def new_interaction(where: SimpleNamespace, calls: list[tuple[str, dict[str, Any]]]) -> Any:
    async def delete_original_response() -> None:
        calls.append(("delete", {}))

    return SimpleNamespace(
        type=discord.InteractionType.application_command,
        channel=where,
        user=SimpleNamespace(id=ALICE),
        response=FakeResponse(calls),
        delete_original_response=delete_original_response,
    )


@pytest.mark.parametrize(
    ("where", "key"),
    [
        (channel(PRIVATE, DM), (str(DM), None, True)),
        (channel(PUBLIC_THREAD, THREAD), (str(CHANNEL), str(THREAD), False)),
        (channel(), (str(CHANNEL), None, False)),
    ],
)
async def test_new_is_answered_first_then_a_new_conversation(
    where: SimpleNamespace, key: tuple[str, str | None, bool]
) -> None:
    bot = platform()
    calls: list[tuple[str, dict[str, Any]]] = []

    async def on_message(incoming: Incoming) -> None:
        calls.append(("message", {"incoming": incoming}))

    bot.on_message = on_message
    await bot.handle_new(cast(discord.Interaction, new_interaction(where, calls)))
    assert [kind for kind, _ in calls] == ["defer", "message", "delete"]
    # Only its user sees the acknowledgement, and it is gone after: the adapter's reply is the
    # one visible answer.
    assert calls[0][1] == {"ephemeral": True, "thinking": True}
    incoming: Incoming = calls[1][1]["incoming"]
    assert (incoming.chat_id, incoming.thread_id, incoming.direct) == key
    assert incoming.new_conversation
    assert incoming.mentioned
    assert incoming.text == ""
    assert incoming.user_id == str(ALICE)


async def test_the_command_is_synced_only_when_it_changed() -> None:
    calls: list[str] = []

    class Tree:
        def __init__(self, remote: list[tuple[str, str]]) -> None:
            self.remote = remote

        def get_commands(self) -> list[Any]:
            return [SimpleNamespace(name="new", description="Start")]

        async def fetch_commands(self) -> list[Any]:
            return [SimpleNamespace(name=name, description=text) for name, text in self.remote]

        async def sync(self) -> None:
            calls.append("sync")

    await sync_commands(cast(Any, Tree([("new", "Start")])))
    assert calls == []
    await sync_commands(cast(Any, Tree([])))
    await sync_commands(cast(Any, Tree([("new", "Older text")])))
    assert calls == ["sync", "sync"]


# --- start and stop --------------------------------------------------------------------------


async def no_message(_: Incoming) -> None:
    pass


async def no_press(_: Press) -> None:
    pass


def test_the_client_has_the_message_content_intent() -> None:
    bot = DiscordPlatform(CONFIG)
    assert bot.client.intents.message_content
    assert bot.client.intents.guild_messages
    assert bot.client.intents.dm_messages
    assert TOKEN not in repr(bot)


async def test_stop_before_connecting_does_not_raise() -> None:
    await DiscordPlatform(CONFIG).stop()


class Started(discord.Client):
    """A real client whose gateway is stood in for; `run` would set up logging."""

    def __init__(self, ready: bool) -> None:
        super().__init__(intents=discord.Intents.default())
        self.ready = ready
        self.logged_in_with = ""

    def run(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("Client.run is never called")

    async def login(self, token: str) -> None:
        self.logged_in_with = token

    async def connect(self, *, reconnect: bool = True) -> None:
        if not self.ready:
            raise discord.PrivilegedIntentsRequired(None)
        await asyncio.Event().wait()

    async def wait_until_ready(self) -> None:
        if not self.ready:
            await asyncio.Event().wait()


async def test_start_returns_once_ready_and_syncs_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synced: list[Any] = []

    async def sync(tree: Any) -> None:
        synced.append(tree)

    monkeypatch.setattr("data_agent.im.discord_bot.sync_commands", sync)
    client = Started(ready=True)
    bot = DiscordPlatform(CONFIG, client)
    await asyncio.wait_for(bot.start(no_message, no_press), timeout=2)
    assert client.logged_in_with == TOKEN
    assert [command.name for command in synced[0].get_commands()] == ["new"]
    assert bot.connection is not None and not bot.connection.done()
    await bot.stop()
    assert bot.connection is None


async def test_start_raises_when_the_gateway_ends_before_ready() -> None:
    bot = DiscordPlatform(CONFIG, Started(ready=False))
    with pytest.raises(discord.PrivilegedIntentsRequired):
        await asyncio.wait_for(bot.start(no_message, no_press), timeout=2)
    await bot.stop()


async def serve(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = cast(Any, site)._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/api/v10"


async def test_a_wrong_token_raises_and_no_token_is_in_the_debug_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """discord.py logs its requests at DEBUG (method, URL, body), aiohttp its own; the token
    goes only in the `Authorization` header. The first server refuses the token at login; the
    second takes it and refuses the next request, past `users/@me`."""
    caplog.set_level(logging.DEBUG)
    headers: list[str] = []

    async def refuse(request: web.Request) -> web.Response:
        headers.append(request.headers.get("Authorization", ""))
        return web.json_response({"message": "401: Unauthorized", "code": 0}, status=401)

    async def me(request: web.Request) -> web.Response:
        headers.append(request.headers.get("Authorization", ""))
        # discord.py reads a body as JSON only for exactly `application/json`.
        body = json.dumps({"id": str(BOT), "username": "bot", "discriminator": "0", "avatar": None})
        return web.Response(body=body.encode(), headers={"Content-Type": "application/json"})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", refuse)
    runner, base = await serve(app)
    monkeypatch.setattr(Route, "BASE", base)
    try:
        bot = DiscordPlatform(CONFIG)
        with pytest.raises(discord.LoginFailure) as failed:
            await bot.start(no_message, no_press)
        await bot.stop()
    finally:
        await runner.cleanup()

    app = web.Application()
    app.router.add_get("/api/v10/users/@me", me)
    app.router.add_route("*", "/{tail:.*}", refuse)
    runner, base = await serve(app)
    monkeypatch.setattr(Route, "BASE", base)
    try:
        second = DiscordPlatform(CONFIG)
        with pytest.raises(discord.HTTPException) as refused:
            await second.start(no_message, no_press)
        await second.stop()
    finally:
        await runner.cleanup()

    assert headers[0] == f"Bot {TOKEN}"
    assert len(headers) >= 3
    assert "users/@me" in caplog.text
    assert "applications/@me" in caplog.text
    for text in [caplog.text, str(failed.value), str(refused.value), repr(bot)]:
        assert TOKEN not in text


# --- service ---------------------------------------------------------------------------------


def test_service_makes_the_discord_platform() -> None:
    assert isinstance(service.make_platform(CONFIG), DiscordPlatform)


def test_without_discord_py_service_says_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An entry of None makes the import fail, as when the package is not installed.
    monkeypatch.setitem(sys.modules, "discord", None)
    with pytest.raises(service.ImUnavailable) as refused:
        service.start(FastAPI(), {"discord": CONFIG})
    assert "uv sync --extra im" in str(refused.value)
    assert "discord" in str(refused.value)
