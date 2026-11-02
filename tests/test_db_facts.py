"""Database facts in the base tools (ticket 37): row estimates, table details, query plans, and
what a timeout reports. Driven through the MCP server's tools, in-process, on a DuckDB file."""

import os
from pathlib import Path

import duckdb
import psycopg
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from data_agent.gateway import DuckDBGateway, PostgresGateway, QueryFailed, QueryResult
from data_agent.mcp_server import build_server
from data_agent.run_config import load_config

KEYS_SQL = """
CREATE TABLE customers (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, tier VARCHAR);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER REFERENCES customers (id),
    amount DECIMAL(10, 2)
);
CREATE INDEX orders_customer ON orders (customer_id);
INSERT INTO customers VALUES (1, 'a', 'gold'), (2, 'b', NULL), (3, 'c', 'gold');
INSERT INTO orders VALUES (10, 1, 5.00), (11, 1, 7.50);
CREATE VIEW gold_customers AS SELECT * FROM customers WHERE tier = 'gold';
"""


@pytest.fixture
def keys_db(tmp_path: Path) -> Path:
    path = tmp_path / "keys.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute(KEYS_SQL)
    finally:
        con.close()
    return path


def text_of(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


async def call(
    db: Path, tool: str, arguments: dict[str, object], config: str = "baseline", timeout: float = 30
) -> CallToolResult:
    """The tool's result; a tool error comes back as an error result, as over stdio."""
    server = build_server(DuckDBGateway(db, timeout_seconds=timeout), load_config(config))
    try:
        result = await server.call_tool(tool, arguments)
    except ToolError as exc:
        return CallToolResult(content=[TextContent(type="text", text=str(exc))], is_error=True)
    assert isinstance(result, CallToolResult)
    return result


def message_of(result: CallToolResult, tool: str) -> str:
    """A tool error's own message, after the prefix the MCP server puts on every tool error."""
    text = text_of(result)
    prefix = f"Error executing tool {tool}: "
    assert text.startswith(prefix), text
    return text.removeprefix(prefix)


def line_of(text: str, start: str) -> str:
    [line] = [line for line in text.splitlines() if line.startswith(start)]
    return line


@pytest.mark.parametrize("config", ["baseline", "no-context"])
async def test_the_fact_tools_are_base_tools(config: str, tmp_path: Path) -> None:
    server = build_server(DuckDBGateway(tmp_path / "unused.duckdb"), load_config(config))
    names = {tool.name for tool in await server.list_tools()}
    assert {"query_database", "list_tables", "describe_table", "explain_query"} <= names
    assert not names & {"discover", "hydrate", "dictionary_search"}


async def test_list_tables_gives_each_table_a_row_estimate(keys_db: Path) -> None:
    result = await call(keys_db, "list_tables", {})
    assert not result.is_error, text_of(result)
    text = text_of(result)
    assert line_of(text, "main.customers ").startswith("main.customers (base table, ~3 rows): ")
    assert line_of(text, "main.orders ").startswith("main.orders (base table, ~2 rows): ")
    # A view has no rows of its own to estimate.
    assert line_of(text, "main.gold_customers ").startswith("main.gold_customers (view): ")


async def test_describe_table_gives_columns_keys_indexes_and_estimate(keys_db: Path) -> None:
    result = await call(keys_db, "describe_table", {"table": "orders"})
    assert not result.is_error, text_of(result)
    lines = text_of(result).splitlines()
    assert lines[0] == "main.orders (table, ~2 rows, 3 columns)"
    assert "  id INTEGER, not null" in lines
    assert "  customer_id INTEGER, nullable" in lines
    assert "  amount DECIMAL(10,2), nullable" in lines
    keys = lines[lines.index("Keys:") + 1 : lines.index("Indexes:")]
    assert keys == ["  PRIMARY KEY(id)", "  FOREIGN KEY (customer_id) REFERENCES customers(id)"]
    [index] = lines[lines.index("Indexes:") + 1 :]
    assert index == "  CREATE INDEX orders_customer ON orders(customer_id);"


async def test_describe_table_says_when_there_are_no_keys_or_indexes(keys_db: Path) -> None:
    result = await call(keys_db, "describe_table", {"table": "main.gold_customers"})
    assert not result.is_error, text_of(result)
    lines = text_of(result).splitlines()
    assert lines[0] == "main.gold_customers (view, 3 columns)"
    assert lines[lines.index("Keys:") + 1] == "  none declared"
    assert lines[lines.index("Indexes:") + 1] == "  none"


async def test_hydrate_adds_nullability_keys_and_indexes(keys_db: Path) -> None:
    result = await call(keys_db, "hydrate", {"ref": "main.orders"}, config="full")
    assert not result.is_error, text_of(result)
    lines = text_of(result).splitlines()
    # What hydrate already said: the exact count, column types, join candidates.
    assert lines[0] == "main.orders (table, 2 rows, 3 columns)"
    assert "  customer_id -> main.customers.id" in lines
    assert "  id INTEGER, not null" in lines
    assert "  customer_id INTEGER, nullable" in lines
    assert lines[lines.index("Keys:") + 1] == "  PRIMARY KEY(id)"
    assert lines[-1] == "  CREATE INDEX orders_customer ON orders(customer_id);"


async def test_describe_table_of_an_unknown_table_is_a_readable_error(keys_db: Path) -> None:
    result = await call(keys_db, "describe_table", {"table": "no_such_table"})
    assert result.is_error
    assert "no_such_table" in text_of(result)
    assert "list_tables" in text_of(result)
    assert "Traceback" not in text_of(result)


async def test_explain_query_returns_the_plan_without_running_it(keys_db: Path) -> None:
    sql = (
        "SELECT c.name, sum(o.amount) FROM orders AS o JOIN customers AS c "
        "ON o.customer_id = c.id WHERE c.tier = 'gold' GROUP BY c.name"
    )
    result = await call(keys_db, "explain_query", {"sql": sql})
    assert not result.is_error, text_of(result)
    text = text_of(result)
    # DuckDB's own plan text: operators, the join condition and the filter as written there.
    assert "HASH_JOIN" in text
    assert "customer_id = id" in text
    assert "tier='gold'" in text
    assert "Rows" in text  # each node's estimated row count


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE orders",
        "DELETE FROM orders",
        "SELECT 1; DELETE FROM orders",
        "EXPLAIN ANALYZE SELECT * FROM orders",
        "EXPLAIN SELECT * FROM orders",
        "CALL pragma_version()",
    ],
)
async def test_explain_query_blocks_what_the_gateway_blocks(keys_db: Path, sql: str) -> None:
    result = await call(keys_db, "explain_query", {"sql": sql})
    assert result.is_error, text_of(result)
    assert "blocked" in text_of(result).lower()
    assert "Traceback" not in text_of(result)
    con = duckdb.connect(str(keys_db), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM orders").fetchone() == (2,)
    finally:
        con.close()


# Runs for far longer than the short timeout: every order against a trillion numbers.
SLOW_SQL = (
    "WITH big AS (SELECT range AS n FROM range(1000000000000)) "
    "SELECT count(*) FROM main.orders AS o, customers AS c, big WHERE o.customer_id + n = c.id"
)


async def test_a_timeout_reports_the_plan_and_the_indexes_of_the_tables_read(
    keys_db: Path,
) -> None:
    result = await call(keys_db, "query_database", {"sql": SLOW_SQL}, timeout=0.5)
    assert result.is_error
    text = message_of(result, "query_database")
    assert text.startswith("Query timed out after 0.5s\n")
    plan = text[text.index("Plan (EXPLAIN") : text.index("Indexes on the tables")]
    # Where the time goes, as DuckDB plans it: a nested-loop join over a trillion-row range.
    assert "BLOCKWISE_NL_JOIN" in plan
    assert "~1000000000000 Rows" in plan
    indexes = text[text.index("Indexes on the tables") :].splitlines()[1:]
    assert indexes == [
        "  main.orders: CREATE INDEX orders_customer ON orders(customer_id);",
        "  customers: none",
    ]


async def test_a_timeout_whose_explain_fails_gives_only_the_reason(
    keys_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_explain(self: DuckDBGateway, sql: str) -> QueryResult:
        raise QueryFailed("planner gave up")

    monkeypatch.setattr(DuckDBGateway, "explain", failing_explain)
    result = await call(keys_db, "query_database", {"sql": SLOW_SQL}, timeout=0.5)
    assert result.is_error
    assert message_of(result, "query_database") == (
        "Query timed out after 0.5s\n\nEXPLAIN failed: Query error: planner gave up"
    )


# --- Postgres: the same tools on a scratch database (DATA_AGENT_TEST_POSTGRES_URL) -------------

PG_URL_ENV = "DATA_AGENT_TEST_POSTGRES_URL"
PG_KEYS_SQL = """
DROP SCHEMA IF EXISTS facts37 CASCADE;
CREATE SCHEMA facts37;
CREATE TABLE facts37.customers (id integer PRIMARY KEY, name text NOT NULL, tier text);
CREATE TABLE facts37.orders (
    id integer PRIMARY KEY,
    customer_id integer REFERENCES facts37.customers (id),
    amount numeric(10, 2)
);
CREATE INDEX orders_customer ON facts37.orders (customer_id);
CREATE TABLE facts37.unanalyzed (x integer);
INSERT INTO facts37.customers VALUES (1, 'a', 'gold'), (2, 'b', NULL), (3, 'c', 'gold');
INSERT INTO facts37.orders VALUES (10, 1, 5.00), (11, 1, 7.50);
ANALYZE facts37.customers;
ANALYZE facts37.orders;
"""


@pytest.fixture
def pg_url() -> str:
    url = os.environ.get(PG_URL_ENV)
    if not url:
        pytest.fail(f"{PG_URL_ENV} is not set")
    with psycopg.connect(url, autocommit=True) as con:
        con.execute(PG_KEYS_SQL)  # pyright: ignore[reportArgumentType, reportCallIssue]
    return url


async def pg_call(
    url: str, tool: str, arguments: dict[str, object], timeout: float = 30
) -> CallToolResult:
    server = build_server(PostgresGateway(url, timeout_seconds=timeout), load_config("baseline"))
    try:
        result = await server.call_tool(tool, arguments)
    except ToolError as exc:
        return CallToolResult(content=[TextContent(type="text", text=str(exc))], is_error=True)
    assert isinstance(result, CallToolResult)
    return result


@pytest.mark.integration
async def test_postgres_list_tables_gives_row_estimates(pg_url: str) -> None:
    text = text_of(await pg_call(pg_url, "list_tables", {"name_contains": "customers"}))
    assert line_of(text, "facts37.customers ").startswith(
        "facts37.customers (base table, ~3 rows): "
    )


@pytest.mark.integration
async def test_postgres_describe_table(pg_url: str) -> None:
    result = await pg_call(pg_url, "describe_table", {"table": "facts37.orders"})
    assert not result.is_error, text_of(result)
    lines = text_of(result).splitlines()
    assert lines[0] == "facts37.orders (table, ~2 rows, 3 columns)"
    assert "  id integer, not null; n_distinct=-1, null_frac=0" in lines
    assert "  customer_id integer, nullable; n_distinct=-0.5, null_frac=0" in lines
    keys = lines[lines.index("Keys:") + 1 : lines.index("Indexes:")]
    assert keys == [
        "  PRIMARY KEY (id)",
        "  FOREIGN KEY (customer_id) REFERENCES facts37.customers(id)",
    ]
    assert lines[lines.index("Indexes:") + 1 :] == [
        "  CREATE INDEX orders_customer ON facts37.orders USING btree (customer_id)",
        "  CREATE UNIQUE INDEX orders_pkey ON facts37.orders USING btree (id)",
    ]


@pytest.mark.integration
async def test_postgres_describe_table_says_when_there_are_no_statistics(pg_url: str) -> None:
    result = await pg_call(pg_url, "describe_table", {"table": "facts37.unanalyzed"})
    assert not result.is_error, text_of(result)
    lines = text_of(result).splitlines()
    assert "  x integer, nullable" in lines
    assert "Column statistics: none in pg_stats for this table" in lines


@pytest.mark.integration
async def test_postgres_explain_query(pg_url: str) -> None:
    sql = (
        "SELECT c.name, sum(o.amount) FROM facts37.orders AS o JOIN facts37.customers AS c "
        "ON o.customer_id = c.id WHERE c.tier = 'gold' GROUP BY c.name"
    )
    result = await pg_call(pg_url, "explain_query", {"sql": sql})
    assert not result.is_error, text_of(result)
    text = text_of(result)
    assert "rows=" in text  # each node's estimate
    assert "customer_id = c.id" in text or "c.id = o.customer_id" in text
    assert "Filter: (tier = 'gold'::text)" in text
    assert "actual time" not in text  # planned, not run


@pytest.mark.integration
@pytest.mark.parametrize(
    "sql",
    [
        "EXPLAIN ANALYZE DELETE FROM facts37.orders",
        "DELETE FROM facts37.orders",
        "SELECT 1; DELETE FROM facts37.orders",
    ],
)
async def test_postgres_explain_query_blocks_what_the_gateway_blocks(pg_url: str, sql: str) -> None:
    result = await pg_call(pg_url, "explain_query", {"sql": sql})
    assert result.is_error
    assert "blocked" in text_of(result).lower()
    with psycopg.connect(pg_url) as con:
        assert con.execute("SELECT count(*) FROM facts37.orders").fetchone() == (2,)


@pytest.mark.integration
async def test_postgres_timeout_reports_the_plan_and_indexes(pg_url: str) -> None:
    sql = (
        "SELECT count(*) FROM facts37.orders AS o, generate_series(1, 1000000000000) AS g "
        "WHERE o.customer_id + g = o.id"
    )
    result = await pg_call(pg_url, "query_database", {"sql": sql}, timeout=0.5)
    assert result.is_error
    text = message_of(result, "query_database")
    assert text.startswith("Query timed out after 0.5s\n")
    assert "Function Scan on generate_series g" in text
    assert "Indexes on the tables this query reads (from pg_indexes):" in text
    assert (
        "  facts37.orders: CREATE INDEX orders_customer ON facts37.orders USING btree (customer_id)"
        in text.splitlines()
    )
