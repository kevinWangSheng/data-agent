"""The Telegram platform (.scratch/im/spec.md "进程与接口"; ticket 132): aiogram's dispatcher over
long polling, so the bot needs no public address. Part of the extra `data-agent[im]`; only
`data_agent.im.service.start` imports this module.

- Only messages and callback queries are polled (the dispatcher asks for the update types it
  has handlers for). A message with no text (a join, a photo), from a bot (the bot itself,
  others) or a channel's post is no message to the bot; edits are never polled. A message
  already handed on is dropped.
- A chat has no threads: `thread_id` is None, and a message in a forum topic is an ordinary
  group message (topics are not supported; the bot's replies go to the chat, in no topic).
- In a group the bot is mentioned by `@<its username>` (in any case), a `text_mention` of it,
  a command ending in `@<its username>`, or a reply to one of its messages (but not a reply to
  a forum topic's creation, which every message of the topic is). Its `@username` is taken out
  of `text`. A chat whose type is `private` is direct.
- A message starting with `/new` or `/new@<its username>` is `new_conversation`, the rest of
  it the `text`; `/new@<another bot>` is an ordinary message. `start` registers `/new` in the
  bot's command menu (`setMyCommands`).
- Every message goes out as MarkdownV2 (`to_markdown_v2`); when Telegram cannot parse it
  ("can't parse entities") it is sent once more as the Markdown itself, with no parse mode.
  Buttons are an inline keyboard, one button a row; an edit without buttons sends an empty
  keyboard, which takes the old one off. An edit that changes nothing ("message is not
  modified") is no error.
- A button press (a callback query) is answered (`answerCallbackQuery`) first, so the client
  stops spinning, then handed on.
- Updates are awaited one after another (`handle_as_tasks=False`), and one lock hands messages
  and presses to the adapter one at a time, in the order they came.
- `start` makes the bot with the token (aiogram checks its shape, and raises on a malformed
  one), checks it with `getMe` (which raises when Telegram refuses it), registers `/new`, then
  returns with polling going on in a task of its own. aiogram tries `getUpdates` again after a
  failure, logging each. It never touches the process's signal handlers (`handle_signals=False`;
  uvicorn keeps its own). `stop` ends polling and closes the bot's HTTP session.
- The token is given explicitly; aiogram reads no environment variable. It is in the URL of
  every request (`/bot<token>/<method>`): aiogram 3.34.0 keeps it out of its errors (a network
  error's text is redacted and the error it wraps is not chained) and logs no URL, nor does
  aiohttp's client (tests/test_im_telegram.py checks this at DEBUG).
"""

import asyncio
import contextlib
import logging
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
)

from data_agent.im.config import PlatformConfig
from data_agent.im.format import FENCE, is_fence
from data_agent.im.platform import (
    Button,
    Capabilities,
    Incoming,
    Limits,
    OnMessage,
    OnPress,
    Outgoing,
    Press,
)

log = logging.getLogger(__name__)

PLATFORM = "telegram"
NEW = "/new"
NEW_DESCRIPTION = "Start a new conversation"
MARKDOWN_V2 = "MarkdownV2"
# sendMessage and editMessageText take "1-4096 characters after entities parsing"
# (core.telegram.org/bots/api#sendmessage): MarkdownV2's escapes and markers are parsed away,
# so a converted message shows at most the characters of the Markdown handed in, and the plain
# text sent when Telegram cannot parse it is that Markdown. 96 are left over for what that
# reading may miss: the docs do not say how a character outside the BMP (an emoji) is counted
# (未确认: as two UTF-16 code units, as entity offsets are). Edits: about one a second (spec
# "进度").
MAX_TEXT = 4096
LIMITS = Limits(max_chars=4000, edit_interval=1.0)
# The longest a rate-limited send waits before it is tried again (as data_agent.im.slack).
RATE_LIMIT_WAIT = 30.0
# Telegram's descriptions of a text it cannot parse and of an edit that changes nothing.
UNPARSABLE = "can't parse entities"
NOT_MODIFIED = "message is not modified"
# How many messages are remembered to drop one handed on again.
SEEN = 1000

