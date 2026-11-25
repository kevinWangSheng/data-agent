"""The Telegram platform (data_agent.im.telegram): Telegram's updates <-> the platform interface,
the MarkdownV2 conversion, the limits, start and stop, and no token in the logs. No Telegram: a
bot that records its calls, or a real bot pointed at a closed port on the loopback address."""

import asyncio
import json
import logging
import re
import socket
import sys
import traceback
from pathlib import Path
from typing import Any, cast

import pytest
import uvicorn
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter, TelegramUnauthorizedError
from aiogram.methods import EditMessageText, GetMe, SendMessage
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, Update, User

from data_agent import cli
from data_agent.im import service
from data_agent.im.config import PlatformConfig
from data_agent.im.platform import Button, Incoming, Outgoing, Press
from data_agent.im.telegram import (
    LIMITS,
    MAX_TEXT,
    TelegramPlatform,
    to_incoming,
    to_markdown_v2,
    to_press,
)

UPDATES = json.loads(
    (Path(__file__).parent / "fixtures" / "im" / "telegram_updates.json").read_text(
        encoding="utf-8"
    )
)
BOT_ID = 777000111
BOT_NAME = "data_agent_test_bot"
# Made up, in the shape aiogram checks (`<bot id>:<secret>`) but no real token's.
TOKEN = str(BOT_ID) + ":" + "made-up-" + "telegram-token"
CONFIG = PlatformConfig(
    "telegram",
    frozenset({"10001"}),
    {"bot_token_env": "DATA_AGENT_TELEGRAM_BOT_TOKEN"},
    {"bot_token_env": TOKEN},
)


def message(name: str) -> Message:
    return Message.model_validate(UPDATES[name])


def incoming(name: str) -> Incoming | None:
    return to_incoming(message(name), BOT_ID, BOT_NAME)


def query(name: str) -> CallbackQuery:
    return CallbackQuery.model_validate(UPDATES[name])


# --- Telegram's updates -> the interface ---------------------------------------------------


def test_a_private_message_is_direct() -> None:
    assert incoming("private") == Incoming(
        platform="telegram",
        chat_id="10001",
        thread_id=None,
        user_id="10001",
        direct=True,
        mentioned=False,
        text="How many orders?",
    )


def test_a_mention_in_a_group_is_mentioned_and_taken_out() -> None:
    # The username's case differs from the bot's: a mention is not case-sensitive.
    assert incoming("group_mention") == Incoming(
        platform="telegram",
        chat_id="-1009001",
        thread_id=None,
        user_id="10001",
        direct=False,
        mentioned=True,
        text="how many orders?",
    )


def test_a_mention_after_an_emoji_is_found_by_its_utf16_offset() -> None:
    got = incoming("group_mention_after_emoji")
    assert got is not None
    assert got.mentioned
    assert got.text == "📊 revenue?"


def test_a_text_mention_of_the_bot_is_mentioned_and_taken_out() -> None:
    got = incoming("group_text_mention")
    assert got is not None
    assert got.mentioned
    assert not got.direct
    assert got.text == "revenue by month?"


def test_stop_after_a_text_mention_is_the_bare_stop() -> None:
    """The adapter stops the turn only on a text that is `stop` once the mention is out."""
    update = json.loads(json.dumps(UPDATES["group_text_mention"]))
    update["text"] = "Data Agent stop"
    got = to_incoming(Message.model_validate(update), BOT_ID, BOT_NAME)
    assert got is not None
    assert got.text == "stop"


def test_a_mention_of_someone_else_is_not_the_bots() -> None:
    got = incoming("group_other_mention")
    assert got is not None
    assert not got.mentioned
    assert got.text == "@someone_else lunch?"


def test_a_plain_group_message_is_handed_on_unmentioned() -> None:
    got = incoming("group_plain")
    assert got is not None
    assert not got.mentioned
    assert not got.direct
    assert got.text == "Lunch at noon?"


def test_a_reply_to_the_bot_counts_as_a_mention() -> None:
    got = incoming("group_reply_to_bot")
    assert got is not None
    assert got.mentioned
    assert got.text == "Only paid ones"


