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
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from data_agent import agent
from data_agent.agent import ModelSettings, Task
from data_agent.gateway import DuckDBGateway, PostgresGateway
from data_agent.run_config import load_config
from data_agent.verifier_tools import SNAPSHOT_ENV, parse_task_columns
from data_agent.verifier_v2 import (
    compare_by_key,
    contract_check,
    grain_facts,
    model_diff,
    take_snapshot,
    yaml_lite,
)
from data_agent.verifier_v2 import sql as model_sql
from data_agent.verifier_v2.project import model_alias, model_config, project_config
from data_agent.verifier_v2.render import render_grain
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


def test_table_references_leave_out_ctes_and_unparsable_sql() -> None:
    sql = "WITH x AS (SELECT * FROM raw.stg_orders) SELECT k FROM x JOIN cmp_model USING (k)"
    assert model_sql.table_references(sql, "duckdb") == [(None, "cmp_model"), ("raw", "stg_orders")]
    assert model_sql.table_references("SELECT FROM WHERE (", "duckdb") is None


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


def test_yaml_lite_reads_block_scalars_flow_mappings_and_continued_scalars() -> None:
    """Return 2 (ticket 45): shapes real dbt projects use must not turn the file into `{}`."""
    data = yaml_lite.load(
        "models:\n"
        "  - name: orders\n"
        "    description: |\n"
        "      Literal text.\n"
        "      # kept: not a comment\n"
        "        indented\n"
        "    config: {materialized: table, tags: [a, 'b']}\n"
        "    data_tests:\n"
        "      - dbt_utils.unique_combination_of_columns:\n"
        "          combination_of_columns: [order_id,\n"
        "                                   customer_id]\n"
        "      - dbt_utils.unique_combination_of_columns: {combination_of_columns: [a, b]}\n"
        "    columns:\n"
        "      - name: order_id\n"
        "        description: >-\n"
        "          folded\n"
        "          text\n"
        "\n"
        "          new paragraph\n"
        "        tests: [unique, not_null]\n"
        "      - name: note\n"
        "        description: plain text that\n"
        "          continues here\n"
        "        tests:\n"
        "          - not_null\n"
        "sources:\n"
        "  - name: raw\n"
        "    tables: [{name: customers}, {name: orders, identifier: ord}]\n"
    )
    model = data["models"][0]
    assert model["description"] == "Literal text.\n# kept: not a comment\n  indented\n"
    assert model["config"] == {"materialized": "table", "tags": ["a", "b"]}
    assert [
        t["dbt_utils.unique_combination_of_columns"]["combination_of_columns"]
        for t in model["data_tests"]
    ] == [
        ["order_id", "customer_id"],
        ["a", "b"],
    ]
    assert model["columns"][0]["description"] == "folded text\nnew paragraph"
    assert model["columns"][0]["tests"] == ["unique", "not_null"]
    assert model["columns"][1]["description"] == "plain text that continues here"
    assert model["columns"][1]["tests"] == ["not_null"]
    assert data["sources"][0]["tables"] == [
        {"name": "customers"},
        {"name": "orders", "identifier": "ord"},
    ]
    assert yaml_lite.load("") == {}


@pytest.mark.parametrize(
    "text",
    [
        "key: &anchor value\nother: *anchor\n",
        "bad\n  indentation: here\n",
        "key: |2\n  text\n",
        "key: [a,\n",
        "key: !!str 1\n",
        "- - 1\n  - 2\n",
    ],
)
def test_yaml_lite_raises_on_unsupported_input(text: str) -> None:
    """Return 2: what the subset cannot read is an error the tools report, not `{}`."""
    with pytest.raises(yaml_lite.YamlLiteError, match="line 1"):
        yaml_lite.load(text)


