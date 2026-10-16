"""Verifier subagents: offered only to dbt runs whose config turns the layer on, read-only, and
recorded as verifier_start / verifier_end events with their per-check verdicts."""

import json
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, HookContext, HookMatcher
from claude_agent_sdk.types import HookEvent

from data_agent import agent
from data_agent.agent import ModelSettings, Task
from data_agent.events import EventLog
from data_agent.run_config import load_config
from data_agent.verifiers import VERIFIER_NAMES, VERIFIERS_PROMPT, parse_checks, verifier_hooks

SETTINGS = ModelSettings.from_env({"DATA_AGENT_API_KEY": "k"})
REPORT = """\
## Structure Report

### customer_ltv
- CHECK 1: PASS - materialized as a table
- CHECK 2: FAIL - missing lifetime_value
- CHECK 3: **NEEDS_INFO** - audit_model_sources errored
- CHECK 4: PASS - none
- CHECK 5: N/A - created from scratch

### Summary
PASS: 0 models
FAIL: 1 models - customer_ltv (CHECK 2)
"""


def options(tmp_path: Path, task_type: str, config: str) -> ClaudeAgentOptions:
    task = Task(text="t", type=task_type, workspace=tmp_path, duckdb=tmp_path / "x.duckdb")  # pyright: ignore[reportArgumentType]
    return agent.build_options(task, load_config(config), SETTINGS, [], lambda *_, **__: None)


def test_dbt_runs_offer_both_read_only_verifiers(tmp_path: Path) -> None:
    built = options(tmp_path, "dbt", "full")
    assert set(built.agents or {}) == set(VERIFIER_NAMES) == {"verifier", "value-verifier"}
    assert "Agent" in (built.tools or []) and "Agent" in built.allowed_tools
    for definition in (built.agents or {}).values():
        tools = set(definition.tools or [])
        assert tools >= {"Read", "mcp__data__query_database"}
        assert not tools & {"Write", "Edit", "Bash", "Agent"}
        assert "NEEDS_INFO" in definition.prompt and "N/A" in definition.prompt
    assert {"SubagentStart", "SubagentStop"} <= set(built.hooks or {})
    # A background verifier ends the main turn before its report is in, splitting the run.
    assert built.env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    servers: Any = built.mcp_servers
    server = servers["data"]
    assert server["args"][-2:] == ["--workspace", str(tmp_path.resolve())]
    assert "--no-verifier-tools" not in server["args"]
    assert VERIFIERS_PROMPT in str(built.system_prompt)


@pytest.mark.parametrize(
    ("task_type", "config"), [("dbt", "no-verifiers"), ("dbt", "baseline"), ("query", "full")]
)
def test_verifiers_are_absent_when_off_or_for_query_runs(
    tmp_path: Path, task_type: str, config: str
) -> None:
    built = options(tmp_path, task_type, config)
    assert not built.agents
    assert "Agent" not in (built.tools or []) and "Agent" not in built.allowed_tools
    assert not {"SubagentStart", "SubagentStop"} & set(built.hooks or {})
    assert VERIFIERS_PROMPT not in str(built.system_prompt)
    # The server leaves the verifier tools out too, so the ablation holds for query runs.
    servers: Any = built.mcp_servers
    assert "--no-verifier-tools" in servers["data"]["args"]


def test_verifier_definitions_name_only_tools_the_server_has() -> None:
    built = options(Path("."), "dbt", "full")
    for definition in (built.agents or {}).values():
        for tool in definition.tools or []:
            if tool.startswith("mcp__"):
                assert tool.removeprefix("mcp__data__") in {
                    "list_tables",
                    "query_database",
                    "map_columns",
                    "check_model_schema",
                    "audit_model_sources",
                    "verify_model_values",
                }


def verifier_prompt() -> str:
    agents = options(Path("."), "dbt", "full").agents or {}
    return agents["verifier"].prompt


def test_verifier_needs_no_table_for_ephemeral_or_disabled_models() -> None:
    check_1 = verifier_prompt().split("### CHECK 1")[1].split("### CHECK 2")[0]
    assert "ephemeral" in check_1 and "enabled: false" in check_1
    assert "dbt_project.yml" in check_1


def test_verifier_reads_fewer_rows_against_the_models_grain() -> None:
    check_3 = verifier_prompt().split("### CHECK 3")[1].split("### CHECK 4")[0]
    assert "Ratio < 0.9 = over-filtering" not in check_3
    assert "GROUP BY" in check_3 and "grain" in check_3


@pytest.mark.parametrize(
    "line",
    [
        "- CHECK 2: FAIL - missing",
        "- **CHECK 2**: FAIL - missing",
        "**CHECK 2 - Aggregate Cross-Validation: FAIL**",
        "- CHECK 2: **FAIL** - missing",
    ],
)
def test_parse_checks_reads_markdown_variants(line: str) -> None:
    assert parse_checks(f"### m\n{line}") == [{"model": "m", "check": "CHECK 2", "status": "FAIL"}]


