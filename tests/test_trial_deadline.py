"""The trial deadline (ticket 54): with too little time left for a verifier run, the dispatch is
denied and the Stop hook lets the run end (`ended_unverified`); at the deadline less its margin
the main loop stops itself and writes a `result` with `terminal_reason: "deadline"`."""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    HookMatcher,
    ResultMessage,
    StreamEvent,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import HookEvent

from data_agent import agent, cli
from data_agent.agent import Outcome, Task
from data_agent.run_config import load_config
from data_agent.verifier_v2 import run_hooks
from data_agent.verifier_v2.hooks import VERIFY_MIN_REMAINING_S
from data_agent.verifiers import VERIFIER_NAMES

VERIFIER = {"tool_name": "Agent", "tool_input": {"subagent_type": "verifier", "prompt": "go"}}
DEADLINE = 10_000.0


def tiny_project(root: Path, **models: str) -> None:
    (root / "models").mkdir(parents=True)
    (root / "dbt_project.yml").write_text(
        'name: tiny\nversion: "1.0.0"\nconfig-version: 2\nprofile: tiny\nmodel-paths: ["models"]\n'
    )
    for name, sql in models.items():
        (root / "models" / f"{name}.sql").write_text(sql)


@pytest.fixture
def edited(tmp_path: Path) -> tuple[Path, Path]:
    """(workspace, snapshot): `alpha` changed since the snapshot, so Stop would block (rule 1)."""
    workspace, snapshot = tmp_path / "ws", tmp_path / "run" / "snapshot"
    tiny_project(snapshot / "project", alpha="SELECT 1 AS x\n")
    tiny_project(workspace / "project", alpha="SELECT 1 AS x, 2 AS y\n")
    return workspace, snapshot


async def call(
    hooks: dict[HookEvent, list[HookMatcher]], event: HookEvent, **fields: Any
) -> dict[str, Any]:
    hook_input: dict[str, Any] = {"hook_event_name": event, "session_id": "s", **fields}
    merged: dict[str, Any] = {}
    for matcher in hooks.get(event, []):
        for hook in matcher.hooks:
            merged |= dict(await hook(hook_input, None, {"signal": None}))  # type: ignore[arg-type]
    return merged


def hooks_at(
    edited: tuple[Path, Path], events: list[dict[str, Any]], remaining: float
) -> dict[HookEvent, list[HookMatcher]]:
    """The run's hooks with a clock that reads `remaining` seconds before the deadline."""
    workspace, snapshot = edited
    return run_hooks(
        load_config("full"),
        workspace=workspace,
        snapshot=snapshot,
        task_file=workspace.parent / "task.md",
        emit=lambda type, **fields: events.append({"type": type, **fields}),
        deadline=DEADLINE,
        now=lambda: DEADLINE - remaining,
    )


