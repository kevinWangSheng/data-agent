"""Verifier v2 (ADR-0009): one test per line of the spec's "Testing Decisions" (ticket 44).

Every seam is a pure function in `data_agent.verifier_v2`; the MCP tools and the SDK hooks are
thin wrappers. Tests call the functions directly; hook tests use constructed hook inputs and never
call a model, except the one `integration` test at the end.

Interface fixed here (all paths are `Path`; results are plain dicts and lists):

- Snapshot: `take_snapshot(workspace, dest) -> dest`. `dest` lies outside the workspace and
  mirrors each dbt project at its path relative to the workspace (`dest/<project>/models/...`);
  `dbt_packages/`, `target/` and `logs/` are left out. Tests below build such a layout by hand.
- `model_diff(workspace, snapshot, dialect="duckdb") -> list[dict]`, one per new or changed
  model: `{"model", "status": "new"|"changed", "parsed": bool, "changes": [...], "text_diff":
  str|None}`. Each change is `{"category", "kind", "before", "after"}` with category in
  `source | filter | join | rows | grain | window | config`, kind in `added | removed | changed`,
  `before`/`after` the SQL (or config value) text, None when absent. Unchanged models and
  unchanged logic do not appear. A model whose SQL cannot be parsed (or holds `{% if/for/set %}`)
  has `parsed=False`, `changes=[]` and a unified `text_diff`.
- `contract_check(workspace, snapshot, gateway, models, task_columns=None) -> {model: {...}}`
  with `exists`, `columns`, `window`, each `{"status": "PASS"|"FAIL"|"N/A", "evidence": str}`;
  `columns` adds `source` (`"task"` or `"snapshot_yml"`), `missing` and `extra` lists; `window`
  adds `functions` (the changed window functions it checked). `task_columns` maps a model to
  the output columns the task text requires.
- `grain_facts(workspace, snapshot, gateway, model, key_columns) -> dict` with `key_columns`
  (the given keys plus snapshot-YML unique+not_null columns), `null_rows`, `duplicate_rows`
  (rows beyond the first in each key group) and `exemption`: None, or `{"source":
  "snapshot_yml", "path": str, "columns": [...]}` when the snapshot YML declares a finer grain.
  The verdict is the verifier's (the task text may also exempt; the tool cannot read it).
- `compare_by_key(gateway, model, key_columns, reference_sql, changed_models=()) -> dict`:
  `{"accepted": False, "reason": str}` when the reference SQL reads the model or any of
  `changed_models`; otherwise `{"accepted": True, "only_in_model", "only_in_reference",
  "mismatched"}` (counts) and `samples` with up to 20 entries each: key dicts for the first two,
  `{"key", "column", "model", "reference"}` for mismatches. Compared columns are the reference's
  non-key columns; equality follows the model column's type (spec "compare_by_key").
- `parse_report(report, hit_max_turns=False) -> {"parsed": bool, "models": {model: {n:
  status}}, "overall"}`; `overall` is NEEDS_INFO when nothing parsed or the turn cap was hit,
  else FAIL if any FAIL, NEEDS_INFO if any NEEDS_INFO, else PASS. A check line is
  `CHECK <n>: <STATUS> — <reason>` at the start of a line under a `### <model>` heading.
- `summarize_transcript(path) -> {"turns", "tool_calls"}`; a turn is one model call.
- `rewrite_dispatch(tool_name, tool_input, *, task_file, project_dir, models,
  dispatches_so_far) -> dict`: `{}` unless `tool_name == "Agent"` and `subagent_type` is
  `verifier`; otherwise a PreToolUse hook output whose `updatedInput` is the whole input with
  only `prompt` replaced.
- `record_report(state, report, workspace) -> state` and `stop_decision(state, workspace,
  snapshot) -> {"block": bool, "reasons": [{"model", "rule": 1|2|3, "detail"}]}`; `state`
  starts as `{}` and is opaque.
- `run_hooks(config, *, workspace, snapshot, task_file, emit) -> {HookEvent: [HookMatcher]}`
  with PreToolUse, SubagentStop and Stop when `config.verifiers` is on, `{}` otherwise. Stop
  blocks at most twice, then lets the run end and emits `ended_with_unresolved`. SubagentStop
  feeds the report into the Stop state and emits `verifier_end` with `turns`, `tool_calls` and
  `checks` (the `models` mapping of `parse_report`).
- `agent.run` takes the snapshot through `verifier_v2.take_snapshot` (looked up on the package,
  so a test can replace it) only when the config's verifier layer is on.
- The verifier configs define exactly one subagent, `verifier`, with `maxTurns` 50.
- MCP tools `model_diff`, `contract_check`, `grain_facts`, `compare_by_key` are registered only
  when the verifier layer is on.
"""