def test_parse_checks_reads_each_models_verdicts() -> None:
    assert parse_checks(REPORT) == [
        {"model": "customer_ltv", "check": "CHECK 1", "status": "PASS"},
        {"model": "customer_ltv", "check": "CHECK 2", "status": "FAIL"},
        {"model": "customer_ltv", "check": "CHECK 3", "status": "NEEDS_INFO"},
        {"model": "customer_ltv", "check": "CHECK 4", "status": "PASS"},
        {"model": "customer_ltv", "check": "CHECK 5", "status": "N/A"},
    ]


async def fire(
    hooks: dict[HookEvent, list[HookMatcher]], event: HookEvent, hook_input: Any
) -> None:
    for matcher in hooks[event]:
        for callback in matcher.hooks:
            assert await callback(hook_input, None, HookContext(signal=None)) == {}


async def test_hooks_record_verifier_start_and_end_with_verdicts(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    base = {"session_id": "s", "transcript_path": "t", "cwd": str(tmp_path)}
    with EventLog(path, secrets=[]) as log:
        hooks = verifier_hooks(log.emit)
        start = {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "verifier"}
        await fire(hooks, "SubagentStart", base | start)
        stop = {
            "hook_event_name": "SubagentStop",
            "stop_hook_active": False,
            "agent_id": "a1",
            "agent_transcript_path": "x",
            "agent_type": "verifier",
            "last_assistant_message": REPORT,
        }
        await fire(hooks, "SubagentStop", base | stop)
        other = {"hook_event_name": "SubagentStart", "agent_id": "a2", "agent_type": "Explore"}
        await fire(hooks, "SubagentStart", base | other)
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [e["type"] for e in events] == ["verifier_start", "verifier_end"]
    assert events[0]["verifier"] == "verifier" and events[0]["agent_id"] == "a1"
    assert events[1]["report"] == REPORT
    assert [c["status"] for c in events[1]["checks"]] == [
        "PASS",
        "FAIL",
        "NEEDS_INFO",
        "PASS",
        "N/A",
    ]


def value_verifier_prompt() -> str:
    agents = options(Path("."), "dbt", "full").agents or {}
    return agents["value-verifier"].prompt


def test_value_verifier_reads_total_as_a_count_only_for_row_nouns() -> None:
    check_2 = value_verifier_prompt().split("### CHECK 2")[1].split("### CHECK 3")[0]
    assert 'Column named "total_X" → MUST match COUNT(*)' not in check_2
    rule = next(line for line in check_2.splitlines() if "total_<row noun>" in line)
    assert "total_orders" in rule
    sums = check_2.split("is not a count")[1]
    assert "total_amount" in sums and "total_revenue" in sums


def test_value_verifier_compares_sums_and_averages_only_up_to_rounding() -> None:
    """The verifier recomputes over the same slice and filters, so a sum or average may differ
    from the model's only by rounding or float error; a 10% band would pass wrong results."""
    prompt = value_verifier_prompt()
    check_2 = prompt.split("### CHECK 2")[1].split("### CHECK 3")[0]
    flat = " ".join(check_2.split())
    assert "Counts compare exactly: one row off is a mismatch." in flat
    assert "rounded to the column's scale (its DECIMAL places)" in flat
    assert "relative error of at most 1e-6" in flat
    assert "Anything beyond that is a MISMATCH" in flat
    assert "10%" not in prompt


def test_value_verifier_lets_the_tools_sql_verdict_override_the_name_rules() -> None:
    check_2 = value_verifier_prompt().split("### CHECK 2")[1].split("### CHECK 3")[0]
    flat = " ".join(check_2.split())
    read, _, unread = flat.partition('Only when the output says "Model SQL could not be parsed"')
    assert unread, "the name rules need their own unparsed-SQL branch"
    # With the SQL read, the tool's verdict decides, even against a "total" name.
    assert 'starts with "Model SQL read"' in read and "whatever the column is named" in read
    assert '`total_customers` marked "Model matches COUNT(DISTINCT customer_id)" is PASS' in read
    # The name rules, the under-counting FAIL among them, sit only in the unparsed branch.
    for rule in ("total_<row noun>", "unique_X", "under-counting"):
        assert rule not in read and rule in unread, rule


def test_verifier_calls_duplicate_lookup_keys_a_possible_fan_out() -> None:
    check_3 = verifier_prompt().split("### CHECK 3")[1].split("### CHECK 4")[0]
    assert "the fan-out is CORRECT" not in check_3
    assert "byte-identical" not in check_3
    assert "never a reason for PASS" in check_3


def test_verifier_finds_models_under_the_model_paths() -> None:
    check_1 = verifier_prompt().split("### CHECK 1")[1].split("### CHECK 2")[0]
    assert "model-paths" in check_1 and "recursively" in check_1
    assert "models/*.yml" not in check_1


def test_verifier_passes_yml_types_to_check_model_schema() -> None:
    check_2 = verifier_prompt().split("### CHECK 2")[1].split("### CHECK 3")[0]
    assert "check_model_schema" in check_2 and "name:data_type" in check_2