# The characters MarkdownV2 wants escaped outside code and links; in code only `\` and `` ` ``,
# in a link's `(...)` only `\` and `)` (core.telegram.org/bots/api#markdownv2-style).
SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")
CODE_SPECIAL = re.compile(r"([`\\])")
URL_SPECIAL = re.compile(r"([)\\])")
# Markdown's inline elements, in one scan: code, a link (its URL may hold one level of
# parentheses), bold, strikethrough, italic.
INLINE = re.compile(
    r"`(?P<code>[^`\n]+)`"
    r"|\[(?P<label>[^\]\n]+)\]\((?P<url>(?:[^()\s]|\([^()\s]*\))+)\)"
    r"|\*\*(?=\S)(?P<bold>.+?)(?<=\S)\*\*"
    r"|__(?=\S)(?P<bold_>.+?)(?<=\S)__"
    r"|~~(?=\S)(?P<strike>.+?)(?<=\S)~~"
    r"|(?<![\w*])\*(?=\S)(?P<italic>[^*\n]+?)(?<=\S)\*(?![\w*])"
)
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
BULLET = re.compile(r"^(\s*)[-*+]\s+")
LANGUAGE = re.compile(r"^[\w+#-]+$")


class TelegramPlatform:
    name = PLATFORM
    capabilities = Capabilities(edit=True, buttons=True, typing=False)
    limits = LIMITS

    def __init__(self, config: PlatformConfig, bot: Bot | None = None) -> None:
        """`bot` is for the tests; the bot's own is made at `start`, so a malformed token is an
        error `start` raises (and data_agent.im.service logs), not one that stops the web UI."""
        self.token = config.tokens["bot_token_env"]
        self.bot = bot
        self.dispatcher = Dispatcher()
        self.dispatcher.message.register(self.handle_message)
        self.dispatcher.callback_query.register(self.handle_query)
        self.polling: asyncio.Task[None] | None = None
        self.bot_id = 0
        self.bot_username = ""
        self.on_message: OnMessage | None = None
        self.on_press: OnPress | None = None
        self.lock = asyncio.Lock()
        self.seen: OrderedDict[tuple[int, int], None] = OrderedDict()
        # The tests wait for no time.
        self.sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    def __repr__(self) -> str:
        return f"TelegramPlatform(bot_username={self.bot_username!r})"

    async def start(self, on_message: OnMessage, on_press: OnPress) -> None:
        if self.bot is None:
            self.bot = Bot(self.token)
        bot = self.bot
        me = await bot.me()
        self.bot_id = me.id
        self.bot_username = me.username or ""
        self.on_message = on_message
        self.on_press = on_press
        await bot.set_my_commands(
            commands=[BotCommand(command=NEW.removeprefix("/"), description=NEW_DESCRIPTION)]
        )
        self.polling = asyncio.create_task(
            self.dispatcher.start_polling(
                bot,
                handle_signals=False,
                handle_as_tasks=False,
                # `stop` closes the session, also when `start` failed before polling.
                close_bot_session=False,
            ),
            name="im-telegram-polling",
        )

    async def stop(self) -> None:
        polling, self.polling = self.polling, None
        if polling is not None:
            # "Polling is not started" when it has not taken its lock yet, or has ended.
            with contextlib.suppress(RuntimeError):
                await self.dispatcher.stop_polling()
            polling.cancel()
            try:
                await polling
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("telegram: polling had failed")
        if self.bot is not None:
            await self.bot.session.close()

    async def send(self, chat_id: str, thread_id: str | None, message: Outgoing) -> str:
        """`thread_id` is always None (a chat has no threads). A rate-limited send waits as long
        as `retry_after` says, at most `RATE_LIMIT_WAIT` seconds, and is tried once more: the
        adapter's own second try comes at once, inside the limit. Other failures raise."""
        try:
            sent = await self.post(chat_id, message)
        except TelegramRetryAfter as exc:
            wait = min(max(float(exc.retry_after), 0.0), RATE_LIMIT_WAIT)
            log.warning("telegram: a send was rate-limited; trying again in %.0f s", wait)
            await self.sleep(wait)
            sent = await self.post(chat_id, message)
        return str(sent.message_id)

    async def post(self, chat_id: str, message: Outgoing) -> Message:
        bot = self.connected()
        markup = keyboard(message.buttons)
        try:
            return await bot.send_message(
                chat_id=int(chat_id),
                text=to_markdown_v2(message.text),
                parse_mode=MARKDOWN_V2,
                reply_markup=markup,
            )
        except TelegramBadRequest as exc:
            if UNPARSABLE not in str(exc).lower():
                raise
            log.warning("telegram: the MarkdownV2 was refused (%s); sending plain text", exc)
            return await bot.send_message(
                chat_id=int(chat_id), text=message.text, parse_mode=None, reply_markup=markup
            )

    async def edit(self, chat_id: str, message_id: str, message: Outgoing) -> None:
        """A rate-limited edit raises (`TelegramRetryAfter`), and the adapter skips it."""
        bot = self.connected()
        markup = keyboard(message.buttons) or InlineKeyboardMarkup(inline_keyboard=[])
        try:
            try:
                await bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=to_markdown_v2(message.text),
                    parse_mode=MARKDOWN_V2,
                    reply_markup=markup,
                )
            except TelegramBadRequest as exc:
                if UNPARSABLE not in str(exc).lower():
                    raise
                log.warning("telegram: the MarkdownV2 was refused (%s); editing as plain text", exc)
                await bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=message.text,
                    parse_mode=None,
                    reply_markup=markup,
                )
        except TelegramBadRequest as exc:
            if NOT_MODIFIED not in str(exc).lower():
                raise

    async def typing(self, chat_id: str, thread_id: str | None) -> None:
        """Not used (`capabilities.typing` is false): the adapter never calls it."""

    async def handle_message(self, message: Message) -> None:
        incoming = to_incoming(message, self.bot_id, self.bot_username)
        if incoming is None or self.on_message is None:
            return
        seen = (message.chat.id, message.message_id)
        if seen in self.seen:
            return
        self.seen[seen] = None
        while len(self.seen) > SEEN:
            self.seen.popitem(last=False)
        async with self.lock:
            await self.on_message(incoming)

    async def handle_query(self, query: CallbackQuery) -> None:
        try:
            await self.connected().answer_callback_query(callback_query_id=query.id)
        except Exception as exc:
            # An old query cannot be answered any more; the press still counts.
            log.warning("telegram: a button press could not be answered (%s)", type(exc).__name__)
        press = to_press(query)
        if press is None or self.on_press is None:
            return
        async with self.lock:
            await self.on_press(press)

    def connected(self) -> Bot:
        if self.bot is None:
            raise RuntimeError("the Telegram bot is not started")
        return self.bot