def unreadable_snapshot(tmp_path: Path) -> tuple[Path, Path]:
    """The fixture pair with the snapshot's schema.yml replaced by one the reader refuses."""
    workspace, snapshot = tmp_path / "ws", tmp_path / "snap"
    shutil.copytree(AFTER, workspace / "project")
    shutil.copytree(BEFORE, snapshot / "project")
    (snapshot / "project" / "models" / "schema.yml").write_text(
        "version: 2\ndefaults: &d\n  x: 1\nmodels:\n  - name: stg_orders\n    <<: *d\n"
    )
    return workspace, snapshot


def test_contract_check_says_when_the_snapshot_yml_could_not_be_read(
    cmp_db: Path, tmp_path: Path
) -> None:
    """Return 2: an unreadable snapshot YML is reported, not taken as declaring nothing."""
    workspace, snapshot = unreadable_snapshot(tmp_path)
    gateway = DuckDBGateway(cmp_db)
    results = contract_check(
        workspace,
        snapshot,
        gateway,
        ["stg_orders", "customer_ltv"],
        {"customer_ltv": ["customer_id"]},
    )
    columns = results["stg_orders"]["columns"]
    assert columns["status"] == "N/A"
    assert "snapshot YML could not be read" in columns["evidence"]
    assert "models/schema.yml" in columns["evidence"] and "line 2" in columns["evidence"]
    with_task = results["customer_ltv"]["columns"]
    assert with_task["status"] == "PASS" and with_task["source"] == "task"
    assert "snapshot YML could not be read" in with_task["evidence"]


def test_grain_facts_says_when_the_snapshot_yml_could_not_be_read(
    cmp_db: Path, tmp_path: Path
) -> None:
    workspace, snapshot = unreadable_snapshot(tmp_path)
    facts = grain_facts(workspace, snapshot, DuckDBGateway(cmp_db), "daily_sales", ["store_id"])
    assert facts["exemption"] is None
    assert len(facts["unreadable_yml"]) == 1
    assert facts["unreadable_yml"][0].startswith("project/models/schema.yml (line 2")
    assert "could not be read" in render_grain(facts)


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
    daily_sql = "SELECT 1 WHERE y > {{ var('cutoff') }} AND r = '{{ var(\"region\") }}'"
    daily = model_config(config, project, project / "models" / "marts" / "daily.sql", daily_sql)
    other = model_config(config, project, project / "models" / "marts" / "other.sql", "SELECT 1")
    staging = model_config(config, project, project / "models" / "stg.sql", "SELECT 1")
    assert daily.values == {"materialized": "incremental", "where": "d > 1"}
    assert other.values == {"materialized": "table", "where": None}
    assert staging.values == {"materialized": "view", "where": None}
    # Only the vars a model's SQL reads are its configuration (a var edit must not mark every
    # model changed).
    assert daily.vars == {"cutoff": 2024, "region": "eu"}
    assert other.vars == {}
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


def test_model_diff_shows_a_var_change_only_on_models_that_read_it(tmp_path: Path) -> None:
    workspace, snapshot = tmp_path / "ws", tmp_path / "snap"
    shutil.copytree(BEFORE, workspace / "p")
    shutil.copytree(BEFORE, snapshot / "p")
    for root in (workspace, snapshot):
        path = root / "p" / "models" / "marts" / "top_orders.sql"
        path.write_text(path.read_text() + " OFFSET {{ var('skip', 0) }}")
    (workspace / "p" / "dbt_project.yml").write_text(
        (snapshot / "p" / "dbt_project.yml").read_text() + "vars:\n  skip: 5\n"
    )
    diffs = model_diff(workspace, snapshot)
    assert [d["model"] for d in diffs] == ["top_orders"]
    [change] = diffs[0]["changes"]
    assert change["category"] == "config" and "skip=5" in change["after"]


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


def test_grain_facts_does_not_exempt_on_a_primary_key_inside_the_key(cmp_db: Path) -> None:
    """The snapshot YML tests order_id unique+not_null, so it joins the key; a declaration the
    key already covers is not a finer or different grain (spec review, ticket 45)."""
    workspace, snapshot = cmp_db.parent / "ws", cmp_db.parent / "snap"
    shutil.copytree(AFTER, workspace / "project")
    shutil.copytree(BEFORE, snapshot / "project")
    facts = grain_facts(workspace, snapshot, DuckDBGateway(cmp_db), "stg_orders", ["customer_id"])
    assert facts["key_columns"] == ["customer_id", "order_id"]
    assert facts["exemption"] is None
    assert facts["null_rows"] == 1 and facts["duplicate_rows"] == 0


