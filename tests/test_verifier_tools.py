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
from data_agent.db_facts import load_catalog
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


UPSTREAMS = "stg_customers, stg_orders"


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

    model = verifier_tools.resolve(load_catalog(run), "m")
    text = "\n".join(
        verifier_tools.audit(run, lambda: load_catalog(run), model, ["m"], sample_nulls=True)
    )
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

    model = verifier_tools.resolve(load_catalog(run), "m")
    text = "\n".join(
        verifier_tools.audit(run, lambda: load_catalog(run), model, ["m"], sample_nulls=True)
    )
    assert "Grain key: row_id (3 distinct = row count, unique)" in text


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

    model = verifier_tools.resolve(load_catalog(run), "m")
    text = "\n".join(
        verifier_tools.audit(run, lambda: load_catalog(run), model, ["m"], sample_nulls=True)
    )
    assert "payload: distinct not counted (JSON), 0 nulls" in text
    assert "Grain key: id" in text


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


def test_declared_columns_split_only_at_top_level_commas() -> None:
    assert verifier_tools.declared_columns(
        "payload:STRUCT(id INT, amount DECIMAL(10,2)), flag:BOOLEAN, id"
    ) == [("payload", "STRUCT(id INT, amount DECIMAL(10,2))"), ("flag", "BOOLEAN"), ("id", "")]


async def test_no_verifier_tools_flag_serves_only_the_other_tools(shop: Path) -> None:
    async with mcp_server(shop, "--no-verifier-tools") as session:
        names = {tool.name for tool in (await session.list_tools()).tools}
    assert "query_database" in names
    assert not names & {
        "map_columns",
        "check_model_schema",
        "audit_model_sources",
        "model_diff",
        "contract_check",
        "grain_facts",
        "compare_by_key",
    }
