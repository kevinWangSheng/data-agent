"""Verifier v2 hooks (ticket 46), beyond the acceptance seams: the report parser's line-start
rule, the SubagentStop hook's turn cap and transcript failure, the dispatch counter, and the
Stop rules for models that were removed or deleted."""

import json
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import HookEvent

from data_agent.run_config import load_config
from data_agent.verifier_v2 import parse_report, record_report, run_hooks, stop_decision
from data_agent.verifier_v2.dispatch import UNVERIFIED_HEADING
from data_agent.verifier_v2.hooks import VERIFIER_MAX_TURNS

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "verifier_v2"
TRANSCRIPT = FIXTURES / "verifier_transcript.jsonl"


def test_parse_report_reads_checks_only_at_line_start_under_a_heading() -> None:
    report = (
        "CHECK 1: PASS — before any heading, ignored\n"
        "### m\n"
        "CHECK 2: PASS — the task says CHECK 3: FAIL is fine\n"
        "- CHECK 3: N/A — a bullet is allowed\n"
        "The earlier CHECK 4: FAIL was wrong\n"
    )
    assert parse_report(report) == {
        "parsed": True,
        "models": {"m": {2: "PASS", 3: "N/A"}},
        "overall": "PASS",
    }


@pytest.mark.parametrize(
    ("heading", "line"),
    [
        ("### `orders.sql`", "- **CHECK 2**: **FAIL** - bold, as the old prompt's output"),
        ("### models/marts/orders", "CHECK 2: FAIL — a path"),
        ("### **orders**", "* CHECK 2: FAIL — a star bullet"),
        ("### `orders` (changed)", "**CHECK 2:** FAIL — bold closed after the colon"),
        ("### main.orders", "CHECK 2: FAIL — schema-qualified"),
    ],
)
def test_parse_report_normalizes_headings_and_allows_bold(heading: str, line: str) -> None:
    assert parse_report(f"{heading}\n{line}\n")["models"] == {"orders": {2: "FAIL"}}


def test_parse_report_overall_needs_info_when_any_check_does() -> None:
    assert parse_report("### m\nCHECK 1: PASS — ok\nCHECK 2: NEEDS_INFO — key?\n")["overall"] == (
        "NEEDS_INFO"
    )


def tiny_project(root: Path, **models: str) -> None:
    (root / "models").mkdir(parents=True)
    (root / "dbt_project.yml").write_text(
        'name: tiny\nversion: "1.0.0"\nconfig-version: 2\nprofile: tiny\nmodel-paths: ["models"]\n'
    )
    for name, sql in models.items():
        (root / "models" / f"{name}.sql").write_text(sql)


@pytest.fixture
def edited(tmp_path: Path) -> tuple[Path, Path]:
    """(workspace, snapshot): `alpha` changed, `gone` removed, `same` untouched."""
    workspace, snapshot = tmp_path / "ws", tmp_path / "run" / "snapshot"
    tiny_project(
        snapshot / "project", alpha="SELECT 1 AS x\n", gone="SELECT 0\n", same="SELECT 9\n"
    )
    tiny_project(workspace / "project", alpha="SELECT 1 AS x, 2 AS y\n", same="SELECT 9\n")
    return workspace, snapshot


def test_stop_ignores_removed_and_untouched_models(edited: tuple[Path, Path]) -> None:
    workspace, snapshot = edited
    decision = stop_decision({}, workspace, snapshot)
    assert [(r["model"], r["rule"]) for r in decision["reasons"]] == [("alpha", 1)]


def test_stop_blocks_a_reported_model_reconfigured_since(edited: tuple[Path, Path]) -> None:
    """A dbt_project.yml change model_diff reports counts as a change to the model (rule 2)."""
    workspace, snapshot = edited
    state = record_report({}, "### alpha\nCHECK 1: PASS — ok\n", workspace)
    assert stop_decision(state, workspace, snapshot)["block"] is False
    with (workspace / "project" / "dbt_project.yml").open("a") as handle:
        handle.write("models:\n  tiny:\n    alpha:\n      +materialized: view\n")
    decision = stop_decision(state, workspace, snapshot)
    assert [(r["model"], r["rule"]) for r in decision["reasons"]] == [("alpha", 2)]


def test_stop_fingerprint_ignores_vars_order(edited: tuple[Path, Path]) -> None:
    """model_diff compares configuration order-insensitively; so does the fingerprint."""
    workspace, snapshot = edited
    (workspace / "project" / "models" / "alpha.sql").write_text("SELECT {{ var('a') }} AS x\n")
    project_file = workspace / "project" / "dbt_project.yml"
    project_file.write_text(project_file.read_text() + "vars:\n  a: 1\n  b: 2\n")
    state = record_report({}, "### alpha\nCHECK 1: PASS — ok\n", workspace)
    reordered = project_file.read_text().replace("  a: 1\n  b: 2\n", "  b: 2\n  a: 1\n")
    project_file.write_text(reordered)
    assert stop_decision(state, workspace, snapshot)["block"] is False


def test_stop_blocks_a_reported_model_deleted_since(edited: tuple[Path, Path]) -> None:
    workspace, snapshot = edited
    state = record_report({}, "### alpha\nCHECK 1: PASS — ok\n", workspace)
    (workspace / "project" / "models" / "alpha.sql").unlink()
    decision = stop_decision(state, workspace, snapshot)
    assert [(r["model"], r["rule"]) for r in decision["reasons"]] == [("alpha", 2)]


