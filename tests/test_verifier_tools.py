"""Verifier tools beyond the acceptance tests, driven over stdio like seam B."""

import json
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


async def test_verify_model_values_does_not_bridge_on_a_bare_id(tmp_path: Path) -> None:
    db = make_db(
        tmp_path / "bare_id.duckdb",
        "CREATE TABLE orders (id INT, customer_id INT);"
        "INSERT INTO orders VALUES (1, 2), (2, 2), (3, 1);"
        "CREATE TABLE customers (id INT, name VARCHAR);"
        "INSERT INTO customers VALUES (1, 'ann'), (2, 'bob');"
        "CREATE TABLE by_name AS SELECT c.name, count(*) AS n_orders "
        "FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.name",
    )
    result = await call(
        db, "verify_model_values", model_name="by_name", source_tables="orders, customers"
    )
    block = metric_block(text_of(result), "n_orders")
    # orders.id and customers.id are different entities' keys; joining on them proves nothing.
    assert "main.orders (3 rows) — no slice column and no safe join" in block
    assert "via main.customers.id" not in block


async def test_verify_model_values_slices_on_a_timestamp(tmp_path: Path) -> None:
    db = make_db(
        tmp_path / "hourly.duckdb",
        "CREATE TABLE events (event_id INT, region VARCHAR, hour TIMESTAMP);"
        "INSERT INTO events VALUES (1, 'north', TIMESTAMP '2025-01-01 10:00:00'),"
        " (2, 'north', TIMESTAMP '2025-01-01 10:00:00'),"
        " (3, 'north', TIMESTAMP '2025-01-01 11:00:00');"
        "CREATE TABLE hourly AS SELECT region, hour, count(*) AS n_events "
        "FROM events GROUP BY region, hour",
    )
    result = await call(db, "verify_model_values", model_name="hourly", source_tables="events")
    block = metric_block(text_of(result), "n_events")
    assert "sliced on region, hour" in block
    assert "COUNT(*): 2" in block and "Model matches COUNT(*)" in block


async def test_verify_model_values_marks_an_unsliceable_grouping_column_inconclusive(
    tmp_path: Path,
) -> None:
    db = make_db(
        tmp_path / "blob.duckdb",
        "CREATE TABLE events (event_id INT, region VARCHAR, tag BLOB);"
        "INSERT INTO events VALUES (1, 'north', 'a'::BLOB), (2, 'north', 'a'::BLOB),"
        " (3, 'north', 'b'::BLOB);"
        "CREATE TABLE by_tag AS SELECT region, tag, count(*) AS n_events "
        "FROM events GROUP BY region, tag",
    )
    result = await call(db, "verify_model_values", model_name="by_tag", source_tables="events")
    block = metric_block(text_of(result), "n_events")
    assert "not sliced on tag" in block and "inconclusive" in block
    assert "→" not in block


async def test_verify_model_values_compares_a_scalar_aggregate_with_the_whole_table(
    tmp_path: Path,
) -> None:
    db = make_db(
        tmp_path / "scalar.duckdb",
        "CREATE TABLE events (event_id INT);"
        "INSERT INTO events VALUES (1), (2), (3);"
        "CREATE TABLE totals AS SELECT count(*) AS n_events FROM events",
    )
    result = await call(db, "verify_model_values", model_name="totals", source_tables="events")
    block = metric_block(text_of(result), "n_events")
    assert "Model value: 3" in block
    assert "COUNT(*): 3" in block and "Model matches COUNT(*)" in block


def test_source_relations_reads_a_schema_after_the_tables(tmp_path: Path) -> None:
    (tmp_path / "sources.yml").write_text(
        "version: 2\nsources:\n"
        "  - name: shop\n    tables:\n      - name: orders\n        columns:\n"
        "          - name: order_id\n    schema: raw\n"
        "  - name: other\n    tables:\n      - name: t\n"
    )
    assert source_relations(tmp_path) == {
        ("shop", "orders"): ("raw", "orders"),
        ("other", "t"): ("other", "t"),
    }


