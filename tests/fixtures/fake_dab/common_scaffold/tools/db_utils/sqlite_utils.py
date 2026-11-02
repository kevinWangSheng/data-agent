"""Stand-in for DAB's SQLite query module, written for our tests (no DAB code).

Same shape as DAB's: `exec` opens a new read-only connection through the module's `sqlite3` name
on every call, and raises on a failed query.
"""

import sqlite3
from typing import Any


def exec(db_path: str, sql: str) -> list[dict[str, Any]]:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        try:
            cursor = conn.execute(sql)
            columns = [c[0] for c in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise ValueError(f"SQLite error: {e}") from e