def test_contract_check_says_not_checked_for_an_unparsed_model(
    cmp_db: Path, edited: tuple[Path, Path]
) -> None:
    """Return 4: a model whose SQL was not parsed has unknown window functions; the check is
    not checked, not "no window function was added or changed"."""
    window = contract_check(*edited, DuckDBGateway(cmp_db), ["conditional"])["conditional"][
        "window"
    ]
    assert window["status"] == "N/A"
    assert "not checked" in window["evidence"] and "not parsed" in window["evidence"]


def test_contract_check_finds_the_diff_under_a_qualified_name_or_alias(
    cmp_db: Path, edited: tuple[Path, Path], tmp_path: Path
) -> None:
    """Return 6: the changed window of ranked_orders is found when the model is named
    `main.ranked_orders`, and when it is a model `rank` built under the alias ranked_orders."""
    gateway = DuckDBGateway(cmp_db)
    qualified = contract_check(*edited, gateway, ["main.ranked_orders"])["main.ranked_orders"]
    assert qualified["window"]["status"] == "FAIL"
    assert qualified["columns"]["source"] is None  # ranked_orders has no snapshot YML entry
    stg = contract_check(*edited, gateway, ["main.stg_orders"])["main.stg_orders"]["columns"]
    assert stg["source"] == "snapshot_yml" and "status" in stg["missing"]
    workspace, snapshot = tmp_path / "ws2", tmp_path / "snap2"
    for root, order_by in ((workspace, "order_date"), (snapshot, "order_date, order_id")):
        (root / "p" / "models").mkdir(parents=True)
        (root / "p" / "dbt_project.yml").write_text("name: p\n")
        (root / "p" / "models" / "rank.sql").write_text(
            "{{ config(alias='ranked_orders') }}\nSELECT *, row_number() OVER (PARTITION BY "
            f"customer_id ORDER BY {order_by}) AS rn FROM {{{{ ref('stg_orders') }}}}\n"
        )
    aliased = contract_check(workspace, snapshot, gateway, ["ranked_orders"])["ranked_orders"]
    assert aliased["exists"]["status"] == "PASS"
    assert aliased["window"]["status"] == "FAIL", aliased["window"]
    assert "order_date" in aliased["window"]["evidence"]


def test_grain_facts_reports_a_declared_key_column_the_model_lacks(
    cmp_db: Path, tmp_path: Path
) -> None:
    """Return 7: a column the snapshot YML tests unique+not_null but the built model does not
    have is a fact about the model, not a tool error; the counts still come back."""
    workspace, snapshot = tmp_path / "ws", tmp_path / "snap"
    shutil.copytree(AFTER, workspace / "project")
    shutil.copytree(BEFORE, snapshot / "project")
    (snapshot / "project" / "models" / "returns.yml").write_text(
        "version: 2\nmodels:\n  - name: daily_returns\n    columns:\n"
        "      - name: return_id\n        tests: [unique, not_null]\n"
    )
    facts = grain_facts(workspace, snapshot, DuckDBGateway(cmp_db), "daily_returns", ["store_id"])
    assert facts["key_columns"] == ["store_id"]
    assert facts["missing_key_columns"] == ["return_id"]
    assert (facts["null_rows"], facts["duplicate_rows"]) == (0, 1)
    assert "return_id" in render_grain(facts)
    with pytest.raises(ToolError, match="no column 'nope'"):
        grain_facts(workspace, snapshot, DuckDBGateway(cmp_db), "daily_returns", ["nope"])