SUMS_SQL = """
CREATE TABLE orders (order_id INT, customer_id VARCHAR, quantity INT);
INSERT INTO orders VALUES (1, 'c1', 5), (2, 'c1', 4), (3, 'c2', 1);
CREATE TABLE per_customer AS SELECT customer_id, sum(quantity)::BIGINT AS units,
  count(*) AS orders_placed FROM orders GROUP BY customer_id;
"""


async def test_verify_model_values_holds_only_counts_against_row_counts(tmp_path: Path) -> None:
    db = make_db(tmp_path / "sums.duckdb", SUMS_SQL)
    project = tmp_path / "proj"
    (project / "models").mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: proj\n")
    (project / "models" / "per_customer.sql").write_text(
        "SELECT customer_id, sum(quantity) AS units, count(*) AS orders_placed\n"
        "FROM {{ ref('orders') }} GROUP BY customer_id\n"
    )
    # An integer SUM is not a row count, with or without the SQL.
    bare = text_of(
        await call(db, "verify_model_values", model_name="per_customer", source_tables="orders")
    )
    assert "→" not in metric_block(bare, "units")
    assert "→" not in metric_block(bare, "orders_placed")
    read = text_of(
        await call(
            db,
            "verify_model_values",
            model_name="per_customer",
            source_tables="orders",
            project_dir=str(project),
        )
    )
    assert "→" not in metric_block(read, "units")
    assert "Model matches COUNT(*)" in metric_block(read, "orders_placed")


def test_audit_skips_distinct_counts_of_types_that_cannot_be_compared(tmp_path: Path) -> None:
    db = make_db(
        tmp_path / "json.duckdb",
        "CREATE TABLE m AS SELECT range AS id, '{}'::JSON AS payload FROM range(3)",
    )
    gateway = DuckDBGateway(db)

    def run(sql: str, row_limit: int) -> QueryResult:
        # Postgres cannot compare json values, so count(DISTINCT) on one fails there.
        assert 'DISTINCT "payload"' not in sql
        return run_query(gateway, sql, row_limit)

    model = verifier_tools.resolve(verifier_tools.load_catalog(run), "m")
    text = "\n".join(verifier_tools.audit(run, model, ["m"], sample_nulls=True))
    assert "payload: distinct not counted (JSON), 0 nulls" in text
    assert "Grain key: id" in text


async def test_no_verifier_tools_flag_serves_only_the_other_tools(shop: Path) -> None:
    async with mcp_server(shop, "--no-verifier-tools") as session:
        names = {tool.name for tool in (await session.list_tools()).tools}
    assert "query_database" in names
    assert not names & {
        "map_columns",
        "check_model_schema",
        "audit_model_sources",
        "verify_model_values",
    }


def verify(db: Path, model: str, sources: list[str], sql: str | None) -> str:
    gateway = DuckDBGateway(db)

    def run(statement: str, row_limit: int) -> QueryResult:
        return run_query(gateway, statement, row_limit)

    table = verifier_tools.resolve(verifier_tools.load_catalog(run), model)
    return "\n".join(verifier_tools.verify_values(run, table, sources, sql))


ORDERS_SQL = """
CREATE TABLE orders (order_id INT, customer_id VARCHAR, region VARCHAR, year INT,
  status VARCHAR, amount DOUBLE, ordered_at DATE);
INSERT INTO orders VALUES
  (1, 'c1', 'north', 2024, 'placed', 10, DATE '2024-01-01'),
  (2, 'c1', 'north', 2024, 'returned', 20, DATE '2024-02-01'),
  (3, 'c1', 'north', 2025, 'placed', 30, DATE '2025-03-01'),
  (4, 'c2', 'north', 2025, 'returned', 40, DATE '2025-04-01'),
  (5, 'c3', 'south', 2025, 'placed', 50, DATE '2025-05-01');
"""


def model_db(tmp_path: Path, model_sql: str) -> Path:
    """The orders table plus `m`, built from `model_sql` over it."""
    return make_db(tmp_path / "m.duckdb", f"{ORDERS_SQL}CREATE TABLE m AS {model_sql};")


