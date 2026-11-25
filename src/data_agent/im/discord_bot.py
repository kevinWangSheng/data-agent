"""The Discord platform (.scratch/im/spec.md "进程与接口"; ticket 133): a discord.py `Client` over
the gateway, so the bot needs no public address. Part of the extra `data-agent[im]`; only
`data_agent.im.service.make_platform` imports this module. Named `discord_bot`, not `discord`:
type checkers look for `import discord` next to the importing file first.

- Messages come as `on_message`. A message of another type than default or reply (a join, a
  pin, a thread's starter, a slash command's) or from a bot (this one, others) is no message to
  the bot; edits and deletions are other events, never handed on. A message whose id was
  already handed on is dropped.
- Keys (`Incoming.chat_id`, `thread_id`): in a direct message the private channel's id and no
  thread; in a server channel the channel's id and the message's own id, which a thread made
  from the message will have; in a thread its parent's id and the thread's id, so a
  conversation begun with a mention goes on in the thread the bot made from it. A thread
  discord.py has not cached (an archived one) comes with a channel of no type, which is
  fetched to tell it from a channel. User ids are strings.
- The bot is mentioned when `message.mentions` has it: a reply to the bot that pings it is one;
  `@everyone`, `@here` and role mentions are not. `text` has `<@id>` and `<@!id>` taken out.
- `send` into a thread that is not there yet (not made by this process, not found by
  `fetch_channel`) makes it from the message first: named after the message's first
  `THREAD_NAME_CHARS` characters (mentions out), archived after a day without messages.
  The adapter only sends where it answers, so a message it ignores leaves no thread. When the
  thread cannot be made (no permission, a channel without threads), it is logged, and that
  conversation's messages go into the channel as replies to the message instead (Chat SDK's
  fallback).
- Every message goes out as Discord Markdown, which the common Markdown already is (tables are
  code blocks by `format.format_answer`), with `AllowedMentions.none()`: no `@everyone`, `@here`
  or other mention in an answer notifies anyone. Buttons are components of the message, five to
  a row, their `custom_id` `CUSTOM_ID` and the button's value; no `View` is stored for them, so
  `on_interaction` handles every press, a button of a message from before a restart too.
- A button press is deferred first (Discord wants an answer within 3 seconds), then handed on.
  An edit sends the components again, so one without buttons takes them off.
- `/new` is a global slash command. Its interaction is deferred as an ephemeral "thinking"
  answer only its user sees, an `Incoming` with `new_conversation` (and `mentioned`: the
  command is to the bot) is handed on, and the deferred answer is deleted: whatever the
  adapter replies is the one visible reply. A `/new` typed as text is an ordinary message.
  `/new` in a bot's thread drops the thread's key in the adapter, so the next message there
  needs a mention again.
- discord.py runs each event in a task of its own; one lock hands messages and presses to the
  adapter one at a time, in the order they came.
- `start` logs in (`LoginFailure` when the token is wrong), connects in a task of its own (the
  library reconnects), returns once the gateway is ready, raising when the connection ends
  before, and syncs `/new` when Discord's global commands differ (a failed sync is logged, and
  the bot goes on: the adapter must poll the turns its messages start). Never `Client.run`, which
  sets up logging; the token is given explicitly and goes only into the `Authorization` header
  and the gateway's IDENTIFY, neither of which discord.py logs.
- discord.py waits out a 429 itself, up to `RATE_LIMIT_WAIT` seconds; past that it raises
  `RateLimited`. An edit then raises, and the adapter skips it; a send waits and tries once more.
"""

import asyncio
import contextlib
import logging
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

import discord
from discord import app_commands

from data_agent.im.config import PlatformConfig
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

