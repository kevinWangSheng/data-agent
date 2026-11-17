"""Several data sources in one run (ticket 89, .scratch/chat-ui/spec.md decision 20): the MCP
server reaches every database and each query and context tool names the one it uses; with one
database the tools are what they were. Driven over stdio like tests/test_mcp_server.py."""

import os
import shutil
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fake_model import FakeModel
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

# The classify_text tests' fake model and fixtures (`fake`, `articles_db`, `workspace`).
from test_classify import articles_db, call_tool, classify_args, fake, workspace

from data_agent.gateway import DuckDBGateway
from data_agent.label_tables import LABELS_DIR
from data_agent.mcp_server import build_multi_server
from data_agent.run_config import load_config

__all__ = ["articles_db", "fake", "workspace"]

DATA_AGENT = Path(sys.executable).with_name("data-agent")

# Every query and context tool of a `full` query run's server.
DATABASE_TOOLS = {
    "query_database",
    "list_tables",
    "describe_table",
    "explain_query",
    "classify_text",
    "query_labels",
    "discover",
    "hydrate",
    "dictionary_search",
}


def make_orders(path: Path) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE SCHEMA sales")
        con.execute(
            "CREATE TABLE sales.orders AS SELECT * FROM (VALUES (1, 'C1', 'refunded'), "
            "(2, 'C2', 'completed'), (3, 'C1', 'completed')) AS t(order_id, customer_id, status)"
        )
    finally:
        con.close()
    return path


def make_customers(path: Path) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE SCHEMA crm")
        con.execute(
            "CREATE TABLE crm.customers AS SELECT * FROM (VALUES ('C1', 'North'), "
            "('C2', 'South')) AS t(customer_id, region)"
        )
    finally:
        con.close()
    return path


def text_of(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


@asynccontextmanager
async def server(*args: str) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(
        command=str(DATA_AGENT), args=["mcp-server", *args], env=dict(os.environ)
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


@pytest.fixture
def two_sources(tmp_path: Path) -> list[str]:
    orders = make_orders(tmp_path / "orders.duckdb")
    customers = make_customers(tmp_path / "customers.duckdb")
    return [
        *("--source", "orders", "duckdb", str(orders)),
        *("--source", "customers", "duckdb", str(customers)),
    ]


async def call(session: ClientSession, tool: str, arguments: dict[str, Any]) -> CallToolResult:
    return await session.call_tool(tool, arguments)


async def test_each_query_and_context_tool_takes_a_required_database(
    two_sources: list[str], tmp_path: Path
) -> None:
    async with server("--config", "full", "--workspace", str(tmp_path), *two_sources) as session:
        tools = {tool.name: tool.input_schema for tool in (await session.list_tools()).tools}
    assert set(tools) == DATABASE_TOOLS
    for name, schema in tools.items():
        assert schema["required"][0] == "database", name
        assert schema["properties"]["database"]["type"] == "string", name


async def test_each_tool_reaches_the_database_it_names(two_sources: list[str]) -> None:
    async with server("--config", "full", *two_sources) as session:
        orders = await call(
            session,
            "query_database",
            {"database": "orders", "sql": "SELECT count(*) AS n FROM sales.orders"},
        )
        customers = await call(
            session,
            "query_database",
            {"database": "customers", "sql": "SELECT region FROM crm.customers ORDER BY 1"},
        )
        listed = await call(session, "list_tables", {"database": "customers"})
        described = await call(
            session, "describe_table", {"database": "orders", "table": "sales.orders"}
        )
        plan = await call(
            session,
            "explain_query",
            {"database": "customers", "sql": "SELECT * FROM crm.customers"},
        )
        found = await call(session, "discover", {"database": "customers", "query": "region"})
        hydrated = await call(session, "hydrate", {"database": "orders", "ref": "sales.orders"})
        values = await call(session, "dictionary_search", {"database": "orders", "keyword": "ref"})
    assert "3" in text_of(orders).splitlines()[2]
    assert text_of(customers).splitlines()[2:4] == ["North", "South"]
    assert text_of(listed).startswith("crm.customers ")
    assert "sales.orders" not in text_of(listed)
    assert "customer_id" in text_of(described) and "status" in text_of(described)
    assert not plan.is_error, text_of(plan)
    assert "crm.customers" in text_of(found)
    assert text_of(hydrated).startswith("sales.orders (")
    assert "sales.orders.status: 'refunded' (1)" in text_of(values)


async def test_a_table_of_one_database_is_not_found_in_the_other(two_sources: list[str]) -> None:
    """The catalog and the dictionary are each database's own."""
    async with server("--config", "full", *two_sources) as session:
        described = await call(
            session, "describe_table", {"database": "customers", "table": "sales.orders"}
        )
        found = await call(session, "discover", {"database": "orders", "query": "region"})
        values = await call(
            session, "dictionary_search", {"database": "customers", "keyword": "refunded"}
        )
    assert described.is_error
    assert "crm.customers" not in text_of(found)
    assert "sales.orders.status" not in text_of(values)
    assert "value_not_in_sample" in text_of(values)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("query_database", {"sql": "SELECT 1"}),
        ("list_tables", {}),
        ("describe_table", {"table": "t"}),
        ("explain_query", {"sql": "SELECT 1"}),
        ("query_labels", {"sql": "SELECT 1"}),
        ("discover", {"query": "orders"}),
        ("hydrate", {"ref": "t"}),
        ("dictionary_search", {"keyword": "x"}),
    ],
)
async def test_an_unknown_database_is_an_error_naming_the_databases(
    two_sources: list[str], tool: str, arguments: dict[str, Any]
) -> None:
    async with server("--config", "full", *two_sources) as session:
        result = await call(session, tool, {"database": "warehouse", **arguments})
    assert result.is_error
    assert "warehouse" in text_of(result)
    assert "orders, customers" in text_of(result)


async def test_every_database_keeps_the_gateways_read_only_rules(two_sources: list[str]) -> None:
    async with server("--config", "baseline", *two_sources) as session:
        for database in ("orders", "customers"):
            result = await call(
                session, "query_database", {"database": database, "sql": "DROP SCHEMA sales"}
            )
            assert result.is_error
            assert "Blocked" in text_of(result), text_of(result)


# --- label tables: each database's own, with names that never repeat ------------------------


async def test_label_tables_are_each_databases_own(
    fake: FakeModel, articles_db: Path, workspace: Path, tmp_path: Path
) -> None:
    archive = tmp_path / "archive.duckdb"
    shutil.copy(articles_db, archive)
    gateways = {"articles": DuckDBGateway(articles_db), "archive": DuckDBGateway(archive)}
    server = build_multi_server(gateways, load_config("baseline"), workspace=workspace)
    first = await call_tool(server, "classify_text", {"database": "articles", **classify_args()})
    second = await call_tool(server, "classify_text", {"database": "archive", **classify_args()})
    assert "`labels_1`" in text_of(first), text_of(first)
    assert "`labels_2`" in text_of(second), text_of(second)
    count = "SELECT count(*) AS n FROM labels_1"
    own = await call_tool(server, "query_labels", {"database": "articles", "sql": count})
    other = await call_tool(server, "query_labels", {"database": "archive", "sql": count})
    assert text_of(own).splitlines()[2] == "20"
    assert other.is_error and "labels_1" in text_of(other)
    stored = sorted(path.name for path in (workspace / LABELS_DIR).iterdir())
    assert stored == ["labels_1.csv", "labels_2.csv"]
