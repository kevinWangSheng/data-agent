"""Paths and fixture builders shared by the seam acceptance tests (ticket 01).

Every test here drives a seam from outside: the `data-agent` executable, an MCP client over stdio,
or `python -m evals`. Database drivers are imported inside the builders, so a missing driver fails
the test (xfail) instead of breaking collection.
"""

import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SHOP_SQL = FIXTURES / "shop.sql"
DBT_SHOP = FIXTURES / "dbt_shop"
HARBOR_JOB = FIXTURES / "harbor_job"
EVAL_MANIFEST = FIXTURES / "eval_manifest.json"

# Console script installed into the project venv by `uv sync`.
DATA_AGENT = Path(sys.executable).with_name("data-agent")

# Postgres variants read a connection string to a scratch database owned by this worktree.
POSTGRES_URL_ENV = "DATA_AGENT_TEST_POSTGRES_URL"


def build_shop_duckdb(path: Path) -> Path:
    import duckdb  # pyright: ignore[reportMissingImports]

    con = duckdb.connect(str(path))
    try:
        con.execute(SHOP_SQL.read_text())
    finally:
        con.close()
    return path


def load_shop_postgres(url: str) -> None:
    import psycopg  # pyright: ignore[reportMissingImports]

    with psycopg.connect(url, autocommit=True) as con:
        con.execute(SHOP_SQL.read_text())


def duckdb_rows(db: Path, sql: str) -> list[tuple[Any, ...]]:
    """Read the fixture database directly, as ground truth for what a seam reports."""
    import duckdb  # pyright: ignore[reportMissingImports]

    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def make_dbt_workspace(root: Path) -> tuple[Path, Path]:
    """A copy of the mini dbt project plus its private DuckDB file under root."""
    root.mkdir(parents=True, exist_ok=True)
    db = build_shop_duckdb(root / "shop.duckdb")
    project = root / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    (project / "profiles.yml").write_text(
        "shop:\n"
        "  target: dev\n"
        "  outputs:\n"
        "    dev:\n"
        "      type: duckdb\n"
        f"      path: {db}\n"
        "      schema: main\n"
        "      threads: 1\n"
    )
    return project, db
