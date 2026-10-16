"""MCP server behavior beyond the acceptance tests, driven over stdio like seam B."""

import os
import subprocess
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


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(DATA_AGENT), "mcp-server", *args], capture_output=True, text=True, timeout=30
    )


def text_of(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def make_db(path: Path) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE items AS SELECT range AS id FROM range(3)")
    finally:
        con.close()
    return path


def make_secret(tmp_path: Path) -> Path:
    secret = tmp_path / "secret.csv"
    secret.write_text("token\nsecret-marker\n")
    return secret


def test_unknown_config_is_rejected(tmp_path: Path) -> None:
    proc = run_cli("--config", "nope", "--duckdb", str(make_db(tmp_path / "x.duckdb")))
    assert proc.returncode != 0
    assert "nope" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_missing_duckdb_file_is_rejected(tmp_path: Path) -> None:
    missing = tmp_path / "missing.duckdb"
    proc = run_cli("--config", "full", "--duckdb", str(missing))
    assert proc.returncode != 0
    assert str(missing) in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not missing.exists()


async def test_database_stays_writable_between_queries(tmp_path: Path) -> None:
    """`dbt run` must be able to write the file while the server is up (the only write path)."""
    db = make_db(tmp_path / "x.duckdb")
    async with mcp_server(db) as session:
        await session.call_tool("query_database", {"sql": "SELECT count(*) FROM items"})
        writer = duckdb.connect(str(db))
        try:
            writer.execute("CREATE TABLE built_by_dbt AS SELECT 1 AS x")
        finally:
            writer.close()
        result = await session.call_tool("query_database", {"sql": "SELECT x FROM built_by_dbt"})
    assert not result.is_error, text_of(result)
    assert "1" in text_of(result)


# Path and URL payloads from SignalPilot's tests/test_sql_governance_live.py (DuckDB section).
UPSTREAM_PATH_PAYLOADS = [
    "SELECT * FROM 'https://example.com/x.parquet'",
    "SELECT * FROM 'http://169.254.169.254/latest/meta-data'",
    "SELECT * FROM 's3://bucket/key.parquet'",
    "SELECT * FROM '/etc/passwd'",
    "SELECT * FROM 'data.csv'",
    "SELECT * FROM read_csv_auto('/etc/passwd')",
    "SELECT * FROM read_parquet('https://example.com/x.parquet')",
    "SELECT read_text('/etc/passwd')",
    "SELECT load_extension('httpfs')",
]


async def test_path_and_url_reads_are_blocked(tmp_path: Path) -> None:
    secret = make_secret(tmp_path)
    statements = [
        *UPSTREAM_PATH_PAYLOADS,
        # query() / query_table() run SQL or a path hidden in a string, out of the AST's sight.
        f"SELECT * FROM query('SELECT * FROM read_csv(''{secret}'')')",
        f"SELECT * FROM query_table('{secret}')",
    ]
    async with mcp_server(make_db(tmp_path / "x.duckdb")) as session:
        for sql in statements:
            result = await session.call_tool("query_database", {"sql": sql})
            assert result.is_error, f"not blocked: {sql}"
            assert "blocked" in text_of(result).lower(), text_of(result)
            assert "secret-marker" not in text_of(result)


async def test_file_readers_missed_by_the_denylist_cannot_read_files(tmp_path: Path) -> None:
    """Defense in depth: the connection itself has external access off."""
    secret = make_secret(tmp_path)
    async with mcp_server(make_db(tmp_path / "x.duckdb")) as session:
        result = await session.call_tool(
            "query_database", {"sql": f"SELECT * FROM sniff_csv('{secret}')"}
        )
    assert result.is_error, text_of(result)
    assert "secret-marker" not in text_of(result)


@pytest.mark.parametrize(("row_limit", "shown"), [(2, "2 rows"), (0, "1 rows")])
async def test_row_limit_caps_rows(tmp_path: Path, row_limit: int, shown: str) -> None:
    async with mcp_server(make_db(tmp_path / "x.duckdb")) as session:
        result = await session.call_tool(
            "query_database", {"sql": "SELECT id FROM items ORDER BY id", "row_limit": row_limit}
        )
    assert not result.is_error, text_of(result)
    assert shown in text_of(result)
