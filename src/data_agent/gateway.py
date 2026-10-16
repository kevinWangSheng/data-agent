"""Read-only gateway: every SQL statement against a data source passes the engine's AST check first.

The engine (copied from SignalPilot) blocks DDL, DML, statement stacking and dangerous functions,
and caps the row count. Only then does the statement reach the database, over a read-only
connection.
"""

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import duckdb
import psycopg

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


class Gateway(Protocol):
    dialect: str

    def query(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult: ...


def check(sql: str, dialect: str, row_limit: int) -> str:
    """The statement as it may run: blocked ones raise, the rest get the row cap."""
    verdict = validate_sql(sql, dialect=dialect)
    if not verdict.ok:
        raise QueryBlocked(verdict.blocked_reason or "Blocked by the read-only gateway")
    limit = max(1, min(row_limit, MAX_ROWS))
    return inject_limit(sql, max_rows=limit, dialect=dialect)


class DuckDBGateway:
    dialect = "duckdb"

    def __init__(self, path: Path, timeout_seconds: float = TIMEOUT_SECONDS) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds

    def query(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult:
        governed = check(sql, self.dialect, row_limit)
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


class PostgresGateway:
    """`url` is a libpq connection string. One connection serves every statement, each in its own
    READ ONLY transaction that is rolled back afterwards, with the server's statement timeout."""

    dialect = "postgres"

    def __init__(self, url: str, timeout_seconds: float = TIMEOUT_SECONDS) -> None:
        self._url = url
        self.timeout_seconds = timeout_seconds
        self._con: psycopg.Connection[tuple[Any, ...]] | None = None

    def query(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult:
        governed = check(sql, self.dialect, row_limit)
        con = self._connect()
        try:
            # Not LiteralString: the statement is the agent's, and the gateway has just checked it.
            cursor = con.execute(governed)  # pyright: ignore[reportArgumentType, reportCallIssue]
            columns = [column.name for column in cursor.description or []]
            return QueryResult(columns=columns, rows=cursor.fetchall())
        except psycopg.errors.QueryCanceled as exc:
            raise QueryFailed(f"Query timed out after {self.timeout_seconds}s") from exc
        except psycopg.Error as exc:
            raise QueryFailed(str(exc).strip()) from exc
        finally:
            if con.closed:
                self._con = None
            else:
                con.rollback()

    def _connect(self) -> psycopg.Connection[tuple[Any, ...]]:
        if self._con is not None and not self._con.closed:
            return self._con
        timeout_ms = max(1, int(self.timeout_seconds * 1000))
        try:
            con = psycopg.connect(
                self._url, options=f"-c statement_timeout={timeout_ms}", connect_timeout=10
            )
        except psycopg.Error as exc:
            raise QueryFailed(f"Could not connect to the Postgres database: {reason(exc)}") from exc
        con.read_only = True
        self._con = con
        return con


def reason(exc: psycopg.Error) -> str:
    """libpq's own words without the server address it prefixes them with."""
    text = " ".join(str(exc).split())
    return text.rsplit(" failed: ", 1)[-1] or type(exc).__name__