def test_an_aggregate_output_is_not_a_slice_dimension(tmp_path: Path) -> None:
    sql = (
        "WITH per_customer AS (SELECT customer_id, MAX(ordered_at) AS ordered_at,"
        " count(*) AS n_orders FROM orders GROUP BY customer_id)"
        " SELECT * FROM per_customer"
    )
    text = verify(model_db(tmp_path, sql), "m", ["orders"], sql)
    block = metric_block(text, "n_orders")
    assert "sliced on customer_id\n" in block
    assert "Model matches COUNT(*)" in block


def test_a_numeric_group_by_column_is_a_dimension(tmp_path: Path) -> None:
    sql = "SELECT year, count(*) AS n_orders FROM orders GROUP BY year"
    text = verify(model_db(tmp_path, sql), "m", ["orders"], sql)
    assert "### Metric: year" not in text
    block = metric_block(text, "n_orders")
    assert "sliced on year" in block and "Model matches COUNT(*)" in block


def test_only_an_outermost_count_is_a_count(tmp_path: Path) -> None:
    sql = (
        "SELECT customer_id, CAST(SUM(amount) / COUNT(*) AS BIGINT) AS avg_amount,"
        " COALESCE(COUNT(order_id), 0)::BIGINT AS placed FROM orders GROUP BY customer_id"
    )
    text = verify(model_db(tmp_path, sql), "m", ["orders"], sql)
    assert "→" not in metric_block(text, "avg_amount")
    assert "Model matches COUNT(order_id)" in metric_block(text, "placed")


def test_count_distinct_is_compared_with_count_distinct(tmp_path: Path) -> None:
    sql = (
        "SELECT region, count(DISTINCT o.customer_id) AS customers FROM orders AS o GROUP BY region"
    )
    text = verify(model_db(tmp_path, sql), "m", ["orders"], sql)
    block = metric_block(text, "customers")
    assert "Model matches COUNT(DISTINCT customer_id)" in block
    assert "MISMATCH" not in block


def test_a_conditional_count_carries_its_predicate(tmp_path: Path) -> None:
    sql = (
        "SELECT customer_id, count(CASE WHEN status = 'returned' THEN 1 END) AS returns,"
        " count(*) FILTER (WHERE status = 'returned') AS returns_too"
        " FROM orders GROUP BY customer_id"
    )
    text = verify(model_db(tmp_path, sql), "m", ["orders"], sql)
    for metric in ("returns", "returns_too"):
        block = metric_block(text, metric)
        assert "status = 'returned'" in block and "Model matches" in block, block
        assert "MISMATCH" not in block


def test_a_predicate_the_upstream_lacks_is_inconclusive(tmp_path: Path) -> None:
    sql = (
        "WITH o AS (SELECT *, amount > 15 AS is_big FROM orders)"
        " SELECT customer_id, count(*) FILTER (WHERE is_big) AS n_big"
        " FROM o GROUP BY customer_id"
    )
    text = verify(model_db(tmp_path, sql), "m", ["orders"], sql)
    block = metric_block(text, "n_big")
    assert "is_big" in block and "inconclusive" in block
    assert "→" not in block


def test_counts_are_compared_exactly(tmp_path: Path) -> None:
    db = make_db(
        tmp_path / "off.duckdb",
        "CREATE TABLE orders AS SELECT range AS order_id, 'c1' AS customer_id FROM range(10);"
        "CREATE TABLE m AS SELECT 'c1' AS customer_id, 11 AS n_orders;",
    )
    sql = "SELECT customer_id, count(*) AS n_orders FROM orders GROUP BY customer_id"
    assert "MISMATCH vs COUNT(*)" in metric_block(verify(db, "m", ["orders"], sql), "n_orders")
    # Without the SQL the name rule applies, still exactly.
    assert "MISMATCH vs COUNT(*)" in metric_block(verify(db, "m", ["orders"], None), "n_orders")


def test_unparsed_sql_falls_back_to_the_name_rules(tmp_path: Path) -> None:
    sql = "SELECT region, count(*) AS n_orders FROM orders GROUP BY region"
    db = model_db(tmp_path, sql)
    text = verify(db, "m", ["orders"], "SELECT region,, count(*) AS n_orders FROM (((")
    assert "Model SQL could not be parsed" in text
    assert "Model matches COUNT(*)" in metric_block(text, "n_orders")


