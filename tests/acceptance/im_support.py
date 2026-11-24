"""Stand-ins shared by the IM acceptance tests (.scratch/im/spec.md "Testing Decisions", ticket
130): test_im_sessions.py, test_im_triggers.py, test_im_clarification.py, test_im_progress.py,
test_im_steer_cancel.py, test_im_format.py, test_im_config.py, test_im_errors.py.

No real platform and no model: the seam is a platform in memory (`FakePlatform`, an
`data_agent.im.platform.Platform`), a stand-in for the chat backend's `Conversations`
(`FakeConversations`, same methods, same answers, no process), and a temporary runs directory
whose event files the tests write line by line (`append_events`), read by the adapter through
`RunsDir` as the web UI reads them. Time is a `FakeClock` the tests move by hand.

Nothing here asserts: a stand-in used wrongly, or a fixture that cannot be set up, raises
`RuntimeError`, which a test marked `xfail(raises=(NotImplementedError, AssertionError))`
reports as a failure, never as the expected one.
"""

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from seam_support import FIXTURES

from data_agent.im.adapter import Adapter
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
from data_agent.web.launcher import Refused
from data_agent.web.runs import RunsDir

CLARIFICATION_RUN = FIXTURES / "chat" / "conversation" / "20261117T100000Z-a1a1a1" / "events.jsonl"
CHARTS_RUN = FIXTURES / "charts" / "run" / "events.jsonl"

# Users on the allowed list, and one who is not. Ids as strings, as `Incoming` carries them.
ALICE = "U0ALICE01"
BOB = "U0BOB0002"
OUTSIDER = "U0EVE0099"
ALLOWED = (ALICE, BOB)

# The fake platform's limits: small, and none of the real platforms' values.
MAX_CHARS = 300
EDIT_INTERVAL = 1.5

# Every run starts at T0; a test's events give their time in seconds after it.
T0 = datetime(2026, 11, 24, 9, 0, 0, tzinfo=UTC)
QUERY_TOOL = "mcp__data__query_database"


