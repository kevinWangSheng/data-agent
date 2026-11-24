"""display_chart (ticket 119): the chart of one query_database result in the answer.

The parameters follow nao's `displayChart` input (`apps/shared/src/tools/display-chart.ts`): a
chart type, the query whose rows it draws, the x column and one or more series columns. Unlike
nao, the model never sends the data: the server takes the rows from the stored result, checks them
and returns the chart's definition with the data the web UI draws (.scratch/charts/spec.md
"`display_chart` 工具"). Written after nao's design, no code copied.
"""

import math
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Literal, TypeGuard

from pydantic import BaseModel

from data_agent.gateway import QueryResult

ChartType = Literal["bar", "line", "area", "scatter", "pie"]
# Chart types whose series may stack.
STACKABLE: tuple[ChartType, ...] = ("bar", "area")
# The most data points (rows times series) one chart draws.
MAX_POINTS = 500

DESCRIPTION = """\
Show a chart of one query_database result in your answer, under the text.

`query_id` is the id at the end of a query_database result of this run. `x` is the column on the \
x axis (for `pie`, the slice names); each of `series` is a numeric column to draw, with an \
optional legend `label` (the column's name by default). `stacked` stacks the series of a `bar` or \
`area` chart. `scatter` needs a numeric `x`; `pie` takes one series with no negative values. A \
chart draws at most 500 points (rows times series): aggregate or filter in SQL first. The rows \
come from the stored result, so query exactly the rows and columns the chart should show.
"""


class ChartSeries(BaseModel):
    column: str
    label: str | None = None


class ChartError(Exception):
    """A chart the result cannot give; the message says what to change."""


def is_number(value: object) -> TypeGuard[int | float | Decimal]:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def json_value(value: object) -> object:
    """A number stays a number (a Decimal becomes a float); another value is the text
    `format_rows` shows for it; NULL is None."""
    if value is None:
        return None
    if is_number(value):
        return float(value) if isinstance(value, Decimal) else value
    return str(value)


def chart_payload(
    results: Mapping[str, QueryResult],
    query_id: str,
    chart_type: ChartType,
    x: str,
    series: Sequence[ChartSeries],
    title: str,
    stacked: bool = False,
) -> dict[str, Any]:
    """`{"chart": the checked arguments, defaults filled in, "data": one object per row of the
    result, keyed by `x` and the series columns, in the result's order}`; ChartError when a
    check fails: the spec's list, in its order, plus a column name the result repeats, a
    series column given twice and a value JSON cannot hold (infinity, NaN)."""
    result = results.get(query_id)
    if result is None:
        known = ", ".join(results) or "none yet"
        raise ChartError(
            f"unknown query id {query_id}: display_chart takes an id query_database gave in this "
            f"run ({known})"
        )
    columns = list(result.columns)
    index = {column: i for i, column in enumerate(columns)}
    keep = [x, *(s.column for s in series)]
    for column in keep:
        if column not in index:
            raise ChartError(
                f"column {column} is not in {query_id}; its columns: {', '.join(columns)}"
            )
        if columns.count(column) > 1:
            raise ChartError(
                f"{column} is more than one column of {query_id}; give each an alias in the SQL "
                "(AS) and query again"
            )
    drawn = [s.column for s in series]
    for column in drawn:
        if drawn.count(column) > 1:
            raise ChartError(f"series column {column} is given twice; give each column once")
    # JSON has no infinity or NaN, so a chart holding one could not be read.
    for column in dict.fromkeys(keep):
        values = (row[index[column]] for row in result.rows)
        if any(is_number(v) and not math.isfinite(v) for v in values):
            raise ChartError(
                f"{column} has values that are not finite (infinity or NaN, as from a division "
                "by zero); filter them out or make them NULL in the SQL"
            )

    def numeric(column: str) -> bool:
        return all(
            row[index[column]] is None or is_number(row[index[column]]) for row in result.rows
        )

    for s in series:
        if not numeric(s.column):
            raise ChartError(f"series column {s.column} is not numeric; a series draws numbers")
    if chart_type == "scatter" and not numeric(x):
        raise ChartError(f"a scatter chart needs a numeric x column; {x} is not numeric")
    if chart_type == "pie":
        if len(series) != 1:
            raise ChartError(f"a pie chart takes one series, not {len(series)}")
        column = index[series[0].column]
        if any(row[column] is not None and row[column] < 0 for row in result.rows):
            raise ChartError(
                f"a pie chart cannot show negative values, and {series[0].column} has some"
            )
    points = len(result.rows) * len(series)
    if points > MAX_POINTS:
        raise ChartError(
            f"{points} data points ({len(result.rows)} rows x {len(series)} series); a chart "
            f"draws at most {MAX_POINTS}: aggregate or filter in SQL first"
        )
    if stacked and chart_type not in STACKABLE:
        raise ChartError(f"stacked goes with {' or '.join(STACKABLE)} charts, not {chart_type}")
    return {
        "chart": {
            "query_id": query_id,
            "chart_type": chart_type,
            "x": x,
            "series": [{"column": s.column, "label": s.label or s.column} for s in series],
            "stacked": stacked,
            "title": title,
        },
        "data": [
            {column: json_value(row[index[column]]) for column in keep} for row in result.rows
        ],
    }
