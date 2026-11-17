"""Builds the chat end-to-end tests' data sources into a directory (ticket 88).

`python build_sources.py DIR` writes `DIR/orders.duckdb` and `DIR/customers.duckdb` from
orders.sql and customers.sql, and copies sources.toml to `DIR/sources.toml`, the file
`data-agent web --data-sources` reads. A committed DuckDB file would be far larger than the
scripts, so each test run builds fresh ones (as tests/acceptance/seam_support.py does for shop.sql).
Used by tests/acceptance/test_chat_e2e.py and by the Playwright main flow (web/e2e/).
"""

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATABASES = {"orders": HERE / "orders.sql", "customers": HERE / "customers.sql"}
SOURCES = HERE / "sources.toml"


def build(directory: Path) -> Path:
    """The two DuckDB files and the data-source file in `directory`; returns the file's path."""
    import duckdb  # pyright: ignore[reportMissingImports]

    directory.mkdir(parents=True, exist_ok=True)
    for name, script in DATABASES.items():
        path = directory / f"{name}.duckdb"
        path.unlink(missing_ok=True)
        con = duckdb.connect(str(path))
        try:
            con.execute(script.read_text())
        finally:
            con.close()
    return Path(shutil.copy(SOURCES, directory / "sources.toml"))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: build_sources.py DIR")
    print(build(Path(sys.argv[1])))
