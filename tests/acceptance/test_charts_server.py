"""Answer charts, the MCP server (.scratch/charts/spec.md "Testing Decisions": query ids and the
`display_chart` checks), driven by a real MCP client over stdio as test_seam_b_mcp_server.py does.

Written by ticket 118. Every test is `TICKET_119` until ticket 119 builds the feature, which then
drops the marks and changes nothing else.

Contract fixed here (ticket 118, "本工单定下的契约" 2-6):

- `data-agent mcp-server --charts` (off by default) serves `display_chart`, with one database or
  several (`--source`), with or without `--sandbox`.
- Query ids: in a server with `--charts`, the text of every successful `query_database` result
  ends with a line the web UI's pattern `^\\[result id: [^\\]]+\\]$` matches, starting with
  `[result id: query_N` followed by `;` or `]`. Ids count from `query_1` up by one, one numbering
  across the server's databases. The wording after the id is ticket 119's.
- `display_chart(query_id, chart_type, x, series, stacked=false, title)`: `chart_type` one of bar,
  line, area, scatter, pie; `series` one or more `{column, label?}`; `title` required; no
  `database` argument with several databases; annotated read-only.
- A check that fails is an MCP error result (`isError`), with the text each case below asks for.
  "Numeric" is int, float or Decimal for every value that is not NULL (bool is not).
- Success returns a dict, so the event's `tool_end.content` is its JSON:
  `{"chart": <the arguments checked, with label defaulting to the column and stacked to false>,
  "data": [one object per row, keyed by x and the series columns only, in the query's order]}`.
  Numbers are JSON numbers (Decimal too); another value that is not NULL is `str(value)` as
  `format_rows` shows it (a DATE is "2024-01-01"); NULL is null.
- tests/fixtures/charts/run/ is a query run with those calls in it: sent again to a real server
  with `--charts`, each `display_chart` returns what the fixture recorded.
"""

import functools
import json
import os
import re
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from seam_support import DATA_AGENT, FIXTURES

TICKET_119 = pytest.mark.xfail(
    strict=True, reason="ticket 119: mcp-server --charts and display_chart are not built yet"
)
pytestmark = TICKET_119

RUN = FIXTURES / "charts" / "run"
# The web UI's pattern for the line (web/src/run-view/model.ts, RESULT_ID_NOTE).
RESULT_ID_NOTE = r"^\[result id: [^\]]+\]$"
CHART_TYPES = {"bar", "line", "area", "scatter", "pie"}
MAX_POINTS = 500


@functools.cache
def server_help() -> str:
    done = subprocess.run(
        [str(DATA_AGENT), "mcp-server", "--help"], capture_output=True, text=True, check=True
    )
    return done.stdout


