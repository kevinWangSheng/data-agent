"""Ticket 59: every verifier dispatch the PreToolUse hook rewrites records a `verifier_dispatch`
event — which dispatch it is (`n`), the models it lists, and where the list came from (`first`,
`again`, or `fallback_all` when there are none to verify again or they cannot be read). A
dispatch denied at the trial deadline records none; the eval readers ignore the event."""

import json
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import HookEvent

from data_agent.run_config import load_config
from data_agent.verifier_v2 import hooks as hooks_module
from data_agent.verifier_v2 import run_hooks
from data_agent.verifier_v2.dispatch import AGAIN_MODELS_LABEL, FIRST_MODELS_LABEL
from evals.atif import convert
from evals.harbor import Trial
from evals.process import trial_metrics

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "verifier_v2"
TRANSCRIPT = FIXTURES / "verifier_transcript.jsonl"
VERIFIER = {"tool_name": "Agent", "tool_input": {"subagent_type": "verifier", "prompt": "go"}}
DEADLINE = 10_000.0


def tiny_project(root: Path, models: dict[str, str]) -> None:
    (root / "models").mkdir(parents=True)
    (root / "dbt_project.yml").write_text(
        'name: tiny\nversion: "1.0.0"\nconfig-version: 2\nprofile: tiny\nmodel-paths: ["models"]\n'
    )
    for name, sql in models.items():
        (root / "models" / f"{name}.sql").write_text(sql)


@pytest.fixture
def edited(tmp_path: Path) -> tuple[Path, Path]:
    """(workspace, snapshot): `a` and `b` are new since the snapshot, `raw` untouched."""
    workspace, snapshot = tmp_path / "ws", tmp_path / "run" / "snapshot"
    tiny_project(snapshot / "project", {"raw": "SELECT 0 AS r\n"})
    tiny_project(
        workspace / "project",
        {"a": "SELECT 1 AS x\n", "b": "SELECT 2 AS y\n", "raw": "SELECT 0 AS r\n"},
    )
    return workspace, snapshot


def hooks_for(
    edited: tuple[Path, Path], events: list[dict[str, Any]], *, remaining: float | None = None
) -> dict[HookEvent, list[HookMatcher]]:
    workspace, snapshot = edited
    return run_hooks(
        load_config("full"),
        workspace=workspace,
        snapshot=snapshot,
        task_file=workspace.parent / "task.md",
        emit=lambda type, **fields: events.append({"type": type, **fields}),
        deadline=None if remaining is None else DEADLINE,
        now=lambda: DEADLINE - (remaining or 0.0),
    )


async def call(
    hooks: dict[HookEvent, list[HookMatcher]], event: HookEvent, **fields: Any
) -> dict[str, Any]:
    hook_input: dict[str, Any] = {"hook_event_name": event, "session_id": "s", **fields}
    merged: dict[str, Any] = {}
    for matcher in hooks.get(event, []):
        for hook in matcher.hooks:
            merged |= dict(await hook(hook_input, None, {"signal": None}))  # type: ignore[arg-type]
    return merged


async def dispatch(hooks: dict[HookEvent, list[HookMatcher]]) -> list[str]:
    """The models the rewritten dispatch prompt lists."""
    output = await call(hooks, "PreToolUse", **VERIFIER)
    prompt = output["hookSpecificOutput"]["updatedInput"]["prompt"]
    labels = tuple(f"{label}: " for label in (FIRST_MODELS_LABEL, AGAIN_MODELS_LABEL))
    [line] = [line for line in prompt.splitlines() if line.startswith(labels)]
    return line.split(": ", 1)[1].split(", ")


async def report(hooks: dict[HookEvent, list[HookMatcher]], **statuses: str) -> None:
    text = "".join(f"### {model}\nCHECK 1: {status} — x\n" for model, status in statuses.items())
    await call(
        hooks,
        "SubagentStop",
        agent_id="a1",
        agent_type="verifier",
        agent_transcript_path=str(TRANSCRIPT),
        last_assistant_message=text,
        stop_hook_active=False,
    )


def dispatch_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == "verifier_dispatch"]


async def test_the_first_dispatch_records_its_models(edited: tuple[Path, Path]) -> None:
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    listed = await dispatch(hooks)
    assert dispatch_events(events) == [
        {"type": "verifier_dispatch", "n": 1, "models": listed, "source": "first"}
    ]
    assert listed == ["a", "b"]


