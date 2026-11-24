"""The common part of the IM entry (.scratch/im/spec.md "会话映射", "澄清、进度、取消", "权限",
"出错"; contract items 2, 3 and 9 of ticket 130): one `Adapter` per platform.

It drives the chat backend only through `Conversations.create`, `send` and `cancel`, and reads a
turn's progress from its event file through `RunsDir`, as the web UI does. It writes no event and
no `conversation.json`; its own state is the thread map. Nothing it logs, writes or sends carries
a token: it is never given one (`config.PlatformConfig` goes to the platform implementation).

The adapter never sleeps and starts no task of its own: a handler (`on_message`, `on_press`)
returns once every call it makes is done, and `poll` makes one pass over the turns it follows.
`run` is the loop that calls `poll` every `POLL_SECONDS`; the clock it is given (seconds, only
ever read) times the edits.

Conversations (contract item 3):

- The key is platform + chat id + thread id. A message under a key with no conversation yet
  calls `create()` once, then `send()` with that id; under a key with one, only `send()`.
- The map is `<runs-dir>/im/threads.json` (`IM_DIR`, `THREADS_FILE`), replaced whole on every
  change and read back when an adapter starts, so a restart keeps it. It is no `*.jsonl` file,
  so `RunsDir` lists no run for `im/`.
- `Incoming.new_conversation` (Telegram's `/new`) drops the key's conversation; the next message,
  or the command's own text when not blank, starts another.

Who gets an answer:

- A direct message, always. In a channel or group: a message that mentions the bot, or any
  message in a thread the bot is in (a mapped key whose thread id is not None). A group without
  threads (Telegram) needs the mention every time; otherwise the bot would answer every message
  of the group after its first.
- Only the users of the allowed list (`allowed_users`) make the bot do anything: a turn, a steer,
  `stop`, `/new`, a button press. Anyone else, in a direct chat, gets one reply that gives their
  user id, and nothing else happens; in a channel or group, nothing at all (no reply, no call).

A turn:

- `send()` answering `{"kind": "turn", ...}`: on a platform that edits, a status message goes
  into the thread (with a Stop button, when the platform has buttons) and is edited as the turn's
  events come (`progress.status_text`), at most once per `Limits.edit_interval`. When the turn
  ends, its status shows how it ended, with duration and cost, and the Stop button is gone. Not
  fixed: whether that last edit waits out the interval.
- `{"kind": "steer", ...}`: no new status message; the running turn's carries on. Not fixed:
  whether the bot acknowledges the steer.
- A `clarification` event: one message with the question and its options numbered (`1. ...`
  on a line each); with buttons, one per option showing only its number. Pressing button k is
  `send()` of option k's text; a typed reply in the thread is `send()` of what was typed.
- The answer (`answer.text`): sent after the turn ends, as `format.format_answer` splits it.
- A turn that ended without an answer:
  - after a clarification (`result` is `success`, no `answer`): the status shows it waits for
    the answer, and nothing more is sent;
  - a failed `result` (the model call failed, the budget or the turn limit ran out, or another
    error): one reply saying which, taken from `result` (`terminal_reason`, `subtype`,
    `api_error`), with the run's id, where the web UI shows it;
  - no `result`, and `RunsDir.ended` (the record says `cancelled` or `failure`): one reply
    saying it was stopped, or that it failed, with the run's id.
- Without edits there is no status message: the clarification, the answer or the failure reply
  only.

Stopping:

- The Stop button, or a message whose text is `stop` (`STOP`: after taking out the mention and
  the surrounding blanks, in any case; "stop the query" is an ordinary message), calls
  `cancel()` of the key's conversation and never `send()`. With no turn running (`cancel()`
  refused, or no conversation under the key) the bot replies that nothing is running.

Refusals: `send()` refused (`Refused`: 409 while another run goes, one run at a time across the
web UI and every conversation; 400 when no data source is configured, `--im` without
`--data-sources`): one reply in the thread with the refusal's `detail`. A conversation just
created for it stays mapped, so the next message in the thread goes to it and no empty
conversation is left for each try.

The bot writes in English, as the web UI does.
"""

import asyncio
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from data_agent.im.format import format_answer, split_message
from data_agent.im.platform import Button, Incoming, Outgoing, Platform, Press
from data_agent.im.progress import failed, failure_cause, find_result, status_text
from data_agent.web.launcher import Refused
from data_agent.web.runs import RunsDir

log = logging.getLogger(__name__)

# Under the runs directory: the adapter's own files, beside the runs and `conversations/`.
IM_DIR = "im"
THREADS_FILE = "threads.json"
STOP = "stop"
# How often `run` calls `poll`.
POLL_SECONDS = 0.5

