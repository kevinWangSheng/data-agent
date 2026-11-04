"""The display bounds on list_tables and query_database (ticket 41, limits from SignalPilot)."""

from pathlib import Path

import duckdb
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from data_agent.gateway import DuckDBGateway
from data_agent.mcp_server import LIST_TABLES_CHAR_BUDGET, build_server
from data_agent.run_config import load_config

LONG = "x" * 120  # a column or table name long enough to blow the character budget


def text_of(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


async def call(db: Path, tool: str, arguments: dict[str, object]) -> CallToolResult:
    server = build_server(DuckDBGateway(db, timeout_seconds=30), load_config("baseline"))
    try:
        result = await server.call_tool(tool, arguments)
    except ToolError as exc:
        return CallToolResult(content=[TextContent(type="text", text=str(exc))], is_error=True)
    assert isinstance(result, CallToolResult)
    return result


def table_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if " (" in line]


@pytest.fixture(scope="module")
def many_tables(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """300 tables: 100 each in schemas a, b, c, with short column names."""
    path = tmp_path_factory.mktemp("db") / "many.duckdb"
    con = duckdb.connect(str(path))
    try:
        for schema in "abc":
            con.execute(f"CREATE SCHEMA {schema}")
            for i in range(100):
                con.execute(f"CREATE TABLE {schema}.t{i:03d} (id INTEGER, name VARCHAR)")
    finally:
        con.close()
    return path


@pytest.fixture(scope="module")
def wide_tables(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """40 tables whose 10 long column names put the listing far over the character budget
    (about 1,300 characters per line), while the names alone fit."""
    path = tmp_path_factory.mktemp("db") / "wide.duckdb"
    con = duckdb.connect(str(path))
    try:
        for i in range(40):
            columns = ", ".join(f"c{j}_{LONG} INTEGER" for j in range(10))
            con.execute(f"CREATE TABLE w{i:02d} ({columns})")
    finally:
        con.close()
    return path


@pytest.fixture(scope="module")
def long_named_tables(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """300 tables whose names alone (about 150 characters a line) exceed the budget."""
    path = tmp_path_factory.mktemp("db") / "long.duckdb"
    con = duckdb.connect(str(path))
    try:
        for i in range(300):
            con.execute(f"CREATE TABLE n{i:03d}_{LONG} (id INTEGER)")
    finally:
        con.close()
    return path


async def test_list_tables_shows_200_tables_by_default_with_a_footer(many_tables: Path) -> None:
    text = text_of(await call(many_tables, "list_tables", {}))
    lines = table_lines(text)
    assert len(lines) == 200
    assert lines[0].startswith("a.t000 (base table, ~0 rows): id, name")
    assert lines[-1].startswith("b.t099 ")
    assert text.splitlines()[-1] == (
        "100 more tables not shown; narrow with schema= or name_contains="
    )


async def test_list_tables_max_tables_and_no_footer_when_all_shown(many_tables: Path) -> None:
    text = text_of(await call(many_tables, "list_tables", {"max_tables": 300}))
    assert len(table_lines(text)) == 300
    assert "more tables not shown" not in text


async def test_list_tables_schema_filter_ignores_case(many_tables: Path) -> None:
    text = text_of(await call(many_tables, "list_tables", {"schema": "B"}))
    lines = table_lines(text)
    assert len(lines) == 100
    assert all(line.startswith("b.") for line in lines)
    assert "more tables not shown" not in text


async def test_list_tables_schema_and_name_filters_combine(many_tables: Path) -> None:
    text = text_of(await call(many_tables, "list_tables", {"schema": "c", "name_contains": "T09"}))
    assert [line.split(" ")[0] for line in table_lines(text)] == [f"c.t09{i}" for i in range(10)]


async def test_list_tables_without_columns_gives_a_column_count(many_tables: Path) -> None:
    text = text_of(await call(many_tables, "list_tables", {"columns": False, "max_tables": 2}))
    assert text.splitlines()[0] == "a.t000 (base table, ~0 rows, 2 cols)"
    assert "id, name" not in text


async def test_list_tables_drops_columns_before_tables(wide_tables: Path) -> None:
    text = text_of(await call(wide_tables, "list_tables", {}))
    assert len(text) <= LIST_TABLES_CHAR_BUDGET
    lines = table_lines(text)
    assert len(lines) == 40, text
    assert all(line.endswith("rows, 10 cols)") for line in lines)
    assert "more tables not shown" not in text


async def test_list_tables_drops_tables_when_names_alone_exceed_the_budget(
    long_named_tables: Path,
) -> None:
    text = text_of(await call(long_named_tables, "list_tables", {"max_tables": 300}))
    assert len(text) <= LIST_TABLES_CHAR_BUDGET
    shown = len(table_lines(text))
    assert 0 < shown < 300
    assert text.splitlines()[-1] == (
        f"{300 - shown} more tables not shown; narrow with schema= or name_contains="
    )


async def test_list_tables_rejects_max_tables_below_one(many_tables: Path) -> None:
    result = await call(many_tables, "list_tables", {"max_tables": 0})
    assert result.is_error
    assert "max_tables" in text_of(result)


async def test_list_tables_says_when_a_schema_has_no_tables(many_tables: Path) -> None:
    text = text_of(await call(many_tables, "list_tables", {"schema": "nope"}))
    assert "nope" in text
    assert "more tables not shown" not in text


INCOMPLETE = "[INCOMPLETE DISPLAY]"


async def test_query_display_stops_at_400_small_rows(many_tables: Path) -> None:
    sql = "SELECT range AS n FROM range(1000)"
    text = text_of(await call(many_tables, "query_database", {"sql": sql}))
    rows = [line for line in text.splitlines() if line.isdigit()]
    assert len(rows) == 400
    assert rows[-1] == "399"
    assert "(1000 rows)" in text
    assert text.splitlines()[-1].startswith(
        f"{INCOMPLETE} 1000 rows total; only the first 400 are shown above. "
        "The remaining 600 rows exist but are not displayed."
    )
    assert "OFFSET 400" in text


async def test_query_display_shows_50_wide_rows_then_stops_at_the_character_budget(
    many_tables: Path,
) -> None:
    sql = "SELECT repeat('x', 300) AS s FROM range(200)"
    text = text_of(await call(many_tables, "query_database", {"sql": sql}))
    assert text.count("x" * 300) == 50
    assert "OFFSET 50" in text
    assert "(200 rows)" in text


async def test_query_display_keeps_filling_narrow_rows_within_the_budget(many_tables: Path) -> None:
    sql = "SELECT repeat('x', 100) AS s FROM range(200)"
    text = text_of(await call(many_tables, "query_database", {"sql": sql}))
    shown = text.count("x" * 100)
    assert 50 < shown < 200
    assert shown * 101 <= 12_000 + 101
    assert f"OFFSET {shown}" in text


async def test_query_display_is_silent_when_everything_is_shown(many_tables: Path) -> None:
    sql = "SELECT range AS n FROM range(10)"
    text = text_of(await call(many_tables, "query_database", {"sql": sql}))
    assert INCOMPLETE not in text
    assert text.splitlines()[-1] == "(10 rows)"
