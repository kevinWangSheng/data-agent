"""An answer as IM messages (.scratch/im/spec.md "答案排版"; contract item 5 of ticket 130).

- `split_message`: a text longer than the platform's limit goes out as several messages, split
  between paragraphs (blank lines), each as long as fits. The blank line at a split is dropped
  and nothing else, so the parts joined with a blank line are the text again. A paragraph longer
  than the limit by itself is split between lines, and a line longer than the limit between
  characters. A fenced code block is never split while it fits in one message; one that does
  not is split between lines, each part fenced again.
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

from collections.abc import Iterable, Mapping
from typing import Any

TABLE_ROWS = 20
DISPLAY_CHART = "mcp__data__display_chart"


def split_message(text: str, limit: int) -> list[str]:
    raise NotImplementedError("ticket 131")


def tables_to_code(text: str, max_rows: int = TABLE_ROWS, limit: int | None = None) -> str:
    raise NotImplementedError("ticket 131")


def chart_count(events: Iterable[Mapping[str, Any]]) -> int:
    raise NotImplementedError("ticket 131")


def format_answer(text: str, events: Iterable[Mapping[str, Any]], limit: int) -> list[str]:
    """The messages for the answer `text` of the run whose events are `events`."""
    raise NotImplementedError("ticket 131")