def test_compare_by_key_refuses_the_model_under_a_qualified_name_or_alias(
    cmp_db: Path, tmp_path: Path
) -> None:
    gateway = DuckDBGateway(cmp_db)
    qualified = compare_by_key(gateway, "main.cmp_model", ["k"], "SELECT k, int_v FROM cmp_model")
    assert qualified["accepted"] is False and "cmp_model" in qualified["reason"]
    # A changed model `ltv` built under the alias customer_ltv: the alias is refused too.
    project = tmp_path / "ws" / "p"
    (project / "models").mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: p\n")
    (project / "models" / "ltv.sql").write_text("{{ config(alias='customer_ltv') }} SELECT 1")
    aliased = compare_by_key(
        gateway,
        "cmp_model",
        ["k"],
        "SELECT 1 AS k, 5 AS int_v FROM customer_ltv",
        changed_models=["ltv"],
        workspace=tmp_path / "ws",
    )
    assert aliased["accepted"] is False and "customer_ltv" in aliased["reason"]


def test_compare_by_key_counts_keys_not_joined_rows(cmp_db: Path) -> None:
    con = duckdb.connect(str(cmp_db))
    try:
        con.execute("CREATE TABLE dup_model (k INTEGER, v INTEGER)")
        con.execute("INSERT INTO dup_model VALUES (1, 1), (1, 2), (2, 5), (3, 7), (3, 7)")
    finally:
        con.close()
    sql = "SELECT * FROM (VALUES (1, 9), (1, 9), (2, 5), (4, 0)) AS r(k, v)"
    result = compare_by_key(DuckDBGateway(cmp_db), "dup_model", ["k"], sql)
    assert (result["only_in_model"], result["only_in_reference"], result["mismatched"]) == (1, 1, 1)


def test_compare_by_key_pairs_null_keys_on_both_sides(cmp_db: Path) -> None:
    """A NULL key pairs with a NULL key (as the values rule: NULL equals NULL); a NULL key on
    one side only is one-sided. The pairing must not rely on a join condition Postgres refuses
    (return 1), which the same scenario checks against Postgres below."""
    con = duckdb.connect(str(cmp_db))
    try:
        con.execute("CREATE TABLE null_keys (k INTEGER, v INTEGER)")
        con.execute("INSERT INTO null_keys VALUES (1, 1), (NULL, 2), (3, 3)")
    finally:
        con.close()
    paired = "SELECT * FROM (VALUES (1, 1), (NULL, 2), (3, 9)) AS r(k, v)"
    result = compare_by_key(DuckDBGateway(cmp_db), "null_keys", ["k"], paired)
    assert (result["only_in_model"], result["only_in_reference"], result["mismatched"]) == (0, 0, 1)
    assert result["samples"]["mismatched"][0]["key"] == {"k": 3}
    unpaired = "SELECT * FROM (VALUES (1, 1), (3, 3)) AS r(k, v)"
    result = compare_by_key(DuckDBGateway(cmp_db), "null_keys", ["k"], unpaired)
    assert (result["only_in_model"], result["only_in_reference"], result["mismatched"]) == (1, 0, 0)
    assert result["samples"]["only_in_model"] == [{"k": None}]


def test_compare_by_key_pairs_a_key_column_that_is_null_everywhere(cmp_db: Path) -> None:
    """A composite key whose column is NULL on every row of both sides (a nullable dimension in
    the grain) has no sentinel value; the rows must still pair on the other columns."""
    con = duckdb.connect(str(cmp_db))
    try:
        con.execute("CREATE TABLE all_null (a INTEGER, b VARCHAR, v INTEGER)")
        con.execute("INSERT INTO all_null VALUES (1, NULL, 5), (2, NULL, 6)")
    finally:
        con.close()
    sql = "SELECT * FROM (VALUES (1, CAST(NULL AS VARCHAR), 5), (2, NULL, 7)) AS r(a, b, v)"
    result = compare_by_key(DuckDBGateway(cmp_db), "all_null", ["a", "b"], sql)
    assert (result["only_in_model"], result["only_in_reference"], result["mismatched"]) == (0, 0, 1)
    assert result["samples"]["mismatched"][0]["key"] == {"a": 2, "b": None}