def test_a_reply_to_a_forum_topics_creation_is_no_mention() -> None:
    """A message in a forum topic replies to the topic's creation, by whoever made it."""
    got = incoming("group_reply_to_topic_created")
    assert got is not None
    assert not got.mentioned
    # Forum topics are not supported: the message is an ordinary group message.
    assert got.thread_id is None


@pytest.mark.parametrize("name", ["other_bot", "member_joined", "channel_post"])
def test_bots_service_messages_and_channel_posts_are_no_message(name: str) -> None:
    assert incoming(name) is None


@pytest.mark.parametrize(
    ("name", "text", "mentioned"),
    [
        ("new_bare", "", False),
        ("new_with_question", "revenue by month?", False),
        ("new_at_bot", "top customers", True),
    ],
)
def test_new_starts_a_conversation_with_the_rest_as_text(
    name: str, text: str, mentioned: bool
) -> None:
    got = incoming(name)
    assert got is not None
    assert got.new_conversation
    assert got.text == text
    assert got.mentioned == mentioned


def test_new_for_another_bot_is_no_new() -> None:
    got = incoming("new_at_other_bot")
    assert got is not None
    assert not got.new_conversation
    assert not got.mentioned
    assert got.text == "/new@other_bot top customers"


def test_a_button_press_in_a_group() -> None:
    assert to_press(query("stop_press")) == Press(
        platform="telegram",
        chat_id="-1009001",
        thread_id=None,
        user_id="10001",
        direct=False,
        message_id="31",
        value="stop",
    )


def test_a_button_press_in_a_private_chat_is_direct() -> None:
    press = to_press(query("private_press"))
    assert press is not None
    assert press.direct
    assert press.value == "answer:1"


def test_a_press_without_a_message_is_no_press() -> None:
    assert to_press(query("inline_press")) is None


# --- Markdown -> MarkdownV2 ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("markdown", "v2"),
    [
        ("**Revenue** grew", "*Revenue* grew"),
        # `__` is underline in MarkdownV2; Markdown's `__x__` is bold.
        ("__Revenue__ grew", "*Revenue* grew"),
        ("an *aside* here", "an _aside_ here"),
        ("~~old~~ new", "~old~ new"),
        (
            "see [the docs (v2)](https://example.com/a_(b)?c=1)",
            r"see [the docs \(v2\)](https://example.com/a_(b\)?c=1)",
        ),
        ("## Totals\nby month", "*Totals*\nby month"),
        # Bold in a heading, either way, would close the heading's own bold.
        ("# Revenue __2025__ **net**", "*Revenue 2025 net*"),
        ("- one\n* two", "• one\n• two"),
        ("use `a_b\\c` here", "use `a_b\\\\c` here"),
        (
            "```sql\nSELECT `x` FROM t WHERE a > 1.5\n```",
            "```sql\nSELECT \\`x\\` FROM t WHERE a > 1.5\n```",
        ),
        (
            "1. Revenue is $1,234.50 (up 3%) - see #2 > a=b | c {d} !e [f] _g_ \\",
            r"1\. Revenue is $1,234\.50 \(up 3%\) \- see \#2 \> a\=b \| c \{d\} \!e \[f\] \_g\_ \\",
        ),
        # Bold holds no code entity (core.telegram.org/bots/api#formatting-options).
        ("**total `n` rows**", r"*total \`n\` rows*"),
        ("**bold with *italic* inside**", "*bold with _italic_ inside*"),
    ],
    ids=[
        "bold",
        "bold-underscores",
        "italic",
        "strike",
        "link",
        "heading",
        "bold-in-heading",
        "bullets",
        "inline-code",
        "code-block",
        "escapes",
        "code-in-bold",
        "nested",
    ],
)
def test_markdown_to_markdown_v2(markdown: str, v2: str) -> None:
    assert to_markdown_v2(markdown) == v2


def test_an_unclosed_code_block_is_closed() -> None:
    assert to_markdown_v2("```\nSELECT 1") == "```\nSELECT 1\n```"