async def test_check_model_schema_compares_declared_types(tmp_path: Path) -> None:
    db = make_db(
        tmp_path / "types.duckdb",
        "CREATE TABLE m (id INTEGER, name VARCHAR, amount DECIMAL(10, 2), at TIMESTAMP)",
    )
    result = await call(
        db,
        "check_model_schema",
        model_name="m",
        yml_columns="id:int4, name:text, amount:numeric(10,2), at:varchar",
    )
    text = text_of(result)
    assert not result.is_error, text
    assert "[OK] Matching: id, name, amount, at" in text
    assert "[X] Type mismatch: at (expected varchar, actual TIMESTAMP)" in text
    assert "id (" not in text.split("Type mismatch")[1]


def test_a_count_over_a_column_the_sql_transforms_is_inconclusive(tmp_path: Path) -> None:
    sql = (
        "WITH o AS (SELECT customer_id, COALESCE(status, 'unknown') AS status FROM orders)"
        " SELECT customer_id, COUNT(status) AS n_statuses FROM o GROUP BY customer_id"
    )
    db = make_db(
        tmp_path / "derived.duckdb",
        "CREATE TABLE orders (customer_id VARCHAR, status VARCHAR);"
        "INSERT INTO orders VALUES ('c1', NULL), ('c1', 'ok');"
        f"CREATE TABLE m AS {sql};",
    )
    block = metric_block(verify(db, "m", ["orders"], sql), "n_statuses")
    assert "inconclusive" in block and "→" not in block


def test_a_count_of_an_aggregated_ctes_rows_is_inconclusive(tmp_path: Path) -> None:
    sql = (
        "WITH per_customer AS (SELECT region, customer_id FROM orders GROUP BY region, customer_id)"
        " SELECT region, count(*) AS n_customers FROM per_customer GROUP BY region"
    )
    block = metric_block(verify(model_db(tmp_path, sql), "m", ["orders"], sql), "n_customers")
    assert "inconclusive" in block and "→" not in block


def test_a_wrapped_cte_count_is_still_a_count(tmp_path: Path) -> None:
    sql = (
        "WITH o AS (SELECT customer_id, count(*) AS placed FROM orders GROUP BY customer_id)"
        " SELECT customer_id, COALESCE(placed, 0) AS placed FROM o"
    )
    db = model_db(tmp_path, sql)
    con = duckdb.connect(str(db))
    con.execute("UPDATE m SET placed = 20")
    con.close()
    assert "MISMATCH vs COUNT(*)" in metric_block(verify(db, "m", ["orders"], sql), "placed")


def test_a_computed_non_count_ignores_its_count_like_name(tmp_path: Path) -> None:
    sql = (
        "SELECT customer_id, CAST(SUM(amount) OVER (PARTITION BY customer_id) AS BIGINT)"
        " AS n_amount FROM orders"
    )
    block = metric_block(verify(model_db(tmp_path, sql), "m", ["orders"], sql), "n_amount")
    assert "→" not in block


LOOKUP_SQL = """
CREATE TABLE customers (customer_id VARCHAR, status VARCHAR);
INSERT INTO customers VALUES ('c1', 'active');
CREATE TABLE orders (order_id INT, customer_id VARCHAR, status VARCHAR, other_status VARCHAR,
  year INT);
INSERT INTO orders VALUES (1, 'c1', 'inactive', 'active', 2024),
  (2, 'c2', 'inactive', 'active', 2024);
"""


def test_a_count_keeps_its_columns_table(tmp_path: Path) -> None:
    sql = (
        "SELECT o.customer_id, COUNT(*) FILTER (WHERE customers.status = 'active') AS n_active"
        " FROM orders AS o JOIN customers USING (customer_id) GROUP BY o.customer_id"
    )
    db = make_db(tmp_path / "bind.duckdb", f"{LOOKUP_SQL}CREATE TABLE m AS {sql};")
    block = metric_block(verify(db, "m", ["orders", "customers"], sql), "n_active")
    assert "MISMATCH" not in block
    assert "Model matches" in block.split("main.customers")[1]


