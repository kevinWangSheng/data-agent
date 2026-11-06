"""Read-only gateway: every SQL statement against a data source passes the engine's AST check first.

The engine (copied from SignalPilot) blocks DDL, DML, statement stacking and dangerous functions,
and caps the row count. Only then does the statement reach the database, over a read-only
connection.
"""

import contextlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import duckdb
import psycopg
from psycopg.conninfo import conninfo_to_dict

from data_agent.engine import inject_limit, validate_sql

MAX_ROWS = 10_000
TIMEOUT_SECONDS = 150  # upstream query_database's timeout_seconds
# Per DuckDB instance (every gateway connection is one). Unset, DuckDB takes 80% of the
# container's memory.max (6.2 GiB) and 90% of free disk for spills, each instance its own share;
# the eval host has 7.7 GB and runs 3 trials. Values from ticket 56's server check: one trial's
# processes peaked at 1762 MiB; 64G disk free. Binary units: DuckDB's "GB" means 1000^3.
DUCKDB_MEMORY_LIMIT = "2GiB"
DUCKDB_MAX_TEMP_DIRECTORY_SIZE = "4GiB"


class QueryBlocked(Exception):
    """The gateway refused the statement; the message says why."""


class QueryFailed(Exception):
    """The statement passed the gateway but the database rejected it."""


class QueryTimedOut(QueryFailed):
    """The statement ran past the gateway's timeout and was stopped."""


class QueryInterrupted(QueryFailed):
    """The session was interrupted: the running statement was stopped and no more may run."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]


class Session(Protocol):
    """One read-only connection held open for a batch of statements (the dictionary build).
    Each statement passes the gateway's check and timeout like any other; `interrupt` (from any
    thread) stops the running statement and makes later ones fail; `close` releases the
    connection and must be called by the thread that queries."""

    def query(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult: ...

    def interrupt(self) -> None: ...

    def close(self) -> None: ...


class Gateway(Protocol):
    dialect: str

    def query(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult: ...

    def explain(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult: ...

    def session(self, timeout_seconds: float) -> Session:
        """Open one read-only connection for a batch of statements; raises QueryFailed."""
        ...

    def catalog_key(self) -> object | None:
        """A value that changes whenever the database's catalog may have changed, for the catalog
        cache; None when the gateway has no cheap way to tell and the cache must expire by age."""
        ...


def check(sql: str, dialect: str, row_limit: int) -> str:
    """The statement as it may run: blocked ones raise, the rest get the row cap."""
    verdict = validate_sql(sql, dialect=dialect)
    if not verdict.ok:
        raise QueryBlocked(verdict.blocked_reason or "Blocked by the read-only gateway")
    limit = max(1, min(row_limit, MAX_ROWS))
    return inject_limit(sql, max_rows=limit, dialect=dialect)


def explain_statement(sql: str, dialect: str, row_limit: int = MAX_ROWS) -> str:
    """EXPLAIN (never ANALYZE: the query is planned, not run) of the statement as the gateway would
    run it. The statement itself passes the gateway's check first; EXPLAIN is only put in front."""
    return f"EXPLAIN {check(sql, dialect, row_limit)}"