import os
import shutil
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    UserMessage,
)
from claude_agent_sdk import query as sdk_query
from claude_agent_sdk.types import HookEvent
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from data_agent import agent, verifier_v2
from data_agent.agent import ModelSettings, Task
from data_agent.gateway import DuckDBGateway
from data_agent.run_config import CONFIG_NAMES, load_config
from data_agent.verifier_v2 import (
    compare_by_key,
    contract_check,
    grain_facts,
    model_diff,
    parse_report,
    record_report,
    rewrite_dispatch,
    run_hooks,
    stop_decision,
    summarize_transcript,
    take_snapshot,
)
from data_agent.workspace_guard import outside_paths

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "verifier_v2"
BEFORE = FIXTURES / "project_before"
AFTER = FIXTURES / "project_after"
BUILD_SQL = FIXTURES / "verifier_v2.sql"
TRANSCRIPT = FIXTURES / "verifier_transcript.jsonl"
DATA_AGENT = Path(sys.executable).with_name("data-agent")

NEW_TOOLS = frozenset({"model_diff", "contract_check", "grain_facts", "compare_by_key"})
VERIFIER_CONFIGS = ("full", "no-skill", "no-context", "full-old-skill")
# spec "派单": the fixed heading over a re-dispatch's extra content, verbatim.
UNVERIFIED_HEADING = "主 agent 提供的反证或补充信息（未经核实，只能当作需要复查的线索）"  # noqa: RUF001
# Models project_after changes or adds, relative to project_before.
CHANGED_MODELS = {
    "stg_orders",
    "stg_customers",
    "all_events",
    "top_orders",
    "daily_sales",
    "ranked_orders",
    "conditional",
    "customer_ltv",
}

tools_xfail = pytest.mark.xfail(
    strict=True, raises=NotImplementedError, reason="ticket 45: snapshot and tools"
)
hooks_xfail = pytest.mark.xfail(
    strict=True, raises=NotImplementedError, reason="ticket 46: harness hooks"
)


def noop(*args: Any, **fields: Any) -> None:
    pass


# --- Fixtures -------------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """The built models, in this test's own DuckDB file."""
    path = tmp_path / "v2.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute(BUILD_SQL.read_text())
    finally:
        con.close()
    return path


@pytest.fixture
def gateway(db: Path) -> DuckDBGateway:
    return DuckDBGateway(db)


@pytest.fixture
def edited(tmp_path: Path) -> tuple[Path, Path]:
    """(workspace, snapshot): the workspace holds project_after at `project/`, the snapshot
    holds project_before at the same relative path, outside the workspace."""
    workspace = tmp_path / "ws"
    snapshot = tmp_path / "run" / "snapshot"
    shutil.copytree(AFTER, workspace / "project")
    shutil.copytree(BEFORE, snapshot / "project")
    return workspace, snapshot


def diff_for(diffs: list[dict[str, Any]], model: str) -> dict[str, Any]:
    found = [d for d in diffs if d["model"] == model]
    assert len(found) == 1, f"{model}: {found}"
    return found[0]


def changes(diff: dict[str, Any], category: str) -> list[dict[str, Any]]:
    return [c for c in diff["changes"] if c["category"] == category]


def change_text(change: dict[str, Any], side: str) -> str:
    return str(change.get(side) or "").lower()


# --- Snapshot and model_diff ----------------------------------------------------------------


