"""The SDK hooks of the verifier layer, one set per run (spec "派单", "Stop 钩子", "记录"):

- PreToolUse on `Agent`: rewrites the verifier's dispatch (`rewrite_dispatch`), counting the
  dispatches so the first one is the template alone.
- SubagentStop: records a verifier call as `verifier_end` (its report, the parsed checks, the
  turns and tool calls from its transcript) and feeds the report into the Stop state.
- Stop: blocks the run while `stop_decision` names a model, at most MAX_BLOCKS times; the next
  attempt ends the run and records `ended_with_unresolved`.

Pure functions do the work; the closures here only hold the run's counters and state.
"""

from pathlib import Path
from typing import Any

from claude_agent_sdk import HookContext, HookInput, HookJSONOutput, HookMatcher
from claude_agent_sdk.types import HookEvent

from data_agent.run_config import RunConfig
from data_agent.verifier_v2.dispatch import AGENT_TOOL, rewrite_dispatch
from data_agent.verifier_v2.model_diff import model_diff
from data_agent.verifier_v2.project import dbt_projects
from data_agent.verifier_v2.report import parse_report, summarize_transcript
from data_agent.verifier_v2.stop import describe, record_report, stop_decision
from data_agent.verifiers import VERIFIER_NAMES, Emit

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

    def changed_models() -> list[str]:
        return [d["model"] for d in model_diff(workspace, snapshot) if d["status"] != "removed"]

    async def dispatch(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        nonlocal dispatches
        fields: dict[str, Any] = dict(hook_input)
        projects = dbt_projects(workspace)
        output = rewrite_dispatch(
            str(fields.get("tool_name") or ""),
            dict(fields.get("tool_input") or {}),
            task_file=task_file,
            project_dir=projects[0] if projects else workspace,
            models=changed_models(),
            dispatches_so_far=dispatches,
        )
        if output:
            dispatches += 1
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
        parsed = parse_report(report, hit_max_turns=hit_max_turns)
        state = record_report(state, report, workspace)
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
        decision = stop_decision(state, workspace, snapshot)
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
