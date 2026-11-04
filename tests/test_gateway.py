"""The read-only gateway's own contract: a runaway query is stopped, not left to hang the server."""

import time
from pathlib import Path

import duckdb
import pytest

from data_agent.gateway import DuckDBGateway, QueryFailed


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
