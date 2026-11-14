"""The turn budget a query agent sees (ticket 69): the task prompt states the run's turn limit, and
the PostToolUse hooks remind the agent once at 10 and once at 3 turns left.

The bundled CLI (2.1.301) counts a turn per main-thread response whose tool calls ran, and ends the
run with `error_max_turns` once a response past the limit would follow; the budget counts the same
thing, one turn per main-thread response (`message_id`) that carries a tool call."""

import json
import shutil
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    HookContext,
    HookInput,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from data_agent import agent
from data_agent.agent import ModelSettings, Task, TurnBudget
from data_agent.run_config import RunConfig, load_config

BASE = {"session_id": "s", "transcript_path": "/t/session.jsonl", "cwd": "/w"}


def response(turn: int, tools: int = 1, parent: str | None = None) -> list[AssistantMessage]:
    """One model response as the SDK delivers it: one AssistantMessage per content block, all
    with the response's id."""
    blocks: list[Any] = [TextBlock(text="thinking aloud")]
    blocks += [ToolUseBlock(id=f"t{turn}-{i}", name="Read", input={}) for i in range(tools)]
    return [
        AssistantMessage(
            content=[block], model="m", parent_tool_use_id=parent, message_id=f"msg{turn}"
        )
        for block in blocks
    ]


async def after_tool(
    budget: TurnBudget, tool_use_id: str, failed: bool = False, **extra: Any
) -> list[dict[str, Any]]:
    """Fire the budget's hook for one finished tool call; returns the hook outputs."""
    event = "PostToolUseFailure" if failed else "PostToolUse"
    outputs = []
    events: list[tuple[str, dict[str, Any]]] = []
    for matcher in budget.hooks(lambda kind, **fields: events.append((kind, fields)))[event]:
        for callback in matcher.hooks:
            hook_input = BASE | {
                "hook_event_name": event,
                "tool_name": "Read",
                "tool_input": {},
                "tool_use_id": tool_use_id,
            }
            hook_input |= {"error": "boom"} if failed else {"tool_response": "ok"}
            output = await callback(
                cast(HookInput, hook_input | extra), None, HookContext(signal=None)
            )
            outputs.append(dict(output))
    return [o for o in outputs if o]


async def play(budget: TurnBudget, turns: int, tools: int = 1) -> dict[int, list[str]]:
    """Run `turns` responses through the budget; maps each turn to the reminders it got."""
    got: dict[int, list[str]] = {}
    for turn in range(1, turns + 1):
        for message in response(turn, tools):
            budget.observe(message)
        for i in range(tools):
            for output in await after_tool(budget, f"t{turn}-{i}"):
                got.setdefault(turn, []).append(output["hookSpecificOutput"]["additionalContext"])
    return got


async def test_reminders_come_once_at_ten_and_at_three_turns_left() -> None:
    got = await play(TurnBudget(50), 50, tools=3)
    # After turn 40's tools, 10 responses are left (41..50); after turn 47's, 3 (48..50).
    assert sorted(got) == [40, 47]
    (ten,) = got[40]
    (three,) = got[47]
    assert "10 turns left" in ten
    assert "3 turns left" in three


async def test_the_reminder_goes_back_as_post_tool_use_context() -> None:
    budget = TurnBudget(4)
    for message in response(1):
        budget.observe(message)
    (output,) = await after_tool(budget, "t1-0")
    assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "3 turns left" in output["hookSpecificOutput"]["additionalContext"]


async def test_a_failed_tool_call_carries_the_reminder_too() -> None:
    budget = TurnBudget(4)
    for message in response(1):
        budget.observe(message)
    (output,) = await after_tool(budget, "t1-0", failed=True)
    assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUseFailure"
    assert "3 turns left" in output["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        # 10 left after turn 1 (11 - 1): both points still apply.
        (11, {1: ["10 turns left"], 8: ["3 turns left"]}),
        # Fewer than 10 left from the start: the 10 point is gone, the first reminder says what
        # is left, and the 3 point still comes.
        (8, {1: ["7 turns left"], 5: ["3 turns left"]}),
        # Both points passed together: one reminder.
        (3, {1: ["2 turns left"]}),
        (4, {1: ["3 turns left"]}),
        # The run ends right after the only turn: no response could read a reminder.
        (1, {}),
    ],
)
async def test_low_limits_get_the_reminders_that_still_apply(
    limit: int, expected: dict[int, list[str]]
) -> None:
    got = await play(TurnBudget(limit), limit)
    assert {turn: len(texts) for turn, texts in got.items()} == {
        turn: len(texts) for turn, texts in expected.items()
    }
    for turn, texts in expected.items():
        for text, want in zip(got[turn], texts, strict=True):
            assert want in text