def test_a_count_follows_a_renamed_cte_column(tmp_path: Path) -> None:
    sql = (
        "WITH o AS (SELECT customer_id, other_status AS status FROM orders)"
        " SELECT customer_id, COUNT(CASE WHEN status = 'active' THEN 1 END) AS n_active"
        " FROM o GROUP BY customer_id"
    )
    db = make_db(tmp_path / "rename.duckdb", f"{LOOKUP_SQL}CREATE TABLE m AS {sql};")
    block = metric_block(verify(db, "m", ["orders"], sql), "n_active")
    assert "other_status = 'active'" in block and "Model matches" in block


def test_a_computed_grouping_expression_is_not_sliced_as_its_column(tmp_path: Path) -> None:
    sql = "SELECT year % 10 AS year, count(*) AS n_orders FROM orders GROUP BY year % 10"
    db = make_db(tmp_path / "computed.duckdb", f"{LOOKUP_SQL}CREATE TABLE m AS {sql};")
    block = metric_block(verify(db, "m", ["orders"], sql), "n_orders")
    assert "MISMATCH" not in block and "inconclusive" in block


def test_a_cte_named_after_its_table_is_read_through(tmp_path: Path) -> None:
    sql = (
        "WITH orders AS (SELECT * FROM {{ ref('orders') }}),"
        " per_customer AS (SELECT customer_id, count(DISTINCT region) AS regions,"
        " count(*) FILTER (WHERE status = 'placed') AS placed FROM orders GROUP BY customer_id)"
        " SELECT * FROM per_customer"
    )
    plain = sql.replace("{{ ref('orders') }}", "main.orders")
    db = make_db(tmp_path / "idiom.duckdb", f"{ORDERS_SQL}CREATE TABLE m AS {plain};")
    text = verify(db, "m", ["orders"], sql)
    assert "Model matches COUNT(DISTINCT region)" in metric_block(text, "regions")
    assert "Model matches COUNT(*) FILTER(WHERE status = 'placed')" in metric_block(text, "placed")


def aliased_project(tmp_path: Path, model_sql: str, *files: tuple[str, str]) -> Path:
    """A dbt project whose model `m` is `model_sql`, plus the (relative path, text) files."""
    project = tmp_path / "aliased"
    (project / "models").mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: aliased\nversion: '1.0'\n")
    (project / "models" / "m.sql").write_text(model_sql)
    for relative, text in files:
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return project


ALIASED_COUNT = (
    "SELECT customer_id, count(DISTINCT region) AS regions,"
    " count(*) FILTER (WHERE o.status = 'placed') AS placed"
    " FROM {relation} AS o GROUP BY o.customer_id"
)


async def test_verify_model_values_reads_a_ref_through_the_model_alias(tmp_path: Path) -> None:
    plain = ALIASED_COUNT.format(relation="orders_2024")
    db = make_db(
        tmp_path / "alias.duckdb",
        f"{ORDERS_SQL}ALTER TABLE orders RENAME TO orders_2024;CREATE TABLE m_final AS {plain};",
    )
    project = aliased_project(
        tmp_path,
        ALIASED_COUNT.format(relation="{{ ref('stg_orders') }}"),
        (
            "models/stg_orders.sql",
            "{{ config(materialized='table', alias='orders_2024') }}\nSELECT * FROM raw",
        ),
        (
            "models/schema.yml",
            "version: 2\nmodels:\n  - name: m\n    config:\n"
            "      alias: m_final\n    columns:\n      - name: customer_id\n",
        ),
    )
    result = await call(
        db,
        "verify_model_values",
        model_name="m_final",
        source_tables="orders_2024",
        project_dir=str(project),
    )
    text = text_of(result)
    assert "Model SQL read" in text, text
    assert "Model matches COUNT(DISTINCT region)" in metric_block(text, "regions")
    assert "Model matches COUNT(*) FILTER(WHERE status = 'placed')" in metric_block(text, "placed")


