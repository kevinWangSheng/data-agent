"""Verifier v2 hooks (ticket 46), beyond the acceptance seams: the report parser's line-start
rule and heading normalization, the SubagentStop hook's turn cap and transcript failure, the
dispatch counter and dispatch-time fingerprints, the Stop rules for models that were removed or
deleted, and what the hooks do when their own file reads fail."""

import json
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import HookEvent

from data_agent.run_config import load_config
from data_agent.verifier_v2 import parse_report, record_report, run_hooks, stop_decision
from data_agent.verifier_v2.dispatch import UNVERIFIED_HEADING
from data_agent.verifier_v2.hooks import MAX_OUTSTANDING_DISPATCHES, VERIFIER_MAX_TURNS
from data_agent.verifier_v2.stop import model_hashes

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "verifier_v2"
TRANSCRIPT = FIXTURES / "verifier_transcript.jsonl"
VERIFIER = {"tool_name": "Agent", "tool_input": {"subagent_type": "verifier", "prompt": "go"}}
ALPHA_PASS = "### alpha\nCHECK 1: PASS — ok\n"


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
        ("### orders", "- CHECK 2 - Aggregate Cross-Validation: FAIL - the prompt's own title"),
        ("### orders", "**CHECK 2 - Aggregate Cross-Validation: FAIL**"),
        ("### orders", "CHECK 2 — Grain: FAIL — an em-dash before the title"),
        ("### Model: orders", "CHECK 2: FAIL — a labelled heading"),
        ("### 1. orders", "CHECK 2: FAIL — a numbered heading"),
        ("### Model `orders`", "CHECK 2: FAIL — a label without a colon"),
        ("### **Model:** orders", "CHECK 2: FAIL — a bold label"),
        ("### orders", "2. CHECK 2: FAIL — a numbered list"),
        ("### orders", "1) **CHECK 2**: FAIL — a numbered list with a parenthesis"),
    ],
)
def test_parse_report_normalizes_headings_and_allows_bold(heading: str, line: str) -> None:
    assert parse_report(f"{heading}\n{line}\n")["models"] == {"orders": {2: "FAIL"}}


@pytest.mark.parametrize("heading", ["### table", "### Model: table", "### 2. Model table"])
def test_parse_report_keeps_a_model_named_like_a_label(heading: str) -> None:
    assert parse_report(f"{heading}\nCHECK 1: PASS — ok\n")["models"] == {"table": {1: "PASS"}}


def test_parse_report_resolves_aliases_and_case_against_the_workspace(tmp_path: Path) -> None:
    """With a workspace, a heading is resolved as the tools resolve a verifier's name: an alias
    the model is built under, or a differently cased name, maps to the model file's stem."""
    tiny_project(
        tmp_path / "project", alpha="{{ config(alias='alpha_v2') }}\nSELECT 1\n", same="SELECT 9\n"
    )
    report = "### alpha_v2\nCHECK 1: PASS — ok\n### SAME\nCHECK 1: FAIL — bad\n"
    assert parse_report(report, workspace=tmp_path)["models"] == {
        "alpha": {1: "PASS"},
        "same": {1: "FAIL"},
    }


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


def test_stop_forgets_a_reported_model_deleted_since(edited: tuple[Path, Path]) -> None:
    """A model deleted after its report leaves the Stop state instead of blocking on rule 2 (a
    temporary model the agent built, had verified, then removed): nothing could clear it, as
    a report names only models in the workspace. model_diff still reports it removed."""
    workspace, snapshot = edited
    state = record_report({}, "### alpha\nCHECK 1: PASS — ok\n", workspace)
    (workspace / "project" / "models" / "alpha.sql").unlink()
    assert stop_decision(state, workspace, snapshot) == {"block": False, "reasons": []}
    state = record_report(state, "### same\nCHECK 1: PASS — ok\n", workspace)
    assert set(state) == {"same"}


def test_record_report_fingerprints_from_the_dispatch(edited: tuple[Path, Path]) -> None:
    """The hooks hash the models when the verifier is dispatched; a report then describes
    those files, so an edit made while the verifier ran counts as a change after the report."""
    workspace, snapshot = edited
    at_dispatch = model_hashes(workspace)
    (workspace / "project" / "models" / "alpha.sql").write_text("SELECT 3 AS x\n")
    state = record_report({}, "### alpha\nCHECK 1: PASS — ok\n", workspace, hashes=at_dispatch)
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