@asynccontextmanager
async def mcp_server(*args: str) -> AsyncIterator[Any]:
    """A server with `--charts`. Its option is looked for first, so a missing feature reads as
    such, not as a server that failed to start."""
    assert "--charts" in server_help(), "ticket 119: mcp-server has no --charts yet"
    from mcp import ClientSession, StdioServerParameters  # pyright: ignore[reportMissingImports]
    from mcp.client.stdio import stdio_client  # pyright: ignore[reportMissingImports]

    params = StdioServerParameters(
        command=str(DATA_AGENT),
        args=["mcp-server", "--config", "no-context", *args, "--charts"],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


async def call(session: Any, name: str, arguments: dict[str, Any]) -> Any:
    from mcp.types import CallToolResult  # pyright: ignore[reportMissingImports]

    result = await session.call_tool(name, arguments)
    assert isinstance(result, CallToolResult), f"{name} returned {type(result).__name__}"
    return result


def text_of(result: Any) -> str:
    from mcp.types import TextContent  # pyright: ignore[reportMissingImports]

    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


async def query(session: Any, sql: str, **extra: Any) -> str:
    """Runs a query that must succeed; returns its result's id from the last line."""
    result = await call(session, "query_database", {"sql": sql, **extra})
    text = text_of(result)
    assert not result.is_error, text
    return result_id(text)


def result_id(text: str) -> str:
    last = text.splitlines()[-1]
    assert re.match(RESULT_ID_NOTE, last), f"no result id line: {last!r}"
    found = re.match(r"^\[result id: (query_\d+)[;\]]", last)
    assert found, f"the id line does not start with [result id: query_N: {last!r}"
    return found.group(1)


async def chart(session: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """A display_chart call that must succeed: its result as the event records it (the JSON)."""
    result = await call(session, "display_chart", arguments)
    text = text_of(result)
    assert not result.is_error, text
    parsed = json.loads(text)
    assert isinstance(parsed, dict), text
    return parsed  # pyright: ignore[reportUnknownVariableType]


async def chart_error(session: Any, arguments: dict[str, Any]) -> str:
    result = await call(session, "display_chart", arguments)
    text = text_of(result)
    assert result.is_error, f"not an error result: {text!r}"
    assert "Traceback" not in text, text
    return text


@pytest.fixture
def empty_duckdb(tmp_path: Path) -> list[str]:
    """An empty DuckDB: every query here brings its own rows (`VALUES`, `range`)."""
    import duckdb  # pyright: ignore[reportMissingImports]

    path = tmp_path / "empty.duckdb"
    duckdb.connect(str(path)).close()
    return ["--duckdb", str(path)]


# A sales table of text, DECIMAL and INTEGER columns, with a negative one for the pie check.
SALES = (
    "SELECT tier, online, store, delta FROM (VALUES "
    "('Gold', 410.50::DECIMAL(10,2), 300, 12), ('Silver', 280.25::DECIMAL(10,2), 190, -4), "
    "('Bronze', 120.00::DECIMAL(10,2), 95, 3)) AS t(tier, online, store, delta)"
)
SALES_COLUMNS = ["tier", "online", "store", "delta"]


def points(rows: int) -> str:
    """`rows` rows of an integer x and two numeric series."""
    return f"SELECT range AS n, range * 1.5 AS a, range * 2 AS b FROM range(1, {rows + 1})"


# --- query ids ---------------------------------------------------------------------------------


async def test_without_a_sandbox_each_query_gets_the_next_id(empty_duckdb: list[str]) -> None:
    async with mcp_server(*empty_duckdb) as session:
        first = await call(session, "query_database", {"sql": "SELECT 1 AS one"})
        second = await call(session, "query_database", {"sql": "SELECT 2 AS two"})
    assert not first.is_error and not second.is_error
    assert result_id(text_of(first)) == "query_1"
    assert result_id(text_of(second)) == "query_2"
    # The rows are still there above the line, as format_rows writes them.
    assert text_of(first).splitlines()[:4] == ["one", "---", "1", "(1 rows)"]


async def test_with_a_sandbox_each_query_gets_one_id_the_chart_tool_reads(
    empty_duckdb: list[str], tmp_path: Path
) -> None:
    """The web UI's runs: --charts beside --sandbox. One id line per result, one numbering for
    both tools."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sandbox = ["--sandbox", "local", "--workspace", str(workspace)]
    async with mcp_server(*empty_duckdb, *sandbox) as session:
        first = await call(session, "query_database", {"sql": "SELECT 'a' AS k, 1 AS v"})
        second = await call(session, "query_database", {"sql": "SELECT 'b' AS k, 2 AS v"})
        drawn = await chart(session, bar("query_2", "k", "v"))
    for result, expected in ((first, "query_1"), (second, "query_2")):
        text = text_of(result)
        assert not result.is_error, text
        assert result_id(text) == expected
        assert text.count("[result id:") == 1, text
    assert drawn["data"] == [{"k": "b", "v": 2}]


@pytest.fixture
def two_sources(tmp_path: Path) -> list[str]:
    import duckdb  # pyright: ignore[reportMissingImports]

    args: list[str] = []
    for name in ("orders", "customers"):
        path = tmp_path / f"{name}.duckdb"
        duckdb.connect(str(path)).close()
        args += ["--source", name, "duckdb", str(path)]
    return args


async def test_several_databases_share_one_numbering_and_one_chart_tool(
    two_sources: list[str],
) -> None:
    async with mcp_server(*two_sources) as session:
        first = await query(session, "SELECT 'a' AS k, 1 AS v", database="orders")
        second = await query(session, "SELECT 'b' AS k, 2 AS v", database="customers")
        listed = await session.list_tools()
        drawn = await chart(
            session,
            {
                "query_id": "query_2",
                "chart_type": "bar",
                "x": "k",
                "series": [{"column": "v"}],
                "title": "From the second database",
            },
        )
    assert (first, second) == ("query_1", "query_2")
    [tool] = [tool for tool in listed.tools if tool.name == "display_chart"]
    assert "database" not in tool.input_schema["properties"]
    assert drawn["data"] == [{"k": "b", "v": 2}]


# --- display_chart's parameters ----------------------------------------------------------------


def resolve(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """`node` with a `$ref` into the schema's `$defs` followed."""
    while "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        node = schema["$defs"][name]
    return node


def types_of(schema: dict[str, Any], node: dict[str, Any]) -> set[str]:
    """The JSON types a property allows, through `anyOf` (an optional value may add null)."""
    node = resolve(schema, node)
    if "anyOf" in node:
        return set().union(*(types_of(schema, option) for option in node["anyOf"]))
    kind = node.get("type")
    return set(kind) if isinstance(kind, list) else {kind} if kind else set()


async def test_display_chart_takes_the_agreed_parameters(empty_duckdb: list[str]) -> None:
    async with mcp_server(*empty_duckdb) as session:
        listed = await session.list_tools()
    [tool] = [tool for tool in listed.tools if tool.name == "display_chart"]
    schema = tool.input_schema
    props = schema["properties"]
    assert set(props) == {"query_id", "chart_type", "x", "series", "stacked", "title"}
    assert set(schema["required"]) == {"query_id", "chart_type", "x", "series", "title"}
    for name in ("query_id", "x", "title"):
        assert types_of(schema, props[name]) == {"string"}, name
    chart_type = resolve(schema, props["chart_type"])
    assert set(chart_type["enum"]) == CHART_TYPES
    stacked = resolve(schema, props["stacked"])
    assert types_of(schema, stacked) == {"boolean"}
    assert stacked["default"] is False
    series = resolve(schema, props["series"])
    assert types_of(schema, series) == {"array"}
    assert series["minItems"] == 1
    item = resolve(schema, series["items"])
    assert set(item["properties"]) == {"column", "label"}
    assert set(item.get("required", [])) == {"column"}
    assert types_of(schema, item["properties"]["column"]) == {"string"}
    assert types_of(schema, item["properties"]["label"]) - {"null"} == {"string"}
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True


# --- the checks: each failure is an error result naming what is wrong ---------------------------


def bar(query_id: str, x: str, *columns: str, **extra: Any) -> dict[str, Any]:
    return {
        "query_id": query_id,
        "chart_type": "bar",
        "x": x,
        "series": [{"column": column} for column in columns],
        "title": "A chart",
        **extra,
    }


async def test_an_unknown_query_id_is_named(empty_duckdb: list[str]) -> None:
    async with mcp_server(*empty_duckdb) as session:
        await query(session, SALES)
        text = await chart_error(session, bar("query_9", "tier", "online"))
    assert "query_9" in text


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(bar("query_1", "region", "online"), id="x"),
        pytest.param(bar("query_1", "tier", "online", "returns"), id="series"),
    ],
)
async def test_a_column_not_in_the_result_lists_the_results_columns(
    empty_duckdb: list[str], arguments: dict[str, Any]
) -> None:
    async with mcp_server(*empty_duckdb) as session:
        await query(session, SALES)
        text = await chart_error(session, arguments)
    for column in SALES_COLUMNS:
        assert column in text, f"{column} missing from {text!r}"


