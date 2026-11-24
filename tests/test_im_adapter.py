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

    def create(self) -> str:
        if self.fail:
            raise OSError("disk full")
        self.created.append(f"conv{len(self.created) + 1}")
        return self.created[-1]

    def send(self, conversation: str, text: str, network: bool = True) -> dict[str, Any]:
        self.sent.append((conversation, text))
        run_id = f"run{len(self.sent)}"
        (self.runs.path / run_id).mkdir()
        self.runs.write_record(run_id, {"id": run_id, "status": "running"})
        return {"kind": "turn", "turn": 1, "run_id": run_id}

    def cancel(self, conversation: str) -> dict[str, Any]:
        return {}


def mention(text: str, thread: str = "T1", platform: str = "slack") -> Incoming:
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
