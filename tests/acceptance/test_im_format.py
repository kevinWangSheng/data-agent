"""IM entry, the answer's layout (.scratch/im/spec.md "答案排版"; "Testing Decisions" 排版).

Written by ticket 130. The tests marked TICKET_131 are xfail(strict) until ticket 131 makes them
pass and removes the marker, changing nothing else.

The contract (data_agent.im.format, human-reviewed; its docstring has the rules):

- `split_message(text, limit)`: between paragraphs, each part within the limit, the parts joined
  by a blank line giving the text again; a paragraph over the limit split between lines (or
  characters); a code block that fits in one message never split.
- `tables_to_code`: a pipe table as a fenced code block; past `TABLE_ROWS` data rows the first
  `TABLE_ROWS` are kept and a line after the block gives the total.
- `chart_count`: the charts the web UI's answer shows: the main agent's successful
  `display_chart` calls whose result parses as a chart. The charts fixture
  (tests/fixtures/charts/run/events.jsonl, read only) has 6 calls, 5 of them successful.
- `format_answer`: with charts, a line giving their number; without, the answer as it is.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from im_support import (
    CHARTS_RUN,
    MAX_CHARS,
    answer,
    append_events,
    fixture_events,
    header,
    message,
    result,
    start_bot,
)

from data_agent.im.format import (
    DISPLAY_CHART,
    TABLE_ROWS,
    chart_count,
    format_answer,
    split_message,
    tables_to_code,
)

TICKET_131 = pytest.mark.xfail(
    strict=True,
    raises=(NotImplementedError, AssertionError),
    reason="ticket 131: the IM adapter is not implemented yet",
)

LIMIT = 200
FENCE = "```"


def paragraph(number: int, length: int = 90) -> str:
    words = f"Paragraph {number} says something about the orders of the month. "
    return (words * (length // len(words) + 1))[:length].strip() + "."


def squeezed(text: str) -> str:
    return "".join(text.split())


def outside_fences(text: str) -> str:
    return "\n".join(text.split(FENCE)[0::2])


def inside_fences(text: str) -> str:
    return "\n".join(text.split(FENCE)[1::2])


def table(rows: int) -> str:
    lines = ["| item | amount |", "|---|---|"]
    lines += [f"| item-r{number:03d} | {number * 3}.5 |" for number in range(1, rows + 1)]
    return "\n".join(lines)


@TICKET_131
def test_a_long_answer_is_split_between_paragraphs_within_the_limit() -> None:
    text = "\n\n".join(paragraph(number) for number in range(1, 13))
    parts = split_message(text, LIMIT)
    assert len(parts) > 1
    assert all(len(part) <= LIMIT for part in parts)
    assert "\n\n".join(parts) == text


@TICKET_131
def test_a_paragraph_over_the_limit_is_split_and_nothing_is_lost() -> None:
    lines = "\n".join(
        f"Line {number} of a long paragraph, with no blank line." for number in range(12)
    )
    word = "x" * (LIMIT * 2 + 17)
    text = f"{paragraph(1)}\n\n{lines}\n\n{word}"
    parts = split_message(text, LIMIT)
    assert all(len(part) <= LIMIT for part in parts)
    assert squeezed("".join(parts)) == squeezed(text)


@TICKET_131
def test_a_code_block_that_fits_is_never_split() -> None:
    code = FENCE + "\n" + "\n".join(f"SELECT {number} AS n;" for number in range(6)) + "\n" + FENCE
    text = f"{paragraph(1, 150)}\n\n{code}\n\n{paragraph(2, 150)}"
    if len(code) > LIMIT:
        raise RuntimeError("the code block is longer than the limit")
    parts = split_message(text, LIMIT)
    assert all(len(part) <= LIMIT for part in parts)
    assert any(code in part for part in parts)


@TICKET_131
def test_a_small_table_becomes_a_code_block_with_every_row() -> None:
    text = f"The amounts:\n\n{table(5)}\n\nThat is all."
    converted = tables_to_code(text)
    assert FENCE in converted
    assert "|---" not in converted
    inside = inside_fences(converted)
    for number in range(1, 6):
        assert f"item-r{number:03d}" in inside
    assert "The amounts:" in outside_fences(converted)
    assert "That is all." in outside_fences(converted)


@TICKET_131
def test_a_long_table_is_cut_and_its_total_given() -> None:
    converted = tables_to_code(f"The amounts:\n\n{table(137)}")
    inside = inside_fences(converted)
    kept = re.findall(r"item-r(\d{3})", inside)
    assert kept == [f"{number:03d}" for number in range(1, TABLE_ROWS + 1)]
    assert re.search(r"(?<!\d)137(?!\d)", outside_fences(converted))


def chart_call(tool_use_id: str, parent: str | None, content: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "tool_start",
            "ts": "2026-11-23T10:00:40+00:00",
            "tool": DISPLAY_CHART,
            "tool_use_id": tool_use_id,
            "parent": parent,
            "input": {},
        },
        {
            "type": "tool_end",
            "ts": "2026-11-23T10:00:41+00:00",
            "tool": DISPLAY_CHART,
            "tool_use_id": tool_use_id,
            "parent": parent,
            "is_error": False,
            "content": content,
        },
    ]


@TICKET_131
def test_charts_are_counted_as_the_web_ui_shows_them() -> None:
    events = fixture_events(CHARTS_RUN)
    assert chart_count(events) == 5
    # One more by the main agent counts; a verifier's, and one whose result is no chart, do not.
    [successful] = [e for e in events if e.get("tool_use_id") == "t-c1" and e["type"] == "tool_end"]
    payload = successful["content"]
    if not isinstance(payload, str) or "chart" not in json.loads(payload):
        raise RuntimeError("the fixture's first chart call has no chart payload")
    more = [
        *chart_call("t-main", None, payload),
        *chart_call("t-sub", "t-verifier", payload),
        *chart_call("t-text", None, "The chart is shown."),
    ]
    assert chart_count([*events, *more]) == 6


@TICKET_131
def test_an_answer_with_charts_says_how_many() -> None:
    text = "Gold customers bring the most revenue."
    with_charts = format_answer(text, fixture_events(CHARTS_RUN), MAX_CHARS)
    joined = "\n".join(with_charts)
    assert text in joined
    assert re.search(r"(?<![\d.])5(?![\d.])", joined.replace(text, ""))

    without = format_answer(text, [*header(), answer(20, text), result(30)], MAX_CHARS)
    assert without == [text]


@TICKET_131
async def test_the_bot_sends_a_long_answer_in_parts_within_the_platforms_limit(
    tmp_path: Path,
) -> None:
    text = "\n\n".join(paragraph(number, 120) for number in range(1, 9))
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("Summarize the month", thread="T1"))
    run_id = bot.conversations.only_run()
    append_events(bot.runs, run_id, [*header(), answer(20, text), result(30)])
    bot.clock.advance(60)
    await bot.adapter.poll()
    sends = [sent.text for sent in bot.platform.sends()]
    parts = [sent for sent in sends if "Paragraph" in sent]
    assert len(parts) > 1
    assert all(len(sent) <= MAX_CHARS for sent in sends)
    assert "\n\n".join(parts) == text
