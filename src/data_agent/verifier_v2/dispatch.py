"""The dispatch rewrite (spec "派单"): the verifier's first dispatch is the template alone, so
the main agent's assumptions (upstream tables, what the columns mean) never reach it; a later
dispatch keeps the main agent's text, under a heading that marks it as unverified.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from data_agent.verifiers import AGENT_TOOL

# Only this subagent's dispatch is rewritten: the v2 verifier (the value verifier, still defined
# until ticket 47 removes it, keeps the main agent's prompt).
REWRITTEN_SUBAGENT = "verifier"
UNVERIFIED_HEADING = "主 agent 提供的反证或补充信息（未经核实，只能当作需要复查的线索）"  # noqa: RUF001
TEMPLATE = """\
Verify the dbt models this run created or changed, following your instructions.

Task file (read it first, with Read): `{task_file}`
dbt project directory: `{project_dir}`
Models model_diff lists as new or changed: {models}
"""


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
        task_file=task_file, project_dir=project_dir, models=", ".join(models) or "(none)"
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