async def test_one_turn_left_is_singular() -> None:
    budget = TurnBudget(2)
    for message in response(1):
        budget.observe(message)
    (output,) = await after_tool(budget, "t1-0")
    assert "1 turn left" in output["hookSpecificOutput"]["additionalContext"]


async def test_subagent_tool_calls_neither_count_nor_get_reminders() -> None:
    budget = TurnBudget(5)
    for turn in range(1, 4):
        for message in response(100 + turn, parent="agent-call"):
            budget.observe(message)
        assert await after_tool(budget, f"t{100 + turn}-0", agent_id="sub") == []
    for message in response(1):
        budget.observe(message)
    (output,) = await after_tool(budget, "t1-0")
    assert "4 turns left" in output["hookSpecificOutput"]["additionalContext"]


async def test_a_tool_call_the_budget_has_not_seen_gets_no_reminder() -> None:
    # Never a guessed count: the call's response is unknown, so nothing is said this time.
    budget = TurnBudget(4)
    assert await after_tool(budget, "unknown") == []
    for message in response(1):
        budget.observe(message)
    assert len(await after_tool(budget, "t1-0")) == 1


async def test_a_response_without_an_id_stops_the_reminders() -> None:
    budget = TurnBudget(4)
    budget.observe(
        AssistantMessage(content=[ToolUseBlock(id="x", name="Read", input={})], model="m")
    )
    for message in response(2):
        budget.observe(message)
    assert await after_tool(budget, "x") == []
    assert await after_tool(budget, "t2-0") == []


async def test_each_reminder_is_recorded_as_an_event() -> None:
    budget = TurnBudget(4)
    for message in response(1):
        budget.observe(message)
    events: list[tuple[str, dict[str, Any]]] = []
    (matcher,) = budget.hooks(lambda kind, **fields: events.append((kind, fields)))["PostToolUse"]
    hook_input = BASE | {
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_input": {},
        "tool_response": "ok",
        "tool_use_id": "t1-0",
    }
    await matcher.hooks[0](cast(HookInput, hook_input), None, HookContext(signal=None))
    assert events == [
        (
            "turn_budget",
            {
                "turn": 1,
                "turns_left": 3,
                "max_turns": 4,
                "tool_use_id": "t1-0",
                "transcript_path": "/t/session.jsonl",
                "text": events[0][1]["text"],
            },
        )
    ]
    assert "3 turns left" in events[0][1]["text"]


async def fire_post_tool_use(options: Any, tool_use_id: str) -> list[str]:
    """What the CLI would add to the context after one main-thread Read call: the
    additionalContext of every PostToolUse hook that matches Read."""
    texts = []
    for matcher in (options.hooks or {}).get("PostToolUse", []):
        if matcher.matcher is not None and "Read" not in matcher.matcher.split("|"):
            continue
        for callback in matcher.hooks:
            hook_input = BASE | {
                "hook_event_name": "PostToolUse",
                "tool_name": "Read",
                "tool_input": {},
                "tool_response": "ok",
                "tool_use_id": tool_use_id,
            }
            output: Any = await callback(
                cast(HookInput, hook_input), None, HookContext(signal=None)
            )
            text = (output or {}).get("hookSpecificOutput", {}).get("additionalContext")
            if text:
                texts.append(text)
    return texts


SETTINGS = ModelSettings.from_env({"DATA_AGENT_API_KEY": "k"})