async def test_verify_model_values_reads_a_source_through_its_identifier(tmp_path: Path) -> None:
    plain = ALIASED_COUNT.format(relation="orders_2024")
    db = make_db(
        tmp_path / "ident.duckdb",
        f"{ORDERS_SQL}ALTER TABLE orders RENAME TO orders_2024;CREATE TABLE m AS {plain};",
    )
    project = aliased_project(
        tmp_path,
        ALIASED_COUNT.format(relation="{{ source('shop', 'orders') }}"),
        (
            "models/sources.yml",
            "version: 2\nsources:\n  - name: shop\n    schema: main\n"
            "    tables:\n      - name: orders\n        identifier: orders_2024\n",
        ),
    )
    result = await call(
        db,
        "verify_model_values",
        model_name="m",
        source_tables="orders_2024",
        project_dir=str(project),
    )
    text = text_of(result)
    assert "Model matches COUNT(DISTINCT region)" in metric_block(text, "regions"), text
    assert "Model matches COUNT(*) FILTER(WHERE status = 'placed')" in metric_block(text, "placed")


async def test_verify_model_values_prefers_the_manifests_relation_names(tmp_path: Path) -> None:
    plain = ALIASED_COUNT.format(relation="orders_2024")
    db = make_db(
        tmp_path / "manifest.duckdb",
        f"{ORDERS_SQL}ALTER TABLE orders RENAME TO orders_2024;CREATE TABLE m AS {plain};",
    )
    manifest = {
        "nodes": {
            "model.aliased.stg_orders": {
                "resource_type": "model",
                "name": "stg_orders",
                "alias": "orders_2024",
                "schema": "main",
            },
            "model.aliased.m": {
                "resource_type": "model",
                "name": "m",
                "alias": "m",
                "schema": "main",
            },
        },
        "sources": {},
    }
    project = aliased_project(
        tmp_path,
        ALIASED_COUNT.format(relation="{{ ref('stg_orders') }}"),
        # The SQL file's alias is stale; the manifest is what dbt last compiled.
        ("models/stg_orders.sql", "{{ config(alias='orders_old') }}\nSELECT * FROM raw"),
        ("target/manifest.json", json.dumps(manifest)),
    )
    result = await call(
        db,
        "verify_model_values",
        model_name="m",
        source_tables="orders_2024",
        project_dir=str(project),
    )
    text = text_of(result)
    assert "Model matches COUNT(DISTINCT region)" in metric_block(text, "regions"), text


async def test_check_model_schema_compares_nested_types_by_their_element_types(
    tmp_path: Path,
) -> None:
    db = make_db(
        tmp_path / "nested.duckdb",
        "CREATE TABLE m (ids INTEGER[], tags VARCHAR[], attrs MAP(VARCHAR, INTEGER),"
        " point STRUCT(x DOUBLE, y DOUBLE))",
    )
    result = await call(
        db,
        "check_model_schema",
        model_name="m",
        yml_columns=(
            "ids:int4[], tags:integer[], attrs:map(text, int), point:struct(x double, y int)"
        ),
    )
    text = text_of(result)
    assert not result.is_error, text
    mismatched = text.split("Type mismatch")[1]
    assert "tags (" in mismatched and "point (" in mismatched
    assert "ids (" not in mismatched and "attrs (" not in mismatched


def test_an_array_without_element_types_matches_any_array() -> None:
    assert verifier_tools.same_type("integer[]", "ARRAY")
    assert verifier_tools.same_type("LIST(INTEGER)", "INTEGER[]")
    assert not verifier_tools.same_type("integer[]", "VARCHAR[]")


def test_a_count_named_column_the_sql_does_not_show_is_inconclusive(tmp_path: Path) -> None:
    sql = (
        "WITH both_years AS (SELECT customer_id, amount FROM orders WHERE year = 2024"
        " UNION ALL SELECT customer_id, amount FROM orders WHERE year = 2025),"
        " per_customer AS (SELECT customer_id, SUM(amount)::BIGINT AS n_units FROM both_years"
        " GROUP BY customer_id)"
        " SELECT u.customer_id, u.n_units FROM (SELECT * FROM per_customer UNION ALL"
        " SELECT * FROM per_customer WHERE false) AS u"
    )
    text = verify(model_db(tmp_path, sql), "m", ["orders"], sql)
    assert "Model SQL read" in text
    block = metric_block(text, "n_units")
    assert "inconclusive" in block and "→" not in block


