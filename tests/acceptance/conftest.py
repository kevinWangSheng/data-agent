"""Fixtures for the seam acceptance tests (spec "Testing Decisions", ticket 01)."""

import os
from pathlib import Path

import pytest
from seam_support import POSTGRES_URL_ENV, build_shop_duckdb, load_shop_postgres


@pytest.fixture
def shop_duckdb(tmp_path: Path) -> Path:
    """A fresh DuckDB file built from shop.sql, private to this test."""
    return build_shop_duckdb(tmp_path / "shop.duckdb")


@pytest.fixture
def postgres_url() -> str:
    """Connection string of a scratch Postgres database, freshly loaded with shop.sql."""
    url = os.environ.get(POSTGRES_URL_ENV)
    if not url:
        pytest.fail(f"{POSTGRES_URL_ENV} is not set")
    load_shop_postgres(url)
    return url