class FakeClock:
    """Seconds, as `time.monotonic` gives them; only `advance` moves it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Shown:
    """A message the bot sent, as the platform shows it now (after every edit)."""

    id: str
    chat_id: str
    thread_id: str | None
    text: str
    buttons: tuple[Button, ...]
    # What `send` got, then each `edit`.
    versions: list[Outgoing] = field(default_factory=list)


class FakePlatform:
    """A platform in memory. `deliver` and `press` hand a message or a button press to the
    adapter's handlers, as a platform's library would, and wait for them."""

    def __init__(
        self,
        name: str = "slack",
        *,
        edit: bool = True,
        buttons: bool = True,
        max_chars: int = MAX_CHARS,
        edit_interval: float = EDIT_INTERVAL,
    ) -> None:
        self.name = name
        self.capabilities = Capabilities(edit=edit, buttons=buttons, typing=False)
        self.limits = Limits(max_chars=max_chars, edit_interval=edit_interval)
        self.shown: list[Shown] = []
        # Every call, in order: ("send", Outgoing) or ("edit", Outgoing).
        self.calls: list[tuple[str, Outgoing]] = []
        self.on_message: OnMessage | None = None
        self.on_press: OnPress | None = None

    # --- the Platform interface ------------------------------------------------------------

    async def start(self, on_message: OnMessage, on_press: OnPress) -> None:
        self.on_message = on_message
        self.on_press = on_press

    async def stop(self) -> None:
        self.on_message = None
        self.on_press = None

    async def send(self, chat_id: str, thread_id: str | None, message: Outgoing) -> str:
        shown = Shown(
            f"m{len(self.shown) + 1}", chat_id, thread_id, message.text, message.buttons, [message]
        )
        self.shown.append(shown)
        self.calls.append(("send", message))
        return shown.id

    async def edit(self, chat_id: str, message_id: str, message: Outgoing) -> None:
        self.calls.append(("edit", message))
        [shown] = [shown for shown in self.shown if shown.id == message_id] or [None]
        if shown is None or shown.chat_id != chat_id:
            raise RuntimeError(f"edit of a message the bot never sent: {chat_id} {message_id}")
        shown.text = message.text
        shown.buttons = message.buttons
        shown.versions.append(message)

    async def typing(self, chat_id: str, thread_id: str | None) -> None:
        pass

    # --- driving it ------------------------------------------------------------------------

    async def deliver(self, message: Incoming) -> None:
        if self.on_message is None:
            raise RuntimeError("the adapter has not started the platform")
        await self.on_message(message)

    async def press(self, shown: Shown, button: Button, user_id: str = ALICE) -> None:
        if self.on_press is None:
            raise RuntimeError("the adapter has not started the platform")
        await self.on_press(
            Press(
                platform=self.name,
                chat_id=shown.chat_id,
                thread_id=shown.thread_id,
                user_id=user_id,
                direct=False,
                message_id=shown.id,
                value=button.value,
            )
        )

    # --- reading it ------------------------------------------------------------------------

    def edits(self) -> list[Outgoing]:
        return [message for kind, message in self.calls if kind == "edit"]

    def sends(self) -> list[Outgoing]:
        return [message for kind, message in self.calls if kind == "send"]

    def in_thread(self, chat_id: str, thread_id: str | None) -> list[Shown]:
        return [s for s in self.shown if s.chat_id == chat_id and s.thread_id == thread_id]

    def texts(self) -> list[str]:
        """Every text the platform was ever given, sent or edited."""
        return [message.text for _, message in self.calls]


def message(
    text: str,
    *,
    user: str = ALICE,
    chat: str = "C1",
    thread: str | None = "T1",
    direct: bool = False,
    mentioned: bool = True,
    platform: str = "slack",
    new_conversation: bool = False,
) -> Incoming:
    return Incoming(
        platform=platform,
        chat_id=chat,
        thread_id=thread,
        user_id=user,
        direct=direct,
        mentioned=mentioned,
        text=text,
        new_conversation=new_conversation,
    )


@dataclass(frozen=True)
class Sent:
    conversation: str
    text: str
    network: bool


class FakeConversations:
    """`Conversations` without a launcher: `send` makes the run's directory and its record
    (`web-run.json`, status running) where the launcher would, and the test writes the event
    file. A conversation's turn is running while its run has no `result` and its record still
    says running, so `send` then answers a steer, as the real one does. `refusals` are raised by
    the next `send` calls, in order."""

    def __init__(self, runs: RunsDir) -> None:
        self.runs = runs
        self.created: list[str] = []
        self.sent: list[Sent] = []
        self.cancelled: list[str] = []
        self.refusals: list[Refused] = []
        # Each conversation's turns' run ids, in order.
        self.turns: dict[str, list[str]] = {}

    def create(self) -> str:
        conversation = f"20261124T080000Z-conv{len(self.created) + 1:02d}"
        self.created.append(conversation)
        self.turns[conversation] = []
        return conversation

    def send(self, conversation: str, text: str, network: bool = True) -> dict[str, Any]:
        self.sent.append(Sent(conversation, text, network))
        if not text.strip():
            raise Refused(400, "the message is empty")
        if conversation not in self.turns:
            raise Refused(404, f"no conversation {conversation}")
        if self.refusals:
            raise self.refusals.pop(0)
        running = self.running(conversation)
        if running is not None:
            return {"kind": "steer", "run_id": running, "id": str(uuid.uuid4())}
        number = sum(len(turns) for turns in self.turns.values()) + 1
        run_id = f"20261124T0900{number:02d}Z-imrun{number:02d}"
        (self.runs.path / run_id).mkdir()
        # The fields of the launcher's record (launcher.Launcher.launch).
        self.runs.write_record(
            run_id,
            {
                "id": run_id,
                "task": text,
                "task_type": "query",
                "config": "full",
                "status": "running",
                "started_at": at(0),
                "duration_ms": None,
                "cost_usd": None,
            },
        )
        self.turns[conversation].append(run_id)
        return {"kind": "turn", "turn": len(self.turns[conversation]), "run_id": run_id}

    def cancel(self, conversation: str) -> dict[str, Any]:
        self.cancelled.append(conversation)
        if conversation not in self.turns:
            raise Refused(404, f"no conversation {conversation}")
        if self.running(conversation) is None:
            raise Refused(409, f"conversation {conversation} has no turn running")
        return {"id": conversation, "single_run": False, "running": True, "turns": []}

    def running(self, conversation: str) -> str | None:
        turns = self.turns.get(conversation, [])
        if not turns:
            return None
        last = turns[-1]
        record = self.runs.record(last)
        if self.runs.has_result(last) or (record is not None and record["status"] != "running"):
            return None
        return last

    # --- reading it ------------------------------------------------------------------------

    def only_run(self) -> str:
        runs = [run for turns in self.turns.values() for run in turns]
        if len(runs) != 1:
            raise RuntimeError(f"expected one turn started, got {runs}")
        return runs[0]

    def latest_run(self, conversation: str) -> str:
        turns = self.turns.get(conversation)
        if not turns:
            raise RuntimeError(f"no turn of {conversation} started")
        return turns[-1]


# --- event files ---------------------------------------------------------------------------


def at(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def append_events(runs: RunsDir, run_id: str, events: Iterable[dict[str, Any]]) -> None:
    """Appends whole lines to the run's event file (`<run_id>.jsonl`), numbering them on from
    the lines already there."""
    run_dir = runs.path / run_id
    if not run_dir.is_dir():
        raise RuntimeError(f"no run directory {run_dir}")
    path = run_dir / f"{run_id}.jsonl"
    seq = len(path.read_text().splitlines()) if path.exists() else 0
    with path.open("a", encoding="utf-8") as file:
        for event in events:
            file.write(json.dumps({"seq": seq, **event}) + "\n")
            seq += 1


def header(task: str = "How many orders?") -> list[dict[str, Any]]:
    return [
        {"type": "version", "ts": at(0), "config": "full", "model": "deepseek-flash"},
        {"type": "task", "ts": at(0), "task": task, "task_type": "query"},
        {"type": "status", "ts": at(1), "state": "started", "session_id": "sess-im"},
    ]


def query(seconds: float, tool_use_id: str = "q1") -> list[dict[str, Any]]:
    """A query: `tool_start` and the `sql` event that follows it."""
    return [
        {
            "type": "tool_start",
            "ts": at(seconds),
            "tool": QUERY_TOOL,
            "tool_use_id": tool_use_id,
            "parent": None,
            "input": {"sql": "SELECT count(*) FROM orders"},
        },
        {"type": "sql", "ts": at(seconds), "tool_use_id": tool_use_id, "sql": "SELECT 1"},
    ]


def query_end(seconds: float, tool_use_id: str = "q1") -> dict[str, Any]:
    return {
        "type": "tool_end",
        "ts": at(seconds),
        "tool": QUERY_TOOL,
        "tool_use_id": tool_use_id,
        "parent": None,
        "is_error": False,
        "content": '{"result": "count\\n-----\\n12\\n(1 row)"}',
    }


def verifier_start(seconds: float) -> dict[str, Any]:
    return {"type": "verifier_start", "ts": at(seconds), "verifier": "result-verifier"}


def answer(seconds: float, text: str) -> dict[str, Any]:
    return {"type": "answer", "ts": at(seconds), "text": text}


def result(
    seconds: float,
    *,
    ended: float | None = None,
    subtype: str = "success",
    is_error: bool = False,
    terminal_reason: str = "completed",
    cost_usd: float = 0.25,
    api_error: str | None = None,
) -> dict[str, Any]:
    """A `result` event (agent.result_fields); `ended` gives `ended_ts` (ticket 122)."""
    event: dict[str, Any] = {
        "type": "result",
        "ts": at(seconds),
        "subtype": subtype,
        "is_error": is_error,
        "num_turns": 4,
        "terminal_reason": terminal_reason,
        "api_error_status": None,
        "api_error": api_error,
        "cost_usd": cost_usd,
        "errors": [],
        "session_id": "sess-im",
    }
    if ended is not None:
        event["ended_ts"] = at(ended)
    return event


def end_without_result(runs: RunsDir, run_id: str, status: str) -> None:
    """The server's record of a run that exited without a `result` (launcher.finish)."""
    record = runs.record(run_id)
    if record is None:
        raise RuntimeError(f"run {run_id} has no record")
    runs.write_record(run_id, {**record, "status": status})


