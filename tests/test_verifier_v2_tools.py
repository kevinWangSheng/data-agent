"""Verifier v2 tools beyond the acceptance tests (ticket 45): both SQL dialects parse the fixture
models, the YAML subset reader, the snapshot's seed limit, the diff's config() reading, the MCP
wrappers (over stdio, with the snapshot named by the environment) and the run's wiring."""

import os
import shutil
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import duckdb
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from data_agent import agent
from data_agent.agent import ModelSettings, Task
from data_agent.gateway import DuckDBGateway
from data_agent.run_config import load_config
from data_agent.verifier_tools import SNAPSHOT_ENV, parse_task_columns
from data_agent.verifier_v2 import compare_by_key, model_diff, take_snapshot, yaml_lite
from data_agent.verifier_v2 import sql as model_sql
from data_agent.verifier_v2.project import model_alias, model_config, project_config
from data_agent.verifier_v2.snapshot import LARGE_SEEDS_FILE, SEED_SIZE_LIMIT

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "verifier_v2"
BEFORE, AFTER = FIXTURES / "project_before", FIXTURES / "project_after"
DATA_AGENT = Path(sys.executable).with_name("data-agent")
DIALECTS = ("duckdb", "postgres")


def fixture_models() -> list[Path]:
    return sorted(p for p in [*BEFORE.rglob("*.sql"), *AFTER.rglob("*.sql")])


# --- sqlglot in both dialects -----------------------------------------------------------------


@pytest.mark.parametrize("dialect", DIALECTS)
def test_every_fixture_model_parses_in_the_dialect(dialect: str) -> None:
    """Acceptance: the fixtures parse under duckdb and postgres alike; only the model with Jinja
    statements falls back."""
    for path in fixture_models():
        tree = model_sql.parse(path.read_text(), dialect)
        if path.stem == "conditional":
            assert tree is None, path
        else:
            assert tree is not None, (dialect, path)


@pytest.mark.parametrize("dialect", DIALECTS)
def test_facts_are_the_same_in_both_dialects(dialect: str) -> None:
    sql = (AFTER / "models" / "staging" / "stg_orders.sql").read_text()
    tree = model_sql.parse(sql, dialect)
    assert tree is not None
    facts = model_sql.facts(tree, dialect)
    assert set(facts["source"]) == {"raw.orders"}
    assert set(facts["filter"]) == {"WHERE amount > 0"}


@pytest.mark.parametrize("dialect", DIALECTS)
def test_dialect_specific_syntax_parses_in_its_dialect(dialect: str) -> None:
    sql = {
        "duckdb": (
            "SELECT a, b FROM {{ ref('t') }} "
            "QUALIFY row_number() OVER (PARTITION BY a ORDER BY b) = 1"
        ),
        "postgres": "SELECT a::text, b FROM {{ ref('t') }} WHERE a ~ '^x' LIMIT 5",
    }[dialect]
    tree = model_sql.parse(sql, dialect)
    assert tree is not None
    facts = model_sql.facts(tree, dialect)
    assert facts["rows"], facts


def test_render_replaces_jinja_with_plain_sql() -> None:
    rendered = model_sql.render(
        "{{ config(materialized='table') }}\n{# note #}\n"
        "SELECT {{ var('x') }} AS v, {{ dbt_utils.star(ref('a')) }} "
        "FROM {{ ref('pkg', 'b') }} JOIN {{ source('raw', 'c') }} USING (id)"
    )
    assert "{{" not in rendered and "{#" not in rendered
    assert "FROM b JOIN raw.c" in rendered
    assert "__dbt_var_x__" in rendered


def test_row_number_equals_one_in_where_is_a_rows_fact() -> None:
    sql = "SELECT * FROM (SELECT a, row_number() OVER (ORDER BY b) AS rn FROM t) WHERE rn = 1"
    tree = model_sql.parse("SELECT a FROM t WHERE row_number() OVER (ORDER BY b) = 1", "duckdb")
    assert tree is not None
    assert any(f.startswith("row_number() = 1") for f in model_sql.facts(tree, "duckdb")["rows"])
    nested = model_sql.parse(sql, "duckdb")
    assert nested is not None
    assert model_sql.facts(nested, "duckdb")["window"]