def test_compare_by_key_rejects_query_table(cmp_db: Path) -> None:
    """DuckDB's query_table() names its table in a string sqlglot's table list cannot see."""
    sql = "SELECT k, int_v FROM query_table('cmp_model')"
    result = compare_by_key(DuckDBGateway(cmp_db), "cmp_model", ["k"], sql)
    assert result["accepted"] is False and "query_table" in result["reason"]


def test_compare_by_key_compares_qualified_references_by_schema(cmp_db: Path) -> None:
    """Return 3: a model `orders` built in main may be checked against `raw.orders`, the source
    it reads; `orders` and `main.orders` are still the model itself, and a changed model whose
    schema the catalog does not know is refused under any qualifier."""
    con = duckdb.connect(str(cmp_db))
    try:
        con.execute("CREATE SCHEMA raw")
        con.execute("CREATE TABLE raw.orders (k INTEGER, v INTEGER)")
        con.execute("INSERT INTO raw.orders VALUES (1, 10), (2, 20)")
        con.execute("CREATE TABLE orders AS SELECT * FROM raw.orders")
    finally:
        con.close()
    gateway = DuckDBGateway(cmp_db)
    source = compare_by_key(gateway, "main.orders", ["k"], "SELECT k, v FROM raw.orders")
    assert source["accepted"] is True, source
    assert (source["only_in_model"], source["only_in_reference"], source["mismatched"]) == (0, 0, 0)
    for itself in ("SELECT k, v FROM orders", "SELECT k, v FROM main.orders"):
        refused = compare_by_key(gateway, "main.orders", ["k"], itself)
        assert refused["accepted"] is False and "orders" in refused["reason"], itself
    ghost = compare_by_key(
        gateway, "main.orders", ["k"], "SELECT k, v FROM raw.ghost", changed_models=["ghost"]
    )
    assert ghost["accepted"] is False and "raw.ghost" in ghost["reason"]


def test_compare_by_key_refuses_the_model_under_its_database_name(cmp_db: Path) -> None:
    """Return A: DuckDB reads `<dbname>.<table>` from the default schema, so a qualifier that
    is not a schema the catalog knows is no schema at all and the bare name decides."""
    gateway = DuckDBGateway(cmp_db)
    for itself in ("SELECT k, int_v FROM cmp.cmp_model", 'SELECT k, int_v FROM "cmp".cmp_model'):
        refused = compare_by_key(gateway, "cmp_model", ["k"], itself)
        assert refused["accepted"] is False and "cmp_model" in refused["reason"], itself


def test_compare_by_key_casts_a_reference_key_of_another_type(cmp_db: Path) -> None:
    """Return B: a reference whose key is VARCHAR against an INTEGER model key (a VALUES list,
    or a staging model that casts a raw text id) joins on the model column's type instead of
    failing in COALESCE; a key the database cannot cast is a tool error with its reason."""
    gateway = DuckDBGateway(cmp_db)
    sql = "SELECT * FROM (VALUES ('1', 5), ('2', 8), ('4', 1)) AS r(k, int_v)"
    result = compare_by_key(gateway, "cmp_model", ["k"], sql)
    assert result["accepted"] is True, result
    assert (result["only_in_model"], result["only_in_reference"], result["mismatched"]) == (1, 1, 1)
    assert result["samples"]["only_in_model"] == [{"k": 3}]
    assert result["samples"]["only_in_reference"] == [{"k": 4}]
    assert result["samples"]["mismatched"][0]["key"] == {"k": 2}
    bad = "SELECT * FROM (VALUES ('one', 5)) AS r(k, int_v)"
    with pytest.raises(ToolError, match=r"Conversion Error.*'one'"):
        compare_by_key(gateway, "cmp_model", ["k"], bad)