async def test_a_series_column_that_is_not_numeric_is_named(empty_duckdb: list[str]) -> None:
    async with mcp_server(*empty_duckdb) as session:
        await query(session, SALES)
        text = await chart_error(session, bar("query_1", "online", "tier"))
    assert "tier" in text


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({**bar("query_1", "tier", "online"), "chart_type": "scatter"}, id="scatter-x"),
        pytest.param(
            {**bar("query_1", "tier", "online", "store"), "chart_type": "pie"}, id="pie-2"
        ),
        pytest.param({**bar("query_1", "tier", "delta"), "chart_type": "pie"}, id="pie-negative"),
        pytest.param(
            {**bar("query_1", "online", "store", stacked=True), "chart_type": "line"},
            id="stacked-line",
        ),
        pytest.param(
            {**bar("query_1", "online", "store", stacked=True), "chart_type": "scatter"},
            id="stacked-scatter",
        ),
        pytest.param(
            {**bar("query_1", "tier", "online", stacked=True), "chart_type": "pie"},
            id="stacked-pie",
        ),
    ],
)
async def test_a_chart_the_type_cannot_show_is_refused(
    empty_duckdb: list[str], arguments: dict[str, Any]
) -> None:
    async with mcp_server(*empty_duckdb) as session:
        await query(session, SALES)
        await chart_error(session, arguments)
        # The control changes one thing: unstacked when stacking was the fault, otherwise the
        # same columns as a plain bar chart.
        control = {"stacked": False} if arguments.get("stacked") else {"chart_type": "bar"}
        await chart(session, {**arguments, **control})


