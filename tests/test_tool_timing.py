"""Tool timing (ticket 51): every tool call's execution time comes from the SDK's PostToolUse and
PostToolUseFailure hooks (`duration_ms`), one `tool_timing` event per call, main agent and
subagents alike; `tool_start`/`tool_end` carry the message's `parent_tool_use_id` as `parent`."""

import json
from pathlib import Path
from typing import Any, cast

from claude_agent_sdk import (
    AssistantMessage,
    HookContext,
    HookInput,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from data_agent import agent
from data_agent.agent import ModelSettings, Task, record_message, tool_timing_hooks
from data_agent.events import EventLog
from data_agent.run_config import load_config

BASE = {"session_id": "s", "transcript_path": "t", "cwd": "/w"}


async def fire(tmp_path: Path, *inputs: dict[str, Any]) -> list[dict[str, Any]]:
    path = tmp_path / "events.jsonl"
    with EventLog(path, secrets=[]) as log:
        hooks = tool_timing_hooks(log.emit)
        assert set(hooks) == {"PostToolUse", "PostToolUseFailure"}
        for hook_input in inputs:
            for matcher in hooks[hook_input["hook_event_name"]]:
                assert matcher.matcher is None  # every tool, built-in and MCP
                for callback in matcher.hooks:
                    fake = cast(HookInput, BASE | hook_input)
                    result = await callback(fake, None, HookContext(signal=None))
                    assert result == {}
    return [json.loads(line) for line in path.read_text().splitlines()]


def timing(event: dict[str, Any]) -> dict[str, Any]:
    keys = ("tool", "tool_use_id", "agent_id", "duration_ms", "failed", "timed_out")
    assert event["type"] == "tool_timing"
    return {key: event[key] for key in keys}


async def test_a_successful_main_agent_call(tmp_path: Path) -> None:
    (event,) = await fire(
        tmp_path,
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_response": {"stdout": "a", "stderr": ""},
            "tool_use_id": "c1",
            "duration_ms": 412,
        },
    )
    assert timing(event) == {
        "tool": "Bash",
        "tool_use_id": "c1",
        "agent_id": None,
        "duration_ms": 412,
        "failed": False,
        "timed_out": False,
    }


async def test_a_failed_call(tmp_path: Path) -> None:
    (event,) = await fire(
        tmp_path,
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Read",
            "tool_input": {"file_path": "/w/x"},
            "tool_use_id": "c2",
            "error": "File does not exist.",
            "duration_ms": 3,
        },
    )
    assert timing(event) == {
        "tool": "Read",
        "tool_use_id": "c2",
        "agent_id": None,
        "duration_ms": 3,
        "failed": True,
        "timed_out": False,
    }


async def test_a_subagent_call_carries_its_agent_id(tmp_path: Path) -> None:
    (event,) = await fire(
        tmp_path,
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "mcp__data__compare_by_key",
            "tool_input": {},
            "tool_response": [{"type": "text", "text": "ok"}],
            "tool_use_id": "c3",
            "agent_id": "a1",
            "agent_type": "verifier",
            "duration_ms": 27000,
        },
    )
    assert timing(event)["agent_id"] == "a1"
    assert timing(event)["duration_ms"] == 27000


async def test_a_gateway_timeout_is_marked_whether_the_call_failed_or_returned(
    tmp_path: Path,
) -> None:
    message = "Query timed out after 150s"
    events = await fire(
        tmp_path,
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "mcp__data__query_database",
            "tool_input": {"sql": "select 1"},
            "tool_use_id": "c4",
            "error": f"Error: {message}",
            "duration_ms": 150004,
        },
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "mcp__data__query_database",
            "tool_input": {"sql": "select 1"},
            "tool_response": [{"type": "text", "text": message}],
            "tool_use_id": "c5",
            "duration_ms": 150002,
        },
    )
    assert [(e["failed"], e["timed_out"]) for e in events] == [(True, True), (False, True)]


async def test_a_call_without_duration_records_none(tmp_path: Path) -> None:
    (event,) = await fire(
        tmp_path,
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Glob",
            "tool_input": {},
            "tool_response": {},
            "tool_use_id": "c6",
        },
    )
    assert event["duration_ms"] is None


def test_every_run_records_tool_timing(tmp_path: Path) -> None:
    settings = ModelSettings.from_env({"DATA_AGENT_API_KEY": "k"})
    for task_type, config in (("dbt", "full"), ("query", "baseline")):
        task = Task(text="t", type=task_type, workspace=tmp_path, duckdb=tmp_path / "x.duckdb")  # pyright: ignore[reportArgumentType]
        built = agent.build_options(task, load_config(config), settings, [], lambda *_, **__: None)
        hooks = built.hooks or {}
        assert {"PostToolUse", "PostToolUseFailure"} <= set(hooks)


def test_tool_start_and_end_carry_the_parent_tool_use_id(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    tools: dict[str, str] = {}
    with EventLog(path, secrets=[]) as log:
        record_message(
            log,
            AssistantMessage(
                content=[ToolUseBlock(id="c1", name="Read", input={})],
                model="m",
                parent_tool_use_id="agent-call",
            ),
            tools,
        )
        record_message(
            log,
            UserMessage(
                content=[ToolResultBlock(tool_use_id="c1", content="x")],
                parent_tool_use_id="agent-call",
            ),
            tools,
        )
        record_message(
            log,
            AssistantMessage(content=[ToolUseBlock(id="c2", name="Bash", input={})], model="m"),
            tools,
        )
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(e["type"], e["parent"]) for e in events] == [
        ("tool_start", "agent-call"),
        ("tool_end", "agent-call"),
        ("tool_start", None),
    ]
