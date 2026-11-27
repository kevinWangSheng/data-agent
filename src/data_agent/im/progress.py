"""The status message's text of a turn (.scratch/im/spec.md "澄清、进度、取消"; contract item 6 of
ticket 130), in English as the web UI.

While the turn runs, the text is its current step, from the latest event that names one:

- `tool_start`, by the tool's kind as the web UI's `toolKind` has it
  (web/src/run-page/tools.ts): querying the database (`query_database`, ...), reading the
  schema or the context, checking, running a sandbox command, running dbt, drawing a chart, or
  an external server's tool by its server and tool name;
- `verifier_start`: verifying;
- `thinking`: thinking;
- `answer`: writing the answer.

Before any of them it says the turn has started. Once the turn has ended the text says how,
differently from every running text:

- a `result`: done (or, after a `clarification` with no `answer`, waiting for the answer to the
  question), or failed; with the duration and the cost (`result.cost_usd`). The duration runs
  from the first event's `ts` to `result.ended_ts` (ticket 122), or `result.ts` without it, as
  the web UI's run list counts it;
- no `result`, and the server's record says `cancelled` or `failure` (`ended`): stopped, or
  failed.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

# web/src/run-page/tools.ts: `DATA`, `EXTERNAL`, the kinds' name sets.
DATA = "mcp__data__"
EXTERNAL = re.compile(r"^mcp__(.+?)__(.+)$")
SQL = {"query_database", "query_labels", "explain_query", "classify_text"}
CONTEXT = {"discover", "hydrate", "dictionary_search"}
CHECK = {
    "map_columns",
    "check_model_schema",
    "audit_model_sources",
    "verify_model_values",  # no longer served; kept, like tools.ts, for older runs' events
    "model_diff",
    "contract_check",
    "grain_facts",
    "compare_by_key",
}
# What the status says while a tool of each kind runs.
STEPS = {
    "sql": "Querying the database…",
    "context": "Reading the context…",
    "schema": "Reading the schema…",
    "check": "Checking…",
    "sandbox": "Running a sandbox command…",
    "dbt": "Running dbt…",
    "chart": "Drawing a chart…",
    "file": "Reading files…",
    "edit": "Editing files…",
    "shell": "Running a command…",
    "agent": "Starting a subagent…",
}
CLARIFICATION_TOOL = DATA + "clarification"


def short_tool(tool: str) -> str:
    """`shortTool` of the web UI."""
    if tool.startswith(DATA):
        return tool[len(DATA) :]
    external = EXTERNAL.match(tool)
    return f"{external[1]} · {external[2]}" if external else tool


def tool_kind(tool: str) -> str:
    """`toolKind` of the web UI."""
    if tool.startswith(DATA):
        name = short_tool(tool)
        if name in SQL:
            return "sql"
        if name in CONTEXT:
            return "context"
        if name in CHECK:
            return "check"
        if name == "sandbox_exec":
            return "sandbox"
        if name == "dbt_execute":
            return "dbt"
        if name == "display_chart":
            return "chart"
        return "schema"
    if EXTERNAL.match(tool):
        return "external"
    if tool in ("Read", "Glob", "Grep"):
        return "file"
    if tool in ("Write", "Edit"):
        return "edit"
    if tool == "Bash":
        return "shell"
    if tool in ("Agent", "Task"):
        return "agent"
    return "other"


def step_text(event: Mapping[str, Any]) -> str | None:
    """What the status says after this event, when the event names a step."""
    kind = event.get("type")
    if kind == "tool_start":
        tool = str(event.get("tool", ""))
        if tool == CLARIFICATION_TOOL:
            return "Asking a question…"
        kind_of_tool = tool_kind(tool)
        if kind_of_tool in STEPS:
            return STEPS[kind_of_tool]
        return f"Calling {short_tool(tool)}…"
    if kind == "verifier_start":
        return "Verifying…"
    if kind == "thinking":
        return "Thinking…"
    if kind == "clarification":
        return "Asking a question…"
    if kind == "answer":
        return "Writing the answer…"
    return None


def find_result(events: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return next((event for event in reversed(events) if event.get("type") == "result"), None)


def failed(result: Mapping[str, Any]) -> bool:
    """Whether the `result` is a failure: the API error's has subtype `success`, so `is_error`
    decides, with the error subtypes besides."""
    return bool(result.get("is_error")) or str(result.get("subtype", "")).startswith("error")


def failure_cause(result: Mapping[str, Any]) -> str:
    """Why a failed `result` ended the turn, in a sentence."""
    reason = result.get("terminal_reason")
    subtype = result.get("subtype")
    if reason == "api_error":
        detail = result.get("api_error")
        return f"The model call failed: {detail}" if detail else "The model call failed."
    if reason == "budget_exhausted" or subtype == "error_max_budget_usd":
        return "The run's budget ran out before it had an answer."
    if reason == "max_turns" or subtype == "error_max_turns":
        return "The run used up its turns before it had an answer."
    return f"The run failed ({reason or subtype or 'no reason given'})."


def status_text(events: Sequence[dict[str, Any]], ended: str | None = None) -> str:
    """The text for a turn whose events so far are `events`; `ended` is the record's status of a
    run that exited without a `result` (`cancelled`, `failure`), else None."""
    result = find_result(events)
    if result is not None:
        if failed(result):
            outcome = "Failed"
        elif any(event.get("type") == "answer" for event in events):
            outcome = "Done"
        elif any(event.get("type") == "clarification" for event in events):
            outcome = "Waiting for your answer to the question"
        else:
            outcome = "Ended without an answer"
        return " · ".join([outcome, *spent(events, result)])
    if ended == "cancelled":
        return "Stopped"
    if ended is not None:
        return "Failed: the run exited without a result"
    step = next(
        (text for text in map(step_text, reversed(events)) if text is not None),
        "Started…",
    )
    return step


def spent(events: Sequence[Mapping[str, Any]], result: Mapping[str, Any]) -> list[str]:
    """The turn's duration and cost, as far as the events give them."""
    out: list[str] = []
    start = parse_ts(events[0].get("ts")) if events else None
    end = parse_ts(result.get("ended_ts")) or parse_ts(result.get("ts"))
    if start is not None and end is not None:
        out.append(duration(max((end - start).total_seconds(), 0.0)))
    cost = result.get("cost_usd")
    if isinstance(cost, int | float) and not isinstance(cost, bool):
        out.append(f"${cost:.2f}" if cost >= 0.01 else f"${cost:.4f}")
    return out


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def duration(seconds: float) -> str:
    whole = round(seconds)
    if whole < 60:
        return f"{whole}s"
    minutes, rest = divmod(whole, 60)
    return f"{minutes}m {rest:02d}s"