async def test_more_than_500_points_are_refused_and_500_pass(empty_duckdb: list[str]) -> None:
    async with mcp_server(*empty_duckdb) as session:
        at_limit = await query(session, points(250))
        over = await query(session, points(251))
        drawn = await chart(session, bar(at_limit, "n", "a", "b"))
        text = await chart_error(session, bar(over, "n", "a", "b"))
    assert len(drawn["data"]) == 250
    assert str(MAX_POINTS) in text


# --- the five kinds of chart -------------------------------------------------------------------

MONTHS = (
    "SELECT CAST(month AS DATE) AS month, orders, revenue, note FROM (VALUES "
    "('2024-01-01', 12, 1520.5::DOUBLE, 'a'), ('2024-02-01', 15, NULL, 'b'), "
    "('2024-03-01', 11, 1390.25::DOUBLE, 'c')) AS t(month, orders, revenue, note)"
)
BASKETS = (
    "SELECT items, amount, shop FROM (VALUES (1, 19.9::DOUBLE, 'x'), (3, 52.0::DOUBLE, 'y'), "
    "(2, 35.5::DOUBLE, 'z')) AS t(items, amount, shop)"
)

CASES: list[Any] = [
    pytest.param(
        SALES,
        {
            "chart_type": "bar",
            "x": "tier",
            "series": [{"column": "online", "label": "Online"}, {"column": "store"}],
            "stacked": True,
            "title": "Revenue by tier",
        },
        {
            "series": [
                {"column": "online", "label": "Online"},
                {"column": "store", "label": "store"},
            ],
            "stacked": True,
        },
        [
            {"tier": "Gold", "online": 410.5, "store": 300},
            {"tier": "Silver", "online": 280.25, "store": 190},
            {"tier": "Bronze", "online": 120.0, "store": 95},
        ],
        id="bar",
    ),
    pytest.param(
        MONTHS,
        {"chart_type": "line", "x": "month", "series": [{"column": "revenue"}], "title": "Revenue"},
        {"series": [{"column": "revenue", "label": "revenue"}], "stacked": False},
        [
            {"month": "2024-01-01", "revenue": 1520.5},
            {"month": "2024-02-01", "revenue": None},
            {"month": "2024-03-01", "revenue": 1390.25},
        ],
        id="line",
    ),
    pytest.param(
        MONTHS,
        {
            "chart_type": "area",
            "x": "month",
            "series": [{"column": "orders", "label": "Orders"}],
            "stacked": True,
            "title": "Orders",
        },
        {"series": [{"column": "orders", "label": "Orders"}], "stacked": True},
        [
            {"month": "2024-01-01", "orders": 12},
            {"month": "2024-02-01", "orders": 15},
            {"month": "2024-03-01", "orders": 11},
        ],
        id="area",
    ),
    pytest.param(
        BASKETS,
        {"chart_type": "scatter", "x": "items", "series": [{"column": "amount"}], "title": "Size"},
        {"series": [{"column": "amount", "label": "amount"}], "stacked": False},
        [
            {"items": 1, "amount": 19.9},
            {"items": 3, "amount": 52.0},
            {"items": 2, "amount": 35.5},
        ],
        id="scatter",
    ),
    pytest.param(
        SALES,
        {"chart_type": "pie", "x": "tier", "series": [{"column": "store"}], "title": "In store"},
        {"series": [{"column": "store", "label": "store"}], "stacked": False},
        [
            {"tier": "Gold", "store": 300},
            {"tier": "Silver", "store": 190},
            {"tier": "Bronze", "store": 95},
        ],
        id="pie",
    ),
]