@tools_xfail
def test_snapshot_keeps_logic_files_outside_the_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = workspace / "project"
    shutil.copytree(BEFORE, project)
    (project / "dbt_packages" / "pkg" / "macros").mkdir(parents=True)
    (project / "dbt_packages" / "pkg" / "macros" / "m.sql").write_text(
        "{% macro m() %}{% endmacro %}"
    )
    (project / "target").mkdir()
    (project / "target" / "manifest.json").write_text("{}")
    (project / "logs").mkdir()
    (project / "logs" / "dbt.log").write_text("log")
    # One file of each kind the spec's snapshot scope names (spec "快照": 范围).
    logic = {
        "macros/clean.sql": "{% macro clean(x) %}trim({{ x }}){% endmacro %}\n",
        "snapshots/orders_snapshot.sql": "{% snapshot orders_snapshot %}{% endsnapshot %}\n",
        "tests/assert_positive.sql": "SELECT * FROM {{ ref('stg_orders') }} WHERE amount < 0\n",
        "tests/schema.yml": "version: 2\n",
        "seeds/countries.csv": "code,name\nDE,Germany\n",
    }
    for relative, content in logic.items():
        (project / relative).parent.mkdir(parents=True, exist_ok=True)
        (project / relative).write_text(content)
    dest = tmp_path / "run" / "snapshot"

    snapshot = take_snapshot(workspace, dest)

    assert snapshot == dest
    assert not snapshot.resolve().is_relative_to(workspace.resolve())
    copied = snapshot / "project"
    assert (copied / "dbt_project.yml").read_text() == (BEFORE / "dbt_project.yml").read_text()
    for relative in ("models/schema.yml", "models/staging/stg_orders.sql"):
        assert (copied / relative).read_text() == (BEFORE / relative).read_text()
    for relative, content in logic.items():
        assert (copied / relative).read_text() == content, relative
    assert not (copied / "dbt_packages").exists()
    assert not (copied / "target").exists()
    assert not (copied / "logs").exists()
    # The agent's Read is kept inside the workspace by the guard, so the snapshot is out of reach.
    read_input: dict[str, object] = {"file_path": str(copied / "models/staging/stg_orders.sql")}
    assert outside_paths("Read", read_input, workspace.resolve())


@tools_xfail
def test_model_diff_reports_a_removed_filter(edited: tuple[Path, Path]) -> None:
    diff = diff_for(model_diff(*edited), "stg_orders")
    assert diff["status"] == "changed" and diff["parsed"]
    removed = [c for c in changes(diff, "filter") if c["kind"] == "removed"]
    assert any("cancelled" in change_text(c, "before") for c in removed), diff["changes"]
    # The kept predicate is unchanged logic and must not appear.
    assert not any(
        "amount" in change_text(c, "before") + change_text(c, "after") for c in diff["changes"]
    )


@tools_xfail
def test_model_diff_reports_a_swapped_source(edited: tuple[Path, Path]) -> None:
    diff = diff_for(model_diff(*edited), "stg_customers")
    sources = changes(diff, "source")
    assert sources, diff["changes"]
    assert any("customers_v2" in change_text(c, "after") for c in sources)
    assert any(
        "customers" in change_text(c, "before") and "customers_v2" not in change_text(c, "before")
        for c in sources
    )


@tools_xfail
def test_model_diff_reports_union_to_union_all(edited: tuple[Path, Path]) -> None:
    diff = diff_for(model_diff(*edited), "all_events")
    rows = changes(diff, "rows")
    assert any("union all" in change_text(c, "after") for c in rows), diff["changes"]
    assert any(
        "union" in change_text(c, "before") and "union all" not in change_text(c, "before")
        for c in rows
    )


@tools_xfail
def test_model_diff_reports_an_added_limit(edited: tuple[Path, Path]) -> None:
    diff = diff_for(model_diff(*edited), "top_orders")
    added = [c for c in changes(diff, "rows") if c["kind"] == "added"]
    assert any(
        "limit" in change_text(c, "after") and "100" in change_text(c, "after") for c in added
    ), diff["changes"]


@tools_xfail
def test_model_diff_reports_a_materialization_change(edited: tuple[Path, Path]) -> None:
    diff = diff_for(model_diff(*edited), "daily_sales")
    config = changes(diff, "config")
    assert any(
        "view" in change_text(c, "before") and "table" in change_text(c, "after") for c in config
    ), diff["changes"]
    # Its SQL is untouched: no window change (contract_check records that window as N/A).
    assert not changes(diff, "window")


@tools_xfail
def test_model_diff_falls_back_to_a_text_diff_for_jinja_control_flow(
    edited: tuple[Path, Path],
) -> None:
    diff = diff_for(model_diff(*edited), "conditional")
    assert diff["parsed"] is False
    assert diff["changes"] == []
    assert diff["text_diff"] and "'review'" in diff["text_diff"]


@tools_xfail
def test_model_diff_leaves_out_untouched_models(edited: tuple[Path, Path]) -> None:
    diffs = model_diff(*edited)
    assert {d["model"] for d in diffs} == CHANGED_MODELS
    assert diff_for(diffs, "customer_ltv")["status"] == "new"


# --- grain_facts ----------------------------------------------------------------------------


@tools_xfail
def test_grain_facts_counts_null_keys(edited: tuple[Path, Path], gateway: DuckDBGateway) -> None:
    facts = grain_facts(*edited, gateway, "customer_ltv", ["customer_id"])
    assert facts["null_rows"] == 1
    assert facts["duplicate_rows"] == 0
    assert facts["exemption"] is None