def keyboard(buttons: tuple[Button, ...]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=button.label, callback_data=button.value)]
            for button in buttons
        ]
    )


def to_incoming(message: Message, bot_id: int, bot_username: str) -> Incoming | None:
    """The message to the bot `message` is, or None for any other: one without text (a join,
    a photo), from a bot (the bot itself, others), or a channel's post."""
    user = message.from_user
    if message.text is None or user is None or user.is_bot or message.chat.type == "channel":
        return None
    text = message.text
    name = "@" + bot_username.lower()
    mentioned = False
    new_conversation = False
    # The spans of the text the bot's mentions take, in Python's indices.
    cuts: list[tuple[int, int]] = []
    for entity in message.entities or []:
        span = python_span(text, entity)
        part = text[span[0] : span[1]]
        if entity.type == "mention" and part.lower() == name:
            mentioned = True
            cuts.append(span)
        elif entity.type == "text_mention" and entity.user is not None:
            mentioned = mentioned or entity.user.id == bot_id
        elif entity.type == "bot_command":
            command, _, target = part.partition("@")
            if target and "@" + target.lower() != name:
                continue
            mentioned = mentioned or bool(target)
            if entity.offset == 0 and command.lower() == NEW:
                new_conversation = True
                cuts.append(span)
    reply = message.reply_to_message
    if (
        reply is not None
        and reply.from_user is not None
        and reply.from_user.id == bot_id
        and reply.forum_topic_created is None
    ):
        mentioned = True
    return Incoming(
        platform=PLATFORM,
        chat_id=str(message.chat.id),
        thread_id=None,
        user_id=str(user.id),
        direct=message.chat.type == "private",
        mentioned=mentioned,
        text=cut_out(text, cuts),
        new_conversation=new_conversation,
    )


