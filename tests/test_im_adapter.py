"""The adapter's handling of platform failures and its thread map (data_agent.im.adapter), past
what the acceptance tests in tests/acceptance/test_im_*.py hold: a failed send is tried once more,
a failed edit (a rate limit) is skipped and caught up, an error is only logged, and two platforms
share `threads.json`. A platform and conversations in memory; no process, no network."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from data_agent.im.adapter import IM_DIR, THREADS_FILE, Adapter
from data_agent.im.platform import (
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

INTERVAL = 2.0


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Platform:
    """Fails the first `send_failures` sends and the first `edit_failures` edits."""

    def __init__(self, name: str = "slack", send_failures: int = 0, edit_failures: int = 0) -> None:
        self.name = name
        self.capabilities = Capabilities(edit=True, buttons=True, typing=False)
        self.limits = Limits(max_chars=500, edit_interval=INTERVAL)
        self.send_failures = send_failures
        self.edit_failures = edit_failures
        self.sent: list[Outgoing] = []
        self.edited: list[Outgoing] = []
        self.on_message: OnMessage | None = None
        self.on_press: OnPress | None = None

    async def start(self, on_message: OnMessage, on_press: OnPress) -> None:
        self.on_message, self.on_press = on_message, on_press

    async def stop(self) -> None:
        pass

    async def send(self, chat_id: str, thread_id: str | None, message: Outgoing) -> str:
        if self.send_failures:
            self.send_failures -= 1
            raise ConnectionError("the platform is away")
        self.sent.append(message)
        return f"m{len(self.sent)}"

    async def edit(self, chat_id: str, message_id: str, message: Outgoing) -> None:
        if self.edit_failures:
            self.edit_failures -= 1
            raise RuntimeError("ratelimited")
        self.edited.append(message)

    async def typing(self, chat_id: str, thread_id: str | None) -> None:
        pass


class Conversations:
    """Each `send` starts a turn whose run directory it makes; `fail` makes `create` raise."""

    def __init__(self, runs: RunsDir, fail: bool = False) -> None:
        self.runs = runs
        self.fail = fail
        self.created: list[str] = []
        self.sent: list[tuple[str, str]] = []
        # Raised by the next `send` calls, in order.
        self.refusals: list[Refused] = []

    def create(self) -> str:
        if self.fail:
            raise OSError("disk full")
        self.created.append(f"conv{len(self.created) + 1}")
        return self.created[-1]

    def send(self, conversation: str, text: str, network: bool = True) -> dict[str, Any]:
        if self.refusals:
            raise self.refusals.pop(0)
        self.sent.append((conversation, text))
        run_id = f"run{len(self.sent)}"
        (self.runs.path / run_id).mkdir()
        self.runs.write_record(run_id, {"id": run_id, "status": "running"})
        return {"kind": "turn", "turn": 1, "run_id": run_id}

    def cancel(self, conversation: str) -> dict[str, Any]:
        return {}


def mention(text: str, thread: str | None = "T1", platform: str = "slack") -> Incoming:
    return Incoming(platform, "C1", thread, "U1", False, True, text)


async def bot(
    tmp_path: Path, platform: Platform, fail: bool = False
) -> tuple[Adapter, Conversations, RunsDir, Clock]:
    runs = RunsDir(tmp_path)
    conversations = Conversations(runs, fail)
    clock = Clock()
    adapter = Adapter(platform, conversations, runs, ["U1"], clock)
    await adapter.start()
    return adapter, conversations, runs, clock


def write_events(runs: RunsDir, run_id: str, *events: dict[str, Any]) -> None:
    path = runs.path / run_id / "events.jsonl"
    with path.open("a", encoding="utf-8") as file:
        for event in events:
            file.write(json.dumps({"ts": "2026-11-24T09:00:00+00:00", **event}) + "\n")


async def test_a_failed_send_is_tried_once_more(tmp_path: Path) -> None:
    platform = Platform(send_failures=1)
    adapter, _, _, _ = await bot(tmp_path, platform)
    await adapter.on_message(mention("How many orders?"))
    assert [message.text for message in platform.sent] == ["Started…"]


async def test_a_send_failing_twice_is_logged_and_nothing_else_breaks(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    platform = Platform(send_failures=2)
    adapter, conversations, runs, _ = await bot(tmp_path, platform)
    await adapter.on_message(mention("How many orders?"))
    assert platform.sent == []
    assert "sending failed again" in caplog.text
    # The turn is followed all the same: its answer goes out.
    write_events(runs, "run1", {"type": "answer", "text": "Fourteen."}, {"type": "result"})
    await adapter.poll()
    assert [message.text for message in platform.sent] == ["Fourteen."]
    assert conversations.sent == [("conv1", "How many orders?")]


async def test_a_failed_edit_is_skipped_and_caught_up_later(tmp_path: Path) -> None:
    platform = Platform(edit_failures=1)
    adapter, _, runs, clock = await bot(tmp_path, platform)
    await adapter.on_message(mention("How many orders?"))
    write_events(runs, "run1", {"type": "thinking", "text": "..."})
    clock.now += INTERVAL
    await adapter.poll()
    assert platform.edited == []
    # Within the interval of the skipped edit: no try.
    clock.now += INTERVAL / 2
    await adapter.poll()
    assert platform.edited == []
    clock.now += INTERVAL / 2
    await adapter.poll()
    assert [message.text for message in platform.edited] == ["Thinking…"]


async def test_a_failed_last_edit_is_tried_again(tmp_path: Path) -> None:
    platform = Platform(edit_failures=1)
    adapter, _, runs, clock = await bot(tmp_path, platform)
    await adapter.on_message(mention("How many orders?"))
    write_events(runs, "run1", {"type": "answer", "text": "Fourteen."}, {"type": "result"})
    await adapter.poll()
    assert platform.edited == []
    clock.now += INTERVAL
    await adapter.poll()
    [last] = platform.edited
    assert last.text.startswith("Done")
    assert last.buttons == ()
    # The answer went out once.
    assert [message.text for message in platform.sent].count("Fourteen.") == 1


async def test_an_error_in_a_handler_is_only_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    platform = Platform()
    adapter, _, _, _ = await bot(tmp_path, platform, fail=True)
    with caplog.at_level(logging.ERROR):
        await adapter.on_message(mention("How many orders?"))
    assert "could not be handled" in caplog.text
    assert "OSError" in caplog.text


async def test_two_platforms_share_the_thread_map(tmp_path: Path) -> None:
    slack, telegram = Platform("slack"), Platform("telegram")
    first, _, runs, _ = await bot(tmp_path, slack)
    conversations = Conversations(runs)
    second = Adapter(telegram, conversations, runs, ["U1"], Clock())
    await second.start()
    await first.on_message(mention("How many orders?"))
    await second.on_message(mention("Top customers?", platform="telegram"))
    await first.on_message(mention("Refunds?", thread="T2"))
    data = json.loads((tmp_path / IM_DIR / THREADS_FILE).read_text(encoding="utf-8"))
    keys = {(entry["platform"], entry["thread_id"]) for entry in data["threads"]}
    assert keys == {("slack", "T1"), ("slack", "T2"), ("telegram", "T1")}


async def test_a_question_asked_before_a_restart_says_it_is_no_longer_open(
    tmp_path: Path,
) -> None:
    platform = Platform()
    adapter, conversations, _, _ = await bot(tmp_path, platform)
    await adapter.on_press(Press("slack", "C1", "T1", "U1", False, "m9", "answer:1"))
    assert "no longer open" in platform.sent[-1].text
    assert conversations.sent == []


async def test_a_refused_answer_keeps_the_question_open(tmp_path: Path) -> None:
    platform = Platform()
    adapter, conversations, runs, _ = await bot(tmp_path, platform)
    await adapter.on_message(mention("What was the 2024 revenue?"))
    clarification = {
        "type": "clarification",
        "question": "Net or gross?",
        "options": ["Net", "Gross"],
    }
    write_events(runs, "run1", clarification, {"type": "result"})
    await adapter.poll()
    # The question is the last message sent so far, with a button per option.
    asked_id = f"m{len(platform.sent)}"
    assert "Net or gross?" in platform.sent[-1].text
    assert len(platform.sent[-1].buttons) == 2
    press = Press("slack", "C1", "T1", "U1", False, asked_id, "answer:1")

    conversations.refusals.append(Refused(409, "another run is going; one run at a time"))
    await adapter.on_press(press)
    assert "one run at a time" in platform.sent[-1].text
    assert all(edit.buttons or "Net or gross?" not in edit.text for edit in platform.edited)

    await adapter.on_press(press)
    assert conversations.sent[-1] == ("conv1", "Net")


async def ask_net_or_gross(
    tmp_path: Path, platform: Platform
) -> tuple[Adapter, Conversations, Press]:
    """A turn whose run asked "Net or gross?" with two buttons; the press of the first."""
    adapter, conversations, runs, _ = await bot(tmp_path, platform)
    await adapter.on_message(mention("What was the 2024 revenue?"))
    clarification = {
        "type": "clarification",
        "question": "Net or gross?",
        "options": ["Net", "Gross"],
    }
    write_events(runs, "run1", clarification, {"type": "result"})
    await adapter.poll()
    asked_id = f"m{len(platform.sent)}"
    assert len(platform.sent[-1].buttons) == 2
    return adapter, conversations, Press("slack", "C1", "T1", "U1", False, asked_id, "answer:1")


async def test_a_typed_answer_closes_the_question(tmp_path: Path) -> None:
    platform = Platform()
    adapter, conversations, press = await ask_net_or_gross(tmp_path, platform)
    await adapter.on_message(mention("Gross, please"))
    assert conversations.sent[-1] == ("conv1", "Gross, please")
    # The question's buttons were taken off.
    assert any("Net or gross?" in edit.text and edit.buttons == () for edit in platform.edited)

    await adapter.on_press(press)
    assert len(conversations.sent) == 2
    assert "no longer open" in platform.sent[-1].text


async def test_a_refused_typed_answer_keeps_the_question_open(tmp_path: Path) -> None:
    platform = Platform()
    adapter, conversations, press = await ask_net_or_gross(tmp_path, platform)
    conversations.refusals.append(Refused(409, "another run is going; one run at a time"))
    await adapter.on_message(mention("Gross, please"))
    assert "one run at a time" in platform.sent[-1].text
    assert all("Net or gross?" not in edit.text for edit in platform.edited)

    await adapter.on_press(press)
    assert conversations.sent[-1] == ("conv1", "Net")


# --- threads the bot is in, past `/new` (ticket 137) ---------------------------------------


def plain(text: str, platform: str = "slack", thread: str | None = "T1") -> Incoming:
    """A message in the thread that does not mention the bot."""
    return Incoming(platform, "C1", thread, "U1", False, False, text)


def new(text: str = "", platform: str = "slack", thread: str | None = "T1") -> Incoming:
    return Incoming(platform, "C1", thread, "U1", False, True, text, new_conversation=True)


@pytest.mark.parametrize("name", ["slack", "discord"])
async def test_after_a_bare_new_the_bots_thread_needs_no_mention(tmp_path: Path, name: str) -> None:
    platform = Platform(name)
    adapter, conversations, runs, _ = await bot(tmp_path, platform)
    await adapter.on_message(mention("How many orders?", platform=name))
    await adapter.on_message(new(platform=name))
    assert platform.sent[-1].text == "Your next message starts a new conversation."
    await adapter.on_message(plain("Top customers?", name))
    assert conversations.created == ["conv1", "conv2"]
    assert conversations.sent[-1] == ("conv2", "Top customers?")

    # A new adapter on the same runs directory: the thread is still the bot's, and its
    # conversation the new one.
    restarted = Adapter(Platform(name), conversations, runs, ["U1"], Clock())
    await restarted.start()
    await restarted.on_message(plain("And last year?", name))
    assert conversations.created == ["conv1", "conv2"]
    assert conversations.sent[-1] == ("conv2", "And last year?")


@pytest.mark.parametrize("name", ["slack", "discord"])
async def test_after_new_with_a_question_the_bots_thread_needs_no_mention(
    tmp_path: Path, name: str
) -> None:
    platform = Platform(name)
    adapter, conversations, _, _ = await bot(tmp_path, platform)
    await adapter.on_message(mention("How many orders?", platform=name))
    await adapter.on_message(new("Refunds by month?", platform=name))
    await adapter.on_message(plain("And by week?", name))
    assert conversations.created == ["conv1", "conv2"]
    assert conversations.sent[1:] == [("conv2", "Refunds by month?"), ("conv2", "And by week?")]


async def test_a_bare_new_before_a_restart_keeps_the_thread_the_bots(tmp_path: Path) -> None:
    adapter, conversations, runs, _ = await bot(tmp_path, Platform())
    await adapter.on_message(mention("How many orders?"))
    await adapter.on_message(new())
    restarted = Adapter(Platform(), conversations, runs, ["U1"], Clock())
    await restarted.start()
    await restarted.on_message(plain("Top customers?"))
    assert conversations.created == ["conv1", "conv2"]
    assert conversations.sent[-1] == ("conv2", "Top customers?")


async def test_a_thread_map_of_the_old_shape_is_read_back(tmp_path: Path) -> None:
    """Written before ticket 137: an entry per key with a conversation, nothing else."""
    old = {
        "threads": [
            {"platform": "slack", "chat_id": "C1", "thread_id": "T1", "conversation": "conv9"},
            {"platform": "telegram", "chat_id": "42", "thread_id": None, "conversation": "c7"},
        ]
    }
    (tmp_path / IM_DIR).mkdir()
    (tmp_path / IM_DIR / THREADS_FILE).write_text(json.dumps(old), encoding="utf-8")
    adapter, conversations, _, _ = await bot(tmp_path, Platform())
    await adapter.on_message(plain("And last year?"))
    assert conversations.sent == [("conv9", "And last year?")]
    await adapter.on_message(new())
    await adapter.on_message(plain("Top customers?"))
    assert conversations.sent[-1] == ("conv1", "Top customers?")
    data = json.loads((tmp_path / IM_DIR / THREADS_FILE).read_text(encoding="utf-8"))
    telegram = {"platform": "telegram", "chat_id": "42", "thread_id": None, "conversation": "c7"}
    assert telegram in data["threads"]


async def test_after_new_a_chat_without_threads_still_needs_the_mention(tmp_path: Path) -> None:
    platform = Platform("telegram")
    adapter, conversations, _, _ = await bot(tmp_path, platform)
    await adapter.on_message(mention("How many orders?", thread=None, platform="telegram"))
    await adapter.on_message(new(platform="telegram", thread=None))
    sent = len(platform.sent)
    await adapter.on_message(plain("Top customers?", "telegram", thread=None))
    assert conversations.created == ["conv1"]
    assert len(conversations.sent) == 1
    assert len(platform.sent) == sent