@tools_xfail
def test_grain_facts_exempts_duplicates_the_snapshot_yml_explains(
    edited: tuple[Path, Path], gateway: DuckDBGateway
) -> None:
    facts = grain_facts(*edited, gateway, "daily_sales", ["store_id"])
    assert facts["duplicate_rows"] > 0
    exemption = facts["exemption"]
    assert exemption is not None
    assert exemption["source"] == "snapshot_yml"
    assert set(exemption["columns"]) == {"store_id", "sale_date"}
    assert exemption["path"].endswith("schema.yml")


@tools_xfail
def test_grain_facts_ignores_an_exemption_only_the_agent_wrote(
    edited: tuple[Path, Path], gateway: DuckDBGateway
) -> None:
    facts = grain_facts(*edited, gateway, "daily_returns", ["store_id"])
    assert facts["duplicate_rows"] > 0
    assert facts["exemption"] is None


# --- contract_check -------------------------------------------------------------------------


@tools_xfail
def test_contract_check_uses_the_snapshot_yml(
    edited: tuple[Path, Path], gateway: DuckDBGateway
) -> None:
    checks = contract_check(*edited, gateway, ["stg_orders"])["stg_orders"]
    assert checks["exists"]["status"] == "PASS"
    columns = checks["columns"]
    assert columns["status"] == "FAIL"
    assert columns["source"] == "snapshot_yml"
    assert "status" in columns["missing"]


@tools_xfail
def test_contract_check_uses_only_the_task_for_an_agent_written_yml(
    edited: tuple[Path, Path], gateway: DuckDBGateway
) -> None:
    task_columns = {"customer_ltv": ["customer_id", "lifetime_value"]}
    columns = contract_check(*edited, gateway, ["customer_ltv"], task_columns)["customer_ltv"][
        "columns"
    ]
    assert columns["status"] == "PASS", columns
    assert columns["source"] == "task"
    assert "phantom_col" not in columns["missing"]
    assert "phantom_col" not in columns["evidence"]


@tools_xfail
def test_contract_check_marks_an_unchanged_window_na(
    edited: tuple[Path, Path], gateway: DuckDBGateway
) -> None:
    window = contract_check(*edited, gateway, ["daily_sales"])["daily_sales"]["window"]
    assert window["status"] == "N/A"
    assert window["functions"] == []


@tools_xfail
def test_contract_check_finds_a_changed_window_with_non_unique_ordering(
    edited: tuple[Path, Path], gateway: DuckDBGateway
) -> None:
    window = contract_check(*edited, gateway, ["ranked_orders"])["ranked_orders"]["window"]
    assert window["status"] == "FAIL"
    assert len(window["functions"]) == 1
    assert "order_date" in window["evidence"]


# --- compare_by_key -------------------------------------------------------------------------

REFERENCE_ALL = """
SELECT * FROM (VALUES
  (1, 12.35, 1000000.0, 5, 'a', DATE '2024-01-01', true),
  (2, 0.10, 0.0, 7, 'b', DATE '2024-02-02', false),
  (3, 99.99, 1e-9, NULL, NULL, DATE '2024-03-03', true)
) AS r(k, dec_v, dbl_v, int_v, str_v, day_v, bool_v)
"""


def reference(column: str, *rows: tuple[int, str]) -> str:
    values = ", ".join(f"({k}, {value})" for k, value in rows)
    return f"SELECT * FROM (VALUES {values}) AS r(k, {column})"


@tools_xfail
def test_compare_by_key_rejects_a_reference_that_reads_the_model(gateway: DuckDBGateway) -> None:
    result = compare_by_key(gateway, "cmp_model", ["k"], "SELECT k, dec_v FROM cmp_model")
    assert result["accepted"] is False
    assert "cmp_model" in result["reason"]


@tools_xfail
def test_compare_by_key_rejects_a_reference_that_reads_a_changed_model(
    gateway: DuckDBGateway,
) -> None:
    sql = "SELECT customer_id AS k, sum(amount) AS dec_v FROM stg_orders GROUP BY 1"
    result = compare_by_key(gateway, "cmp_model", ["k"], sql, changed_models=["stg_orders"])
    assert result["accepted"] is False
    assert "stg_orders" in result["reason"]


@tools_xfail
def test_compare_by_key_reports_one_sided_and_mismatched_keys(gateway: DuckDBGateway) -> None:
    sql = reference("int_v", (2, "7"), (3, "9"), (4, "1"))
    result = compare_by_key(gateway, "cmp_model", ["k"], sql)
    assert result["accepted"] is True
    assert (result["only_in_model"], result["only_in_reference"], result["mismatched"]) == (1, 1, 1)
    assert result["samples"]["only_in_model"] == [{"k": 1}]
    assert result["samples"]["only_in_reference"] == [{"k": 4}]
    [mismatch] = result["samples"]["mismatched"]
    assert mismatch["key"] == {"k": 3} and mismatch["column"] == "int_v"


