"""The SDK hooks of the verifier layer, one set per run (spec "派单", "Stop 钩子", "记录"):

- PreToolUse on `Agent`: rewrites the verifier's dispatch (`rewrite_dispatch`), counting the
  dispatches so the first one is the template alone, and fingerprints the models as dispatched
  (the main agent may keep editing while the verifier runs in the background). The first
  dispatch lists every changed model; a later one only those to verify again
  (`redispatch_models`), or every changed model when there are none. Each rewritten dispatch
  records `verifier_dispatch`: its number `n`, the `models` listed, and their `source`.
- SubagentStop: records a verifier call as `verifier_end` (its report, the parsed checks, the
  turns and tool calls from its transcript) and feeds the v2 verifier's report into the Stop
  state, against the dispatch-time fingerprints.
- Stop: blocks the run while `stop_decision` names a model, at most MAX_BLOCKS times; the next
  attempt ends the run and records `ended_with_unresolved`.

With a trial deadline (ticket 54), once less than VERIFY_MIN_REMAINING_S is left a verifier
dispatch is denied and Stop lets the run end, recording `ended_unverified` when it would have
blocked. Both change together: a Stop that asks for a dispatch the PreToolUse hook denies would
loop until MAX_BLOCKS.

Pure functions do the work; the closures here only hold the run's counters and state. When one
of them fails on the workspace (a file deleted mid-read), the hook records an `error` event and
falls back to doing nothing: the SDK would otherwise turn the exception into an error response
whose handling by the CLI is unverified. SubagentStop then leaves the state as it was, and Stop
lets the run end rather than wedge it.
"""

import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import HookContext, HookInput, HookJSONOutput, HookMatcher
from claude_agent_sdk.types import HookEvent

from data_agent.run_config import RunConfig
from data_agent.verifier_v2.dispatch import REWRITTEN_SUBAGENT, deny_dispatch, rewrite_dispatch
from data_agent.verifier_v2.project import dbt_projects
from data_agent.verifier_v2.report import parse_report, summarize_transcript
from data_agent.verifier_v2.stop import (
    changed_models,
    describe,
    model_hashes,
    record_checks,
    redispatch_models,
    stop_decision,
)
from data_agent.verifiers import AGENT_TOOL, VERIFIER_NAMES, Emit

# Where a verifier dispatch's model list came from (the `verifier_dispatch` event, ticket 59).
type DispatchSource = Literal["first", "again", "fallback_all"]

MAX_BLOCKS = 2
# How many verifier dispatches may await a report before the oldest is presumed lost.
MAX_OUTSTANDING_DISPATCHES = 4
# The verifier's turn cap (spec "验证器"); a call that reaches it is read as NEEDS_INFO.
VERIFIER_MAX_TURNS = 50
# Seconds a verifier run needs: 13 of 14 verifier windows in runs/t47-screen took at most 970 s
# (docs/research/2026-11-05-foundation-latency-audit.md §2).
VERIFY_MIN_REMAINING_S = 1000.0
BLOCK_INSTRUCTION = (
    "The run cannot end yet. Fix what the verifier reported (reproduce a FAIL with one read-only "
    "query first), rebuild the changed models, then dispatch the `verifier` again for every "
    "model named here."
)


