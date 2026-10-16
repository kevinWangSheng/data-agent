"""MCP server behavior beyond the acceptance tests, driven over stdio like seam B."""

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

DATA_AGENT = Path(sys.executable).with_name("data-agent")


@asynccontextmanager
async def mcp_server(*args: str) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(
        command=str(DATA_AGENT), args=["mcp-server", *args], env=dict(os.environ)
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def make_db(path: Path) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE items AS SELECT range AS id FROM range(3)")
    finally:
        con.close()
    return path


def test_unknown_config_is_rejected(tmp_path: Path) -> None:
    db = make_db(tmp_path / "x.duckdb")
    proc = subprocess.run(
        [str(DATA_AGENT), "mcp-server", "--config", "nope", "--duckdb", str(db)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0
    assert "nope" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_missing_duckdb_file_is_rejected(tmp_path: Path) -> None:
    missing = tmp_path / "missing.duckdb"
    proc = subprocess.run(
        [str(DATA_AGENT), "mcp-server", "--config", "full", "--duckdb", str(missing)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0
    assert str(missing) in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not missing.exists()


async def test_database_stays_writable_between_queries(tmp_path: Path) -> None:
    """`dbt run` must be able to write the file while the server is up (the only write path)."""
    db = make_db(tmp_path / "x.duckdb")
    async with mcp_server("--config", "full", "--duckdb", str(db)) as session:
        await session.call_tool("query_database", {"sql": "SELECT count(*) FROM items"})
        writer = duckdb.connect(str(db))
        try:
            writer.execute("CREATE TABLE built_by_dbt AS SELECT 1 AS x")
        finally:
            writer.close()
        result = await session.call_tool("query_database", {"sql": "SELECT x FROM built_by_dbt"})
    text = "\n".join(item.text for item in result.content if isinstance(item, TextContent))
    assert not result.is_error, text
    assert "1" in text


async def test_row_limit_caps_rows(tmp_path: Path) -> None:
    db = make_db(tmp_path / "x.duckdb")
    async with mcp_server("--config", "full", "--duckdb", str(db)) as session:
        result = await session.call_tool(
            "query_database", {"sql": "SELECT id FROM items ORDER BY id", "row_limit": 2}
        )
    text = "\n".join(item.text for item in result.content if isinstance(item, TextContent))
    assert not result.is_error, text
    assert "2 rows" in text