async def test_another_subagents_report_is_not_recorded_and_verifies_nothing(
    edited: tuple[Path, Path],
) -> None:
    """Ticket 47: only `verifier` exists; a report from any other subagent (the removed
    value-verifier name included) leaves no event and satisfies no Stop rule."""
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    report = "### alpha\nCHECK 1: PASS — ok\n"
    for agent_type in ("value-verifier", "Explore"):
        await call(hooks, "SubagentStop", **subagent_stop(agent_type, TRANSCRIPT, report))
    assert events == []
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


async def test_hooks_fingerprint_models_at_dispatch_time(edited: tuple[Path, Path]) -> None:
    """The main agent dispatches the verifier in the background and keeps editing: the report
    covers the files as dispatched, so the edit blocks the run (rule 2)."""
    workspace, _ = edited
    hooks = hooks_for(edited, [])
    await call(hooks, "PreToolUse", **VERIFIER)
    (workspace / "project" / "models" / "alpha.sql").write_text("SELECT 3 AS x\n")
    await call(hooks, "SubagentStop", **subagent_stop("verifier", TRANSCRIPT, ALPHA_PASS))
    blocked = await call(hooks, "Stop", stop_hook_active=False)
    assert blocked["decision"] == "block" and "alpha: rule 2" in blocked["reason"]


async def test_hooks_pair_reports_with_the_oldest_outstanding_dispatch(
    edited: tuple[Path, Path],
) -> None:
    """Two verifiers in flight: every report takes the oldest outstanding dispatch's fingerprints
    (the SDK gives no field linking a SubagentStop to its PreToolUse), so an edit between the two
    dispatches still counts as a change after both reports. A dispatch with none outstanding
    pairs with its own fingerprints and clears it. (Three Stops exhaust MAX_BLOCKS, so the last
    one is judged by whether it ended the run unresolved.)"""
    workspace, _ = edited
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    await call(hooks, "PreToolUse", **VERIFIER)
    (workspace / "project" / "models" / "alpha.sql").write_text("SELECT 3 AS x\n")
    await call(hooks, "PreToolUse", **VERIFIER)
    await call(hooks, "SubagentStop", **subagent_stop("verifier", TRANSCRIPT, ALPHA_PASS))
    blocked = await call(hooks, "Stop", stop_hook_active=False)
    assert blocked["decision"] == "block" and "alpha: rule 2" in blocked["reason"]
    await call(hooks, "SubagentStop", **subagent_stop("verifier", TRANSCRIPT, ALPHA_PASS))
    blocked = await call(hooks, "Stop", stop_hook_active=True)
    assert blocked["decision"] == "block" and "alpha: rule 2" in blocked["reason"]
    await call(hooks, "PreToolUse", **VERIFIER)
    await call(hooks, "SubagentStop", **subagent_stop("verifier", TRANSCRIPT, ALPHA_PASS))
    assert await call(hooks, "Stop", stop_hook_active=True) == {}
    assert "ended_with_unresolved" not in {e["type"] for e in events}


async def test_hooks_pair_reports_arriving_out_of_order(edited: tuple[Path, Path]) -> None:
    """Dispatch 1 (alpha as v1), edit alpha to v2, dispatch 2; dispatch 2 reports first and names
    no model, then dispatch 1 reports alpha PASS. That report verified v1, so the run must still
    block on alpha (rule 2): v2 was never verified."""
    workspace, _ = edited
    hooks = hooks_for(edited, [])
    await call(hooks, "PreToolUse", **VERIFIER)
    (workspace / "project" / "models" / "alpha.sql").write_text("SELECT 3 AS x\n")
    await call(hooks, "PreToolUse", **VERIFIER)
    other = "### same\nCHECK 1: PASS — ok\n"
    await call(hooks, "SubagentStop", **subagent_stop("verifier", TRANSCRIPT, other))
    await call(hooks, "SubagentStop", **subagent_stop("verifier", TRANSCRIPT, ALPHA_PASS))
    blocked = await call(hooks, "Stop", stop_hook_active=False)
    assert blocked["decision"] == "block" and "alpha: rule 2" in blocked["reason"]