# --- the interface -> Telegram's calls -----------------------------------------------------


class FakeSession:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class FakeBot:
    """The calls of aiogram's `Bot` the platform makes, recorded; `fail` raises for a method
    the first time it is called."""

    def __init__(self, fail: dict[str, Exception] | None = None) -> None:
        self.id = BOT_ID
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = dict(fail or {})
        self.session = FakeSession()

    def record(self, method: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((method, kwargs))
        if method in self.fail:
            raise self.fail.pop(method)

    async def me(self) -> User:
        self.record("getMe", {})
        return User(id=BOT_ID, is_bot=True, first_name="Data Agent", username=BOT_NAME)

    async def set_my_commands(self, **kwargs: Any) -> bool:
        self.record("setMyCommands", kwargs)
        return True

    async def send_message(self, **kwargs: Any) -> Message:
        self.record("sendMessage", kwargs)
        return Message.model_validate(
            {
                "message_id": 40 + len(self.calls),
                "date": 1764000000,
                "chat": {"id": kwargs["chat_id"], "type": "private"},
                "text": kwargs["text"],
            }
        )

    async def edit_message_text(self, **kwargs: Any) -> bool:
        self.record("editMessageText", kwargs)
        return True

    async def answer_callback_query(self, **kwargs: Any) -> bool:
        self.record("answerCallbackQuery", kwargs)
        return True


def platform(bot: FakeBot | None = None) -> TelegramPlatform:
    return TelegramPlatform(CONFIG, cast(Bot, bot or FakeBot()))


def bad_request(text: str) -> TelegramBadRequest:
    return TelegramBadRequest(SendMessage(chat_id=1, text="x"), f"Bad Request: {text}")


async def test_send_converts_to_markdown_v2_with_a_button_each() -> None:
    bot = FakeBot()
    telegram = platform(bot)
    buttons = (Button("1", "answer:1"), Button("2", "answer:2"))
    message_id = await telegram.send("-1009001", None, Outgoing("Net or **gross**?", buttons))
    [(method, kwargs)] = bot.calls
    assert method == "sendMessage"
    assert message_id == "41"
    assert kwargs["chat_id"] == -1009001
    assert kwargs["text"] == "Net or *gross*?"
    assert kwargs["parse_mode"] == "MarkdownV2"
    # Forum topics are not supported: nothing goes into a topic.
    assert "message_thread_id" not in kwargs
    markup = kwargs["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    assert [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard] == [
        [("1", "answer:1")],
        [("2", "answer:2")],
    ]


async def test_send_without_buttons_has_no_keyboard() -> None:
    bot = FakeBot()
    await platform(bot).send("10001", None, Outgoing("The answer."))
    assert bot.calls[0][1]["reply_markup"] is None


async def test_an_edit_without_buttons_takes_the_keyboard_off() -> None:
    bot = FakeBot()
    await platform(bot).edit("-1009001", "31", Outgoing("Done · 37s · $0.25"))
    [(method, kwargs)] = bot.calls
    assert method == "editMessageText"
    assert kwargs["chat_id"] == -1009001
    assert kwargs["message_id"] == 31
    assert kwargs["text"] == r"Done · 37s · $0\.25"
    assert kwargs["parse_mode"] == "MarkdownV2"
    assert kwargs["reply_markup"] == InlineKeyboardMarkup(inline_keyboard=[])


async def test_an_edit_with_a_button_keeps_it() -> None:
    bot = FakeBot()
    await platform(bot).edit("-1009001", "31", Outgoing("Thinking…", (Button("Stop", "stop"),)))
    markup = bot.calls[0][1]["reply_markup"]
    assert [[b.callback_data for b in row] for row in markup.inline_keyboard] == [["stop"]]


async def test_text_telegram_cannot_parse_is_sent_again_as_plain_text() -> None:
    bot = FakeBot({"sendMessage": bad_request("can't parse entities: Unclosed entity")})
    message_id = await platform(bot).send("10001", None, Outgoing("a **b**"))
    assert [method for method, _ in bot.calls] == ["sendMessage", "sendMessage"]
    retry = bot.calls[1][1]
    assert retry["text"] == "a **b**"
    assert retry["parse_mode"] is None
    assert message_id == "42"


async def test_an_edit_telegram_cannot_parse_is_made_again_as_plain_text() -> None:
    bot = FakeBot({"editMessageText": bad_request("can't parse entities: Unclosed entity")})
    await platform(bot).edit("10001", "31", Outgoing("a **b**"))
    assert [kwargs["parse_mode"] for _, kwargs in bot.calls] == ["MarkdownV2", None]
    assert bot.calls[1][1]["text"] == "a **b**"


async def test_an_edit_that_changes_nothing_is_no_error() -> None:
    bot = FakeBot(
        {
            "editMessageText": bad_request(
                "message is not modified: specified new message content and reply markup are "
                "exactly the same as a current content and reply markup of the message"
            )
        }
    )
    await platform(bot).edit("10001", "31", Outgoing("Thinking…"))
    assert len(bot.calls) == 1


async def test_another_bad_request_raises() -> None:
    bot = FakeBot({"sendMessage": bad_request("chat not found")})
    with pytest.raises(TelegramBadRequest):
        await platform(bot).send("10001", None, Outgoing("x"))


def flood(method: Any, seconds: int) -> TelegramRetryAfter:
    return TelegramRetryAfter(method, f"Too Many Requests: retry after {seconds}", seconds)


async def test_a_rate_limited_edit_raises_for_the_adapter_to_skip() -> None:
    edit = EditMessageText(chat_id=1, message_id=31, text="x")
    bot = FakeBot({"editMessageText": flood(edit, 3)})
    with pytest.raises(TelegramRetryAfter):
        await platform(bot).edit("10001", "31", Outgoing("Thinking…"))
    assert len(bot.calls) == 1


@pytest.mark.parametrize(("retry_after", "waited"), [(3, 3.0), (3600, 30.0)])
async def test_a_rate_limited_send_waits_as_telegram_asks_and_is_tried_again(
    retry_after: int, waited: float
) -> None:
    bot = FakeBot({"sendMessage": flood(SendMessage(chat_id=1, text="x"), retry_after)})
    telegram = platform(bot)
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    telegram.sleep = sleep
    message_id = await telegram.send("10001", None, Outgoing("The answer."))
    assert waits == [waited]
    assert [method for method, _ in bot.calls] == ["sendMessage", "sendMessage"]
    assert message_id == "42"


def visible(v2: str) -> str:
    """MarkdownV2 text without its escapes: what Telegram counts for these test texts, which
    make no entity."""
    return re.sub(r"\\(.)", r"\1", v2, flags=re.DOTALL)


@pytest.mark.parametrize("char", [".", "_", "\\", "-", "!", "a"])
def test_a_message_at_the_limit_fits_after_parsing(char: str) -> None:
    text = char * LIMITS.max_chars
    assert len(visible(to_markdown_v2(text))) <= MAX_TEXT
    # The plain text sent when Telegram cannot parse it.
    assert len(text) <= MAX_TEXT


def test_the_limits() -> None:
    assert MAX_TEXT == 4096
    assert LIMITS.max_chars < MAX_TEXT
    assert LIMITS.edit_interval == 1.0
    assert TelegramPlatform.capabilities.edit
    assert TelegramPlatform.capabilities.buttons
    assert not TelegramPlatform.capabilities.typing


# --- handing on ----------------------------------------------------------------------------


def never_polls(telegram: TelegramPlatform, polled: list[dict[str, Any]] | None = None) -> None:
    """Polling replaced by one that records its arguments and never returns."""

    async def start_polling(*bots: Any, **kwargs: Any) -> None:
        if polled is not None:
            polled.append({"bots": bots, **kwargs})
        await asyncio.Event().wait()

    telegram.dispatcher.start_polling = start_polling  # type: ignore[method-assign]


async def connected(bot: FakeBot, on_message: Any, on_press: Any) -> TelegramPlatform:
    telegram = platform(bot)
    never_polls(telegram)
    await telegram.start(on_message, on_press)
    return telegram


async def test_a_press_is_answered_then_handed_on() -> None:
    bot = FakeBot()
    got: list[Press] = []

    async def on_press(press: Press) -> None:
        # Answered before the adapter sees it, so the client stops spinning at once.
        assert bot.calls[-1] == ("answerCallbackQuery", {"callback_query_id": "4382bfdwdsb323b2d9"})
        got.append(press)

    telegram = await connected(bot, no_message, on_press)
    await telegram.handle_query(query("stop_press"))
    await telegram.stop()
    assert [press.value for press in got] == ["stop"]


async def test_a_press_whose_answer_fails_is_still_handed_on() -> None:
    bot = FakeBot({"answerCallbackQuery": bad_request("query is too old")})
    got: list[Press] = []

    async def on_press(press: Press) -> None:
        got.append(press)

    telegram = await connected(bot, no_message, on_press)
    await telegram.handle_query(query("stop_press"))
    await telegram.stop()
    assert len(got) == 1


async def test_messages_and_presses_are_handed_on_one_at_a_time() -> None:
    running = 0
    overlapped = False
    order: list[str] = []

    async def handler(name: str) -> None:
        nonlocal running, overlapped
        running += 1
        overlapped = overlapped or running > 1
        await asyncio.sleep(0.01)
        order.append(name)
        running -= 1

    async def on_message(incoming: Incoming) -> None:
        await handler(incoming.text)

    async def on_press(press: Press) -> None:
        await handler(press.value)

    telegram = await connected(FakeBot(), on_message, on_press)
    await asyncio.gather(
        telegram.handle_message(message("private")),
        telegram.handle_query(query("stop_press")),
        telegram.handle_message(message("group_mention")),
    )
    await telegram.stop()
    assert not overlapped
    assert order == ["How many orders?", "stop", "how many orders?"]


async def test_a_message_not_to_the_bot_is_not_handed_on() -> None:
    got: list[Incoming] = []

    async def on_message(incoming: Incoming) -> None:
        got.append(incoming)

    telegram = await connected(FakeBot(), on_message, no_press)
    await telegram.handle_message(message("private"))
    await telegram.handle_message(message("other_bot"))
    await telegram.stop()
    assert len(got) == 1


# --- start and stop ------------------------------------------------------------------------


async def test_start_registers_new_and_polls_one_update_at_a_time() -> None:
    bot = FakeBot()
    telegram = platform(bot)
    polled: list[dict[str, Any]] = []
    never_polls(telegram, polled)
    await telegram.start(no_message, no_press)
    await asyncio.sleep(0)
    [kwargs] = polled
    assert kwargs["bots"] == (bot,)
    # uvicorn keeps its own SIGINT/SIGTERM handlers; updates are awaited one after another.
    assert kwargs["handle_signals"] is False
    assert kwargs["handle_as_tasks"] is False
    [(_, commands)] = [call for call in bot.calls if call[0] == "setMyCommands"]
    assert [(c.command, c.description) for c in commands["commands"]] == [
        ("new", "Start a new conversation")
    ]
    await telegram.stop()
    assert bot.session.closed == 1


async def test_start_raises_when_the_token_is_refused() -> None:
    bot = FakeBot({"getMe": TelegramUnauthorizedError(GetMe(), "Unauthorized")})
    telegram = platform(bot)
    never_polls(telegram)
    with pytest.raises(TelegramUnauthorizedError):
        await telegram.start(no_message, no_press)
    await telegram.stop()
    assert bot.session.closed == 1


async def test_stop_is_no_error_before_start_or_twice() -> None:
    bot = FakeBot()
    telegram = platform(bot)
    await telegram.stop()
    await TelegramPlatform(CONFIG).stop()
    telegram = await connected(bot, no_message, no_press)
    await telegram.stop()
    await telegram.stop()


async def test_a_malformed_token_fails_at_start_not_when_made() -> None:
    """`service.start` makes the platform before the app serves; a token aiogram refuses must
    not stop the web UI (data_agent.im.service.Bot logs the error `start` raises)."""
    secret = "not" + "-a-" + "token"
    bad = PlatformConfig("telegram", frozenset({"1"}), CONFIG.token_envs, {"bot_token_env": secret})
    telegram = TelegramPlatform(bad)
    with pytest.raises(Exception) as raised:
        await telegram.start(no_message, no_press)
    assert secret not in str(raised.value)
    await telegram.stop()


async def no_message(_: Incoming) -> None:
    pass


async def no_press(_: Press) -> None:
    pass


# --- the token -----------------------------------------------------------------------------


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_no_token_in_the_libraries_debug_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """aiogram puts the token in every request's URL (`/bot<token>/<method>`). A failed start,
    and polling that cannot reach Telegram, log and raise without it, at DEBUG too. The bot
    goes to a closed port on the loopback address, through no proxy."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    caplog.set_level(logging.DEBUG)
    server = TelegramAPIServer.from_base(f"http://127.0.0.1:{closed_port()}")
    bot = Bot(TOKEN, session=AiohttpSession(api=server))
    telegram = TelegramPlatform(CONFIG, bot)
    with pytest.raises(Exception) as failed:
        await telegram.start(no_message, no_press)
    errors = [str(failed.value), "".join(traceback.format_exception(failed.value))]

    # Past `getMe` and `setMyCommands`, to polling `getUpdates`.
    async def me() -> User:
        return User(id=BOT_ID, is_bot=True, first_name="Data Agent", username=BOT_NAME)

    async def set_my_commands(**_kwargs: Any) -> bool:
        return True

    bot.me = me  # type: ignore[method-assign]
    bot.set_my_commands = set_my_commands  # type: ignore[method-assign]
    await telegram.start(no_message, no_press)
    deadline = asyncio.get_running_loop().time() + 5
    while "Failed to fetch updates" not in caplog.text:
        assert asyncio.get_running_loop().time() < deadline, "polling never failed"
        await asyncio.sleep(0.05)
    await telegram.stop()
    secret = TOKEN.split(":", 1)[1]
    for text in [caplog.text, *errors, repr(telegram)]:
        assert secret not in text


async def test_a_polled_update_reaches_the_adapter_through_the_dispatcher() -> None:
    """The handlers as aiogram calls them: an update fed to the dispatcher, as polling does."""
    got: list[Incoming] = []

    async def on_message(incoming: Incoming) -> None:
        got.append(incoming)

    bot = FakeBot()
    telegram = await connected(bot, on_message, no_press)
    update = Update.model_validate({"update_id": 1, "message": UPDATES["private"]})
    await telegram.dispatcher.feed_update(cast(Bot, bot), update)
    await telegram.stop()
    assert [incoming.text for incoming in got] == ["How many orders?"]
    assert sorted(telegram.dispatcher.resolve_used_update_types()) == ["callback_query", "message"]


# --- data-agent web --im -------------------------------------------------------------------


def test_service_makes_the_telegram_platform_for_a_telegram_table() -> None:
    made = service.make_platform(CONFIG)
    assert isinstance(made, TelegramPlatform)


def test_without_aiogram_im_says_how_to_install_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """As tests/test_im_service.py's missing-extra case, for Telegram."""
    monkeypatch.setattr(uvicorn, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("DATA_AGENT_TELEGRAM_BOT_TOKEN", TOKEN)
    # An entry of None makes the import fail, as when the package is not installed.
    monkeypatch.setitem(sys.modules, "aiogram", None)
    runs = tmp_path / "runs"
    runs.mkdir()
    im = tmp_path / "im.toml"
    im.write_text(
        '[telegram]\nbot_token_env = "DATA_AGENT_TELEGRAM_BOT_TOKEN"\nallowed_users = [10001]\n',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exited:
        cli.main(["web", "--runs-dir", str(runs), "--im", str(im)])
    assert exited.value.code == 2
    err = capsys.readouterr().err
    assert "uv sync --extra im" in err
    assert "telegram" in err
    assert "aiogram" in err
    assert TOKEN not in err
    assert "Traceback" not in err
