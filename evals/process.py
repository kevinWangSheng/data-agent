"""Process metrics of trials, read from their event files (`data_agent.agent` writes them; ticket
28): turns, a run stopped by the spend cap or the turn limit (the SDK result's subtype), verifier
calls and their check statuses, edits to dbt model files, spend; per task, and the mean spend of
the successful trials. For dev trials only: callers keep holdout trials out (ADR-0004).

Model edits count Claude Code's file-writing tools on a `.sql` file under a `models/` directory;
a model rewritten through Bash (`sed -i`, a heredoc) is not counted.
"""

import json
from collections import Counter
from pathlib import PurePosixPath
from typing import Any

from evals.harbor import Trial, last_event_file
from evals.metrics import is_success

EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})
# The SDK ResultMessage subtypes of a run cut off by a limit (data_agent.agent's `Spend` writes the
# budget one too when it stops the run itself).
STOPS = {"error_max_budget_usd": "budget", "error_max_turns": "turns"}


def is_model_edit(event: dict[str, Any]) -> bool:
    if event.get("type") != "tool_start" or event.get("tool") not in EDIT_TOOLS:
        return False
    path = PurePosixPath(str((event.get("input") or {}).get("file_path") or ""))
    return path.suffix == ".sql" and "models" in path.parts[:-1]


def trial_metrics(trial: Trial) -> dict[str, Any]:
    """One trial's process metrics, from the event file it is scored by (`last_event_file`)."""
    events = last_event_file(trial)
    turns, edits, calls, cost = 0, 0, 0, 0.0
    checks: Counter[str] = Counter()
    stopped: str | None = None
    if events is not None:
        with events.open() as f:
            for line in f:
                event = json.loads(line)
                kind = event.get("type")
                if kind == "result":
                    turns += int(event.get("num_turns") or 0)
                    cost += float(event.get("cost_usd") or 0.0)
                    stopped = STOPS.get(str(event.get("subtype")), stopped)
                elif kind == "verifier_start":
                    calls += 1
                elif kind == "verifier_end":
                    checks.update(str(c.get("status")) for c in event.get("checks") or [])
                elif is_model_edit(event):
                    edits += 1
    return {
        "task": trial.task,
        "trial": trial.name,
        "success": is_success(trial.reward),
        "turns": turns,
        "stopped": stopped,
        "verifier_calls": calls,
        "verifier_checks": dict(checks),
        "model_edits": edits,
        "cost_usd": cost,
        "events": str(events) if events else None,
    }


def task_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    checks: Counter[str] = Counter()
    for row in rows:
        checks.update(row["verifier_checks"])
    return {
        "successes": sum(1 for r in rows if r["success"]),
        "trials": len(rows),
        "mean_turns": sum(r["turns"] for r in rows) / len(rows),
        "stopped_by_budget": sum(1 for r in rows if r["stopped"] == "budget"),
        "stopped_by_turns": sum(1 for r in rows if r["stopped"] == "turns"),
        "verifier_calls": sum(r["verifier_calls"] for r in rows),
        "verifier_checks": dict(checks),
        "model_edits": sum(r["model_edits"] for r in rows),
        "cost_usd": sum(r["cost_usd"] for r in rows),
    }


def job_metrics(trials: list[Trial]) -> dict[str, Any]:
    """Process metrics of dev trials: per task, per trial, and the mean spend of a success."""
    rows: dict[str, list[dict[str, Any]]] = {}
    for trial in trials:
        rows.setdefault(trial.task, []).append(trial_metrics(trial))
    succeeded = [r["cost_usd"] for rs in rows.values() for r in rs if r["success"]]
    return {
        "mean_cost_usd_succeeded": sum(succeeded) / len(succeeded) if succeeded else None,
        "per_task": {task: task_metrics(rs) for task, rs in sorted(rows.items())},
        "trials": [r for _, rs in sorted(rows.items()) for r in rs],
    }
