"""Stand-in for DAB's DuckDB query module, written for our tests (no DAB code).

Same shape as DAB's: `exec` opens a new read-only connection through the module's `duckdb` name
on every call, and raises on a failed query.
"""

from typing import Any

import duckdb


def exec(db_path: str, sql: str) -> list[dict[str, Any]]:
    with duckdb.connect(database=db_path, read_only=True) as conn:
        try:
            cursor = conn.execute(sql)
            columns = [c[0] for c in cursor.description or []]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        except Exception as e:
            raise ValueError(f"DuckDB error ({type(e).__name__}): {e}") from e
