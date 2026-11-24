"""IM entry, turns that end without an answer (.scratch/im/spec.md "出错"; "Testing Decisions"
出错).

Written by ticket 130. The tests marked TICKET_131 are xfail(strict) until ticket 131 makes them
pass and removes the marker, changing nothing else.

The contract (data_agent.im.adapter, human-reviewed): a failed `result` (the model call failed,
the budget ran out, the turn limit ran out) brings one reply saying which, with the run's id; a
run with no `result` whose record says `cancelled` or `failure` (launcher.finish) brings one
saying it was stopped, or failed, with the run's id. Each cause reads differently. The
`result` fields are the values the agent writes (agent.py `Spend.result_fields`,
`turn_limit_fields`, `result_fields` with `terminal_reason: "api_error"`).

What the bot shows at the end may be a new message or the status message edited; the tests read
every message in the thread as it is after the end, against how it was before.
"""

from pathlib import Path
from typing import Any

import pytest
from im_support import (
    EDIT_INTERVAL,
    Bot,
    append_events,
    end_without_result,
    header,
    message,
    query,
    result,
    start_bot,
)

TICKET_131 = pytest.mark.xfail(
    strict=True,
    raises=(NotImplementedError, AssertionError),
    reason="ticket 131: the IM adapter is not implemented yet",
)

FAILED_RESULTS: dict[str, dict[str, Any]] = {
    "api-error": {
        "subtype": "success",
        "is_error": True,
        "terminal_reason": "api_error",
        "api_error": "API Error: Request timed out",
    },
    "budget": {
        "subtype": "error_max_budget_usd",
        "is_error": True,
        "terminal_reason": "budget_exhausted",
    },
    "max-turns": {"subtype": "error_max_turns", "is_error": True, "terminal_reason": "max_turns"},
}


async def running_turn(tmp_path: Path) -> tuple[Bot, str, list[str]]:
    """A turn some way in; the thread's texts as they are then."""
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    run_id = bot.conversations.only_run()
    append_events(bot.runs, run_id, [*header(), *query(5)])
    bot.clock.advance(EDIT_INTERVAL + 0.1)
    await bot.adapter.poll()
    return bot, run_id, thread_texts(bot)


def thread_texts(bot: Bot) -> list[str]:
    return [shown.text for shown in bot.platform.in_thread("C1", "T1")]


async def ending(bot: Bot, before: list[str]) -> str:
    """What the end changed in the thread: new messages, and messages whose text changed."""
    for _ in range(2):
        bot.clock.advance(EDIT_INTERVAL + 0.1)
        await bot.adapter.poll()
    after = thread_texts(bot)
    changed = [
        text for index, text in enumerate(after) if index >= len(before) or text != before[index]
    ]
    assert changed, "nothing in the thread changed when the turn ended"
    return "\n".join(changed)


async def failed(tmp_path: Path, cause: str) -> tuple[str, str]:
    bot, run_id, before = await running_turn(tmp_path)
    append_events(bot.runs, run_id, [result(30, ended=30, **FAILED_RESULTS[cause])])
    return await ending(bot, before), run_id


async def without_result(tmp_path: Path, status: str) -> tuple[str, str]:
    bot, run_id, before = await running_turn(tmp_path)
    end_without_result(bot.runs, run_id, status)
    return await ending(bot, before), run_id


@TICKET_131
@pytest.mark.parametrize("cause", list(FAILED_RESULTS))
async def test_a_failed_result_is_told_with_the_run_id(tmp_path: Path, cause: str) -> None:
    told, run_id = await failed(tmp_path, cause)
    assert run_id in told


@TICKET_131
async def test_each_failed_result_reads_differently(tmp_path: Path) -> None:
    told = []
    for cause in FAILED_RESULTS:
        text, run_id = await failed(tmp_path / cause, cause)
        told.append(text.replace(run_id, "<run>"))
    assert len(set(told)) == len(FAILED_RESULTS), told


@TICKET_131
@pytest.mark.parametrize("status", ["cancelled", "failure"])
async def test_a_run_without_a_result_is_told_with_the_run_id(tmp_path: Path, status: str) -> None:
    told, run_id = await without_result(tmp_path, status)
    assert run_id in told


@TICKET_131
async def test_stopped_and_crashed_read_differently(tmp_path: Path) -> None:
    told = [
        (await without_result(tmp_path / status, status))[0] for status in ["cancelled", "failure"]
    ]
    assert told[0] != told[1]
