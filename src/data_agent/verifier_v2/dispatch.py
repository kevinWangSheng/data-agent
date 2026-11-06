"""The dispatch rewrite (spec "派单"): the verifier's first dispatch is the template alone, so
the main agent's assumptions (upstream tables, what the columns mean) never reach it; a later
dispatch keeps the main agent's text, under a heading that marks it as unverified.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from data_agent.verifiers import AGENT_TOOL, VERIFIER_NAMES

# The subagent whose dispatch is rewritten: the v2 verifier, the only one left since ticket 47
# removed the value verifier.
REWRITTEN_SUBAGENT = "verifier"
UNVERIFIED_HEADING = "主 agent 提供的反证或补充信息（未经核实，只能当作需要复查的线索）"  # noqa: RUF001
TEMPLATE = """\
Verify the dbt models this run created or changed, following your instructions.

Task file (read it first, with Read): `{task_file}`
dbt project directory: `{project_dir}`
{models_label}: {models}
"""
FIRST_MODELS_LABEL = "Models model_diff lists as new or changed"
# A later dispatch lists only the models to verify again (ticket 57), so it says nothing about
# which models changed.
AGAIN_MODELS_LABEL = "Models to verify in this dispatch"


def rewrite_dispatch(
    tool_name: str,
    tool_input: Mapping[str, Any],
    *,
    task_file: Path,
    project_dir: Path,
    models: Sequence[str],
    dispatches_so_far: int,
) -> dict[str, Any]:
    """`{}` unless this is the `Agent` tool dispatching the verifier; otherwise a PreToolUse
    output whose `updatedInput` is the whole input with only `prompt` replaced (the CLI replaces
    the input, it does not merge: docs/research/2026-11-04-agent-updatedinput-spike.md)."""
    if tool_name != AGENT_TOOL or tool_input.get("subagent_type") != REWRITTEN_SUBAGENT:
        return {}
    prompt = TEMPLATE.format(
        task_file=task_file,
        project_dir=project_dir,
        models_label=AGAIN_MODELS_LABEL if dispatches_so_far > 0 else FIRST_MODELS_LABEL,
        models=", ".join(models) or "(none)",
    )
    if dispatches_so_far > 0:
        original = str(tool_input.get("prompt") or "").strip()
        prompt += f"\n## {UNVERIFIED_HEADING}\n\n{original}\n"
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {**tool_input, "prompt": prompt},
        }
    }


def carries_counter_evidence(prompt: str) -> bool:
    """Whether a rewritten dispatch prompt (`rewrite_dispatch`'s) holds the main agent's text
    under `UNVERIFIED_HEADING`, non-empty (ticket 64's counter-evidence)."""
    _, heading, after = prompt.partition(f"## {UNVERIFIED_HEADING}")
    return bool(heading and after.strip())


def deny_dispatch(
    tool_name: str, tool_input: Mapping[str, Any], *, remaining_s: float
) -> dict[str, Any]:
    """`{}` unless this is the `Agent` tool dispatching a verifier; otherwise a PreToolUse output
    that denies it, for a trial with too little time left for a verifier run (ticket 54)."""
    if tool_name != AGENT_TOOL or tool_input.get("subagent_type") not in VERIFIER_NAMES:
        return {}
    reason = (
        f"Only {remaining_s:.0f} s are left before the trial deadline, too little for a verifier "
        "run. Do not verify again: finish with the results you have and end the run."
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