@tools_xfail
def test_compare_by_key_rounds_the_reference_to_the_decimal_scale(gateway: DuckDBGateway) -> None:
    equal = reference("dec_v", (1, "12.349"), (2, "0.104"), (3, "99.994"))
    assert compare_by_key(gateway, "cmp_model", ["k"], equal)["mismatched"] == 0
    off = reference("dec_v", (1, "12.344"), (2, "0.10"), (3, "99.99"))
    assert compare_by_key(gateway, "cmp_model", ["k"], off)["mismatched"] == 1


@tools_xfail
def test_compare_by_key_allows_1e6_relative_error_for_floats(gateway: DuckDBGateway) -> None:
    equal = reference("dbl_v", (1, "1000000.5"), (2, "0.0"), (3, "1e-9"))
    assert compare_by_key(gateway, "cmp_model", ["k"], equal)["mismatched"] == 0
    # k=1 is off by 2e-6 relative; k=3 is not 0 while the reference is.
    off = reference("dbl_v", (1, "1000002.0"), (2, "0.0"), (3, "0.0"))
    assert compare_by_key(gateway, "cmp_model", ["k"], off)["mismatched"] == 2


@tools_xfail
def test_compare_by_key_needs_exact_equality_for_other_types(gateway: DuckDBGateway) -> None:
    assert compare_by_key(gateway, "cmp_model", ["k"], REFERENCE_ALL)["mismatched"] == 0
    off = REFERENCE_ALL.replace("'a'", "'A'").replace("DATE '2024-02-02'", "DATE '2024-02-03'")
    result = compare_by_key(gateway, "cmp_model", ["k"], off)
    assert result["mismatched"] == 2
    assert {(m["key"]["k"], m["column"]) for m in result["samples"]["mismatched"]} == {
        (1, "str_v"),
        (2, "day_v"),
    }


@tools_xfail
def test_compare_by_key_treats_two_nulls_as_equal_and_one_as_a_mismatch(
    gateway: DuckDBGateway,
) -> None:
    both_null = reference("int_v", (1, "5"), (2, "7"), (3, "NULL"))
    assert compare_by_key(gateway, "cmp_model", ["k"], both_null)["mismatched"] == 0
    one_null = reference("int_v", (1, "NULL"), (2, "7"), (3, "1"))
    assert compare_by_key(gateway, "cmp_model", ["k"], one_null)["mismatched"] == 2


# --- Report parsing -------------------------------------------------------------------------

FOUR_STATUSES = """## Report

### customer_ltv
CHECK 1: PASS — relation exists
CHECK 2: FAIL — lifetime_value missing
CHECK 3: NEEDS_INFO — which key?
CHECK 4: N/A — no window changed
"""


@hooks_xfail
def test_report_parser_recognizes_the_four_statuses() -> None:
    parsed = parse_report(FOUR_STATUSES)
    assert parsed["parsed"] is True
    assert parsed["models"] == {"customer_ltv": {1: "PASS", 2: "FAIL", 3: "NEEDS_INFO", 4: "N/A"}}
    assert parsed["overall"] == "FAIL"


@hooks_xfail
def test_report_without_check_lines_is_needs_info() -> None:
    parsed = parse_report("I ran out of context before checking anything.")
    assert parsed == {"parsed": False, "models": {}, "overall": "NEEDS_INFO"}


@hooks_xfail
def test_report_cut_off_by_max_turns_is_needs_info() -> None:
    assert parse_report(FOUR_STATUSES, hit_max_turns=True)["overall"] == "NEEDS_INFO"


@hooks_xfail
def test_transcript_summary_counts_turns_and_tool_calls() -> None:
    assert summarize_transcript(TRANSCRIPT) == {"turns": 3, "tool_calls": 4}


# --- Dispatch hook --------------------------------------------------------------------------

DISPATCH = {
    "subagent_type": "verifier",
    "description": "verify models",
    "prompt": "SECRET-ORIGINAL: check customer_ltv; its upstream is raw.orders",
}


def rewrite(
    tool_input: dict[str, Any], dispatches_so_far: int, tmp_path: Path, tool_name: str = "Agent"
) -> dict[str, Any]:
    return rewrite_dispatch(
        tool_name,
        tool_input,
        task_file=tmp_path / "task.md",
        project_dir=tmp_path / "ws" / "project",
        models=["customer_ltv", "stg_orders"],
        dispatches_so_far=dispatches_so_far,
    )


