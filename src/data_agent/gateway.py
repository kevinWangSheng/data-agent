"""Read-only gateway: every SQL statement against a data source passes the engine's AST check first.

The engine (copied from SignalPilot) blocks DDL, DML, statement stacking and dangerous functions,
and caps the row count. Only then does the statement reach the database, over a read-only
connection.
"""

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from data_agent.engine import inject_limit, validate_sql

MAX_ROWS = 10_000
TIMEOUT_SECONDS = 150  # upstream query_database's timeout_seconds


class QueryBlocked(Exception):
    """The gateway refused the statement; the message says why."""


class QueryFailed(Exception):
    """The statement passed the gateway but the database rejected it."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]


class DuckDBGateway:
    dialect = "duckdb"

    def __init__(self, path: Path, timeout_seconds: float = TIMEOUT_SECONDS) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds

    def query(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult:
        verdict = validate_sql(sql, dialect=self.dialect)
        if not verdict.ok:
            raise QueryBlocked(verdict.blocked_reason or "Blocked by the read-only gateway")
        limit = max(1, min(row_limit, MAX_ROWS))
        governed = inject_limit(sql, max_rows=limit, dialect=self.dialect)
        # A connection per statement: DuckDB lets one process hold the write lock, and holding a
        # read-only connection open would keep `dbt run` (the only write path) from writing.
        # External access off: a file or network reader the denylist misses still cannot run.
        try:
            con = duckdb.connect(
                str(self.path),
                read_only=True,
                config={"enable_external_access": False, "lock_configuration": True},
            )
        except duckdb.Error as exc:
            raise QueryFailed(str(exc)) from exc
        # DuckDB has no statement timeout; interrupt the connection from a timer thread instead.
        timer = threading.Timer(self.timeout_seconds, con.interrupt)
        timer.start()
        try:
            cursor = con.execute(governed)
            columns = [column[0] for column in cursor.description or []]
            return QueryResult(columns=columns, rows=cursor.fetchall())
        except duckdb.InterruptException as exc:
            raise QueryFailed(f"Query timed out after {self.timeout_seconds}s") from exc
        except duckdb.Error as exc:
            raise QueryFailed(str(exc)) from exc
        finally:
            timer.cancel()
            con.close()