def to_press(query: CallbackQuery) -> Press | None:
    """The press of one of the bot's buttons `query` is, or None (a button of an inline
    message, which has no chat)."""
    if query.message is None or query.data is None:
        return None
    chat = query.message.chat
    return Press(
        platform=PLATFORM,
        chat_id=str(chat.id),
        thread_id=None,
        user_id=str(query.from_user.id),
        direct=chat.type == "private",
        message_id=str(query.message.message_id),
        value=query.data,
    )


def python_span(text: str, entity: MessageEntity) -> tuple[int, int]:
    """An entity's span in Python's indices: Telegram's offsets count UTF-16 code units."""
    units = 0
    start = end = len(text)
    for index, char in enumerate(text):
        if units == entity.offset:
            start = index
        if units == entity.offset + entity.length:
            end = index
            break
        units += 2 if ord(char) > 0xFFFF else 1
    return start, max(start, end)


def cut_out(text: str, cuts: list[tuple[int, int]]) -> str:
    """The text without the spans, a single blank where one was cut between words."""
    for start, end in sorted(cuts, reverse=True):
        before, after = text[:start].rstrip(), text[end:].lstrip()
        text = f"{before} {after}" if before and after else before + after
    return text.strip()


def to_markdown_v2(markdown: str) -> str:
    """Common Markdown as Telegram's MarkdownV2: bold, italic, strikethrough, links, headings
    (bold lines), bullets, inline code and code blocks (with their language) converted, and
    every other special character escaped. An unclosed code block is closed."""
    out: list[str] = []
    in_code = False
    for line in markdown.split("\n"):
        if is_fence(line):
            language = line.strip()[len(FENCE) :].strip()
            if in_code or not LANGUAGE.match(language):
                language = ""
            out.append(FENCE + language)
            in_code = not in_code
        elif in_code:
            out.append(CODE_SPECIAL.sub(r"\\\1", line))
        else:
            out.append(convert_line(line))
    if in_code:
        out.append(FENCE)
    return "\n".join(out)


def convert_line(line: str) -> str:
    heading = HEADING.match(line)
    if heading:
        # A heading is bold already; bold inside it would be bold in bold.
        return "*" + inline(heading[1].replace("**", ""), styled=True) + "*"
    bullet = BULLET.match(line)
    if bullet:
        return bullet[1] + "• " + inline(line[bullet.end() :])
    return inline(line)


def inline(text: str, styled: bool = False) -> str:
    """`styled`: inside bold, italic, strikethrough or a link's text, which hold no code
    entity, so code there is shown as its text."""
    out: list[str] = []
    end = 0
    for match in INLINE.finditer(text):
        out.append(escape(text[end : match.start()]))
        end = match.end()
        if match["code"] is not None:
            if styled:
                out.append(escape(match[0]))
            else:
                out.append("`" + CODE_SPECIAL.sub(r"\\\1", match["code"]) + "`")
        elif match["label"] is not None:
            url = URL_SPECIAL.sub(r"\\\1", match["url"])
            out.append("[" + inline(match["label"], styled=True) + "](" + url + ")")
        elif match["bold"] is not None or match["bold_"] is not None:
            out.append("*" + inline(match["bold"] or match["bold_"], styled=True) + "*")
        elif match["strike"] is not None:
            out.append("~" + inline(match["strike"], styled=True) + "~")
        else:
            out.append("_" + inline(match["italic"], styled=True) + "_")
    out.append(escape(text[end:]))
    return "".join(out)


def escape(text: str) -> str:
    return SPECIAL.sub(r"\\\1", text)