def test_declared_columns_split_only_at_top_level_commas() -> None:
    assert verifier_tools.declared_columns(
        "payload:STRUCT(id INT, amount DECIMAL(10,2)), flag:BOOLEAN, id"
    ) == [("payload", "STRUCT(id INT, amount DECIMAL(10,2))"), ("flag", "BOOLEAN"), ("id", "")]


def test_a_constant_output_is_not_a_slice_dimension(tmp_path: Path) -> None:
    sql = "SELECT 'all' AS region, count(*) AS n_orders FROM orders"
    text = verify(model_db(tmp_path, sql), "m", ["orders"], sql)
    assert "compared with whole upstreams" in text, text
    assert "Model matches COUNT(*)" in metric_block(text, "n_orders")


def subdir(tmp_path: Path) -> Path:
    path = tmp_path / "second"
    path.mkdir()
    return path


def test_a_rollup_subtotal_is_not_sliced_as_a_null(tmp_path: Path) -> None:
    sql = "SELECT region, count(*) AS n_orders FROM orders GROUP BY ROLLUP (region)"
    text = verify(model_db(tmp_path, sql), "m", ["orders"], sql)
    assert "region IS NULL" not in text and "MISMATCH" not in text, text
    by_channel = (
        "SELECT status, region, count(*) AS n_orders FROM orders GROUP BY status, ROLLUP (region)"
    )
    block = metric_block(
        verify(model_db(subdir(tmp_path), by_channel), "m", ["orders"], by_channel), "n_orders"
    )
    assert "not sliced on region" in block and "inconclusive" in block, block
    assert "MISMATCH" not in block


def test_a_count_keeps_its_columns_schema(tmp_path: Path) -> None:
    sql = (
        "SELECT r.event_id, COUNT(*) FILTER (WHERE s.status = 'ok') AS n_ok"
        " FROM raw.events AS r JOIN staging.events AS s USING (event_id) GROUP BY r.event_id"
    )
    db = make_db(
        tmp_path / "schemas.duckdb",
        "CREATE SCHEMA raw; CREATE SCHEMA staging;"
        "CREATE TABLE raw.events (event_id INT, status VARCHAR);"
        "CREATE TABLE staging.events (event_id INT, status VARCHAR);"
        "INSERT INTO raw.events VALUES (1, 'bad'), (2, 'bad');"
        "INSERT INTO staging.events VALUES (1, 'ok'), (2, 'ok');"
        f"CREATE TABLE m AS {sql};",
    )
    text = verify(db, "m", ["raw.events", "staging.events"], sql)
    block = metric_block(text, "n_ok")
    assert "MISMATCH" not in block, block
    assert "Model matches" in block.split("staging.events")[1]


POSTGRES_COUNTS = (
    "SELECT customer_id, count(*) FILTER (WHERE status ~ '^ret') AS returns,"
    " count(*) FILTER (WHERE to_char(ordered_at, 'YYYY') = '2025') AS orders_2025"
    " FROM orders GROUP BY customer_id"
)


def test_a_postgres_model_is_read_and_counted_in_postgres() -> None:
    """Read as DuckDB, `~` becomes REGEXP_FULL_MATCH and to_char a plain CAST to text."""
    orders = verifier_tools.Table(
        "public",
        "orders",
        "BASE TABLE",
        [("customer_id", "text"), ("status", "text"), ("ordered_at", "date")],
    )
    model = verifier_tools.Table(
        "public",
        "m",
        "BASE TABLE",
        [("customer_id", "text"), ("returns", "bigint"), ("orders_2025", "bigint")],
    )
    roles, note = verifier_tools.column_roles(
        model, POSTGRES_COUNTS, [orders, model], None, "postgres"
    )
    assert "Model SQL read" in note
    labels = {name: verifier_tools.label(count, "postgres") for name, count in roles.counts.items()}
    assert labels == {
        "returns": "COUNT(*) FILTER(WHERE status ~ '^ret')",
        "orders_2025": "COUNT(*) FILTER(WHERE TO_CHAR(ordered_at, 'YYYY') = '2025')",
    }
    baseline = verifier_tools.baseline_sql(roles.counts["orders_2025"], orders, None, "postgres")
    assert baseline == "COUNT(*) FILTER(WHERE TO_CHAR(\"c\".\"ordered_at\", 'YYYY') = '2025')"
