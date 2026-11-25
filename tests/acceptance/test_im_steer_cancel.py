"""IM entry, steering and stopping (.scratch/im/spec.md "澄清、进度、取消"; "Testing Decisions"
插话与取消).

Written by ticket 130. The tests marked TICKET_131 are xfail(strict) until ticket 131 makes them
pass and removes the marker, changing nothing else.

The contract (data_agent.im.adapter, human-reviewed):

- A message while the conversation's turn runs is `send()` (the chat backend makes it a steer,
  `{"kind": "steer"}`), no `create()`. Whether the bot acknowledges a steer is not fixed.
- The status message has one button, Stop (with buttons); pressing it is `cancel()` of the
  turn's conversation.
- A message whose text is `stop` (blanks around it and case aside; the platform has taken the
  mention out) is `cancel()`, never `send()`. "stop the query" is an ordinary message.
- `stop` with no turn running (`cancel()` refused with 409): a reply that nothing runs.
- `send()` refused (409 while another run goes, 400 with no data source): a reply with the
  refusal's detail; the conversation stays mapped, and the next message in the thread goes to it.
"""

from pathlib import Path

import pytest
from im_support import (
    EDIT_INTERVAL,
    Sent,
    end_without_result,
    expect_status,
    expect_stop_button,
    finish_turn,
    message,
    start_bot,
)

from data_agent.web.launcher import Refused


async def test_a_message_while_the_turn_runs_is_sent_as_a_steer(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    [conversation] = bot.conversations.created

    await bot.platform.deliver(message("Only paid ones", thread="T1", mentioned=False))
    assert bot.conversations.sent[-1] == Sent(conversation, "Only paid ones", True)
    assert bot.conversations.created == [conversation]
    # Still the one turn: the stand-in answered a steer.
    bot.conversations.only_run()


async def test_the_stop_button_cancels_the_turn(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    [conversation] = bot.conversations.created
    status = expect_status(bot)
    running = status.text
    await bot.platform.press(status, expect_stop_button(status))
    assert bot.conversations.cancelled == [conversation]
    assert len(bot.conversations.sent) == 1

    # The launcher records the run as cancelled (it wrote no `result`): the status says it ended.
    end_without_result(bot.runs, bot.conversations.only_run(), "cancelled")
    for _ in range(2):
        bot.clock.advance(EDIT_INTERVAL + 0.1)
        await bot.adapter.poll()
    assert status.text != running
    assert status.buttons == ()


@pytest.mark.parametrize("text", ["stop", "STOP", "  Stop \n"])
async def test_the_word_stop_cancels_and_is_not_sent(tmp_path: Path, text: str) -> None:
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    [conversation] = bot.conversations.created
    await bot.platform.deliver(message(text, thread="T1", mentioned=False))
    assert bot.conversations.cancelled == [conversation]
    assert len(bot.conversations.sent) == 1


async def test_a_sentence_with_stop_in_it_is_an_ordinary_message(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    [conversation] = bot.conversations.created
    await bot.platform.deliver(message("stop the query", thread="T1", mentioned=False))
    assert bot.conversations.sent[-1] == Sent(conversation, "stop the query", True)
    assert bot.conversations.cancelled == []


async def test_stop_with_no_turn_running_gets_a_reply(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    [conversation] = bot.conversations.created
    finish_turn(bot, bot.conversations.latest_run(conversation))
    bot.clock.advance(60)
    await bot.adapter.poll()
    shown = len(bot.platform.shown)

    await bot.platform.deliver(message("stop", thread="T1", mentioned=False))
    assert len(bot.platform.shown) == shown + 1
    assert bot.platform.shown[-1] in bot.platform.in_thread("C1", "T1")
    assert len(bot.conversations.sent) == 1


@pytest.mark.parametrize(
    "refusal",
    [
        Refused(409, "run 20261124T085959Z-otherr is still going; one run at a time"),
        Refused(400, "no data source is configured; start the server with --data-sources FILE"),
    ],
    ids=["busy", "no-source"],
)
async def test_a_refused_send_is_told_and_the_conversation_is_kept(
    tmp_path: Path, refusal: Refused
) -> None:
    bot = await start_bot(tmp_path / "runs")
    bot.conversations.refusals.append(refusal)
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    [conversation] = bot.conversations.created
    replies = bot.platform.in_thread("C1", "T1")
    assert len(replies) == 1
    assert refusal.detail in replies[0].text

    await bot.platform.deliver(message("Try again, please", thread="T1", mentioned=False))
    assert bot.conversations.created == [conversation]
    assert len(bot.conversations.sent) == 2
    assert bot.conversations.sent[-1] == Sent(conversation, "Try again, please", True)