PLATFORM = "discord"
# A message's content holds 2,000 characters (Discord's Create Message). The Markdown goes out
# as it is; `format.split_message` counts the fences it adds when it splits a code block, and
# the rest is room for a code block's fences reopened around a cut the adapter's own texts make.
# Discord's edits: about one in two seconds (spec "进度").
MESSAGE_CHARS = 2000
LIMITS = Limits(max_chars=1900, edit_interval=2.0)
# The longest a rate-limited request waits inside discord.py, and a send before its second try.
RATE_LIMIT_WAIT = 30.0
# Thread names hold 1-100 characters; the bot's take the message's first 80 (as Hermes').
THREAD_NAME_CHARS = 80
THREAD_NAME = "Data agent question"
THREAD_ARCHIVE_MINUTES = 1440
# Components: five buttons to an action row, five rows; a button's label holds 80 characters,
# a `custom_id` 100.
ROW_BUTTONS = 5
ROWS = 5
BUTTON_CHARS = 80
CUSTOM_ID = "data_agent:"
NEW_COMMAND = "new"
NEW_DESCRIPTION = "Start a new conversation with the data agent"
# How many ids each of the platform's maps remembers (handed-on messages, messages to make a
# thread from, threads known to be there, where sent messages are, ...).
REMEMBERED = 1000

# `<@id>`, `<@!id>`, `<@&role id>`, `<#channel id>`.
MENTION = re.compile(r"<(?:@[!&]?|#)\d+>")
MESSAGE_TYPES = (discord.MessageType.default, discord.MessageType.reply)
THREAD_TYPES = (
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
    discord.ChannelType.news_thread,
)


def intents() -> discord.Intents:
    """The default intents and Message Content, without which a channel message's text is
    empty unless it mentions the bot."""
    chosen = discord.Intents.default()
    chosen.message_content = True
    return chosen