def test_compare_by_key_refuses_a_reference_key_the_cast_would_round(cmp_db: Path) -> None:
    """The cast must not pair keys that are not equal: CAST(2.6 AS INTEGER) is 3, and a DOUBLE
    1.505 lands on a DECIMAL(5,2) key as 1.51. Such a reference is an error naming the value,
    not a match; a reference key whose text survives the cast (a text '3') is fine, and so
    is one the reference casts itself."""
    con = duckdb.connect(str(cmp_db))
    try:
        con.execute("CREATE TABLE dec_key (k DECIMAL(5,2), v INTEGER)")
        con.execute("INSERT INTO dec_key VALUES (1.50, 1)")
    finally:
        con.close()
    gateway = DuckDBGateway(cmp_db)
    rounded = "SELECT * FROM (VALUES (2.6, 7), (3.0, 1)) AS r(k, int_v)"
    with pytest.raises(ToolError, match=r'"k".*INTEGER.*2\.6'):
        compare_by_key(gateway, "cmp_model", ["k"], rounded)
    for exact in (
        "SELECT * FROM (VALUES ('3', 1)) AS r(k, int_v)",
        "SELECT CAST(k AS INTEGER) AS k, int_v FROM (VALUES (3.0, 1)) AS r(k, int_v)",
    ):
        result = compare_by_key(gateway, "cmp_model", ["k"], exact)
        counts = (result["only_in_model"], result["only_in_reference"], result["mismatched"])
        assert counts == (2, 0, 1), exact
    narrowed = "SELECT * FROM (VALUES (1.505, 1)) AS r(k, v)"
    with pytest.raises(ToolError, match=r'"k".*DECIMAL\(5,2\).*1\.505'):
        compare_by_key(gateway, "dec_key", ["k"], narrowed)


def test_compare_by_key_reports_a_reference_the_gateway_blocks(cmp_db: Path) -> None:
    """A reference the read-only gateway refuses is a tool error with the gateway's reason."""
    blocked = "SELECT * FROM read_csv('/tmp/x.csv') AS r(k, int_v)"
    with pytest.raises(ToolError, match=r"(?i)read_csv"):
        compare_by_key(DuckDBGateway(cmp_db), "cmp_model", ["k"], blocked)


# --- Against Postgres (return 1): the join condition must be one Postgres full-joins on ----


@pytest.fixture
def postgres_cmp(tmp_path: Path) -> PostgresGateway:
    """cmp_model (one column per equality rule, one NULL key) in the scratch database named by
    DATA_AGENT_TEST_POSTGRES_URL, as tests/test_postgres.py does."""
    import psycopg

    url = os.environ.get("DATA_AGENT_TEST_POSTGRES_URL")
    if not url:
        pytest.fail("DATA_AGENT_TEST_POSTGRES_URL is not set")
    with psycopg.connect(url, autocommit=True) as con:
        con.execute("DROP SCHEMA IF EXISTS da_t45 CASCADE")
        con.execute("CREATE SCHEMA da_t45")
        con.execute(
            "CREATE TABLE da_t45.cmp_model (k INTEGER, dec_v NUMERIC(10,2), "
            "dbl_v DOUBLE PRECISION, int_v INTEGER, str_v TEXT, day_v DATE)"
        )
        con.execute("CREATE TABLE da_t45.all_null (a INTEGER, b TEXT, v INTEGER)")
        con.execute("INSERT INTO da_t45.all_null VALUES (1, NULL, 5), (2, NULL, 6)")
        con.execute("CREATE TABLE da_t45.char_key (k CHAR(3), v INTEGER)")
        con.execute("INSERT INTO da_t45.char_key VALUES ('abc', 1), ('abd', 2)")
        con.execute(
            "INSERT INTO da_t45.cmp_model VALUES "
            "(1, 12.35, 1000000.0, 5, 'a', DATE '2024-01-01'), "
            "(2, 0.10, 0.0, 7, 'b', DATE '2024-02-02'), "
            "(NULL, 99.99, 1e-9, NULL, NULL, DATE '2024-03-03')"
        )
    return PostgresGateway(url)


