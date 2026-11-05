"""Reading what one verifier call produced (spec "报告格式", "记录"): the per-model check
statuses in its report, and the turns and tool calls in its transcript.
"""

import json
import re
from pathlib import Path
from typing import Any

from data_agent.verifier_v2 import project

# `### <model>` on its own line.
MODEL_RE = re.compile(r"^###\s+(.+?)\s*$")
# What a heading may put before the model name: a list number (`1.`) or a label (`Model:`).
HEADING_PREFIX_RE = re.compile(r"^(?:\d+[.)]\s*|(?:model|table|relation)\b\s*:?\s*)+", re.I)
# `CHECK <n>: <STATUS>` at the start of a line, so a check quoted inside another line's reason is
# not read as a verdict. A list bullet before it, `**` bold around the tokens, and the check's
# title between the number and the colon (`CHECK 1 - Table Existence: PASS`, as the verifier's
# own instructions head each check) are allowed.
CHECK_RE = re.compile(
    r"^\s*(?:[-*]\s+)?\**CHECK\s*(\d+)\**(?:\s*[-–—]\s*[^:\n]*?)?\**\s*:\**\s*\**"  # noqa: RUF001
    r"(PASS|FAIL|NEEDS_INFO|N/A)\b"
)


def model_name(heading: str, workspace: Path | None = None) -> str:
    """The model a `### ...` heading names, as `model_files` keys it: the heading's first word
    after any list number or `Model:` label, without backticks or bold, a directory or schema
    prefix, or a `.sql` suffix. With a workspace, the name is then resolved as the tools resolve
    a verifier's name (`project.model_name`): case-insensitively, and through an alias the model
    is built under."""
    words = HEADING_PREFIX_RE.sub("", heading.strip().strip("`*")).split()
    name = words[0].strip("`*") if words else ""
    name = name.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".sql")
    return project.model_name(workspace, name) if workspace else project.bare_name(name)


def parse_report(
    report: str, hit_max_turns: bool = False, workspace: Path | None = None
) -> dict[str, Any]:
    """`{"parsed", "models": {model: {n: status}}, "overall"}`. Check lines count only under a
    `### <model>` heading. `overall` is NEEDS_INFO when nothing parsed or the verifier hit its
    turn cap, else FAIL if any check failed, NEEDS_INFO if any needs info, else PASS."""
    models: dict[str, dict[int, str]] = {}
    model: str | None = None
    for line in report.splitlines():
        if heading := MODEL_RE.match(line.strip()):
            model = model_name(heading.group(1), workspace)
        elif (check := CHECK_RE.match(line)) and model is not None:
            models.setdefault(model, {})[int(check.group(1))] = check.group(2)
    parsed = bool(models)
    statuses = {status for checks in models.values() for status in checks.values()}
    if not parsed or hit_max_turns:
        overall = "NEEDS_INFO"
    elif "FAIL" in statuses:
        overall = "FAIL"
    elif "NEEDS_INFO" in statuses:
        overall = "NEEDS_INFO"
    else:
        overall = "PASS"
    return {"parsed": parsed, "models": models, "overall": overall}


def summarize_transcript(path: Path) -> dict[str, int]:
    """`{"turns", "tool_calls"}` from a subagent transcript (the CLI's
    `subagents/agent-<id>.jsonl`, one JSON object per line): a turn is one model call, written
    as one `"type": "assistant"` line per content block sharing a `message.id`; a tool call is
    one `tool_use` block. Raises OSError or ValueError when the file cannot be read as such."""
    calls: set[str] = set()
    tool_calls = 0
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if entry.get("type") != "assistant":
                continue
            message = entry["message"]
            calls.add(str(message["id"]))
            tool_calls += sum(1 for block in message["content"] if block.get("type") == "tool_use")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"subagent transcript {path} line {number}: {exc!r}") from exc
    return {"turns": len(calls), "tool_calls": tool_calls}