def updated_input(output: dict[str, Any]) -> dict[str, Any]:
    specific = output["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "allow"
    return specific["updatedInput"]


@hooks_xfail
def test_first_dispatch_prompt_is_only_the_template(tmp_path: Path) -> None:
    prompt = updated_input(rewrite(DISPATCH, 0, tmp_path))["prompt"]
    assert str(tmp_path / "task.md") in prompt
    assert str(tmp_path / "ws" / "project") in prompt
    assert "customer_ltv" in prompt and "stg_orders" in prompt
    assert "SECRET-ORIGINAL" not in prompt and "raw.orders" not in prompt
    assert UNVERIFIED_HEADING not in prompt
    # Nothing of the main agent's text survives: another original gives the same prompt.
    other = updated_input(rewrite(DISPATCH | {"prompt": "something else"}, 0, tmp_path))["prompt"]
    assert other == prompt


@hooks_xfail
def test_redispatch_appends_the_main_agents_text_under_the_fixed_heading(tmp_path: Path) -> None:
    prompt = updated_input(rewrite(DISPATCH, 1, tmp_path))["prompt"]
    assert str(tmp_path / "task.md") in prompt
    assert UNVERIFIED_HEADING in prompt
    assert prompt.index(UNVERIFIED_HEADING) < prompt.index("SECRET-ORIGINAL")
    assert prompt.index(str(tmp_path / "task.md")) < prompt.index(UNVERIFIED_HEADING)


@hooks_xfail
def test_rewritten_input_keeps_the_other_fields(tmp_path: Path) -> None:
    updated = updated_input(rewrite(DISPATCH, 0, tmp_path))
    assert updated["subagent_type"] == "verifier"
    assert updated["description"] == "verify models"
    assert set(updated) == set(DISPATCH)


@hooks_xfail
def test_other_subagents_and_tools_are_not_rewritten(tmp_path: Path) -> None:
    assert rewrite(DISPATCH | {"subagent_type": "general-purpose"}, 0, tmp_path) == {}
    assert rewrite({"file_path": "models/x.sql"}, 0, tmp_path, tool_name="Read") == {}


# --- Stop hook ------------------------------------------------------------------------------


def tiny_project(root: Path, alpha: str, bravo: str) -> None:
    (root / "models").mkdir(parents=True)
    (root / "dbt_project.yml").write_text(
        'name: tiny\nversion: "1.0.0"\nconfig-version: 2\nprofile: tiny\nmodel-paths: ["models"]\n'
    )
    (root / "models" / "alpha.sql").write_text(alpha)
    (root / "models" / "bravo.sql").write_text(bravo)


@pytest.fixture
def two_models(tmp_path: Path) -> tuple[Path, Path]:
    """(workspace, snapshot): models `alpha` and `bravo`, both changed since the snapshot."""
    workspace = tmp_path / "ws"
    snapshot = tmp_path / "run" / "snapshot"
    tiny_project(snapshot / "project", "SELECT 1 AS x\n", "SELECT 2 AS y\n")
    tiny_project(
        workspace / "project", "SELECT 1 AS x, 'a' AS tag\n", "SELECT 2 AS y, 'b' AS tag\n"
    )
    return workspace, snapshot


def report(**statuses: str) -> str:
    """A v2 report with one section per model; `statuses` sets CHECK 2, the rest PASS."""
    return "".join(
        f"### {model}\nCHECK 1: PASS — exists\nCHECK 2: {status} — detail\nCHECK 3: PASS — ok\n"
        for model, status in statuses.items()
    )


def blocking_rules(decision: dict[str, Any], model: str) -> set[int]:
    return {r["rule"] for r in decision["reasons"] if r["model"] == model}


@hooks_xfail
def test_stop_blocks_a_changed_model_never_verified(two_models: tuple[Path, Path]) -> None:
    workspace, snapshot = two_models
    decision = stop_decision({}, workspace, snapshot)
    assert decision["block"] is True
    assert blocking_rules(decision, "alpha") == {1}
    assert blocking_rules(decision, "bravo") == {1}


@hooks_xfail
def test_stop_blocks_a_model_changed_after_its_report(two_models: tuple[Path, Path]) -> None:
    workspace, snapshot = two_models
    state = record_report({}, report(alpha="PASS", bravo="PASS"), workspace)
    assert stop_decision(state, workspace, snapshot)["block"] is False
    (workspace / "project" / "models" / "alpha.sql").write_text("SELECT 1 AS x, 'a2' AS tag\n")
    decision = stop_decision(state, workspace, snapshot)
    assert decision["block"] is True
    assert blocking_rules(decision, "alpha") == {2}
    assert not blocking_rules(decision, "bravo")


@hooks_xfail
def test_stop_blocks_on_a_fail(two_models: tuple[Path, Path]) -> None:
    workspace, snapshot = two_models
    state = record_report({}, report(alpha="PASS", bravo="FAIL"), workspace)
    decision = stop_decision(state, workspace, snapshot)
    assert decision["block"] is True
    assert blocking_rules(decision, "bravo") == {3}
    assert not blocking_rules(decision, "alpha")


@hooks_xfail
def test_stop_keeps_state_per_model(two_models: tuple[Path, Path]) -> None:
    """A and B verified, B fails; only A is re-verified and passes: B still blocks."""
    workspace, snapshot = two_models
    state = record_report({}, report(alpha="PASS", bravo="FAIL"), workspace)
    state = record_report(state, report(alpha="PASS"), workspace)
    decision = stop_decision(state, workspace, snapshot)
    assert decision["block"] is True
    assert blocking_rules(decision, "bravo") == {3}
    assert not blocking_rules(decision, "alpha")


@hooks_xfail
def test_stop_does_not_block_on_needs_info(two_models: tuple[Path, Path]) -> None:
    workspace, snapshot = two_models
    state = record_report({}, report(alpha="NEEDS_INFO", bravo="PASS"), workspace)
    decision = stop_decision(state, workspace, snapshot)
    assert decision == {"block": False, "reasons": []}


async def call_hooks(
    hooks: dict[HookEvent, list[HookMatcher]], event: HookEvent, **fields: Any
) -> dict[str, Any]:
    """What the CLI does: call every hook registered for `event` with one hook input."""
    hook_input: dict[str, Any] = {
        "hook_event_name": event,
        "session_id": "s",
        "transcript_path": "/dev/null",
        "cwd": ".",
        **fields,
    }
    merged: dict[str, Any] = {}
    for matcher in hooks.get(event, []):
        for hook in matcher.hooks:
            merged |= dict(await hook(hook_input, None, {"signal": None}))  # type: ignore[arg-type]
    return merged


@hooks_xfail
async def test_stop_lets_the_run_end_after_two_blocks_and_records_it(
    two_models: tuple[Path, Path], tmp_path: Path
) -> None:
    workspace, snapshot = two_models
    events: list[dict[str, Any]] = []
    hooks = run_hooks(
        load_config("full"),
        workspace=workspace,
        snapshot=snapshot,
        task_file=tmp_path / "task.md",
        emit=lambda type, **fields: events.append({"type": type, **fields}),
    )
    await call_hooks(
        hooks,
        "SubagentStop",
        agent_id="a1",
        agent_type="verifier",
        agent_transcript_path=str(TRANSCRIPT),
        last_assistant_message=report(alpha="PASS", bravo="FAIL"),
        stop_hook_active=False,
    )
    [end] = [e for e in events if e["type"] == "verifier_end"]
    assert (end["turns"], end["tool_calls"]) == (3, 4)
    assert end["checks"]["bravo"][2] == "FAIL" and end["checks"]["alpha"][2] == "PASS"

    # The report reached the Stop state: alpha is verified, bravo blocks on its FAIL (rule 3),
    # not on never having been verified.
    first = await call_hooks(hooks, "Stop", stop_hook_active=False)
    assert first["decision"] == "block"
    assert "bravo" in first["reason"] and "alpha" not in first["reason"]
    second = await call_hooks(hooks, "Stop", stop_hook_active=True)
    assert second["decision"] == "block"
    third = await call_hooks(hooks, "Stop", stop_hook_active=True)
    assert "decision" not in third
    [unresolved] = [e for e in events if e["type"] == "ended_with_unresolved"]
    assert "bravo" in unresolved["reason"] and "alpha" not in unresolved["reason"]


@hooks_xfail
def test_hooks_follow_the_verifiers_switch(two_models: tuple[Path, Path], tmp_path: Path) -> None:
    workspace, snapshot = two_models
    for name in CONFIG_NAMES:
        config = load_config(name)
        hooks = run_hooks(
            config,
            workspace=workspace,
            snapshot=snapshot,
            task_file=tmp_path / "task.md",
            emit=noop,
        )
        mounted = {event for event, matchers in hooks.items() if matchers}
        if config.verifiers:
            assert mounted == {"PreToolUse", "SubagentStop", "Stop"}, name
        else:
            assert mounted == set(), name


# --- Configuration --------------------------------------------------------------------------


@asynccontextmanager
async def mcp_server(config: str, db: Path) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(
        command=str(DATA_AGENT),
        args=["mcp-server", "--config", config, "--duckdb", str(db)],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


@pytest.mark.xfail(strict=True, reason="ticket 45: registers the new tools by the verifiers switch")
async def test_new_tools_follow_the_verifiers_switch(db: Path) -> None:
    for name in CONFIG_NAMES:
        async with mcp_server(name, db) as session:
            names = {tool.name for tool in (await session.list_tools()).tools}
        # The value verifier's tool goes with it (spec "工具": 保留的旧工具).
        assert "verify_model_values" not in names, name
        if load_config(name).verifiers:
            assert names >= NEW_TOOLS, name
        else:
            assert not names & NEW_TOOLS, name


def dbt_task(tmp_path: Path, db: Path) -> Task:
    workspace = tmp_path / "ws"
    shutil.copytree(AFTER, workspace / "project")
    return Task(text="build the models", type="dbt", workspace=workspace, duckdb=db)


@pytest.mark.xfail(strict=True, reason="ticket 45: snapshot only in verifier configs")
async def test_snapshot_only_in_verifier_configs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, db: Path
) -> None:
    taken: list[str] = []

    def spy(workspace: Path, dest: Path) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        taken.append(workspace.name)
        return dest

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            result="done",
        )

    monkeypatch.setattr(verifier_v2, "take_snapshot", spy)
    monkeypatch.setattr(agent, "query", fake_query)
    env = {"DATA_AGENT_API_KEY": "sk-test", "PATH": os.environ["PATH"]}
    for name in CONFIG_NAMES:
        taken.clear()
        task = dbt_task(tmp_path / name, db)
        await agent.run(task, load_config(name), tmp_path / name / "events", env)
        assert bool(taken) == load_config(name).verifiers, name