class DiscordPlatform:
    name = PLATFORM
    capabilities = Capabilities(edit=True, buttons=True, typing=False)
    limits = LIMITS

    def __init__(self, config: PlatformConfig, client: discord.Client | None = None) -> None:
        """`client` is for the tests; the bot's own is made here."""
        self.token = config.tokens["bot_token_env"]
        self.client = (
            client
            if client is not None
            else discord.Client(intents=intents(), max_ratelimit_timeout=RATE_LIMIT_WAIT)
        )
        self.connection: asyncio.Task[None] | None = None
        self.on_message: OnMessage | None = None
        self.on_press: OnPress | None = None
        self.lock = asyncio.Lock()
        self.seen: OrderedDict[int, None] = OrderedDict()
        # A top-level message's id -> it and its text, until a thread is made from it.
        self.sources: OrderedDict[str, tuple[discord.Message, str]] = OrderedDict()
        # Threads known to be there.
        self.threads: OrderedDict[str, None] = OrderedDict()
        # Conversations whose thread could not be made: their message id -> its channel's.
        self.unthreaded: OrderedDict[str, str] = OrderedDict()
        # A sent message's id -> the channel it is in (a thread's, not `chat_id`).
        self.channels: OrderedDict[str, str] = OrderedDict()
        # A message sent in the channel for an unthreaded conversation -> the conversation's
        # thread id, so a press of its buttons has the conversation's key.
        self.replies: OrderedDict[str, str] = OrderedDict()
        # The tests wait for no time.
        self.sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    def __repr__(self) -> str:
        user = self.client.user
        return f"DiscordPlatform(user_id={user.id if user is not None else None!r})"

    async def start(self, on_message: OnMessage, on_press: OnPress) -> None:
        self.on_message = on_message
        self.on_press = on_press
        client = self.client
        await client.login(self.token)
        tree = self.register(client)
        connection = asyncio.create_task(client.connect(), name="discord-gateway")
        self.connection = connection
        ready = asyncio.create_task(client.wait_until_ready())
        await asyncio.wait({connection, ready}, return_when=asyncio.FIRST_COMPLETED)
        if not ready.done():
            ready.cancel()
            connection.result()
            raise ConnectionError("discord: the gateway closed before it was ready")
        try:
            await sync_commands(tree)
        except Exception:
            # The gateway is up and messages come; only `/new` may be missing or stale.
            log.exception("discord: syncing /new failed; the bot goes on without it")

    def register(self, client: discord.Client) -> app_commands.CommandTree[Any]:
        """The event handlers and the command tree with `/new`."""
        tree = app_commands.CommandTree(client)

        @tree.command(name=NEW_COMMAND, description=NEW_DESCRIPTION)
        @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=False)
        async def new(interaction: discord.Interaction) -> None:  # pyright: ignore[reportUnusedFunction]
            await self.handle_new(interaction)

        @client.event
        async def on_message(message: discord.Message) -> None:  # pyright: ignore[reportUnusedFunction]
            await self.handle_message(message)

        @client.event
        async def on_interaction(interaction: discord.Interaction) -> None:  # pyright: ignore[reportUnusedFunction]
            await self.handle_interaction(interaction)

        return tree

    async def stop(self) -> None:
        connection, self.connection = self.connection, None
        try:
            await self.client.close()
        except Exception:
            log.exception("discord: closing the client failed")
        if connection is not None:
            connection.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await connection

    async def send(self, chat_id: str, thread_id: str | None, message: Outgoing) -> str:
        """A send rate-limited past `RATE_LIMIT_WAIT` (`RateLimited`) waits at most that long
        and is tried once more: the adapter's own second try comes at once, and a part of an
        answer would be lost. Other failures raise."""
        target, reference = await self.target(chat_id, thread_id)
        kwargs = message_kwargs(message)
        if reference is not None:
            kwargs["reference"] = reference
        try:
            sent = await target.send(**kwargs)
        except discord.RateLimited as exc:
            wait = min(max(exc.retry_after, 0.0), RATE_LIMIT_WAIT)
            log.warning("discord: a send was rate-limited; trying again in %.0f s", wait)
            await self.sleep(wait)
            sent = await target.send(**kwargs)
        message_id = str(sent.id)
        remember(self.channels, message_id, str(target.id))
        if reference is not None and thread_id is not None:
            remember(self.replies, message_id, thread_id)
        return message_id

    async def edit(self, chat_id: str, message_id: str, message: Outgoing) -> None:
        """A rate-limited edit raises (`RateLimited`), and the adapter skips it."""
        channel = self.channels.get(message_id, chat_id)
        partial = self.client.get_partial_messageable(int(channel))
        await partial.get_partial_message(int(message_id)).edit(**message_kwargs(message))

    async def typing(self, chat_id: str, thread_id: str | None) -> None:
        """Not used: `capabilities.typing` is false."""

    async def target(
        self, chat_id: str, thread_id: str | None
    ) -> tuple[discord.PartialMessageable, discord.MessageReference | None]:
        """Where a message for the key goes, and the message it replies to (for a conversation
        whose thread could not be made). Makes the thread when it is not there yet."""
        partial = self.client.get_partial_messageable
        if thread_id is None:
            return partial(int(chat_id)), None
        if thread_id in self.threads:
            return partial(int(thread_id)), None
        if thread_id in self.unthreaded:
            return partial(int(chat_id)), reply_to(chat_id, thread_id)
        found = self.sources.pop(thread_id, None)
        if found is None:
            found = await self.thread_or_source(chat_id, thread_id)
            if found is None:
                return partial(int(thread_id)), None
        source, text = found
        try:
            await source.create_thread(
                name=thread_name(text), auto_archive_duration=THREAD_ARCHIVE_MINUTES
            )
        except (discord.HTTPException, ValueError):
            log.warning(
                "discord: no thread could be made from message %s; answering in the channel",
                thread_id,
                exc_info=True,
            )
            remember(self.unthreaded, thread_id, chat_id)
            return partial(int(chat_id)), reply_to(chat_id, thread_id)
        remember(self.threads, thread_id, None)
        return partial(int(thread_id)), None

    async def thread_or_source(
        self, chat_id: str, thread_id: str
    ) -> tuple[discord.Message, str] | None:
        """For a key this process has not seen the message of (a restart): None when the
        thread is there, else the message to make it from."""
        try:
            await self.client.fetch_channel(int(thread_id))
        except discord.NotFound:
            pass
        else:
            remember(self.threads, thread_id, None)
            return None
        # The channel the message came in, a text channel: its message has the guild a thread
        # is made in (a `get_partial_messageable`'s message would have none).
        channel: Any = await self.client.fetch_channel(int(chat_id))
        source: discord.Message = await channel.fetch_message(int(thread_id))
        user = self.client.user
        return source, strip_mention(source.content, user.id if user is not None else 0)

    async def handle_message(self, message: discord.Message) -> None:
        user = self.client.user
        if self.on_message is None or user is None:
            return
        if message.id in self.seen or not is_to_bot(message):
            return
        # Before the fetch `resolve` may make: an event delivered again meanwhile is dropped.
        remember(self.seen, message.id, None)
        incoming = to_incoming(message, user.id, await self.resolve(message))
        if incoming is None:
            return
        if incoming.thread_id is not None:
            if incoming.thread_id == str(message.id):
                remember(self.sources, incoming.thread_id, (message, incoming.text))
            else:
                remember(self.threads, incoming.thread_id, None)
        async with self.lock:
            await self.on_message(incoming)

    async def resolve(self, message: discord.Message) -> Any:
        """The message's channel, fetched when discord.py has no type for it: a server channel
        it has not cached (an archived thread leaves the cache, `state.py` THREAD_UPDATE), whose
        message comes with a `PartialMessageable` that cannot tell a thread from a channel.
        None when `message.channel` will do."""
        channel: Any = message.channel
        if channel.type is not None or message.guild is None:
            return None
        try:
            return await self.client.fetch_channel(channel.id)
        except discord.HTTPException:
            log.warning("discord: channel %s could not be fetched", channel.id, exc_info=True)
            return None

    async def handle_interaction(self, interaction: discord.Interaction) -> None:
        """A press of one of the bot's buttons; `/new` comes through the command tree."""
        if interaction.type != discord.InteractionType.component:
            return
        data: Any = interaction.data or {}
        custom_id = str(data.get("custom_id", ""))
        if not custom_id.startswith(CUSTOM_ID):
            return
        await interaction.response.defer()
        press = self.to_press(interaction, custom_id[len(CUSTOM_ID) :])
        if press is None or self.on_press is None:
            return
        async with self.lock:
            await self.on_press(press)

    def to_press(self, interaction: discord.Interaction, value: str) -> Press | None:
        channel, message = interaction.channel, interaction.message
        if channel is None or message is None:
            return None
        chat, thread, direct = channel_key(channel)
        message_id = str(message.id)
        if thread is None and not direct:
            thread = self.replies.get(message_id)
        return Press(
            platform=PLATFORM,
            chat_id=chat,
            thread_id=thread,
            user_id=str(interaction.user.id),
            direct=direct,
            message_id=message_id,
            value=value,
        )

    async def handle_new(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = interaction.channel
            if self.on_message is None or channel is None:
                return
            chat, thread, direct = channel_key(channel)
            incoming = Incoming(
                platform=PLATFORM,
                chat_id=chat,
                thread_id=thread,
                user_id=str(interaction.user.id),
                direct=direct,
                mentioned=True,
                text="",
                new_conversation=True,
            )
            async with self.lock:
                await self.on_message(incoming)
        finally:
            try:
                await interaction.delete_original_response()
            except discord.HTTPException:
                log.warning("discord: the /new acknowledgement could not be deleted", exc_info=True)


async def sync_commands(tree: app_commands.CommandTree[Any]) -> None:
    """Syncs the global commands (`/new`) when Discord's differ from the tree's (name,
    description, where it can be used): a sync on every start would be rate-limited."""
    local = {
        (command.name, getattr(command, "description", ""), contexts(command.allowed_contexts))
        for command in tree.get_commands()
    }
    remote = {
        (command.name, command.description, contexts(command.allowed_contexts))
        for command in await tree.fetch_commands()
    }
    if remote != local:
        await tree.sync()
        log.info("discord: synced the slash commands")


def contexts(allowed: Any) -> tuple[bool, bool, bool] | None:
    """Where a command can be used (the tree's `AppCommandContext` or Discord's), comparable."""
    if allowed is None:
        return None
    return allowed.guild, allowed.dm_channel, allowed.private_channel


def to_incoming(message: discord.Message, bot_id: int, channel: Any = None) -> Incoming | None:
    """The message to the bot a message is, or None for any other. `channel` is the message's
    channel when `message.channel` is a stand-in without a type (see `handle_message`)."""
    if not is_to_bot(message):
        return None
    chat, thread, direct = channel_key(channel if channel is not None else message.channel)
    if thread is None and not direct:
        # A top-level message: the key of the thread made from it.
        thread = str(message.id)
    return Incoming(
        platform=PLATFORM,
        chat_id=chat,
        thread_id=thread,
        user_id=str(message.author.id),
        direct=direct,
        mentioned=any(user.id == bot_id for user in message.mentions),
        text=strip_mention(message.content, bot_id),
    )


def is_to_bot(message: discord.Message) -> bool:
    """A person's message (default or reply), not a system message's type or a bot's."""
    return message.type in MESSAGE_TYPES and not message.author.bot


def channel_key(channel: Any) -> tuple[str, str | None, bool]:
    """Chat id, thread id and whether it is a direct message, for an interaction's channel:
    a thread's key is its parent's and its own id, a server channel's has no thread."""
    if channel.type == discord.ChannelType.private:
        return str(channel.id), None, True
    if channel.type in THREAD_TYPES:
        return str(channel.parent_id), str(channel.id), False
    return str(channel.id), None, False


def strip_mention(text: str, bot_id: int) -> str:
    return re.sub(rf"<@!?{bot_id}>", "", text).strip()


def thread_name(text: str) -> str:
    """The message's text without mentions (users, roles, channels), on one line, its first
    `THREAD_NAME_CHARS` characters (77 and `...` when it is longer); `THREAD_NAME` when nothing
    is left."""
    name = " ".join(MENTION.sub("", text).split())
    if len(name) > THREAD_NAME_CHARS:
        name = name[: THREAD_NAME_CHARS - 3] + "..."
    return name or THREAD_NAME


def reply_to(chat_id: str, message_id: str) -> discord.MessageReference:
    return discord.MessageReference(
        message_id=int(message_id), channel_id=int(chat_id), fail_if_not_exists=False
    )


class Buttons(discord.ui.View):
    """Components only: never stored for dispatch, so no callback or timeout of a `View`
    handles a press; `on_interaction` does."""

    def is_dispatchable(self) -> bool:
        return False


def buttons_view(buttons: tuple[Button, ...]) -> Buttons | None:
    """Made inside the event loop (a `View` needs a running one)."""
    if not buttons:
        return None
    view = Buttons(timeout=None)
    for index, button in enumerate(buttons[: ROW_BUTTONS * ROWS]):
        view.add_item(
            discord.ui.Button(
                label=button.label[:BUTTON_CHARS],
                custom_id=CUSTOM_ID + button.value,
                row=index // ROW_BUTTONS,
            )
        )
    return view


def message_kwargs(message: Outgoing) -> dict[str, Any]:
    """`send` / `edit` arguments: content, the buttons (None takes them off), no mentions."""
    return {
        "content": message.text,
        "view": buttons_view(message.buttons),
        "allowed_mentions": discord.AllowedMentions.none(),
    }


def remember[K, V](seen: OrderedDict[K, V], key: K, value: V) -> None:
    seen[key] = value
    seen.move_to_end(key)
    while len(seen) > REMEMBERED:
        seen.popitem(last=False)