@pytest.mark.parametrize(("sql", "arguments", "checked", "data"), CASES)
async def test_a_chart_returns_its_definition_and_only_its_columns(
    empty_duckdb: list[str],
    sql: str,
    arguments: dict[str, Any],
    checked: dict[str, Any],
    data: list[dict[str, Any]],
) -> None:
    async with mcp_server(*empty_duckdb) as session:
        query_id = await query(session, sql)
        drawn = await chart(session, {"query_id": query_id, **arguments})
    expected_chart = {
        "query_id": query_id,
        "chart_type": arguments["chart_type"],
        "x": arguments["x"],
        "title": arguments["title"],
        **checked,
    }
    assert drawn == {"chart": expected_chart, "data": data}
    # Numbers are JSON numbers, not text; the DATE is ISO text.
    columns = [series["column"] for series in checked["series"]]
    for row in drawn["data"]:
        for column in columns:
            value = row[column]
            assert value is None or type(value) in (int, float), (column, value)
        if arguments["x"] == "month":
            assert isinstance(row["month"], str)


# --- the shared event fixture ------------------------------------------------------------------


def fixture_calls() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Each tool call of the fixture run: tool, input and its `tool_end`, in order."""
    events = [json.loads(line) for line in (RUN / "events.jsonl").read_text().splitlines()]
    ends = {e["tool_use_id"]: e for e in events if e["type"] == "tool_end"}
    return [
        (e["tool"].removeprefix("mcp__data__"), e["input"], ends[e["tool_use_id"]])
        for e in events
        if e["type"] == "tool_start"
    ]


async def test_the_fixture_run_replays_on_a_real_server(empty_duckdb: list[str]) -> None:
    calls = fixture_calls()
    charts = [entry for entry in calls if entry[0] == "display_chart"]
    assert len(charts) == 6
    async with mcp_server(*empty_duckdb) as session:
        for tool, arguments, end in calls:
            result = await call(session, tool, arguments)
            text = text_of(result)
            assert bool(result.is_error) == end["is_error"], (tool, arguments, text)
            if tool == "query_database":
                # The rows the fixture shows, under the id the fixture used.
                recorded = json.loads(end["content"])["result"].splitlines()
                assert text.splitlines()[:-1] == recorded[:-1]
                assert result_id(text) == result_id(recorded[-1])
            elif not end["is_error"]:
                assert json.loads(text) == json.loads(end["content"]), arguments
