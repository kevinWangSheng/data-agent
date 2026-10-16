"""Context-strategy tools beyond the acceptance tests, driven over stdio like seam B."""

import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

DATA_AGENT = Path(sys.executable).with_name("data-agent")
SHOP_SQL = Path(__file__).resolve().parent / "fixtures" / "shop.sql"


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    path = tmp_path / "shop.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute(SHOP_SQL.read_text())
    finally:
        con.close()
    return path


@asynccontextmanager
async def mcp_server(db: Path) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(
        command=str(DATA_AGENT),
        args=["mcp-server", "--config", "full", "--duckdb", str(db)],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def text_of(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def refs_of(result: CallToolResult) -> list[str]:
    return [item["ref"] for item in json.loads(text_of(result))["results"]]


async def test_discover_ranks_table_name_matches_first(shop: Path) -> None:
    async with mcp_server(shop) as session:
        result = await session.call_tool("discover", {"query": "customers"})
    assert not result.is_error, text_of(result)
    refs = refs_of(result)
    # Tables named after customers come before tables that only have a customer_id column.
    assert set(refs[:2]) == {"raw.customers", "main.stg_customers"}, refs
    assert "raw.web_events" in refs


async def test_discover_respects_limit(shop: Path) -> None:
    async with mcp_server(shop) as session:
        result = await session.call_tool("discover", {"query": "customer", "limit": 2})
    assert len(refs_of(result)) == 2


async def test_discover_with_no_match_returns_no_results(shop: Path) -> None:
    async with mcp_server(shop) as session:
        result = await session.call_tool("discover", {"query": "zebra"})
    assert not result.is_error, text_of(result)
    assert refs_of(result) == []


async def test_hydrate_lists_types_row_count_and_join_candidates(shop: Path) -> None:
    async with mcp_server(shop) as session:
        result = await session.call_tool("hydrate", {"ref": "main.stg_orders"})
    assert not result.is_error, text_of(result)
    text = text_of(result)
    assert "37 rows" in text
    assert "customer_id VARCHAR" in text
    assert "customer_id -> main.stg_customers.customer_id" in text


async def test_hydrate_accepts_a_bare_table_name(shop: Path) -> None:
    async with mcp_server(shop) as session:
        result = await session.call_tool("hydrate", {"ref": "web_events"})
    assert not result.is_error, text_of(result)
    assert "raw.web_events" in text_of(result)


async def test_hydrate_unknown_ref_is_a_readable_error(shop: Path) -> None:
    async with mcp_server(shop) as session:
        result = await session.call_tool("hydrate", {"ref": "raw.no_such_table"})
    assert result.is_error
    assert "raw.no_such_table" in text_of(result)
    assert "discover" in text_of(result)
    assert "Traceback" not in text_of(result)


async def test_dictionary_search_reports_counts_per_value(shop: Path) -> None:
    async with mcp_server(shop) as session:
        result = await session.call_tool("dictionary_search", {"keyword": "C-0001"})
    assert not result.is_error, text_of(result)
    assert "raw.orders.customer_ref: 'C-0001 ' (2)" in text_of(result)


async def test_catalog_past_the_gateway_row_cap_is_complete(tmp_path: Path) -> None:
    """101 tables x 100 columns: more catalog rows than one gateway query returns."""
    db = tmp_path / "wide.duckdb"
    con = duckdb.connect(str(db))
    try:
        for i in range(101):
            columns = ", ".join(f"c{j:03d} INTEGER" for j in range(100))
            con.execute(f"CREATE TABLE t{i:03d} ({columns})")
    finally:
        con.close()
    async with mcp_server(db) as session:
        found = await session.call_tool("discover", {"query": "t100"})
        hydrated = await session.call_tool("hydrate", {"ref": "main.t100"})
    assert refs_of(found) == ["main.t100"]
    assert "100 columns" in text_of(hydrated)


async def test_dictionary_search_shows_every_column_despite_many_values(tmp_path: Path) -> None:
    db = tmp_path / "values.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute(
            "CREATE TABLE items AS SELECT 'gold-' || range AS a_text, 'gold_tier' AS z_tier "
            "FROM range(10001)"
        )
    finally:
        con.close()
    async with mcp_server(db) as session:
        result = await session.call_tool("dictionary_search", {"keyword": "gold"})
    text = text_of(result)
    assert "main.items.z_tier: 'gold_tier' (10001)" in text
    assert "9981 more" in text


@pytest.mark.parametrize("keyword", ["zebra", "o'brien"])
async def test_dictionary_search_without_matches(shop: Path, keyword: str) -> None:
    async with mcp_server(shop) as session:
        result = await session.call_tool("dictionary_search", {"keyword": keyword})
    assert not result.is_error, text_of(result)
    assert "No values" in text_of(result)