def test_record_report_skips_sections_that_name_no_model(edited: tuple[Path, Path]) -> None:
    """The report's `### Summary` and a model the workspace lacks record nothing: a FAIL line
    under them would otherwise block the run with nothing to fix."""
    workspace, _ = edited
    report = "### alpha\nCHECK 1: PASS — ok\n### Summary\nCHECK 1: FAIL — alpha, see above\n"
    state = record_report({}, report, workspace)
    assert set(state) == {"alpha"} and state["alpha"]["checks"] == {1: "PASS"}


async def call(
    hooks: dict[HookEvent, list[HookMatcher]], event: HookEvent, **fields: Any
) -> dict[str, Any]:
    hook_input: dict[str, Any] = {"hook_event_name": event, "session_id": "s", **fields}
    merged: dict[str, Any] = {}
    for matcher in hooks.get(event, []):
        for hook in matcher.hooks:
            merged |= dict(await hook(hook_input, None, {"signal": None}))  # type: ignore[arg-type]
    return merged


def hooks_for(
    edited: tuple[Path, Path], events: list[dict[str, Any]]
) -> dict[HookEvent, list[HookMatcher]]:
    workspace, snapshot = edited
    return run_hooks(
        load_config("full"),
        workspace=workspace,
        snapshot=snapshot,
        task_file=workspace.parent / "task.md",
        emit=lambda type, **fields: events.append({"type": type, **fields}),
    )


def subagent_stop(agent_type: str, transcript: Path, report: str) -> dict[str, Any]:
    return {
        "agent_id": "a1",
        "agent_type": agent_type,
        "agent_transcript_path": str(transcript),
        "last_assistant_message": report,
        "stop_hook_active": False,
    }


async def test_subagent_stop_records_only_verifiers(edited: tuple[Path, Path]) -> None:
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    report = "### alpha\nCHECK 1: PASS — ok\n"
    await call(hooks, "SubagentStop", **subagent_stop("Explore", TRANSCRIPT, report))
    assert events == []
    await call(hooks, "SubagentStop", **subagent_stop("verifier", TRANSCRIPT, report))
    [end] = events
    assert end["type"] == "verifier_end" and end["verifier"] == "verifier"
    assert (end["turns"], end["tool_calls"], end["hit_max_turns"]) == (3, 4, False)
    assert end["checks"] == {"alpha": {1: "PASS"}} and end["overall"] == "PASS"
    assert end["report"] == report
    assert await call(hooks, "Stop", stop_hook_active=False) == {}


async def test_subagent_stop_reads_a_call_at_the_turn_cap_as_needs_info(
    edited: tuple[Path, Path], tmp_path: Path
) -> None:
    transcript = tmp_path / "capped.jsonl"
    transcript.write_text(
        "".join(
            json.dumps({"type": "assistant", "message": {"id": f"m{n}", "content": []}}) + "\n"
            for n in range(VERIFIER_MAX_TURNS)
        )
    )
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    report = "### alpha\nCHECK 1: PASS — ok\nCHECK 2: FAIL — cut off here\n"
    await call(hooks, "SubagentStop", **subagent_stop("verifier", transcript, report))
    [end] = events
    assert (end["turns"], end["hit_max_turns"], end["overall"]) == (
        VERIFIER_MAX_TURNS,
        True,
        "NEEDS_INFO",
    )
    assert end["checks"] == {"alpha": {1: "PASS", 2: "FAIL"}}
    # The check lines it wrote stand (spec: only a capped call with no report is NEEDS_INFO).
    blocked = await call(hooks, "Stop", stop_hook_active=False)
    assert blocked["decision"] == "block" and "alpha: rule 3" in blocked["reason"]


async def test_value_verifier_reports_are_recorded_but_verify_nothing(
    edited: tuple[Path, Path],
) -> None:
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    report = "### alpha\nCHECK 1: PASS — ok\n"
    await call(hooks, "SubagentStop", **subagent_stop("value-verifier", TRANSCRIPT, report))
    [end] = events
    assert end["type"] == "verifier_end" and end["verifier"] == "value-verifier"
    blocked = await call(hooks, "Stop", stop_hook_active=False)
    assert blocked["decision"] == "block" and "alpha: rule 1" in blocked["reason"]


async def test_subagent_stop_survives_an_unreadable_transcript(
    edited: tuple[Path, Path], tmp_path: Path
) -> None:
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    report = "### alpha\nCHECK 2: FAIL — bad\n"
    await call(hooks, "SubagentStop", **subagent_stop("verifier", tmp_path / "missing", report))
    [end] = events
    assert (end["turns"], end["tool_calls"]) == (None, None)
    assert end["checks"] == {"alpha": {2: "FAIL"}}
    blocked = await call(hooks, "Stop", stop_hook_active=False)
    assert blocked["decision"] == "block" and "alpha: rule 3" in blocked["reason"]


async def test_dispatch_hook_counts_only_verifier_dispatches(edited: tuple[Path, Path]) -> None:
    workspace, _ = edited
    hooks = hooks_for(edited, [])
    other = {"tool_name": "Agent", "tool_input": {"subagent_type": "Explore", "prompt": "x"}}
    assert await call(hooks, "PreToolUse", **other) == {}
    verifier = {"tool_name": "Agent", "tool_input": {"subagent_type": "verifier", "prompt": "mine"}}
    first = (await call(hooks, "PreToolUse", **verifier))["hookSpecificOutput"]["updatedInput"]
    assert UNVERIFIED_HEADING not in first["prompt"]
    assert str(workspace / "project") in first["prompt"] and "alpha" in first["prompt"]
    assert "gone" not in first["prompt"] and "same" not in first["prompt"]
    second = (await call(hooks, "PreToolUse", **verifier))["hookSpecificOutput"]["updatedInput"]
    assert UNVERIFIED_HEADING in second["prompt"] and second["prompt"].endswith("mine\n")