@pytest.mark.parametrize("skill", [True, False])
@pytest.mark.parametrize("verifiers", [True, False])
@pytest.mark.parametrize("context", [True, False])
async def test_query_runs_get_the_budget_under_every_layer_switch(
    tmp_path: Path, skill: bool, verifiers: bool, context: bool
) -> None:
    config = replace(load_config("full"), skill=skill, verifiers=verifiers, context=context)
    task = Task(text="q", type="query", workspace=tmp_path, duckdb=tmp_path / "x.duckdb")
    budget = TurnBudget(4)
    options = agent.build_options(
        task, config, SETTINGS, [], lambda *_, **__: None, turn_budget=budget
    )
    # The session's limit is the budget's: the reminders count against the limit in force.
    assert options.max_turns == 4
    for message in response(1):
        budget.observe(message)
    (text,) = await fire_post_tool_use(options, "t1-0")
    assert "3 turns left" in text


def test_a_query_run_given_no_budget_keeps_its_configured_limit(tmp_path: Path) -> None:
    task = Task(text="q", type="query", workspace=tmp_path, duckdb=tmp_path / "x.duckdb")
    config = load_config("baseline")
    options = agent.build_options(task, config, SETTINGS, [], lambda *_, **__: None)
    assert options.max_turns == config.max_turns["query"] == 50


# --- the product run (`data-agent run`) -------------------------------------------------------

RESULT = ResultMessage(
    subtype="success",
    duration_ms=1,
    duration_api_ms=1,
    is_error=False,
    num_turns=1,
    session_id="s",
    usage={},
    total_cost_usd=0.0,
    result="done",
)


def scripted_cli(turns: int, seen: dict[str, Any]) -> Callable[..., AsyncIterator[Any]]:
    """A CLI stand-in: `turns` responses of one Read call each; after each call it fires the
    options' PostToolUse hooks, as the CLI does, and keeps what they add to the context."""

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        seen["prompt"], seen["options"], seen["reminders"] = prompt, options, {}
        for turn in range(1, turns + 1):
            for message in response(turn):
                yield message
            texts = await fire_post_tool_use(options, f"t{turn}-0")
            if texts:
                seen["reminders"][turn] = texts
        yield RESULT

    return fake_query


async def product_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, task_type: str, config: RunConfig, turns: int
) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(agent, "query", scripted_cli(turns, seen))
    monkeypatch.setattr(shutil, "which", lambda *_, **__: "/usr/bin/dbt")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    db = workspace / "x.duckdb"
    duckdb.connect(str(db)).close()
    task = Task(text="How many?", type=task_type, workspace=workspace, duckdb=db)  # pyright: ignore[reportArgumentType]
    outcome = await agent.run(task, config, tmp_path / "events", {"DATA_AGENT_API_KEY": "k"})
    seen["events"] = [json.loads(line) for line in outcome.events_path.read_text().splitlines()]
    return seen


def with_limits(name: str, **limits: int) -> RunConfig:
    config = load_config(name)
    return replace(config, max_turns={**config.max_turns, **limits})


async def test_a_query_run_states_its_limit_and_reminds_the_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = with_limits("baseline", query=12)
    seen = await product_run(monkeypatch, tmp_path, "query", config, turns=12)
    assert "Turn limit: 12" in seen["prompt"]
    assert seen["options"].max_turns == 12
    assert {turn: len(texts) for turn, texts in seen["reminders"].items()} == {2: 1, 9: 1}
    assert "10 turns left" in seen["reminders"][2][0]
    assert "3 turns left" in seen["reminders"][9][0]
    budget_events = [e for e in seen["events"] if e["type"] == "turn_budget"]
    assert [(e["turn"], e["turns_left"]) for e in budget_events] == [(2, 10), (9, 3)]


async def test_the_query_prompt_states_the_configured_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = await product_run(monkeypatch, tmp_path, "query", load_config("no-context"), turns=0)
    assert "Turn limit: 50" in seen["prompt"]


@pytest.mark.parametrize("config", ["full", "baseline", "no-skill", "no-verifiers", "no-context"])
async def test_dbt_runs_have_no_turn_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: str
) -> None:
    seen = await product_run(monkeypatch, tmp_path, "dbt", with_limits(config, dbt=12), turns=12)
    assert "Turn limit" not in seen["prompt"]
    assert seen["options"].max_turns == 12
    hooks = seen["options"].hooks or {}
    callbacks = [cb for matchers in hooks.values() for m in matchers for cb in m.hooks]
    assert not [cb for cb in callbacks if "TurnBudget" in cb.__qualname__]
    assert not [e for e in seen["events"] if e["type"] == "turn_budget"]