@pytest.mark.parametrize("agent_type", VERIFIER_NAMES)
async def test_a_verifier_dispatch_is_denied_with_too_little_time_left(
    edited: tuple[Path, Path], agent_type: str
) -> None:
    events: list[dict[str, Any]] = []
    hooks = hooks_at(edited, events, remaining=999)
    dispatch = {"tool_name": "Agent", "tool_input": {"subagent_type": agent_type, "prompt": "go"}}
    output = (await call(hooks, "PreToolUse", **dispatch))["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "999 s" in output["permissionDecisionReason"]
    assert "finish with the results you have" in output["permissionDecisionReason"]


async def test_stop_lets_the_run_end_unverified_with_too_little_time_left(
    edited: tuple[Path, Path],
) -> None:
    events: list[dict[str, Any]] = []
    hooks = hooks_at(edited, events, remaining=999)
    assert await call(hooks, "Stop", stop_hook_active=False) == {}
    [ended] = events
    assert ended["type"] == "ended_unverified"
    assert ended["remaining_s"] == pytest.approx(999)
    assert [(r["model"], r["rule"]) for r in ended["reasons"]] == [("alpha", 1)]


async def test_with_enough_time_left_the_hooks_behave_as_before(
    edited: tuple[Path, Path],
) -> None:
    events: list[dict[str, Any]] = []
    hooks = hooks_at(edited, events, remaining=VERIFY_MIN_REMAINING_S)
    output = (await call(hooks, "PreToolUse", **VERIFIER))["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    blocked = await call(hooks, "Stop", stop_hook_active=False)
    assert blocked["decision"] == "block" and "alpha: rule 1" in blocked["reason"]
    assert [e["type"] for e in events] == ["verifier_dispatch"]  # ticket 59; no deadline event


async def test_other_agents_are_dispatched_whatever_the_time_left(
    edited: tuple[Path, Path],
) -> None:
    hooks = hooks_at(edited, [], remaining=10)
    other = {"tool_name": "Agent", "tool_input": {"subagent_type": "Explore", "prompt": "x"}}
    assert await call(hooks, "PreToolUse", **other) == {}


async def test_a_run_without_a_deadline_never_denies(edited: tuple[Path, Path]) -> None:
    workspace, snapshot = edited
    hooks = run_hooks(
        load_config("full"),
        workspace=workspace,
        snapshot=snapshot,
        task_file=workspace.parent / "task.md",
        emit=lambda type, **fields: None,
        now=lambda: 1e12,
    )
    output = (await call(hooks, "PreToolUse", **VERIFIER))["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"


ENV = {"DATA_AGENT_API_KEY": "sk-test-secret", "DATA_AGENT_BASE_URL": "https://llm.example/x"}


def stream_event(event: dict[str, Any]) -> StreamEvent:
    return StreamEvent(uuid="u", session_id="s", event=event, parent_tool_use_id=None)


def tool_call(call_id: str, tool: str, output_tokens: int) -> list[Any]:
    """One main-thread model call that asks for a tool, as the SDK streams it."""
    usage = {"input_tokens": 1_000, "output_tokens": 0}
    return [
        stream_event({"type": "message_start", "message": {"id": call_id, "usage": usage}}),
        AssistantMessage(
            content=[ToolUseBlock(id=f"toolu_{call_id}", name=tool, input={})],
            model="deepseek-flash",
        ),
        stream_event(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": output_tokens},
            }
        ),
    ]


def sleeping_stream(closed: list[bool]) -> Callable[..., AsyncIterator[Any]]:
    """Two tool calls, the first answered; then the second runs far past any deadline."""

    async def fake_query(**_: Any) -> AsyncIterator[Any]:
        try:
            for message in tool_call("m1", "Read", 100):
                yield message
            yield UserMessage(content=[ToolResultBlock(tool_use_id="toolu_m1", content="ok")])
            for message in tool_call("m2", "Bash", 200):
                yield message
            await asyncio.sleep(60)
            yield UserMessage(content=[ToolResultBlock(tool_use_id="toolu_m2", content="late")])
        finally:
            closed.append(True)

    return fake_query


async def run_until(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake: Callable[..., AsyncIterator[Any]],
    deadline: float,
) -> tuple[Outcome, list[dict[str, Any]]]:
    monkeypatch.setattr(agent, "query", fake)
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    task = Task(text="q", type="query", workspace=tmp_path, duckdb=db)
    outcome = await agent.run(
        task, load_config("full"), tmp_path / "events", ENV, deadline=deadline
    )
    lines = outcome.events_path.read_text().splitlines()
    return outcome, [json.loads(line) for line in lines]


async def test_the_run_stops_itself_before_the_trial_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    closed: list[bool] = []
    started = time.monotonic()
    deadline = started + agent.DEADLINE_MARGIN_S + 0.5
    outcome, events = await run_until(monkeypatch, tmp_path, sleeping_stream(closed), deadline)
    assert time.monotonic() - started < 10
    assert closed == [True]
    # An unfinished agent (exit 1, which the Harbor adapter passes as a normal end), not a
    # broken run.
    assert (outcome.finished, outcome.infra_error) == (False, False)
    last = events[-1]
    assert last["type"] == "result"
    assert last["terminal_reason"] == "deadline"
    assert last["is_error"] is True
    assert last["open_tool_calls"] == [{"tool_use_id": "toolu_m2", "tool": "Bash"}]
    # Spend is the price table's, from the calls streamed so far.
    assert (last["uncached_input_tokens"], last["output_tokens"]) == (2_000, 300)
    assert last["cost_basis"] == "price_table"
    assert last["cost_usd"] == pytest.approx((2_000 * 0.30 + 300 * 1.2) / 1e6)
    assert last["num_turns"] == 2
    assert outcome.errors == last["errors"] and len(outcome.errors) == 1
    assert "late" not in outcome.events_path.read_text()


async def test_a_run_that_ends_before_its_deadline_is_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_query(**_: Any) -> AsyncIterator[Any]:
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            usage={"input_tokens": 3, "output_tokens": 7},
            total_cost_usd=0.01,
            result="42",
        )

    deadline = time.monotonic() + agent.DEADLINE_MARGIN_S + 60
    outcome, events = await run_until(monkeypatch, tmp_path, fake_query, deadline)
    assert (outcome.finished, outcome.answer) == (True, "42")
    assert events[-1]["terminal_reason"] != "deadline"


def test_the_cli_turns_the_agent_timeout_into_a_monotonic_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    async def fake_run(task: Task, config: Any, events_dir: Path, env: Any, deadline: Any) -> Any:
        seen["deadline"] = deadline
        return Outcome("", True, False, [], events_dir / "e.jsonl")

    monkeypatch.setattr(cli, "run", fake_run)
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    args = [
        *("run", "--type", "query", "--task", "q", "--config", "full"),
        *("--workspace", str(tmp_path), "--duckdb", str(db), "--events-dir", str(tmp_path / "e")),
    ]
    before = time.monotonic()
    with pytest.raises(SystemExit):
        cli.main([*args, "--agent-timeout-sec", "1500"])
    assert before + 1500 <= seen["deadline"] <= time.monotonic() + 1500
    with pytest.raises(SystemExit):
        cli.main(args)
    assert seen["deadline"] is None
