"""Verifier tools beyond the acceptance tests, driven over stdio like seam B."""

import os
import shutil
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from data_agent.verifier_tools import source_relations

DATA_AGENT = Path(sys.executable).with_name("data-agent")
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    path = tmp_path / "shop.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute((FIXTURES / "shop.sql").read_text())
    finally:
        con.close()
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "dbt_shop"
    shutil.copytree(FIXTURES / "dbt_shop", path)
    return path


@asynccontextmanager
async def mcp_server(db: Path) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(
        command=str(DATA_AGENT),
        args=["mcp-server", "--config", "full", "--duckdb", str(db)],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def text_of(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


async def call(db: Path, tool: str, **arguments: str) -> CallToolResult:
    async with mcp_server(db) as session:
        return await session.call_tool(tool, arguments)


def metric_block(text: str, metric: str) -> str:
    return text.split(f"### Metric: {metric}")[1].split("### Metric:")[0]


async def test_verify_model_values_compares_against_named_upstreams(shop: Path) -> None:
    result = await call(
        shop, "verify_model_values", model_name="customer_orders", source_tables="stg_orders, nope"
    )
    text = text_of(result)
    assert not result.is_error, text
    assert "Candidates (named upstreams): main.stg_orders" in text
    assert "Source nope: Error: no table or view 'nope'" in text
    assert "Model matches COUNT(*)" in metric_block(text, "n_orders")


async def test_verify_model_values_holds_only_integer_metrics_against_row_counts(
    shop: Path,
) -> None:
    result = await call(shop, "verify_model_values", model_name="customer_orders_fanout")
    text = text_of(result)
    assert "MISMATCH vs COUNT(*) (0.5x)" in metric_block(text, "n_orders")
    total_amount = metric_block(text, "total_amount")
    assert "COUNT(*): 9" in total_amount
    assert "→" not in total_amount


async def test_audit_model_sources_flags_fewer_model_rows(shop: Path) -> None:
    result = await call(
        shop, "audit_model_sources", model_name="customer_orders", source_tables="raw.orders"
    )
    assert "ratio 0.32x  OBSERVATION: fewer model rows" in text_of(result)


async def test_ambiguous_table_name_asks_for_a_schema(tmp_path: Path) -> None:
    db = tmp_path / "two.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE SCHEMA a; CREATE SCHEMA b; CREATE TABLE a.t (x INT); CREATE TABLE b.t (x INT)"
    )
    con.close()
    result = await call(db, "check_model_schema", model_name="t", yml_columns="x")
    assert result.is_error
    assert "ambiguous" in text_of(result) and "a.t" in text_of(result)
    qualified = await call(db, "check_model_schema", model_name="b.t", yml_columns="x")
    assert "[OK] Matching: x" in text_of(qualified)


async def test_map_columns_resolves_sources_through_schema_and_identifier(
    shop: Path, project: Path
) -> None:
    (project / "models" / "sources.yml").write_text(
        "version: 2\n\nsources:\n  - name: shop\n    schema: raw\n    tables:\n"
        "      - name: orders_src\n        identifier: orders\n"
    )
    (project / "models" / "staging" / "stg_orders.sql").write_text(
        "SELECT order_id FROM {{ source('shop', 'orders_src') }}"
    )
    result = await call(shop, "map_columns", model_name="stg_orders", project_dir=str(project))
    text = text_of(result)
    assert "source('shop', 'orders_src') = raw.orders — 5 columns" in text
    assert "customer_ref (VARCHAR) → UNMAPPED-INCLUDE" in text


async def test_map_columns_reads_an_unbuilt_upstream_from_its_sql(
    shop: Path, project: Path
) -> None:
    marts = project / "models" / "marts"
    (marts / "order_facts.sql").write_text(
        "SELECT o.order_id, o.amount * 2 AS double_amount FROM {{ ref('stg_orders') }} o"
    )
    (marts / "order_report.sql").write_text("SELECT * FROM {{ ref('order_facts') }}")
    result = await call(shop, "map_columns", model_name="order_report", project_dir=str(project))
    text = text_of(result)
    assert "ref('order_facts') = not materialized; columns parsed from its SQL" in text
    assert "double_amount (?) → UNMAPPED-INCLUDE" in text


async def test_map_columns_needs_a_dbt_project(shop: Path, tmp_path: Path) -> None:
    result = await call(shop, "map_columns", model_name="x", project_dir=str(tmp_path))
    assert result.is_error
    assert "not a dbt project" in text_of(result)


def test_source_relations_reads_each_source_block(tmp_path: Path) -> None:
    (tmp_path / "sources.yml").write_text(
        "version: 2\nsources:\n"
        "  - name: one\n    tables:\n      - name: a\n        columns:\n          - name: a_id\n"
        "      - name: b\n        identifier: b_v2\n"
        "  - name: two\n    schema: landing\n    tables:\n      - name: c\n"
        "models:\n  - name: m\n"
    )
    assert source_relations(tmp_path) == {
        ("one", "a"): ("one", "a"),
        ("one", "b"): ("one", "b_v2"),
        ("two", "c"): ("landing", "c"),
    }
