"""The Slack platform (.scratch/im/spec.md "进程与接口"; ticket 131): slack_bolt's asyncio app over
Socket Mode, so the bot needs no public address. Part of the extra `data-agent[im]`; only
`data_agent.im.service.start` imports this module.

- Messages come as `message` events (`message.im`, `message.channels`, ... subscribed in the
  Slack app; no `app_mention`, so a mention arrives once). An event with a `subtype` (an edit, a
  deletion, a join, a file share) or a `bot_id` (the bot's own messages, other bots') is no
  message to the bot. A message whose `ts` was already handed on (Slack delivers an event again
  when its acknowledgement is late) is dropped.
- `Incoming.thread_id` is the message's `thread_ts`, or its own `ts` for a top-level message:
  the bot answers in the message's thread, in a direct message too. The bot is mentioned when
  the text has `<@bot user id>` (from `auth.test` at start), which `text` has taken out.
- Every message goes out as section blocks of Slack mrkdwn (`to_mrkdwn`; one, or more when the
  escapes take it past a section's 3,000 characters) with the buttons in an actions block after
  them, and `text` as the notification's fallback; an edit sends the blocks
  again, so one without buttons takes them off.
- A button press (`block_actions`) is acknowledged first, then handed on. `Press.thread_id` is
  the thread of the button's message (the bot only posts into threads); `direct` is a channel
  id starting with `D` (a direct message's).
- Listeners of slack_bolt run concurrently; one lock hands messages and presses to the adapter
  one at a time, in the order they came.
- `start` checks the bot token (`auth.test`, which raises when it is wrong), then returns once
  Socket Mode is connected: slack_sdk tries again every 10 seconds, logging each failure, until
  it connects or `stop` closes it (data_agent.im.service starts it in a task of its own).
- Both tokens are given explicitly; slack_bolt never reads `SLACK_BOT_TOKEN` or
  `SLACK_APP_TOKEN`.
"""

import asyncio
import re
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncAck, AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from data_agent.im.config import PlatformConfig
from data_agent.im.platform import (
    Capabilities,
    Incoming,
    Limits,
    OnMessage,
    OnPress,
    Outgoing,
    Press,
)

PLATFORM = "slack"
# A section block's text holds 3,000 characters; the Markdown handed in stays under it with room
# for mrkdwn's escapes (`&amp;`, ...). Slack's edits: about one in two seconds (spec "进度").
LIMITS = Limits(max_chars=2900, edit_interval=2.0)
SECTION_CHARS = 3000
# Block Kit: a button's text holds 75 characters, an actions block 25 elements.
BUTTON_CHARS = 75
ACTIONS = 25
# The bot's buttons' `action_id`s: this, then the button's position.
ACTION_ID = "data_agent_button"
# How many messages' `ts` are remembered to drop an event delivered again.
SEEN = 1000

FENCE = "```"
LINK = re.compile(r"\[([^\]\n]+)\]\((\S+?)\)")
BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
BULLET = re.compile(r"^(\s*)[-*+]\s+", re.MULTILINE)
INLINE_CODE = re.compile(r"(`[^`\n]+`)")
# Placeholders while bold and italic are converted: Slack's bold is Markdown's italic star.
BOLD_MARK = "\x00"


class SlackPlatform:
    name = PLATFORM
    capabilities = Capabilities(edit=True, buttons=True, typing=False)
    limits = LIMITS

    def __init__(self, config: PlatformConfig, client: AsyncWebClient | None = None) -> None:
        """`client` is for the tests; the bot's own is made with the bot token."""
        self.client = (
            client if client is not None else AsyncWebClient(token=config.tokens["bot_token_env"])
        )
        self.app_token = config.tokens["app_token_env"]
        self.handler: AsyncSocketModeHandler | None = None
        self.bot_user_id = ""
        self.on_message: OnMessage | None = None
        self.on_press: OnPress | None = None
        self.lock = asyncio.Lock()
        self.seen: OrderedDict[tuple[str, str], None] = OrderedDict()

    def __repr__(self) -> str:
        return f"SlackPlatform(bot_user_id={self.bot_user_id!r})"

    async def start(self, on_message: OnMessage, on_press: OnPress) -> None:
        auth = await self.client.auth_test()
        self.bot_user_id = str(auth["user_id"])
        self.on_message = on_message
        self.on_press = on_press
        app = AsyncApp(client=self.client)

        @app.event("message")
        async def message(event: dict[str, Any]) -> None:
            await self.handle_event(event)

        @app.action(re.compile(f"^{ACTION_ID}"))
        async def action(ack: AsyncAck, body: dict[str, Any]) -> None:
            await ack()
            await self.handle_action(body)

        handler = AsyncSocketModeHandler(app, app_token=self.app_token)
        self.handler = handler
        await handler.connect_async()

    async def stop(self) -> None:
        handler, self.handler = self.handler, None
        if handler is not None:
            await handler.close_async()

    async def send(self, chat_id: str, thread_id: str | None, message: Outgoing) -> str:
        thread = {"thread_ts": thread_id} if thread_id is not None else {}
        response = await self.client.chat_postMessage(
            channel=chat_id, **thread, **message_payload(message)
        )
        return str(response["ts"])

    async def edit(self, chat_id: str, message_id: str, message: Outgoing) -> None:
        """A rate-limited edit raises (`SlackApiError`, status 429), and the adapter skips it."""
        await self.client.chat_update(channel=chat_id, ts=message_id, **message_payload(message))

    async def typing(self, chat_id: str, thread_id: str | None) -> None:
        """Slack has no typing indicator for a bot (`capabilities.typing` is false)."""

    async def handle_event(self, event: Mapping[str, Any]) -> None:
        incoming = to_incoming(event, self.bot_user_id)
        if incoming is None or self.on_message is None:
            return
        seen = (str(event.get("channel")), str(event.get("ts")))
        if seen in self.seen:
            return
        self.seen[seen] = None
        while len(self.seen) > SEEN:
            self.seen.popitem(last=False)
        async with self.lock:
            await self.on_message(incoming)

    async def handle_action(self, body: Mapping[str, Any]) -> None:
        press = to_press(body)
        if press is None or self.on_press is None:
            return
        async with self.lock:
            await self.on_press(press)


