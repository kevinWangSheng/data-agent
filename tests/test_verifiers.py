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
        # A background verifier ends the main turn early and splits the run into several results.
        assert definition.background is False
    assert {"SubagentStart", "SubagentStop"} <= set(built.hooks or {})
    servers: Any = built.mcp_servers
    server = servers["data"]
    assert server["args"][-2:] == ["--workspace", str(tmp_path.resolve())]
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