Clock = Callable[[], float]
"""Seconds, from any start; only differences count (`time.monotonic`)."""

# The values of the bot's buttons: Stop, and option k of a clarification (`answer:k`).
STOP_VALUE = "stop"
ANSWER_VALUE = "answer:"
# A turn whose events cannot be followed (an error on every pass) is dropped after this many.
FOLLOW_TRIES = 3
# The end-of-turn edit of the status is tried this many times, an interval apart.
FINAL_EDIT_TRIES = 3

NOT_RUNNING = "Nothing is running in this conversation."
NEW_CONVERSATION = "Your next message starts a new conversation."
EMPTY = "Ask me a question about your data."

# Every adapter of the process writes `threads.json` under this lock: each one replaces only its
# own platform's entries, keeping the others'.
MAP_LOCK = threading.Lock()

Key = tuple[str, str, str | None]
"""Platform, chat id, thread id."""


class ConversationsLike(Protocol):
    """What the adapter uses of `data_agent.web.conversations.Conversations`."""

    def create(self) -> str: ...

    def send(self, conversation: str, text: str, network: bool = True) -> dict[str, Any]: ...

    def cancel(self, conversation: str) -> dict[str, Any]: ...


@dataclass
class Turn:
    """A turn the adapter follows, from `send()` answering `{"kind": "turn"}` to its end."""

    conversation: str
    chat_id: str
    thread_id: str | None
    run_id: str
    # The status message, when the platform edits; the text it shows and when it was last edited.
    status_id: str | None = None
    shown: str = ""
    edited_at: float = 0.0
    offset: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    # Set once the turn has a `result` or its record says it ended; `ended` is that status.
    over: bool = False
    ended: str | None = None
    concluded: bool = False
    final_tries: int = 0
    errors: int = 0


@dataclass(frozen=True)
class Question:
    """A clarification the bot asked with buttons."""

    conversation: str
    thread_id: str | None
    options: tuple[str, ...]
    text: str