async def test_a_redispatch_records_the_models_to_verify_again(edited: tuple[Path, Path]) -> None:
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    await dispatch(hooks)
    await report(hooks, a="PASS", b="FAIL")
    listed = await dispatch(hooks)
    assert listed == ["b"]
    assert dispatch_events(events)[1] == {
        "type": "verifier_dispatch",
        "n": 2,
        "models": ["b"],
        "source": "again",
    }


async def test_a_redispatch_with_nothing_to_verify_again_falls_back_to_all(
    edited: tuple[Path, Path],
) -> None:
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    await dispatch(hooks)
    await report(hooks, a="PASS", b="PASS")
    listed = await dispatch(hooks)
    assert listed == ["a", "b"]
    assert dispatch_events(events)[1] == {
        "type": "verifier_dispatch",
        "n": 2,
        "models": ["a", "b"],
        "source": "fallback_all",
    }


async def test_a_redispatch_whose_list_cannot_be_read_falls_back_to_all(
    edited: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreadable(*args: Any) -> list[str]:
        raise OSError("gone")

    monkeypatch.setattr(hooks_module, "redispatch_models", unreadable)
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    await dispatch(hooks)
    listed = await dispatch(hooks)
    assert [(e["n"], e["models"], e["source"]) for e in dispatch_events(events)] == [
        (1, ["a", "b"], "first"),
        (2, listed, "fallback_all"),
    ]


async def test_a_dispatch_denied_at_the_deadline_records_nothing(
    edited: tuple[Path, Path],
) -> None:
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events, remaining=10)
    output = await call(hooks, "PreToolUse", **VERIFIER)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert dispatch_events(events) == []


async def test_other_agents_record_nothing(edited: tuple[Path, Path]) -> None:
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    other = {"tool_name": "Agent", "tool_input": {"subagent_type": "Explore", "prompt": "x"}}
    assert await call(hooks, "PreToolUse", **other) == {}
    assert dispatch_events(events) == []


BASE_EVENTS: list[dict[str, Any]] = [
    {"type": "status", "ts": "2026-11-06T10:00:00+00:00"},
    {"type": "tool_start", "ts": "2026-11-06T10:00:01+00:00", "tool": "Agent", "tool_use_id": "t"},
    {
        "type": "verifier_start",
        "ts": "2026-11-06T10:00:02+00:00",
        "verifier": "verifier",
        "agent_id": "a1",
    },
    {
        "type": "verifier_end",
        "ts": "2026-11-06T10:00:30+00:00",
        "verifier": "verifier",
        "agent_id": "a1",
        "checks": {"a": {"1": "PASS"}},
        "report": "### a\nCHECK 1: PASS\n",
    },
    {
        "type": "result",
        "ts": "2026-11-06T10:00:40+00:00",
        "subtype": "success",
        "num_turns": 2,
        "cost_usd": 0.01,
        "terminal_reason": "completed",
    },
]
DISPATCH_EVENT = {
    "type": "verifier_dispatch",
    "ts": "2026-11-06T10:00:01+00:00",
    "n": 1,
    "models": ["a"],
    "source": "first",
}


def trial_with(root: Path, events: list[dict[str, Any]]) -> Trial:
    events_dir = root / "t1__a" / "agent" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "run-1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return Trial(
        task="t1",
        name="t1__a",
        reward=1.0,
        exception_type=None,
        uncached_input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        dir=root / "t1__a",
    )


def test_process_metrics_count_the_dispatch_event_and_change_nothing_else(tmp_path: Path) -> None:
    with_event = [*BASE_EVENTS[:2], DISPATCH_EVENT, *BASE_EVENTS[2:]]
    without = trial_metrics(trial_with(tmp_path / "without", BASE_EVENTS))
    with_dispatch = trial_metrics(trial_with(tmp_path / "with", with_event))
    assert with_dispatch.pop("events") != without.pop("events")  # the file path alone differs
    # Ticket 67 counts the dispatches (tests/test_eval_process.py); nothing else changes.
    dispatch_keys = ("verifier_dispatches", "dispatch_models", "ended_disputed")
    assert [with_dispatch.pop(key) for key in dispatch_keys] == [1, [1], False]
    assert [without.pop(key) for key in dispatch_keys] == [None, None, None]
    assert with_dispatch == without


def test_atif_trajectory_ignores_the_dispatch_event() -> None:
    with_event = [*BASE_EVENTS[:2], DISPATCH_EVENT, *BASE_EVENTS[2:]]
    without = convert(BASE_EVENTS, "do it", "s")
    assert without is not None
    assert convert(with_event, "do it", "s") == without
