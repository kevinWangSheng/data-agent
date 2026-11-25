"""An answer as IM messages (.scratch/im/spec.md "答案排版"; contract item 5 of ticket 130).

- `split_message`: a text longer than the platform's limit goes out as several messages, split
  between paragraphs (blank lines), each as long as fits. The blank line at a split is dropped
  and nothing else, so the parts joined with a blank line are the text again. A paragraph longer
  than the limit by itself is split between lines, and a line longer than the limit between
  characters. A fenced code block is never split while it fits in one message; one that does
  not is split between lines, each part fenced again.
- Code fences, here and on every platform (`fence_after`), as CommonMark reads them: three or
  more backticks open a block when the info string after them holds no backtick, so a line with
  its code closed on it (```` ```SELECT 1``` ````) opens none; a block is closed by a run at
  least as long as its opening's with nothing after it.
- `tables_to_code`: a Markdown pipe table becomes a fenced code block of its rows, columns
  padded to line up. A table of more than `TABLE_ROWS` data rows keeps its header and its first
  `TABLE_ROWS` rows, and a line after the block gives the total number of rows. Given a `limit`,
  a table whose block and that line would not fit in one message keeps fewer rows, as many as
  fit, and the line gives the total too.
- `chart_count`: the charts the web UI's answer shows (`answerCharts`,
  web/src/chart/model.ts): `display_chart` calls of the main agent (`parent` null) whose
  `tool_end` is no error and whose content parses as a chart (`chart` with `chart_type`, `x`,
  `title` and `series`, and a `data` list).
- `format_answer`: tables as code (within `limit`), then, when `chart_count` is not 0, a closing
  line saying the answer has that many charts to see in the web UI, then split.
"""

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

TABLE_ROWS = 20
DISPLAY_CHART = "mcp__data__display_chart"

FENCE = "```"
# A fence's backtick run and what follows it on the line (the info string, when it opens).
FENCE_LINE = re.compile(r"^\s*(?P<ticks>`{3,})(?P<info>.*)$")
# A pipe table's delimiter row: `|---|:---:|`, with or without the outer pipes.
DELIMITER = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$")
# A pipe not escaped as `\|`.
CELL_PIPE = re.compile(r"(?<!\\)\|")
# The chart types the web UI draws (web/src/chart/model.ts `CHART_TYPES`).
CHART_TYPES = ("bar", "line", "area", "scatter", "pie")


def split_message(text: str, limit: int) -> list[str]:
    if limit < 1:
        raise ValueError(f"a message limit of {limit}")
    parts: list[str] = []
    current: str | None = None
    for block in paragraphs(text):
        if len(block) > limit:
            if current is not None:
                parts.append(current)
                current = None
            parts += split_lines(block, limit)
        elif current is None:
            current = block
        elif len(current) + 2 + len(block) <= limit:
            current += "\n\n" + block
        else:
            parts.append(current)
            current = block
    if current is not None and current != "":
        parts.append(current)
    return parts


def fence_after(line: str, opening: str) -> str | None:
    """For a line read in order, `opening` the backtick run of the code block it is in (""
    outside one): None when the line is no fence there, else the run of the block after it, its
    own when it opens one and "" when it closes one."""
    fence = FENCE_LINE.match(line)
    if fence is None:
        return None
    ticks, info = fence["ticks"], fence["info"]
    if not opening:
        return ticks if "`" not in info else None
    return "" if len(ticks) >= len(opening) and not info.strip() else None


def fences_after(lines: Iterable[str], opening: str = "") -> str:
    """The backtick run of the code block open after the lines ("" when none is)."""
    for line in lines:
        after = fence_after(line, opening)
        if after is not None:
            opening = after
    return opening


def paragraphs(text: str) -> list[str]:
    """The text between blank lines; a fenced code block is one piece with its blank lines."""
    blocks: list[str] = []
    opening = ""
    for piece in text.split("\n\n"):
        if blocks and opening:
            blocks[-1] += "\n\n" + piece
        else:
            blocks.append(piece)
        opening = fences_after(piece.split("\n"), opening)
    return blocks


def split_lines(block: str, limit: int) -> list[str]:
    """A block over the limit, split between lines (a line over it between characters). A part
    that ends inside a fenced code block closes it, and the next part opens it again."""
    parts: list[str] = []
    lines: list[str] = []
    # The opening line and the backtick run of the code block the current part is in, else
    # None and "".
    fence: str | None = None
    opening = ""
    content = False

    def size() -> int:
        return len("\n".join(lines))

    def fits(line: str, closing: int) -> bool:
        return size() + (1 if lines else 0) + len(line) + closing <= limit

    def flush() -> None:
        nonlocal lines, content
        if content:
            parts.append("\n".join([*lines, opening] if fence is not None else lines))
        lines = [fence] if fence is not None else []
        content = False

    for line in block.split("\n"):
        ticks = fence_after(line, opening)
        if ticks is None:
            # No fence: the part stays in (or out of) the block it is in.
            after_ticks, after = opening, fence
        elif ticks:
            after_ticks, after = ticks, line
        else:
            after_ticks, after = "", None
        closing = len(after_ticks) + 1 if after is not None else 0
        if not fits(line, closing):
            flush()
        if fits(line, closing):
            lines.append(line)
            content = True
        else:
            room = max(limit - closing - (size() + 1 if lines else 0), 1)
            for start in range(0, len(line), room):
                lines.append(line[start : start + room])
                content = True
                if start + room < len(line):
                    flush()
        fence, opening = after, after_ticks
    if content:
        parts.append("\n".join(lines))
    return parts