def fixture_events(path: Path) -> list[dict[str, Any]]:
    """A fixture's events, read only, without `seq` (append_events numbers them again)."""
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [{key: value for key, value in event.items() if key != "seq"} for event in events]


# --- the adapter ---------------------------------------------------------------------------


@dataclass
class Bot:
    adapter: Adapter
    platform: FakePlatform
    conversations: FakeConversations
    runs: RunsDir
    clock: FakeClock


async def start_bot(
    runs_path: Path,
    platform: FakePlatform | None = None,
    conversations: FakeConversations | None = None,
    allowed: Iterable[str] = ALLOWED,
    clock: FakeClock | None = None,
) -> Bot:
    """An adapter on the fake platform, started. Called in a test's body, after the test has
    set up everything else, so the stubs' NotImplementedError is the test's expected failure."""
    runs_path.mkdir(parents=True, exist_ok=True)
    runs = RunsDir(runs_path)
    platform = platform if platform is not None else FakePlatform()
    conversations = conversations if conversations is not None else FakeConversations(runs)
    clock = clock if clock is not None else FakeClock()
    adapter = Adapter(platform, conversations, runs, allowed, clock)
    await adapter.start()
    return Bot(adapter, platform, conversations, runs, clock)


def finish_turn(bot: Bot, run_id: str, *events: dict[str, Any]) -> None:
    """Writes a whole successful turn: the header, `events`, a `result`."""
    append_events(bot.runs, run_id, [*header(), *events, result(30, ended=30)])


# --- what the adapter did ------------------------------------------------------------------
# These check the code under test, so unlike the stand-ins above they assert.


def expect_status(bot: Bot, chat: str = "C1", thread: str | None = "T1") -> Shown:
    """The first message the bot sent into the thread: a turn's status message."""
    shown = bot.platform.in_thread(chat, thread)
    assert shown, f"the bot sent nothing into {chat}/{thread}"
    return shown[0]


def expect_stop_button(shown: Shown) -> Button:
    assert len(shown.buttons) == 1, f"the status message has buttons {shown.buttons}"
    return shown.buttons[0]