def test_table_names_leave_out_ctes_and_unparsable_sql() -> None:
    sql = "WITH x AS (SELECT * FROM stg_orders) SELECT k FROM x JOIN cmp_model USING (k)"
    assert model_sql.table_names(sql, "duckdb") == ["cmp_model", "stg_orders"]
    assert model_sql.table_names("SELECT FROM WHERE (", "duckdb") is None


# --- YAML subset ------------------------------------------------------------------------------


def test_yaml_lite_reads_dbt_shapes() -> None:
    data = yaml_lite.load(
        "version: 2\n"
        "# comment\n"
        "models:\n"
        "- name: a  # trailing\n"
        "  description: 'it''s #not a comment'\n"
        "  data_tests:\n"
        "    - dbt_utils.unique_combination_of_columns:\n"
        '        combination_of_columns: [x, "y"]\n'
        "  columns:\n"
        "    - name: x\n"
        "      tests: [unique, not_null]\n"
        "    - name: y\n"
        "      tests:\n"
        "        - not_null\n"
        "        - accepted_values:\n"
        "            values: ['a', 'b']\n"
        "flags: {}\n"
        "enabled: true\n"
        "ratio: 1.5\n"
        "nothing:\n"
    )
    assert data["version"] == 2
    model = data["models"][0]
    assert model["description"] == "it's #not a comment"
    assert model["data_tests"][0]["dbt_utils.unique_combination_of_columns"] == {
        "combination_of_columns": ["x", "y"]
    }
    assert model["columns"][0]["tests"] == ["unique", "not_null"]
    assert model["columns"][1]["tests"][1] == {"accepted_values": {"values": ["a", "b"]}}
    assert data["flags"] == {} and data["enabled"] is True and data["ratio"] == 1.5
    assert data["nothing"] is None


def test_yaml_lite_treats_unsupported_input_as_empty() -> None:
    assert yaml_lite.load("") == {}
    assert yaml_lite.load("key: &anchor value\nother: *anchor\n") == {}
    assert yaml_lite.load("bad\n  indentation: here\n") == {}


def test_model_config_resolves_down_the_directory_path(tmp_path: Path) -> None:
    project = tmp_path / "p"
    (project / "models" / "marts").mkdir(parents=True)
    (project / "dbt_project.yml").write_text(
        "name: shop\nmodel-paths: ['models']\n"
        "vars:\n  cutoff: 2024\n  shop:\n    region: eu\n"
        "models:\n  shop:\n    +materialized: view\n    marts:\n      +materialized: table\n"
        "      daily:\n        materialized: incremental\n        +where: d > 1\n"
    )
    config = project_config(project)
    daily = model_config(config, project, project / "models" / "marts" / "daily.sql")
    other = model_config(config, project, project / "models" / "marts" / "other.sql")
    staging = model_config(config, project, project / "models" / "stg.sql")
    assert daily.values == {"materialized": "incremental", "where": "d > 1"}
    assert other.values == {"materialized": "table", "where": None}
    assert staging.values == {"materialized": "view", "where": None}
    assert daily.vars == {"cutoff": 2024, "region": "eu"}
    assert set(daily.lines()) == {"materialized", "where", "vars"}


def test_model_alias_from_config_call_or_yml(tmp_path: Path) -> None:
    project = tmp_path / "p"
    (project / "models").mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: shop\n")
    (project / "models" / "a.sql").write_text("{{ config(alias='a_table') }} SELECT 1")
    (project / "models" / "b.sql").write_text("SELECT 1")
    (project / "models" / "c.sql").write_text("SELECT 1")
    (project / "models" / "schema.yml").write_text(
        "models:\n  - name: b\n    config:\n      alias: b_table\n  - name: c\n"
    )
    assert model_alias(project, "a") == "a_table"
    assert model_alias(project, "b") == "b_table"
    assert model_alias(project, "c") is None


# --- Snapshot and diff beyond the acceptance tests ----------------------------------------------