@pytest.mark.xfail(strict=True, reason="ticket 47: one verifier subagent with maxTurns 50")
def test_verifier_configs_define_one_verifier(tmp_path: Path, db: Path) -> None:
    settings = ModelSettings.from_env({"DATA_AGENT_API_KEY": "sk-test"})
    for name in VERIFIER_CONFIGS:
        options = agent.build_options(
            dbt_task(tmp_path / name, db), load_config(name), settings, [], noop
        )
        assert options.agents is not None, name
        assert set(options.agents) == {"verifier"}, name
        assert options.agents["verifier"].maxTurns == 50, name


# --- Integration: updatedInput replaces the Agent prompt (spike 2026-11-04) ----------------


def visible_text(messages: Sequence[Any]) -> str:
    """Text the main agent produced or received back from its tools."""
    parts: list[str] = []
    for message in messages:
        if isinstance(message, AssistantMessage):
            parts += [b.text for b in message.content if isinstance(b, TextBlock)]
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            parts += [str(b.content) for b in message.content if isinstance(b, ToolResultBlock)]
    return "\n".join(parts)


@pytest.mark.integration
@pytest.mark.xfail(strict=True, reason="ticket 46: dispatch rewrite end to end")
async def test_updated_input_replaces_the_prompt_end_to_end(
    edited: tuple[Path, Path], tmp_path: Path
) -> None:
    """The subagent sees only the rewritten prompt (whole-input replacement, CLI behavior; see
    docs/research/2026-11-04-agent-updatedinput-spike.md)."""
    workspace, snapshot = edited
    task_file = tmp_path / "task-v2-marker.md"
    task_file.write_text("Build customer_ltv.\n")
    hooks = run_hooks(
        load_config("full"), workspace=workspace, snapshot=snapshot, task_file=task_file, emit=noop
    )
    settings = ModelSettings.from_env(os.environ)
    options = ClaudeAgentOptions(
        model=settings.model,
        env=settings.sdk_env(),
        permission_mode="bypassPermissions",
        setting_sources=[],
        cwd=str(workspace),
        agents={
            "verifier": AgentDefinition(
                description="Echo checker",
                prompt=(
                    "Reply with exactly: RECEIVED:<the full task text you were given>. "
                    "Nothing else."
                ),
                tools=["Read"],
                model="inherit",
            )
        },
        hooks={"PreToolUse": hooks["PreToolUse"]},
        max_turns=6,
    )
    messages: list[Any] = []
    async for message in sdk_query(
        prompt=(
            "Use the Agent tool with subagent_type 'verifier' and prompt 'ORIGINAL-123'. "
            "Then print the subagent's reply verbatim."
        ),
        options=options,
    ):
        messages.append(message)
    seen = visible_text(messages)
    assert "RECEIVED:" in seen, seen
    assert "RECEIVED:ORIGINAL" not in seen, seen
    assert "task-v2-marker.md" in seen, seen