class DuckDBGateway:
    dialect = "duckdb"

    def __init__(self, path: Path, timeout_seconds: float = TIMEOUT_SECONDS) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds

    def query(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult:
        return self._execute(check(sql, self.dialect, row_limit))

    def explain(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult:
        return self._execute(explain_statement(sql, self.dialect, row_limit))

    def catalog_key(self) -> object | None:
        """(mtime_ns, size) of the database file and of its write-ahead log: a write by any
        process (`dbt run`) changes one of them, so the catalog is re-read without a TTL."""
        return tuple(
            file_stamp(path) for path in (self.path, self.path.with_name(self.path.name + ".wal"))
        )

    def session(self, timeout_seconds: float) -> "DuckDBSession":
        return DuckDBSession(self.path, timeout_seconds)

    def _execute(self, governed: str) -> QueryResult:
        # A connection per statement: DuckDB lets one process hold the write lock, and holding a
        # read-only connection open would keep `dbt run` (the only write path) from writing.
        con = connect_duckdb(self.path)
        try:
            return run_duckdb(con, governed, self.timeout_seconds)
        finally:
            con.close()


def connect_duckdb(path: Path) -> duckdb.DuckDBPyConnection:
    """A read-only connection. External access off: a file or network reader the denylist
    misses still cannot run. Memory and spill limits go in here: the configuration is locked."""
    try:
        return duckdb.connect(
            str(path),
            read_only=True,
            config={
                "enable_external_access": False,
                "memory_limit": DUCKDB_MEMORY_LIMIT,
                "max_temp_directory_size": DUCKDB_MAX_TEMP_DIRECTORY_SIZE,
                "lock_configuration": True,
            },
        )
    except duckdb.Error as exc:
        raise QueryFailed(str(exc)) from exc


def run_duckdb(
    con: duckdb.DuckDBPyConnection, governed: str, timeout_seconds: float
) -> QueryResult:
    # DuckDB has no statement timeout; interrupt the connection from a timer thread instead.
    timer = threading.Timer(timeout_seconds, con.interrupt)
    timer.start()
    try:
        cursor = con.execute(governed)
        columns = [column[0] for column in cursor.description or []]
        return QueryResult(columns=columns, rows=cursor.fetchall())
    except duckdb.InterruptException as exc:
        raise QueryTimedOut(f"Query timed out after {timeout_seconds}s") from exc
    except duckdb.Error as exc:
        raise QueryFailed(str(exc)) from exc
    finally:
        timer.cancel()


class DuckDBSession:
    """One read-only connection for a batch of statements; see `Session`. The connection is
    opened here, so the caller holds it from construction to `close`."""

    dialect = "duckdb"

    def __init__(self, path: Path, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self._interrupted = False
        self._con = connect_duckdb(path)

    def query(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult:
        governed = check(sql, self.dialect, row_limit)
        if self._interrupted:
            raise QueryInterrupted("Session interrupted")
        try:
            return run_duckdb(self._con, governed, self.timeout_seconds)
        except QueryTimedOut as exc:
            if self._interrupted:
                raise QueryInterrupted("Session interrupted") from exc
            raise

    def interrupt(self) -> None:
        self._interrupted = True
        self._con.interrupt()

    def close(self) -> None:
        self._con.close()


class PostgresGateway:
    """`url` is a libpq connection string. One connection serves every statement, each in its own
    READ ONLY transaction that is rolled back afterwards, with the server's statement timeout. The
    MCP server runs tool calls on worker threads, so one statement's transaction is held at a time.
    """

    dialect = "postgres"

    def __init__(self, url: str, timeout_seconds: float = TIMEOUT_SECONDS) -> None:
        self._url = url
        self.timeout_seconds = timeout_seconds
        self._con: psycopg.Connection[tuple[Any, ...]] | None = None
        self._lock = threading.Lock()

    def query(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult:
        governed = check(sql, self.dialect, row_limit)
        with self._lock:
            return self._run(governed)

    def explain(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult:
        statement = explain_statement(sql, self.dialect, row_limit)
        with self._lock:
            return self._run(statement)

    def catalog_key(self) -> object | None:
        return None  # No cheap change signal: the catalog cache expires by age instead.

    def _run(self, governed: str) -> QueryResult:
        con = self._connect()
        try:
            return run_postgres(con, governed, self.timeout_seconds)
        finally:
            if con.closed:
                self._con = None

    def session(self, timeout_seconds: float) -> "PostgresSession":
        return PostgresSession(self._url, timeout_seconds)

    def _connect(self) -> psycopg.Connection[tuple[Any, ...]]:
        if self._con is not None and not self._con.closed:
            return self._con
        self._con = connect_postgres(self._url, self.timeout_seconds)
        return self._con


def connect_postgres(url: str, timeout_seconds: float) -> psycopg.Connection[tuple[Any, ...]]:
    """A read-only connection whose statements the server stops after `timeout_seconds`."""
    timeout_ms = max(1, int(timeout_seconds * 1000))
    try:
        # Added to the URL's own options (a search_path, say) rather than replacing them.
        given = str(conninfo_to_dict(url).get("options") or "")
        options = f"{given} -c statement_timeout={timeout_ms}".strip()
        con = psycopg.connect(url, options=options, connect_timeout=10)
    except psycopg.Error as exc:
        raise QueryFailed(f"Could not connect to the Postgres database: {reason(exc)}") from exc
    con.read_only = True
    return con


def run_postgres(
    con: psycopg.Connection[tuple[Any, ...]], governed: str, timeout_seconds: float
) -> QueryResult:
    """One statement in a READ ONLY transaction that is rolled back afterwards."""
    try:
        # Not LiteralString: the statement is the agent's, and the gateway has just checked it.
        cursor = con.execute(governed)  # pyright: ignore[reportArgumentType, reportCallIssue]
        columns = [column.name for column in cursor.description or []]
        return QueryResult(columns=columns, rows=cursor.fetchall())
    except psycopg.errors.QueryCanceled as exc:
        raise QueryTimedOut(f"Query timed out after {timeout_seconds}s") from exc
    except psycopg.Error as exc:
        raise QueryFailed(str(exc).strip()) from exc
    finally:
        if not con.closed:
            con.rollback()


class PostgresSession:
    """One read-only connection of its own for a batch of statements; see `Session`."""

    dialect = "postgres"

    def __init__(self, url: str, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self._interrupted = False
        self._con = connect_postgres(url, timeout_seconds)

    def query(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult:
        governed = check(sql, self.dialect, row_limit)
        if self._interrupted:
            raise QueryInterrupted("Session interrupted")
        try:
            return run_postgres(self._con, governed, self.timeout_seconds)
        except QueryTimedOut as exc:
            if self._interrupted:
                raise QueryInterrupted("Session interrupted") from exc
            raise

    def interrupt(self) -> None:
        self._interrupted = True
        # Cancels over a connection of its own, so it is safe from another thread; an error means
        # the connection is already gone and nothing is running on it.
        with contextlib.suppress(psycopg.Error):
            self._con.cancel_safe()

    def close(self) -> None:
        self._con.close()


def file_stamp(path: Path) -> tuple[int, int] | None:
    """(mtime_ns, size) of a file; None when there is no such file."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def reason(exc: psycopg.Error) -> str:
    """libpq's own words without the server address it prefixes them with."""
    text = " ".join(str(exc).split())
    return text.rsplit(" failed: ", 1)[-1] or type(exc).__name__
