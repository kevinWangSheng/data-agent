"""The SDK hooks of the verifier layer, one set per run (spec "派单", "Stop 钩子", "记录"):

- PreToolUse on `Agent`: rewrites the verifier's dispatch (`rewrite_dispatch`), counting the
  dispatches so the first one is the template alone, and fingerprints the models as dispatched
  (the main agent may keep editing while the verifier runs in the background).
- SubagentStop: records a verifier call as `verifier_end` (its report, the parsed checks, the
  turns and tool calls from its transcript) and feeds the v2 verifier's report into the Stop
  state, against the dispatch-time fingerprints.
- Stop: blocks the run while `stop_decision` names a model, at most MAX_BLOCKS times; the next
  attempt ends the run and records `ended_with_unresolved`.

Pure functions do the work; the closures here only hold the run's counters and state. When one
of them fails on the workspace (a file deleted mid-read), the hook records an `error` event and
falls back to doing nothing: the SDK would otherwise turn the exception into an error response
whose handling by the CLI is unverified. SubagentStop then leaves the state as it was, and Stop
lets the run end rather than wedge it.
"""

from pathlib import Path
from typing import Any

from claude_agent_sdk import HookContext, HookInput, HookJSONOutput, HookMatcher
from claude_agent_sdk.types import HookEvent

from data_agent.run_config import RunConfig
from data_agent.verifier_v2.dispatch import REWRITTEN_SUBAGENT, rewrite_dispatch
from data_agent.verifier_v2.project import dbt_projects
from data_agent.verifier_v2.report import parse_report, summarize_transcript
from data_agent.verifier_v2.stop import (
    changed_models,
    describe,
    model_hashes,
    record_report,
    stop_decision,
)
from data_agent.verifiers import AGENT_TOOL, VERIFIER_NAMES, Emit

MAX_BLOCKS = 2
# The verifier's turn cap (spec "验证器"); a call that reaches it is read as NEEDS_INFO.
VERIFIER_MAX_TURNS = 50
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
) -> dict[HookEvent, list[HookMatcher]]:
    """`{}` when the config's verifier layer is off."""
    if not config.verifiers:
        return {}
    dispatches = 0
    blocks = 0
    state: dict[str, Any] = {}
    # The model fingerprints when the verifier was last dispatched; None before the first
    # dispatch (or when hashing failed), which `record_report` reads as "hash now".
    dispatched_hashes: dict[str, str] | None = None

    def failed(hook: str, exc: Exception) -> None:
        emit("error", source="verifier_v2", hook=hook, error=f"{exc!r}")

    async def dispatch(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        nonlocal dispatches, dispatched_hashes
        fields: dict[str, Any] = dict(hook_input)
        projects = dbt_projects(workspace)
        output = rewrite_dispatch(
            str(fields.get("tool_name") or ""),
            dict(fields.get("tool_input") or {}),
            task_file=task_file,
            project_dir=projects[0] if projects else workspace,
            models=changed_models(workspace, snapshot),
            dispatches_so_far=dispatches,
        )
        if output:
            dispatches += 1
            try:
                dispatched_hashes = model_hashes(workspace)
            except (OSError, ValueError) as exc:
                failed("PreToolUse", exc)
                dispatched_hashes = None
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
        try:
            if fields["agent_type"] == REWRITTEN_SUBAGENT:
                state = record_report(state, report, workspace, hashes=dispatched_hashes)
            parsed = parse_report(report, hit_max_turns=hit_max_turns, workspace=workspace)
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
        except (OSError, ValueError) as exc:
            failed("Stop", exc)
            return {}
        if not decision["block"]:
            return {}
        reason = describe(decision["reasons"])
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