def to_incoming(event: Mapping[str, Any], bot_user_id: str) -> Incoming | None:
    """The message to the bot a `message` event is, or None for any other."""
    if event.get("type") != "message" or event.get("subtype") or event.get("bot_id"):
        return None
    user, channel, ts = event.get("user"), event.get("channel"), event.get("ts")
    if not user or not channel or not ts or user == bot_user_id:
        return None
    text = str(event.get("text") or "")
    mention = re.compile(rf"<@{re.escape(bot_user_id)}(\|[^>]*)?>") if bot_user_id else None
    mentioned = mention is not None and mention.search(text) is not None
    if mention is not None:
        text = mention.sub("", text)
    return Incoming(
        platform=PLATFORM,
        chat_id=str(channel),
        thread_id=str(event.get("thread_ts") or ts),
        user_id=str(user),
        direct=event.get("channel_type") == "im",
        mentioned=mentioned,
        text=unescape(text).strip(),
    )


def to_press(body: Mapping[str, Any]) -> Press | None:
    """The press of one of the bot's buttons a `block_actions` payload is, or None."""
    if body.get("type") != "block_actions":
        return None
    actions = body.get("actions") or []
    channel = (body.get("channel") or {}).get("id")
    message = body.get("message") or {}
    user = (body.get("user") or {}).get("id")
    if not actions or not channel or not user or not message.get("ts"):
        return None
    ts = str(message["ts"])
    return Press(
        platform=PLATFORM,
        chat_id=str(channel),
        thread_id=str(message.get("thread_ts") or ts),
        user_id=str(user),
        direct=str(channel).startswith("D"),
        message_id=ts,
        value=str(actions[0].get("value", "")),
    )


def message_payload(message: Outgoing) -> dict[str, Any]:
    """`chat.postMessage` / `chat.update` arguments: `text` and `blocks`."""
    text = to_mrkdwn(message.text)
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": section}}
        for section in sections(text)
    ]
    if message.buttons:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": button.label[:BUTTON_CHARS]},
                        "value": button.value,
                        "action_id": f"{ACTION_ID}_{index}",
                    }
                    for index, button in enumerate(message.buttons[:ACTIONS])
                ],
            }
        )
    return {"text": text, "blocks": blocks}


def sections(text: str) -> list[str]:
    """The mrkdwn in pieces a section block holds: escapes can take a message within
    `LIMITS.max_chars` past `SECTION_CHARS`. Cut after a line, or anywhere in a line longer than
    a section; the pieces joined are the text."""
    pieces: list[str] = []
    while len(text) > SECTION_CHARS:
        cut = text.rfind("\n", 0, SECTION_CHARS) + 1 or SECTION_CHARS
        pieces.append(text[:cut])
        text = text[cut:]
    return [*pieces, text]


def to_mrkdwn(markdown: str) -> str:
    """Common Markdown as Slack mrkdwn: `&`, `<`, `>` escaped everywhere; outside code, bold,
    italic, strikethrough, links, headings (bold lines) and bullets converted. Code blocks keep
    their text and lose the language after the fence."""
    out: list[str] = []
    code: list[str] | None = None
    text: list[str] = []
    for line in markdown.split("\n"):
        if line.lstrip().startswith(FENCE):
            if code is None:
                out += convert_text(text)
                text = []
                code = []
                out.append(FENCE)
            else:
                out.append("\n".join(escape(part) for part in code))
                out.append(FENCE)
                code = None
            continue
        (code if code is not None else text).append(line)
    if code is not None:
        out.append("\n".join(escape(part) for part in code))
    out += convert_text(text)
    return "\n".join(out)


def convert_text(lines: list[str]) -> list[str]:
    if not lines:
        return []
    pieces = INLINE_CODE.split("\n".join(lines))
    converted = [
        escape(piece) if index % 2 else inline(escape(piece)) for index, piece in enumerate(pieces)
    ]
    return ["".join(converted)]


def inline(text: str) -> str:
    text = HEADING.sub(lambda match: f"{BOLD_MARK}{match[1]}{BOLD_MARK}", text)
    text = BOLD.sub(lambda match: f"{BOLD_MARK}{match[1] or match[2]}{BOLD_MARK}", text)
    text = BULLET.sub(lambda match: f"{match[1]}• ", text)
    text = ITALIC.sub(lambda match: f"_{match[1]}_", text)
    text = STRIKE.sub(lambda match: f"~{match[1]}~", text)
    text = LINK.sub(lambda match: f"<{match[2]}|{match[1]}>", text)
    return text.replace(BOLD_MARK, "*")


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