@pytest.mark.integration
def test_compare_by_key_full_joins_every_key_on_postgres(postgres_cmp: PostgresGateway) -> None:
    """Postgres refuses `FULL JOIN ... ON a IS NOT DISTINCT FROM b` (only merge- or
    hash-joinable conditions); the tool must still pair every key, NULL with NULL, and apply
    the equality rules with the NUMERIC scale read from information_schema."""
    reference = (
        "SELECT * FROM (VALUES "
        "(1, 12.345, 1000000.0000005, 5, 'a', DATE '2024-01-01'), "
        "(2, 0.1, 0.0, 7, 'b', DATE '2024-02-02'), "
        "(NULL, 99.99, 1e-9, NULL, NULL, DATE '2024-03-03'), "
        "(4, 1.0, 1.0, 1, 'd', DATE '2024-04-04')"
        ") AS r(k, dec_v, dbl_v, int_v, str_v, day_v)"
    )
    result = compare_by_key(postgres_cmp, "cmp_model", ["k"], reference)
    assert result["accepted"] is True, result
    assert result["dialect"] == "postgres"
    assert result["compared_columns"] == ["dec_v", "dbl_v", "int_v", "str_v", "day_v"]
    assert (result["only_in_model"], result["only_in_reference"], result["mismatched"]) == (0, 1, 0)
    assert result["samples"]["only_in_reference"] == [{"k": 4}]
    off = reference.replace("12.345", "12.344").replace("'b'", "'B'")
    result = compare_by_key(postgres_cmp, "cmp_model", ["k"], off)
    assert result["mismatched"] == 2
    assert {(m["key"]["k"], m["column"]) for m in result["samples"]["mismatched"]} == {
        (1, "dec_v"),
        (2, "str_v"),
    }
    # A composite key with a column NULL on every row of both sides still pairs.
    composite = "SELECT * FROM (VALUES (1, NULL::text, 5), (2, NULL::text, 7)) AS r(a, b, v)"
    result = compare_by_key(postgres_cmp, "all_null", ["a", "b"], composite)
    assert (result["only_in_model"], result["only_in_reference"], result["mismatched"]) == (0, 0, 1)
    assert result["samples"]["mismatched"][0]["key"] == {"a": 2, "b": None}


@pytest.mark.integration
def test_compare_by_key_casts_a_reference_key_of_another_type_on_postgres(
    postgres_cmp: PostgresGateway,
) -> None:
    """Return B on Postgres: `COALESCE(integer, text)` is an error there too, and `FULL JOIN`
    keeps its hash-joinable condition once the reference key is cast to the model's type; a
    key Postgres cannot cast is a tool error carrying the database's reason."""
    sql = "SELECT * FROM (VALUES ('1'::text, 5), ('2', 8), ('4', 1)) AS r(k, int_v)"
    result = compare_by_key(postgres_cmp, "cmp_model", ["k"], sql)
    assert result["accepted"] is True, result
    assert (result["only_in_model"], result["only_in_reference"], result["mismatched"]) == (1, 1, 1)
    assert result["samples"]["only_in_model"] == [{"k": None}]
    assert result["samples"]["only_in_reference"] == [{"k": 4}]
    assert result["samples"]["mismatched"][0]["key"] == {"k": 2}
    bad = "SELECT * FROM (VALUES ('one'::text, 5)) AS r(k, int_v)"
    with pytest.raises(ToolError, match=r'invalid input syntax.*"one"'):
        compare_by_key(postgres_cmp, "cmp_model", ["k"], bad)
    rounded = "SELECT * FROM (VALUES (2.6, 7)) AS r(k, int_v)"
    with pytest.raises(ToolError, match=r'"k".*integer.*2\.6'):
        compare_by_key(postgres_cmp, "cmp_model", ["k"], rounded)
    # information_schema says `character` without the length, which CAST reads as char(1):
    # a char(n) key is left uncast and pairs with a text reference as before.
    chars = "SELECT * FROM (VALUES ('abc'::text, 1), ('abd', 3)) AS r(k, v)"
    result = compare_by_key(postgres_cmp, "char_key", ["k"], chars)
    assert (result["only_in_model"], result["only_in_reference"], result["mismatched"]) == (0, 0, 1)


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