async def test_hooks_drop_dispatches_that_never_report(edited: tuple[Path, Path]) -> None:
    """A dispatch whose SubagentStop never comes would otherwise keep its stale fingerprints at
    the head of the queue for every later report. The queue holds at most
    MAX_OUTSTANDING_DISPATCHES; beyond that the oldest is dropped."""
    workspace, _ = edited
    hooks = hooks_for(edited, [])
    await call(hooks, "PreToolUse", **VERIFIER)  # never reports
    (workspace / "project" / "models" / "alpha.sql").write_text("SELECT 3 AS x\n")
    for _ in range(MAX_OUTSTANDING_DISPATCHES):
        await call(hooks, "PreToolUse", **VERIFIER)
    await call(hooks, "SubagentStop", **subagent_stop("verifier", TRANSCRIPT, ALPHA_PASS))
    assert await call(hooks, "Stop", stop_hook_active=False) == {}


def broken(*args: Any, **kwargs: Any) -> dict[str, Any]:
    raise OSError("models/alpha.sql vanished mid-read")


async def test_stop_hook_lets_the_run_end_when_the_decision_fails(
    edited: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook that raises becomes an SDK error whose handling by the CLI is unverified; the Stop
    hook records the failure as an `error` event and does not block."""
    monkeypatch.setattr("data_agent.verifier_v2.hooks.stop_decision", broken)
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    assert await call(hooks, "Stop", stop_hook_active=False) == {}
    [error] = events
    assert error["type"] == "error" and error["source"] == "verifier_v2"
    assert error["hook"] == "Stop" and "vanished" in error["error"]


async def test_stop_hook_raises_on_a_logic_error(
    edited: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the workspace can fail under the Stop hook (OSError); anything else is a bug that
    must not turn into a silent pass."""

    def bug(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValueError("bad state")

    monkeypatch.setattr("data_agent.verifier_v2.hooks.stop_decision", bug)
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    with pytest.raises(ValueError, match="bad state"):
        await call(hooks, "Stop", stop_hook_active=False)
    assert events == []


async def test_subagent_stop_keeps_its_state_when_recording_fails(
    edited: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("data_agent.verifier_v2.hooks.record_checks", broken)
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    report = "### alpha\nCHECK 1: PASS — ok\n"
    await call(hooks, "SubagentStop", **subagent_stop("verifier", TRANSCRIPT, report))
    assert [e["type"] for e in events] == ["error", "verifier_end"]
    assert events[0]["hook"] == "SubagentStop" and "vanished" in events[0]["error"]
    assert events[1]["checks"] == {"alpha": {1: "PASS"}}
    # The state did not change: alpha is still unverified (rule 1).
    blocked = await call(hooks, "Stop", stop_hook_active=False)
    assert blocked["decision"] == "block" and "alpha: rule 1" in blocked["reason"]


async def test_subagent_stop_settles_its_dispatch_even_when_recording_fails(
    edited: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dispatch whose report could not be recorded must not stay at the head of the queue:
    the next dispatch's report would otherwise pair with the earlier fingerprints and block on
    an edit it did verify (rule 2)."""
    workspace, _ = edited
    events: list[dict[str, Any]] = []
    hooks = hooks_for(edited, events)
    await call(hooks, "PreToolUse", **VERIFIER)

    def unreadable(report: str, **kwargs: Any) -> dict[str, Any]:
        if "workspace" in kwargs:  # the workspace-resolving parse, the one that reads files
            raise OSError("models/alpha.sql vanished mid-read")
        return parse_report(report, **kwargs)

    monkeypatch.setattr("data_agent.verifier_v2.hooks.parse_report", unreadable)
    await call(hooks, "SubagentStop", **subagent_stop("verifier", TRANSCRIPT, ALPHA_PASS))
    monkeypatch.undo()
    assert [e["type"] for e in events] == ["error", "verifier_end"]
    (workspace / "project" / "models" / "alpha.sql").write_text("SELECT 3 AS x\n")
    await call(hooks, "PreToolUse", **VERIFIER)
    await call(hooks, "SubagentStop", **subagent_stop("verifier", TRANSCRIPT, ALPHA_PASS))
    assert await call(hooks, "Stop", stop_hook_active=False) == {}
