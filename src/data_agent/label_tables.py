"""The product server's label tables (ticket 70): each classify_text call's labels as a table
`labels_<n>` in an in-memory DuckDB of their own, which `query_labels` reads with SQL, and as a CSV
file in the workspace (`LABELS_DIR`) for the file tools.

The gateway's database stays untouched (it is read-only, and may be Postgres): the label tables
hold only what the classify query returned (its text column left out) plus the labels. Queries on
them pass the gateway's read-only check, and the database has no access to files or the network.
"""

import csv
import os
import threading
from pathlib import Path
from typing import Any

import duckdb

from data_agent.engine import inject_limit, validate_sql
from data_agent.gateway import MAX_ROWS, QueryBlocked, QueryFailed, QueryResult

LABELS_DIR = "data_agent_labels"  # under the workspace


def column_type(values: list[Any]) -> str:
    """The DuckDB type for a column's Python values; anything but numbers and booleans is text."""
    present = [v for v in values if v is not None]
    if present and all(isinstance(v, bool) for v in present):
        return "BOOLEAN"
    if present and all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        return "BIGINT"
    if present and all(isinstance(v, int | float) and not isinstance(v, bool) for v in present):
        return "DOUBLE"
    return "VARCHAR"


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class LabelTables:
    def __init__(self, workspace: Path | None) -> None:
        self.workspace = workspace
        self._con = duckdb.connect(":memory:", config={"enable_external_access": False})
        self._lock = threading.Lock()
        self._count = 0

    def add(self, columns: list[str], rows: list[tuple[Any, ...]]) -> tuple[str, Path | None]:
        """Store a label table; returns its name and its file (None without a workspace)."""
        with self._lock:
            self._count += 1
            name = f"labels_{self._count}"
            types = [column_type([row[i] for row in rows]) for i in range(len(columns))]
            # Text columns take any value as its text (dates, decimals, ...).
            cells = [
                [
                    str(v) if v is not None and t == "VARCHAR" else v
                    for v, t in zip(row, types, strict=True)
                ]
                for row in rows
            ]
            definition = ", ".join(f"{quoted(c)} {t}" for c, t in zip(columns, types, strict=True))
            self._con.execute(f"CREATE TABLE {name} ({definition})")
            if cells:
                marks = ", ".join("?" for _ in columns)
                self._con.executemany(f"INSERT INTO {name} VALUES ({marks})", cells)
        return name, self._write_file(name, columns, rows)

    def query(self, sql: str, row_limit: int) -> QueryResult:
        verdict = validate_sql(sql, dialect="duckdb")
        if not verdict.ok:
            raise QueryBlocked(verdict.blocked_reason or "Blocked by the read-only gateway")
        governed = inject_limit(sql, max_rows=max(1, min(row_limit, MAX_ROWS)), dialect="duckdb")
        with self._lock:
            try:
                cursor = self._con.execute(governed)
                columns = [d[0] for d in cursor.description or []]
                return QueryResult(columns, cursor.fetchall())
            except duckdb.Error as exc:
                raise QueryFailed(str(exc)) from exc

    def _write_file(
        self, name: str, columns: list[str], rows: list[tuple[Any, ...]]
    ) -> Path | None:
        """A new file under the workspace's LABELS_DIR; an existing file or link is never
        overwritten or followed."""
        if self.workspace is None:
            return None
        directory = self.workspace / LABELS_DIR
        directory.mkdir(exist_ok=True)
        path = directory / f"{name}.csv"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        with os.fdopen(os.open(path, flags, 0o644), "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(rows)
        return path