def test_snapshot_records_large_seeds_instead_of_copying_them(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = workspace / "proj"
    shutil.copytree(BEFORE, project)
    (project / "seeds").mkdir()
    (project / "seeds" / "small.csv").write_text("a\n1\n")
    (project / "seeds" / "big.csv").write_bytes(b"x" * (SEED_SIZE_LIMIT + 1))
    dest = take_snapshot(workspace, tmp_path / "snap")
    copied = dest / "proj"
    assert (copied / "seeds" / "small.csv").read_text() == "a\n1\n"
    assert not (copied / "seeds" / "big.csv").exists()
    record = (copied / LARGE_SEEDS_FILE).read_text()
    assert "big.csv" in record and str(SEED_SIZE_LIMIT + 1) in record and "sha256" in record


def test_snapshot_refuses_a_destination_inside_the_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(ValueError):
        take_snapshot(workspace, workspace / "snap")


def test_model_diff_reports_a_removed_model_and_a_config_call_change(tmp_path: Path) -> None:
    workspace, snapshot = tmp_path / "ws", tmp_path / "snap"
    shutil.copytree(BEFORE, workspace / "p")
    shutil.copytree(BEFORE, snapshot / "p")
    (workspace / "p" / "models" / "marts" / "top_orders.sql").unlink()
    path = workspace / "p" / "models" / "marts" / "daily_returns.sql"
    path.write_text("{{ config(materialized='incremental') }}\n" + path.read_text())
    diffs = {d["model"]: d for d in model_diff(workspace, snapshot)}
    assert set(diffs) == {"top_orders", "daily_returns"}
    assert diffs["top_orders"]["status"] == "removed"
    [change] = [c for c in diffs["daily_returns"]["changes"] if c["category"] == "config"]
    assert change["kind"] == "added" and "incremental" in change["after"]
    assert not [c for c in diffs["daily_returns"]["changes"] if c["category"] != "config"]


def test_model_diff_keeps_several_additions_apart(tmp_path: Path) -> None:
    workspace, snapshot = tmp_path / "ws", tmp_path / "snap"
    shutil.copytree(BEFORE, workspace / "p")
    shutil.copytree(BEFORE, snapshot / "p")
    path = workspace / "p" / "models" / "staging" / "stg_orders.sql"
    path.write_text(path.read_text() + " AND customer_id IS NOT NULL AND order_date > '2024-01-01'")
    [diff] = model_diff(workspace, snapshot)
    added = [c for c in diff["changes"] if c["category"] == "filter"]
    assert [c["kind"] for c in added] == ["added", "added"]
    assert all(c["before"] is None for c in added)


# --- compare_by_key beyond the acceptance tests ----------------------------------------------


@pytest.fixture
def cmp_db(tmp_path: Path) -> Path:
    path = tmp_path / "cmp.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute((FIXTURES / "verifier_v2.sql").read_text())
    finally:
        con.close()
    return path


def test_compare_by_key_refuses_a_reference_it_cannot_parse(cmp_db: Path) -> None:
    result = compare_by_key(DuckDBGateway(cmp_db), "cmp_model", ["k"], "SELECT FROM WHERE (")
    assert result["accepted"] is False and "parsed" in result["reason"]


def test_compare_by_key_lists_reference_columns_the_model_lacks(cmp_db: Path) -> None:
    sql = "SELECT * FROM (VALUES (1, 5, 'x'), (2, 7, 'y'), (3, NULL, 'z')) AS r(k, int_v, nope)"
    result = compare_by_key(DuckDBGateway(cmp_db), "cmp_model", ["k"], sql)
    assert result["accepted"] is True
    assert result["compared_columns"] == ["int_v"]
    assert result["unmatched_reference_columns"] == ["nope"]
    assert (result["only_in_model"], result["only_in_reference"], result["mismatched"]) == (0, 0, 0)


def test_parse_task_columns_reads_models_and_their_columns() -> None:
    assert parse_task_columns("a: x, y; b: z;; c") == {"a": ["x", "y"], "b": ["z"]}
    assert parse_task_columns("") == {}


# --- MCP wrappers over stdio ------------------------------------------------------------------


@asynccontextmanager
async def mcp_server(
    db: Path, workspace: Path, snapshot: Path | None
) -> AsyncIterator[ClientSession]:
    env = dict(os.environ)
    env.pop(SNAPSHOT_ENV, None)
    if snapshot is not None:
        env[SNAPSHOT_ENV] = str(snapshot)
    params = StdioServerParameters(
        command=str(DATA_AGENT),
        args=["mcp-server", "--config", "full", "--duckdb", str(db), "--workspace", str(workspace)],
        env=env,
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def text_of(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


@pytest.fixture
def edited(tmp_path: Path) -> tuple[Path, Path]:
    workspace, snapshot = tmp_path / "ws", tmp_path / "run" / "snapshot"
    shutil.copytree(AFTER, workspace / "project")
    shutil.copytree(BEFORE, snapshot / "project")
    return workspace, snapshot


async def test_tools_render_their_facts_over_mcp(cmp_db: Path, edited: tuple[Path, Path]) -> None:
    workspace, snapshot = edited
    async with mcp_server(cmp_db, workspace, snapshot) as session:
        diff = await session.call_tool("model_diff", {})
        contract = await session.call_tool(
            "contract_check",
            {
                "models": "stg_orders, customer_ltv",
                "task_columns": "customer_ltv: customer_id, lifetime_value",
            },
        )
        grain = await session.call_tool(
            "grain_facts", {"model": "daily_sales", "key_columns": "store_id"}
        )
        compare = await session.call_tool(
            "compare_by_key",
            {
                "model": "cmp_model",
                "key_columns": "k",
                "reference_sql": "SELECT * FROM (VALUES (1, 5), (2, 8)) AS r(k, int_v)",
            },
        )
        refused = await session.call_tool(
            "compare_by_key",
            {
                "model": "cmp_model",
                "key_columns": "k",
                "reference_sql": "SELECT k, int_v FROM stg_orders",
            },
        )
    for result in (diff, contract, grain, compare, refused):
        assert not result.is_error, text_of(result)
    assert "### stg_orders" in text_of(diff) and "cancelled" in text_of(diff)
    assert "未解析" in text_of(diff)  # the conditional model
    assert "columns: FAIL" in text_of(contract) and "status" in text_of(contract)
    assert "columns: PASS" in text_of(contract)
    assert "duplicate rows (beyond the first per key): 1" in text_of(grain)
    assert "schema.yml" in text_of(grain)
    assert "keys with a value mismatch: 1" in text_of(
        compare
    ) and "only in the model: 1" in text_of(compare)
    assert text_of(refused).startswith("Rejected") and "stg_orders" in text_of(refused)


async def test_tools_say_so_without_a_snapshot(cmp_db: Path, edited: tuple[Path, Path]) -> None:
    workspace, _ = edited
    async with mcp_server(cmp_db, workspace, None) as session:
        result = await session.call_tool("model_diff", {})
    assert result.is_error
    assert SNAPSHOT_ENV in text_of(result)


# --- The run's wiring -------------------------------------------------------------------------


def test_build_options_names_the_snapshot_for_the_server(tmp_path: Path, cmp_db: Path) -> None:
    workspace = tmp_path / "ws"
    shutil.copytree(AFTER, workspace / "project")
    task = Task(text="t", type="dbt", workspace=workspace, duckdb=cmp_db)
    settings = ModelSettings.from_env({"DATA_AGENT_API_KEY": "sk-test"})

    def noop(*args: Any, **fields: Any) -> None:
        pass

    with_snapshot = agent.build_options(
        task, load_config("full"), settings, [], noop, snapshot=tmp_path / "snap"
    )
    without = agent.build_options(task, load_config("full"), settings, [], noop)
    assert with_snapshot.env[SNAPSHOT_ENV] == str(tmp_path / "snap")
    assert SNAPSHOT_ENV not in without.env


def test_taken_snapshot_lives_outside_the_workspace_and_is_removed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    shutil.copytree(BEFORE, workspace / "project")
    with agent.taken_snapshot(workspace) as snapshot:
        assert not snapshot.resolve().is_relative_to(workspace.resolve())
        assert (snapshot / "project" / "dbt_project.yml").is_file()
    assert not snapshot.exists()
