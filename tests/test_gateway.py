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
