"""IM entry, progress (.scratch/im/spec.md "澄清、进度、取消", "出错"; "Testing Decisions" 进度).

Written by ticket 130. The tests marked TICKET_131 are xfail(strict) until ticket 131 makes them
pass and removes the marker, changing nothing else.

The contract (data_agent.im.progress, data_agent.im.adapter; human-reviewed):

- `status_text(events)` is the turn's current step while it runs, and changes with the steps;
  once the turn has a `result` it is the end, unlike any running text, with the duration (first
  event's `ts` to `result.ended_ts`, or `result.ts` without it) and the cost.
- A turn sends one status message when it starts and edits it as the events come, at most once
  per `Limits.edit_interval` of the adapter's clock; the answer goes in a message of its own.
- Without edits: no status message, no edit; the answer only.

The wording is ticket 131's: the tests compare texts with each other and look for numbers. The
numbers are picked to be found nowhere else: 37 seconds to `ended_ts`, 51 to `result.ts`, a cost
of 0.25; the run ids have neither.
"""

import re
from pathlib import Path

import pytest
from im_support import (
    EDIT_INTERVAL,
    FakePlatform,
    answer,
    append_events,
    expect_status,
    header,
    message,
    query,
    query_end,
    result,
    start_bot,
    verifier_start,
)

from data_agent.im.progress import status_text

TICKET_131 = pytest.mark.xfail(
    strict=True,
    raises=(NotImplementedError, AssertionError),
    reason="ticket 131: the IM adapter is not implemented yet",
)

ANSWER = "Fourteen orders were placed, all of them paid."


def has_number(text: str, number: str) -> bool:
    return re.search(rf"(?<![\d.]){re.escape(number)}(?![\d])", text) is not None


@TICKET_131
def test_the_status_text_follows_the_steps_and_ends_with_duration_and_cost() -> None:
    events = header()
    started = status_text(events)
    events += query(5)
    querying = status_text(events)
    events += [query_end(8), verifier_start(10)]
    verifying = status_text(events)
    events += [answer(20, ANSWER)]
    answering = status_text(events)
    running = [started, querying, verifying, answering]
    assert querying != started
    assert verifying != querying
    assert answering != verifying

    done = status_text([*events, result(51, ended=37)])
    assert done not in running
    assert has_number(done, "37")
    assert not has_number(done, "51")
    assert "0.25" in done
    # Without `ended_ts` (before ticket 122), the duration runs to `result.ts`.
    assert has_number(status_text([*events, result(51)]), "51")


@TICKET_131
def test_a_turn_that_ends_on_a_clarification_does_not_read_as_answered() -> None:
    asked = [
        *header(),
        {
            "type": "clarification",
            "ts": "2026-11-24T09:00:05+00:00",
            "tool_use_id": "c1",
            "question": "Net or gross?",
            "options": ["Net", "Gross"],
        },
    ]
    answered = [*header(), answer(5, ANSWER)]
    waiting = status_text([*asked, result(51, ended=37)])
    assert waiting not in {status_text(asked), status_text(answered)}
    assert waiting != status_text([*answered, result(51, ended=37)])


@TICKET_131
async def test_one_status_message_edited_through_the_turn_then_the_answer(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    run_id = bot.conversations.only_run()
    status = expect_status(bot)
    assert len(bot.platform.in_thread("C1", "T1")) == 1
    texts = [status.text]

    for events in ([*header(), *query(5)], [query_end(8), verifier_start(10)]):
        append_events(bot.runs, run_id, events)
        bot.clock.advance(EDIT_INTERVAL + 0.1)
        await bot.adapter.poll()
        assert status.text != texts[-1], "the status did not change with the step"
        texts.append(status.text)

    append_events(bot.runs, run_id, [answer(20, ANSWER), result(51, ended=37)])
    bot.clock.advance(EDIT_INTERVAL + 0.1)
    await bot.adapter.poll()
    bot.clock.advance(EDIT_INTERVAL + 0.1)
    await bot.adapter.poll()
    assert status.text not in texts
    assert has_number(status.text, "37")
    assert "0.25" in status.text
    assert status.buttons == ()
    thread = bot.platform.in_thread("C1", "T1")
    assert [shown.text for shown in thread[1:]] == [ANSWER]


@TICKET_131
async def test_edits_wait_out_the_platforms_interval(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    run_id = bot.conversations.only_run()
    status = expect_status(bot)

    append_events(bot.runs, run_id, [*header(), *query(5)])
    bot.clock.advance(EDIT_INTERVAL + 0.1)
    await bot.adapter.poll()
    assert len(bot.platform.edits()) == 1
    first = status.text

    # Two more steps within one interval: no edit yet.
    for events in ([query_end(8), verifier_start(10)], [answer(20, ANSWER)]):
        append_events(bot.runs, run_id, events)
        bot.clock.advance(EDIT_INTERVAL / 4)
        await bot.adapter.poll()
    assert len(bot.platform.edits()) == 1

    # Past the interval the status catches up, in one edit.
    bot.clock.advance(EDIT_INTERVAL)
    await bot.adapter.poll()
    assert len(bot.platform.edits()) == 2
    assert status.text != first


@TICKET_131
async def test_without_edits_only_the_answer_is_sent(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs", platform=FakePlatform(edit=False))
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    run_id = bot.conversations.only_run()
    steps = [[*header(), *query(5)], [query_end(8), verifier_start(10)]]
    for events in [*steps, [answer(20, ANSWER), result(51, ended=37)]]:
        append_events(bot.runs, run_id, events)
        bot.clock.advance(EDIT_INTERVAL + 0.1)
        await bot.adapter.poll()
    assert [sent.text for sent in bot.platform.sends()] == [ANSWER]
    assert bot.platform.edits() == []