def tables_to_code(text: str, max_rows: int = TABLE_ROWS, limit: int | None = None) -> str:
    lines = text.split("\n")
    out: list[str] = []
    opening = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        after = fence_after(line, opening)
        if after is not None:
            opening = after
        elif (
            not opening
            and is_row(line)
            and index + 1 < len(lines)
            and DELIMITER.match(lines[index + 1].strip())
        ):
            header = cells(line)
            end = index + 2
            rows: list[list[str]] = []
            while end < len(lines) and is_row(lines[end]):
                rows.append(cells(lines[end]))
                end += 1
            # A paragraph of its own, so splitting keeps the block whole.
            if out and out[-1].strip():
                out.append("")
            out.append(table_block(header, rows, max_rows, limit))
            if end < len(lines) and lines[end].strip():
                out.append("")
            index = end
            continue
        out.append(line)
        index += 1
    return "\n".join(out)


def is_row(line: str) -> bool:
    return "|" in line and bool(line.strip())


def cells(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [cell.strip().replace("\\|", "|") for cell in CELL_PIPE.split(row)]


def table_block(header: list[str], rows: list[list[str]], max_rows: int, limit: int | None) -> str:
    """The table as a code block of its first rows, as many as `max_rows` and `limit` allow (at
    least one), and a line giving the total when rows were left out."""
    keep = min(len(rows), max_rows)
    while True:
        block = code_block(header, rows[:keep])
        if keep < len(rows):
            block += f"\n({len(rows)} rows; the first {keep} shown)"
        if limit is None or len(block) <= limit or keep <= 1:
            return block
        keep -= 1


def code_block(header: list[str], rows: list[list[str]]) -> str:
    columns = max(len(row) for row in [header, *rows])
    table = [row + [""] * (columns - len(row)) for row in [header, *rows]]
    widths = [max(len(row[column]) for row in table) for column in range(columns)]

    def render(row: list[str]) -> str:
        return " | ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))

    delimiter = "-+-".join("-" * width for width in widths)
    body = [render(table[0]).rstrip(), delimiter, *(render(row).rstrip() for row in table[1:])]
    return "\n".join([FENCE, *body, FENCE])


def chart_count(events: Iterable[Mapping[str, Any]]) -> int:
    events = list(events)
    started = {
        event.get("tool_use_id"): event for event in events if event.get("type") == "tool_start"
    }
    counted: set[Any] = set()
    for event in events:
        if event.get("type") != "tool_end" or event.get("tool") != DISPLAY_CHART:
            continue
        tool_use_id = event.get("tool_use_id")
        start = started.get(tool_use_id, {})
        parent = event["parent"] if "parent" in event else start.get("parent")
        content = event.get("content")
        if (
            tool_use_id not in counted
            and parent is None
            and not event.get("is_error")
            and isinstance(content, str)
            and is_chart(content)
        ):
            counted.add(tool_use_id)
    return len(counted)


def is_chart(text: str) -> bool:
    """Whether a `display_chart` result is a chart the web UI draws (`chartPayload`)."""
    try:
        value = json.loads(text)
    except ValueError:
        return False
    if not isinstance(value, dict):
        return False
    chart, data = value.get("chart"), value.get("data")
    if not isinstance(chart, dict) or not isinstance(data, list):
        return False
    series = chart.get("series")
    return (
        chart.get("chart_type") in CHART_TYPES
        and isinstance(chart.get("x"), str)
        and isinstance(chart.get("title"), str)
        and isinstance(series, list)
        and bool(series)
        and all(
            isinstance(one, dict)
            and isinstance(one.get("column"), str)
            and isinstance(one.get("label"), str)
            for one in series
        )
        and all(isinstance(row, dict) for row in data)
    )


def format_answer(text: str, events: Iterable[Mapping[str, Any]], limit: int) -> list[str]:
    """The messages for the answer `text` of the run whose events are `events`."""
    body = tables_to_code(text, limit=limit)
    charts = chart_count(events)
    if charts:
        noun = "chart" if charts == 1 else "charts"
        body += f"\n\nThis answer has {charts} {noun}; open the run in the web UI to see them."
    return split_message(body, limit)
