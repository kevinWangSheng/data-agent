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

from data_agent import verifier_tools
from data_agent.gateway import DuckDBGateway, QueryResult
from data_agent.mcp_server import run_query
from data_agent.verifier_tools import source_relations, yml_columns

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
async def mcp_server(db: Path, *extra: str) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(
        command=str(DATA_AGENT),
        args=["mcp-server", "--config", "full", "--duckdb", str(db), *extra],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def text_of(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


async def call(
    db: Path, tool: str, server_args: tuple[str, ...] = (), **arguments: str
) -> CallToolResult:
    async with mcp_server(db, *server_args) as session:
        return await session.call_tool(tool, arguments)


def metric_block(text: str, metric: str) -> str:
    return text.split(f"### Metric: {metric}")[1].split("### Metric:")[0]


UPSTREAMS = "stg_customers, stg_orders"


async def test_verify_model_values_compares_against_named_upstreams(shop: Path) -> None:
    result = await call(
        shop,
        "verify_model_values",
        model_name="customer_orders",
        source_tables=f"{UPSTREAMS}, nope",
    )
    text = text_of(result)
    assert not result.is_error, text
    assert "Candidates (named upstreams): main.stg_customers, main.stg_orders" in text
    assert "Source nope: Error: no table or view 'nope'" in text
    block = metric_block(text, "n_orders")
    # customer_name is not in stg_orders; it is reached through stg_customers' unique key.
    assert "sliced on customer_id; customer_name via main.stg_customers.customer_id" in block
    assert "Model matches COUNT(*)" in block
    assert "own filters" in text


async def test_verify_model_values_calls_a_partial_slice_inconclusive(shop: Path) -> None:
    result = await call(shop, "verify_model_values", model_name="customer_orders_fanout")
    block = metric_block(text_of(result), "n_orders")
    assert "not sliced on customer_name" in block and "inconclusive" in block
    assert "→" not in block


async def test_verify_model_values_holds_only_integer_metrics_against_row_counts(
    shop: Path,
) -> None:
    result = await call(
        shop, "verify_model_values", model_name="customer_orders_fanout", source_tables=UPSTREAMS
    )
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


def make_db(path: Path, sql: str) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute(sql)
    finally:
        con.close()
    return path


async def test_verify_model_values_slices_on_the_whole_grouping_grain(tmp_path: Path) -> None:
    db = make_db(
        tmp_path / "grain.duckdb",
        "CREATE TABLE orders (order_id INT, region VARCHAR, channel VARCHAR);"
        "INSERT INTO orders VALUES (1, 'north', 'web'), (2, 'north', 'web'), (3, 'north', 'store');"
        "CREATE TABLE by_channel AS SELECT region, channel, count(*) AS n_orders "
        "FROM orders GROUP BY region, channel",
    )
    result = await call(db, "verify_model_values", model_name="by_channel", source_tables="orders")
    block = metric_block(text_of(result), "n_orders")
    assert "sliced on region, channel" in block
    assert "COUNT(*): 2" in block and "Model matches COUNT(*)" in block


BRIDGE_SQL = """
CREATE TABLE customers (customer_id VARCHAR, segment VARCHAR);
INSERT INTO customers VALUES ('c1', 'retail'), ('c2', 'retail');
CREATE TABLE crm_notes (customer_id VARCHAR, segment VARCHAR);
INSERT INTO crm_notes VALUES ('c1', 'retail'), ('c1', 'retail');
CREATE TABLE orders (order_id INT, customer_id VARCHAR);
INSERT INTO orders VALUES (1, 'c1'), (2, 'c1');
CREATE TABLE by_segment AS SELECT 'retail' AS segment, 2 AS n_orders;
"""


async def test_verify_model_values_joins_only_through_a_unique_named_upstream(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path / "bridge.duckdb", BRIDGE_SQL)
    safe = await call(
        db, "verify_model_values", model_name="by_segment", source_tables="orders, customers"
    )
    block = metric_block(text_of(safe), "n_orders")
    assert "sliced on segment via main.customers.customer_id" in block
    assert "COUNT(*): 2" in block
    # crm_notes repeats customer c1, so joining through it would double the count.
    unsafe = await call(
        db, "verify_model_values", model_name="by_segment", source_tables="orders, crm_notes"
    )
    block = metric_block(text_of(unsafe), "n_orders")
    assert "main.orders (2 rows) — no slice column and no safe join" in block
    assert "COUNT(*): 4" not in block


def test_audit_reports_sampled_statistics_as_a_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = make_db(
        tmp_path / "wide.duckdb",
        "CREATE TABLE m AS SELECT range AS id, NULL::VARCHAR AS empty FROM range(10)",
    )
    monkeypatch.setattr(verifier_tools, "SAMPLE_CELLS", 5)
    monkeypatch.setattr(verifier_tools, "SAMPLE_ROWS", 4)
    gateway = DuckDBGateway(db)

    def run(sql: str, row_limit: int) -> QueryResult:
        return run_query(gateway, sql, row_limit)

    model = verifier_tools.resolve(verifier_tools.load_catalog(run), "m")
    text = "\n".join(verifier_tools.audit(run, model, ["m"], sample_nulls=True))
    assert "over a sample of the first 4 rows" in text
    assert "empty: 0 distinct, 4 nulls (100.0%)" in text
    assert "Grain key: id (10 distinct = row count, unique)" in text


async def test_map_columns_stays_inside_the_workspace(
    shop: Path, project: Path, tmp_path: Path
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = await call(
        shop,
        "map_columns",
        ("--workspace", str(workspace)),
        model_name="customer_orders",
        project_dir=str(project),
    )
    assert outside.is_error
    assert "outside the workspace" in text_of(outside)
    inner = workspace / "dbt_shop"
    shutil.copytree(project, inner)
    (inner / "models" / "marts" / "customer_orders.sql").unlink()
    (inner / "models" / "marts" / "customer_orders.sql").symlink_to(
        project / "models" / "marts" / "customer_orders.sql"
    )
    linked_out = await call(
        shop,
        "map_columns",
        ("--workspace", str(workspace)),
        model_name="customer_orders",
        project_dir=str(inner),
    )
    assert linked_out.is_error
    assert "no SQL file for model 'customer_orders'" in text_of(linked_out)
    inside = await call(
        shop,
        "map_columns",
        ("--workspace", str(workspace)),
        model_name="stg_orders",
        project_dir=str(inner),
    )
    assert not inside.is_error, text_of(inside)


def test_yml_columns_reads_a_column_list_at_the_key_indent(tmp_path: Path) -> None:
    (tmp_path / "schema.yml").write_text(
        "version: 2\nmodels:\n  - name: m\n    columns:\n    - name: customer_id\n"
        "      tests:\n        - unique\n    - name: total_orders\n    description: x\n"
        "  - name: other\n    columns:\n      - name: nope\n"
    )
    assert yml_columns(tmp_path, "m") == ["customer_id", "total_orders"]


def test_project_yaml_may_start_sequences_at_column_zero(tmp_path: Path) -> None:
    (tmp_path / "schema.yml").write_text(
        "models:\n- name: m\n  columns:\n  - name: a\n  - name: b\n"
        "sources:\n- name: src\n  schema: landing\n  tables:\n  - name: t\n"
    )
    assert yml_columns(tmp_path, "m") == ["a", "b"]
    assert source_relations(tmp_path) == {("src", "t"): ("landing", "t")}


async def test_verify_model_values_slices_across_upstreams(tmp_path: Path) -> None:
    db = make_db(
        tmp_path / "across.duckdb",
        "CREATE TABLE customers (customer_id VARCHAR, segment VARCHAR);"
        "INSERT INTO customers VALUES ('c1', 'retail'), ('c2', 'wholesale');"
        "CREATE TABLE orders (order_id INT, customer_id VARCHAR, region VARCHAR);"
        "INSERT INTO orders VALUES (1, 'c1', 'north'), (2, 'c1', 'north'), (3, 'c2', 'north');"
        "CREATE TABLE by_region_segment AS SELECT o.region, c.segment, count(*) AS n_orders "
        "FROM orders o JOIN customers c USING (customer_id) GROUP BY o.region, c.segment",
    )
    result = await call(
        db, "verify_model_values", model_name="by_region_segment", source_tables="orders, customers"
    )
    block = metric_block(text_of(result), "n_orders")
    assert "main.orders (3 rows), sliced on region; segment via main.customers.customer_id" in block
    assert "COUNT(*): 2" in block and "Model matches COUNT(*)" in block


async def test_verify_model_values_slices_on_an_integer_key(tmp_path: Path) -> None:
    db = make_db(
        tmp_path / "intkey.duckdb",
        "CREATE TABLE orders (order_id INT, customer_id INT);"
        "INSERT INTO orders VALUES (1, 7), (2, 7), (3, 8);"
        "CREATE TABLE per_customer AS SELECT customer_id, count(*) AS n_orders "
        "FROM orders GROUP BY customer_id",
    )
    result = await call(
        db, "verify_model_values", model_name="per_customer", source_tables="orders"
    )
    text = text_of(result)
    assert "### Metric: customer_id" not in text
    block = metric_block(text, "n_orders")
    assert "sliced on customer_id" in block
    assert "COUNT(*): 2" in block and "Model matches COUNT(*)" in block


async def test_verify_model_values_keeps_a_null_dimension_in_the_slice(tmp_path: Path) -> None:
    db = make_db(
        tmp_path / "nulldim.duckdb",
        "CREATE TABLE orders (order_id INT, region VARCHAR, channel VARCHAR);"
        "INSERT INTO orders VALUES (1, 'north', NULL), (2, 'north', NULL), (3, 'north', NULL),"
        " (4, 'north', 'web');"
        "CREATE TABLE by_channel AS SELECT region, channel, count(*) AS n_orders "
        "FROM orders GROUP BY region, channel;"
        "CREATE TABLE by_channel_only AS SELECT channel, count(*) AS n_orders "
        "FROM orders GROUP BY channel",
    )
    result = await call(db, "verify_model_values", model_name="by_channel", source_tables="orders")
    text = text_of(result)
    assert "channel IS NULL" in text
    block = metric_block(text, "n_orders")
    assert "sliced on region, channel" in block
    assert "COUNT(*): 3" in block and "Model matches COUNT(*)" in block
    only = await call(
        db, "verify_model_values", model_name="by_channel_only", source_tables="orders"
    )
    assert "Model matches COUNT(*)" in metric_block(text_of(only), "n_orders")


def test_model_sql_looks_only_under_the_model_paths(project: Path) -> None:
    (project / "analyses").mkdir()
    (project / "analyses" / "stg_orders.sql").write_text("SELECT 1 AS wrong")
    (project / "analyses" / "only_analysis.sql").write_text("SELECT 1 AS wrong")
    found = verifier_tools.model_sql(project, "stg_orders")
    assert found == project / "models" / "staging" / "stg_orders.sql"
    assert verifier_tools.model_sql(project, "only_analysis") is None
    (project / "dbt_project.yml").write_text("name: shop\nmodel-paths:\n  - transforms\n")
    (project / "transforms").mkdir()
    (project / "transforms" / "t.sql").write_text("SELECT 1 AS x")
    assert verifier_tools.model_sql(project, "t") == project / "transforms" / "t.sql"
    assert verifier_tools.model_sql(project, "stg_orders") is None


def test_audit_finds_a_grain_key_past_the_fifth_column(tmp_path: Path) -> None:
    db = make_db(
        tmp_path / "late_key.duckdb",
        "CREATE TABLE m AS SELECT 'a' AS c1, 'a' AS c2, 'a' AS c3, 'a' AS c4, 'a' AS c5, "
        "range AS row_id FROM range(3)",
    )
    gateway = DuckDBGateway(db)

    def run(sql: str, row_limit: int) -> QueryResult:
        return run_query(gateway, sql, row_limit)

    model = verifier_tools.resolve(verifier_tools.load_catalog(run), "m")
    text = "\n".join(verifier_tools.audit(run, model, ["m"], sample_nulls=True))
    assert "Grain key: row_id (3 distinct = row count, unique)" in text