class Adapter:
    def __init__(
        self,
        platform: Platform,
        conversations: ConversationsLike,
        runs: RunsDir,
        allowed_users: Iterable[str],
        clock: Clock = time.monotonic,
    ) -> None:
        """Reads the thread map back from `runs`. The conversation calls are synchronous and
        hold the launcher's lock, so the adapter makes them in a worker thread."""
        self.platform = platform
        self.conversations = conversations
        self.runs = runs
        self.allowed = frozenset(allowed_users)
        self.clock = clock
        self.map_file = runs.path / IM_DIR / THREADS_FILE
        self.threads: dict[Key, str] = self.read_map()
        # By run id.
        self.turns: dict[str, Turn] = {}
        # By chat id and the id of the message with the buttons.
        self.questions: dict[tuple[str, str], Question] = {}
        # The handlers and `poll` take turns: each holds it while it runs.
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        """`platform.start` with this adapter's handlers."""
        await self.platform.start(self.on_message, self.on_press)

    async def on_message(self, message: Incoming) -> None:
        async with self.lock:
            try:
                await self.handle_message(message)
            except Exception:
                log.exception("%s: a message could not be handled", self.platform.name)

    async def on_press(self, press: Press) -> None:
        async with self.lock:
            try:
                await self.handle_press(press)
            except Exception:
                log.exception("%s: a button press could not be handled", self.platform.name)

    async def poll(self) -> None:
        """One pass over the turns the adapter follows: reads each one's new events
        (`RunsDir.read` from where it stopped) and sends or edits what they call for; a turn
        that has ended (a `result`, or `RunsDir.ended`) is followed no more."""
        async with self.lock:
            for turn in list(self.turns.values()):
                try:
                    await self.advance(turn)
                except Exception:
                    turn.errors += 1
                    log.exception("%s: following run %s failed", self.platform.name, turn.run_id)
                    if turn.errors >= FOLLOW_TRIES:
                        self.turns.pop(turn.run_id, None)

    async def run(self) -> None:
        """`poll` every `POLL_SECONDS`, until cancelled; an error is logged, not raised."""
        while True:
            try:
                await self.poll()
            except Exception:
                log.exception("%s: a poll failed", self.platform.name)
            await asyncio.sleep(POLL_SECONDS)

    # --- messages and presses --------------------------------------------------------------

    async def handle_message(self, message: Incoming) -> None:
        key: Key = (message.platform, message.chat_id, message.thread_id)
        chat, thread = message.chat_id, message.thread_id
        conversation = self.threads.get(key)
        in_bots_thread = conversation is not None and thread is not None
        if not (message.direct or message.mentioned or in_bots_thread):
            return
        if message.user_id not in self.allowed:
            if message.direct:
                await self.reply(
                    chat,
                    thread,
                    f"You are not allowed to use this bot. Your user id is {message.user_id}; "
                    "ask whoever runs the bot to add it to the allowed users.",
                )
            return
        text = message.text.strip()
        if message.new_conversation:
            if conversation is not None:
                del self.threads[key]
                await self.write_map()
                conversation = None
            if not text:
                await self.reply(chat, thread, NEW_CONVERSATION)
                return
        if text.lower() == STOP:
            await self.stop_turn(conversation, chat, thread)
            return
        if not text:
            await self.reply(chat, thread, EMPTY)
            return
        if conversation is None:
            conversation = await asyncio.to_thread(self.conversations.create)
            self.threads[key] = conversation
            await self.write_map()
        await self.send_turn(conversation, message.text, chat, thread)

    async def handle_press(self, press: Press) -> None:
        if press.user_id not in self.allowed:
            return
        chat, thread = press.chat_id, press.thread_id
        if press.value == STOP_VALUE:
            await self.stop_turn(self.threads.get((press.platform, chat, thread)), chat, thread)
            return
        if not press.value.startswith(ANSWER_VALUE):
            log.warning("%s: a press of an unknown button", self.platform.name)
            return
        question = self.questions.get((chat, press.message_id))
        number = press.value[len(ANSWER_VALUE) :]
        if question is None or not number.isdigit() or not 0 < int(number) <= len(question.options):
            await self.reply(
                chat, thread, "This question is no longer open; type your answer in the thread."
            )
            return
        del self.questions[(chat, press.message_id)]
        if self.platform.capabilities.edit:
            # Its buttons go, so the question is answered once.
            await self.edit(chat, press.message_id, Outgoing(question.text))
        option = question.options[int(number) - 1]
        await self.send_turn(question.conversation, option, chat, question.thread_id)

    async def send_turn(self, conversation: str, text: str, chat: str, thread: str | None) -> None:
        try:
            sent = await asyncio.to_thread(self.conversations.send, conversation, text)
        except Refused as exc:
            await self.reply(chat, thread, exc.detail)
            return
        if sent.get("kind") != "turn":
            return
        turn = Turn(conversation, chat, thread, str(sent["run_id"]), edited_at=self.clock())
        if self.platform.capabilities.edit:
            turn.shown = status_text([])
            turn.status_id = await self.send(chat, thread, Outgoing(turn.shown, self.stop()))
        self.turns[turn.run_id] = turn

    async def stop_turn(self, conversation: str | None, chat: str, thread: str | None) -> None:
        if conversation is None:
            await self.reply(chat, thread, NOT_RUNNING)
            return
        try:
            await asyncio.to_thread(self.conversations.cancel, conversation)
        except Refused as exc:
            await self.reply(chat, thread, NOT_RUNNING if exc.status == 409 else exc.detail)

    def stop(self) -> tuple[Button, ...]:
        return (Button("Stop", STOP_VALUE),) if self.platform.capabilities.buttons else ()

    # --- following a turn ------------------------------------------------------------------

    async def advance(self, turn: Turn) -> None:
        if not turn.over:
            # The record first: once it says the run ended, the event file is complete.
            record = await asyncio.to_thread(self.runs.record, turn.run_id)
            chunk = await asyncio.to_thread(self.runs.read, turn.run_id, turn.offset)
            new = chunk.events if chunk is not None else []
            if chunk is not None:
                turn.offset = chunk.offset
            turn.events += new
            for event in new:
                if event.get("type") == "clarification":
                    await self.ask(turn, event)
            status = record.get("status") if record is not None else None
            if find_result(turn.events) is not None:
                turn.over = True
            elif status is not None and status != "running":
                turn.over, turn.ended = True, str(status)
        await self.show_status(turn)
        if turn.over and not turn.concluded:
            turn.concluded = True
            await self.conclude(turn)
        if turn.over and (
            turn.status_id is None
            or turn.shown == status_text(turn.events, turn.ended)
            or turn.final_tries >= FINAL_EDIT_TRIES
        ):
            del self.turns[turn.run_id]

    async def show_status(self, turn: Turn) -> None:
        """Edits the status message to the turn's step, at most once an interval; the end
        right away, its retries an interval apart."""
        if turn.status_id is None:
            return
        text = status_text(turn.events, turn.ended)
        if text == turn.shown:
            return
        now = self.clock()
        due = now - turn.edited_at >= self.platform.limits.edit_interval
        if not (due or (turn.over and turn.final_tries == 0)):
            return
        buttons = () if turn.over else self.stop()
        turn.edited_at = now
        if await self.edit(turn.chat_id, turn.status_id, Outgoing(text, buttons)):
            turn.shown = text
        elif turn.over:
            turn.final_tries += 1

    async def ask(self, turn: Turn, event: dict[str, Any]) -> None:
        """The clarification as one message, its options numbered; a button per number."""
        question = str(event.get("question", ""))
        options = tuple(str(option) for option in event.get("options") or ())
        numbered = [f"{number}. {option}" for number, option in enumerate(options, start=1)]
        text = "\n".join([question, "", *numbered]) if numbered else question
        buttons = ()
        if self.platform.capabilities.buttons:
            buttons = tuple(
                Button(str(number), f"{ANSWER_VALUE}{number}")
                for number in range(1, len(options) + 1)
            )
        parts = split_message(text, self.platform.limits.max_chars) or [question]
        for part in parts[:-1]:
            await self.send(turn.chat_id, turn.thread_id, Outgoing(part))
        message_id = await self.send(turn.chat_id, turn.thread_id, Outgoing(parts[-1], buttons))
        if message_id is not None and buttons:
            self.questions[(turn.chat_id, message_id)] = Question(
                turn.conversation, turn.thread_id, options, parts[-1]
            )

    async def conclude(self, turn: Turn) -> None:
        """The answer, or what the thread is told of a turn that ended without one."""
        result = find_result(turn.events)
        where = f"Run `{turn.run_id}` in the web UI."
        if result is None:
            if turn.ended == "cancelled":
                text = f"Stopped. {where}"
            else:
                text = f"The run failed before it finished; it wrote no result. {where}"
        elif failed(result):
            text = f"{failure_cause(result)} {where}"
        else:
            answers = [event for event in turn.events if event.get("type") == "answer"]
            if answers:
                text = str(answers[-1].get("text", ""))
                limit = self.platform.limits.max_chars
                for part in format_answer(text, turn.events, limit):
                    await self.send(turn.chat_id, turn.thread_id, Outgoing(part))
                return
            if any(event.get("type") == "clarification" for event in turn.events):
                return
            text = f"The run ended without an answer. {where}"
        await self.reply(turn.chat_id, turn.thread_id, text)

    # --- talking to the platform -----------------------------------------------------------

    async def reply(self, chat: str, thread: str | None, text: str) -> None:
        for part in split_message(text, self.platform.limits.max_chars):
            await self.send(chat, thread, Outgoing(part))

    async def send(self, chat: str, thread: str | None, message: Outgoing) -> str | None:
        """The message's id; None when it could not be sent, twice (and the log says so)."""
        for attempt in (1, 2):
            try:
                return await self.platform.send(chat, thread, message)
            except Exception as exc:
                if attempt == 1:
                    log.warning(
                        "%s: sending failed (%s); trying once more",
                        self.platform.name,
                        type(exc).__name__,
                    )
                else:
                    log.exception("%s: sending failed again; given up", self.platform.name)
        return None

    async def edit(self, chat: str, message_id: str, message: Outgoing) -> bool:
        """Whether the edit went through. One that fails (a platform's rate limit, say) is
        skipped and logged; the status catches up at a later poll."""
        try:
            await self.platform.edit(chat, message_id, message)
        except Exception as exc:
            log.warning(
                "%s: an edit was skipped (%s: %s)", self.platform.name, type(exc).__name__, exc
            )
            return False
        return True

    # --- the thread map --------------------------------------------------------------------

    def read_map(self) -> dict[Key, str]:
        """This platform's entries of `threads.json`."""
        return {
            (entry["platform"], entry["chat_id"], entry["thread_id"]): entry["conversation"]
            for entry in read_entries(self.map_file)
            if entry.get("platform") == self.platform.name
        }

    async def write_map(self) -> None:
        mine = [
            {"platform": platform, "chat_id": chat, "thread_id": thread, "conversation": conv}
            for (platform, chat, thread), conv in self.threads.items()
        ]
        await asyncio.to_thread(write_entries, self.map_file, self.platform.name, mine)


def read_entries(path: os.PathLike[str]) -> list[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        log.warning("the thread map %s is unreadable (%s); starting without it", path, exc)
        return []
    threads = data.get("threads") if isinstance(data, dict) else None
    return [entry for entry in threads or [] if isinstance(entry, dict)]


def write_entries(path: os.PathLike[str], platform: str, mine: list[dict[str, Any]]) -> None:
    """Replaces `platform`'s entries of the map with `mine`, whole, keeping the others'."""
    with MAP_LOCK:
        others = [entry for entry in read_entries(path) if entry.get("platform") != platform]
        target = os.fspath(path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump({"threads": [*others, *mine]}, file, indent=1)
        os.replace(tmp, target)
