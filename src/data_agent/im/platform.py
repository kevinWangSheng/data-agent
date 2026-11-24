"""The platform interface (.scratch/im/spec.md "进程与接口"; contract item 1 of ticket 130): narrow,
after Vercel Chat SDK's adapters and Hermes' platform base.

Required: receiving messages (`Platform.start` hands each one to the adapter) and sending them
(`Platform.send`). Optional, as `Platform.capabilities` declares: editing a sent message, buttons
with their presses, a typing indicator. The adapter only calls an optional method the platform
declares, and degrades without it: no edits means no status message, only the answer; no buttons
means the numbered text alone.

A platform also declares its limits (`Platform.limits`): the longest message it takes, in
characters of the Markdown the adapter hands it, and the shortest interval between two edits of a
message. Each implementation picks values with room for its own conversion (Slack about 4000
characters, Telegram 4096, Discord 2000; Telegram about 1 second, Slack and Discord about 2).

What the platform implementation does, not the adapter:

- `Incoming.thread_id` is the thread the conversation lives in: on Slack the thread of the
  message, or the message's own `ts` for a top-level message (the bot answers in its thread); on
  Discord the thread the bot makes from the message (a thread made from a message has its id); on
  Telegram None (a chat has no threads).
- `Incoming.text` has the mention of the bot taken out.
- Telegram's `/new` is an `Incoming` with `new_conversation` set and the rest of the command as
  `text` (empty for a bare `/new`).
- `send` and `edit` take the common Markdown of the answer and convert it to the platform's own
  format (Slack mrkdwn, Telegram's, Discord Markdown).
- User ids are strings, whatever type the platform uses (Telegram's are numbers).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Incoming:
    """A message someone wrote to the bot, or where the bot can see it."""

    # The platform's name, as in the `--im` file: "slack", "telegram", "discord".
    platform: str
    chat_id: str
    # None where the chat has no threads (Telegram); see the module's docstring.
    thread_id: str | None
    user_id: str
    # A private chat with the bot (true), or a channel or group (false).
    direct: bool
    # Whether the message mentions the bot (@bot); a platform may count a reply to the bot too.
    mentioned: bool
    # With the bot's mention taken out.
    text: str
    # Telegram's `/new`: the chat's current conversation ends; `text`, when not blank, is the
    # first message of the next one.
    new_conversation: bool = False


@dataclass(frozen=True)
class Button:
    # What the button shows (a clarification's button shows only the option's number).
    label: str
    # Handed back in `Press.value`; at most 64 bytes of ASCII (Telegram's `callback_data`).
    value: str


@dataclass(frozen=True)
class Outgoing:
    """A message the bot sends or an edit of one: Markdown text and its buttons, in order. An
    edit replaces both; an edit without buttons takes them off."""

    text: str
    buttons: tuple[Button, ...] = ()


@dataclass(frozen=True)
class Press:
    """Someone pressed a button on one of the bot's messages."""

    platform: str
    chat_id: str
    thread_id: str | None
    user_id: str
    direct: bool
    # The message the button is on, as `Platform.send` returned its id.
    message_id: str
    value: str


@dataclass(frozen=True)
class Capabilities:
    edit: bool
    buttons: bool
    typing: bool


@dataclass(frozen=True)
class Limits:
    # The longest message, in characters of the Markdown handed to `send` or `edit`.
    max_chars: int
    # The shortest time between two edits of one message, in seconds.
    edit_interval: float


OnMessage = Callable[[Incoming], Awaitable[None]]
OnPress = Callable[[Press], Awaitable[None]]


class Platform(Protocol):
    name: str
    capabilities: Capabilities
    limits: Limits

    async def start(self, on_message: OnMessage, on_press: OnPress) -> None:
        """Connects, and from then on hands every message and button press to the handlers,
        awaiting each before the next. Returns once connected; the platform's library keeps the
        connection (and reconnects) in the background."""
        ...

    async def stop(self) -> None: ...

    async def send(self, chat_id: str, thread_id: str | None, message: Outgoing) -> str:
        """Sends the message into the thread (or the chat, with no thread); returns its id."""
        ...

    async def edit(self, chat_id: str, message_id: str, message: Outgoing) -> None:
        """Only when `capabilities.edit`."""
        ...

    async def typing(self, chat_id: str, thread_id: str | None) -> None:
        """Only when `capabilities.typing`."""
        ...