def run_hooks(
    config: RunConfig,
    *,
    workspace: Path,
    snapshot: Path,
    task_file: Path,
    emit: Emit,
    deadline: float | None = None,
    now: Callable[[], float] = time.monotonic,
) -> dict[HookEvent, list[HookMatcher]]:
    """`{}` when the config's verifier layer is off. `deadline` is the trial's end on the `now`
    clock (monotonic seconds); None is a run without one."""
    if not config.verifiers:
        return {}
    dispatches = 0
    blocks = 0
    state: dict[str, Any] = {}
    # The model fingerprints at each verifier dispatch whose report has not arrived, oldest
    # first. The SDK gives no field linking a SubagentStop to its PreToolUse, and reports may
    # arrive in any order, so every report takes the *oldest* outstanding dispatch's
    # fingerprints, and an entry leaves the queue only from the newest end, so the oldest stays
    # until no dispatch is outstanding: with two verifiers in flight that can pair a report with
    # an earlier dispatch, which over-reports an edit (rule 2), never misses one. An empty entry
    # (hashing failed) means "hash now".
    # A dispatch that never reports (whether the SDK fires SubagentStop for a subagent that
    # errors is unverified) cannot be told from one still in flight: it pins its fingerprints at
    # the head for the rest of the run, over-reporting rule 2 until MAX_BLOCKS ends the run.
    # The bound only keeps the queue from growing without limit.
    dispatched: deque[dict[str, str] | None] = deque(maxlen=MAX_OUTSTANDING_DISPATCHES)

    def short_on_time_s() -> float | None:
        """Seconds left when too few are left for a verifier run, else None."""
        if deadline is None:
            return None
        left = deadline - now()
        return left if left < VERIFY_MIN_REMAINING_S else None

    def failed(hook: str, exc: Exception) -> None:
        emit("error", source="verifier_v2", hook=hook, error=f"{exc!r}")

    def dispatch_models() -> tuple[list[str], DispatchSource]:
        """Every changed model for the first dispatch; after it, the ones to verify again
        (ticket 57), or every changed model again when there are none or they cannot be read.
        With the list, where it came from (ticket 59)."""
        if dispatches == 0:
            return changed_models(workspace, snapshot), "first"
        try:
            if again := redispatch_models(state, workspace, snapshot):
                return again, "again"
        except (OSError, ValueError) as exc:
            failed("PreToolUse", exc)
        return changed_models(workspace, snapshot), "fallback_all"

    async def dispatch(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        nonlocal dispatches
        fields: dict[str, Any] = dict(hook_input)
        if (left := short_on_time_s()) is not None:
            denied = deny_dispatch(
                str(fields.get("tool_name") or ""),
                dict(fields.get("tool_input") or {}),
                remaining_s=left,
            )
            if denied:
                return denied  # type: ignore[return-value]
        tool_name = str(fields.get("tool_name") or "")
        tool_input = dict(fields.get("tool_input") or {})
        if tool_name != AGENT_TOOL or tool_input.get("subagent_type") != REWRITTEN_SUBAGENT:
            return {}
        projects = dbt_projects(workspace)
        models, source = dispatch_models()
        output = rewrite_dispatch(
            tool_name,
            tool_input,
            task_file=task_file,
            project_dir=projects[0] if projects else workspace,
            models=models,
            dispatches_so_far=dispatches,
        )
        if output:
            dispatches += 1
            emit("verifier_dispatch", n=dispatches, models=models, source=source)
            try:
                dispatched.append(model_hashes(workspace))
            except (OSError, ValueError) as exc:
                failed("PreToolUse", exc)
                dispatched.append(None)
        return output  # type: ignore[return-value]

    async def subagent_stop(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        nonlocal state
        fields: dict[str, Any] = dict(hook_input)
        if fields.get("agent_type") not in VERIFIER_NAMES:
            return {}
        report = str(fields.get("last_assistant_message") or "")
        path = str(fields.get("agent_transcript_path") or "")
        summary: dict[str, int | None]
        try:
            summary = dict(summarize_transcript(Path(path)))
        except (OSError, ValueError):
            summary = {"turns": None, "tool_calls": None}
        hit_max_turns = (summary["turns"] or 0) >= VERIFIER_MAX_TURNS
        # Only the v2 verifier's reports count as verification; the value verifier's (until
        # ticket 47 removes it) are recorded but satisfy no Stop rule. A call at the turn cap is
        # NEEDS_INFO as a whole (`overall`); the check lines it did write stand.
        # The report settles one dispatch whether or not it can be recorded below.
        dispatch_hashes = dispatched[0] if dispatched else None
        if fields["agent_type"] == REWRITTEN_SUBAGENT and dispatched:
            dispatched.pop()
        try:
            parsed = parse_report(report, hit_max_turns=hit_max_turns, workspace=workspace)
            if fields["agent_type"] == REWRITTEN_SUBAGENT:
                state = record_checks(
                    state, parsed["models"], dispatch_hashes or model_hashes(workspace)
                )
        except (OSError, ValueError) as exc:
            failed("SubagentStop", exc)
            parsed = parse_report(report, hit_max_turns=hit_max_turns)
        emit(
            "verifier_end",
            verifier=fields["agent_type"],
            agent_id=fields.get("agent_id"),
            turns=summary["turns"],
            tool_calls=summary["tool_calls"],
            hit_max_turns=hit_max_turns,
            checks=parsed["models"],
            overall=parsed["overall"],
            report=report,
        )
        return {}

    async def stop(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        nonlocal blocks
        try:
            decision = stop_decision(state, workspace, snapshot)
        except OSError as exc:  # the workspace, not the state: a bug must not pass silently
            failed("Stop", exc)
            return {}
        if not decision["block"]:
            return {}
        reason = describe(decision["reasons"])
        if (left := short_on_time_s()) is not None:
            emit("ended_unverified", reason=reason, reasons=decision["reasons"], remaining_s=left)
            return {}
        blocks += 1
        if blocks > MAX_BLOCKS:
            emit(
                "ended_with_unresolved",
                reason=reason,
                reasons=decision["reasons"],
                blocks=blocks - 1,
            )
            return {}
        return {"decision": "block", "reason": f"{reason}. {BLOCK_INSTRUCTION}"}

    return {
        "PreToolUse": [HookMatcher(matcher=AGENT_TOOL, hooks=[dispatch])],
        "SubagentStop": [HookMatcher(hooks=[subagent_stop])],
        "Stop": [HookMatcher(hooks=[stop])],
    }
