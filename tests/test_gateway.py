"""The read-only gateway's own contract: a runaway query is stopped, not left to hang the server,
and every DuckDB column type the data sources use comes back as a Python value."""

import time
from pathlib import Path

import duckdb
import pytest

from data_agent.gateway import DuckDBGateway, QueryFailed, connect_duckdb


def test_long_query_times_out(tmp_path: Path) -> None:
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    gateway = DuckDBGateway(db, timeout_seconds=0.5)
    started = time.monotonic()
    with pytest.raises(QueryFailed, match=r"timed out after 0\.5s"):
        gateway.query("SELECT count(*) FROM range(1000000000000)")
    assert time.monotonic() - started < 10


def test_timestamptz_column_is_returned(tmp_path: Path) -> None:
    """DuckDB turns a TIMESTAMPTZ value into a Python datetime through `pytz`; without that
    runtime dependency the query fails with "Required module 'pytz' failed to import"."""
    db = tmp_path / "x.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t (at TIMESTAMPTZ)")
    con.execute("INSERT INTO t VALUES ('2026-01-02 03:04:05+00')")
    con.close()
    result = DuckDBGateway(db).query("SELECT at FROM t")
    assert result.columns == ["at"]
    assert len(result.rows) == 1
    assert result.rows[0][0].tzinfo is not None


def test_connection_has_memory_and_temp_file_limits(tmp_path: Path) -> None:
    """Every gateway connection carries the memory and spill caps (why: `DUCKDB_MEMORY_LIMIT`)."""
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    con = connect_duckdb(db)
    try:
        row = con.sql(
            "SELECT current_setting('memory_limit'), current_setting('max_temp_directory_size')"
        ).fetchone()
    finally:
        con.close()
    assert row == ("2.0 GiB", "4.0 GiB")
