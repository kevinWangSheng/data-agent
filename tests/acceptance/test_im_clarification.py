"""IM entry, clarifications (.scratch/im/spec.md "澄清、进度、取消"; "Testing Decisions" 澄清).

Written by ticket 130. The tests marked TICKET_131 are xfail(strict) until ticket 131 makes them
pass and removes the marker, changing nothing else.

The contract (data_agent.im.adapter, human-reviewed): a `clarification` event becomes one
message with the question and its options numbered, one option to a line starting with its
number; with buttons, one per option showing only its number (a button's value is at most 64
bytes of ASCII). Pressing button k is `send()` of option k's text in the turn's conversation; a
typed reply in the thread is `send()` of what was typed. The turn is the chat fixture's
(tests/fixtures/chat/conversation/20261117T100000Z-a1a1a1, read only): it ends with a successful
`result` right after its `clarification`, with no `answer`.
"""

import re
from pathlib import Path

import pytest
from im_support import (
    CLARIFICATION_RUN,
    Bot,
    FakePlatform,
    Sent,
    Shown,
    append_events,
    fixture_events,
    message,
    start_bot,
)

TICKET_131 = pytest.mark.xfail(
    strict=True,
    raises=(NotImplementedError, AssertionError),
    reason="ticket 131: the IM adapter is not implemented yet",
)

EVENTS = fixture_events(CLARIFICATION_RUN)
[CLARIFICATION] = [event for event in EVENTS if event["type"] == "clarification"]
QUESTION: str = CLARIFICATION["question"]
OPTIONS: list[str] = CLARIFICATION["options"]


async def ask(bot: Bot) -> Shown:
    """Starts a turn, writes the fixture's events, and returns the clarification's message."""
    await bot.platform.deliver(message("What was the 2024 revenue?", thread="T1"))
    run_id = bot.conversations.latest_run(bot.conversations.created[0])
    append_events(bot.runs, run_id, EVENTS)
    bot.clock.advance(60)
    await bot.adapter.poll()
    asked = [shown for shown in bot.platform.in_thread("C1", "T1") if QUESTION in shown.text]
    assert len(asked) == 1, "no single message gives the question"
    return asked[0]


def assert_numbered(text: str) -> None:
    for number, option in enumerate(OPTIONS, start=1):
        line = rf"^\W*{number}\b.*{re.escape(option)}"
        assert re.search(line, text, re.MULTILINE), f"option {number} is not on a numbered line"


@TICKET_131
async def test_the_question_and_its_numbered_options_with_number_buttons(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs")
    asked = await ask(bot)
    assert_numbered(asked.text)
    assert [button.label for button in asked.buttons] == [
        str(number) for number in range(1, len(OPTIONS) + 1)
    ]
    for button in asked.buttons:
        assert not any(option in button.label for option in OPTIONS)
        assert button.value.isascii()
        assert len(button.value.encode()) <= 64


@TICKET_131
@pytest.mark.parametrize("number", [1, 2])
async def test_pressing_a_number_sends_that_option(tmp_path: Path, number: int) -> None:
    bot = await start_bot(tmp_path / "runs")
    asked = await ask(bot)
    [conversation] = bot.conversations.created
    await bot.platform.press(asked, asked.buttons[number - 1])
    assert bot.conversations.sent[1:] == [Sent(conversation, OPTIONS[number - 1], True)]


@TICKET_131
async def test_a_typed_reply_is_sent_as_it_is(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs")
    await ask(bot)
    [conversation] = bot.conversations.created
    await bot.platform.deliver(message("Net, please", thread="T1", mentioned=False))
    assert bot.conversations.sent[1:] == [Sent(conversation, "Net, please", True)]


@TICKET_131
async def test_without_buttons_only_the_numbered_text(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs", platform=FakePlatform(buttons=False))
    asked = await ask(bot)
    assert_numbered(asked.text)
    assert all(not shown.buttons for shown in bot.platform.shown)
