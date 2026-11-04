"""The table catalog cache (ticket 39): the metadata tools read `information_schema` once per
database state instead of on every call. Driven through the MCP server's tools in-process on a
DuckDB file, with a gateway that counts the catalog reads."""

import subprocess
import sys
from pathlib import Path

import duckdb
import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from data_agent.db_facts import CatalogCache
from data_agent.gateway import MAX_ROWS, DuckDBGateway, QueryResult
from data_agent.mcp_server import build_server
from data_agent.run_config import load_config

SHOP_SQL = Path(__file__).resolve().parent / "fixtures" / "shop.sql"
# The one line that only the catalog read has: list_tables and describe_table's column read join
# or filter information_schema differently.
CATALOG_MARK = "JOIN information_schema.tables AS t"


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    path = tmp_path / "shop.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute(SHOP_SQL.read_text())
    finally:
        con.close()
    return path


class CountingGateway(DuckDBGateway):
    """A DuckDB gateway that counts the catalog reads it is asked for."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.catalog_reads = 0

    def query(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult:
        if CATALOG_MARK in sql:
            self.catalog_reads += 1
        return super().query(sql, row_limit)


def text_of(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


async def call(server: MCPServer, tool: str, arguments: dict[str, object]) -> str:
    try:
        result = await server.call_tool(tool, arguments)
    except ToolError as exc:
        pytest.fail(f"{tool} failed: {exc}")
    assert isinstance(result, CallToolResult)
    assert not result.is_error, text_of(result)
    return text_of(result)


async def test_second_describe_table_does_not_reread_the_catalog(shop: Path) -> None:
    gateway = CountingGateway(shop)
    server = build_server(gateway, load_config("full"))
    first = await call(server, "describe_table", {"table": "customers"})
    assert gateway.catalog_reads == 1
    second = await call(server, "describe_table", {"table": "orders"})
    assert gateway.catalog_reads == 1
    assert first.startswith("raw.customers (") and second.startswith("raw.orders (")


async def test_a_table_made_by_another_connection_shows_on_the_next_call(shop: Path) -> None:
    gateway = CountingGateway(shop)
    server = build_server(gateway, load_config("full"))
    await call(server, "describe_table", {"table": "customers"})
    con = duckdb.connect(str(shop))
    try:
        con.execute("CREATE TABLE raw.returns (return_id INTEGER, order_id INTEGER)")
    finally:
        con.close()
    text = await call(server, "describe_table", {"table": "returns"})
    assert text.startswith("raw.returns (table, ")
    assert gateway.catalog_reads == 2


WRITE_FROM_ANOTHER_PROCESS = """
import sys, duckdb
con = duckdb.connect(sys.argv[1])
con.execute("CREATE TABLE raw.refunds (refund_id INTEGER)")
con.close()
print("written")
"""


async def test_the_cache_does_not_hold_the_database_file(shop: Path) -> None:
    """With the catalog cached, another process can still open the file read-write and build."""
    server = build_server(CountingGateway(shop), load_config("full"))
    await call(server, "describe_table", {"table": "customers"})
    proc = subprocess.run(
        [sys.executable, "-c", WRITE_FROM_ANOTHER_PROCESS, str(shop)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "written"
    text = await call(server, "describe_table", {"table": "refunds"})
    assert text.startswith("raw.refunds (table, ")


def test_a_keyless_gateway_rereads_only_after_the_ttl(shop: Path) -> None:
    """Postgres reports no key: the catalog is kept for the TTL (SignalPilot's 300 s) and read
    again after it. Driven on DuckDB with the key withheld, so no database server is needed."""
    gateway = CountingGateway(shop)
    now = [1000.0]
    cache = CatalogCache(gateway.query, lambda: None, ttl_seconds=300, clock=lambda: now[0])
    first = cache.get()
    now[0] += 299
    assert cache.get() is first
    assert gateway.catalog_reads == 1
    now[0] += 1
    assert cache.get() is not first
    assert gateway.catalog_reads == 2


async def test_context_and_verifier_tools_share_the_cache(shop: Path) -> None:
    gateway = CountingGateway(shop)
    server = build_server(gateway, load_config("full"))
    await call(server, "hydrate", {"ref": "raw.orders"})
    await call(
        server, "check_model_schema", {"model_name": "stg_orders", "yml_columns": "order_id"}
    )
    await call(server, "discover", {"query": "orders"})
    await call(server, "describe_table", {"table": "stg_orders"})
    assert gateway.catalog_reads == 1
