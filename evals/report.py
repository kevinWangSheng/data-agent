"""Scores and split reports for a Harbor job.

Holdout reports carry aggregates only: no task ids, trial names, per-task outcomes or error
text ever reach the output (ADR-0004). Dev reports add per-task outcomes and where each
trial's events live, for failure attribution.
"""

import json
from pathlib import Path
from typing import Any

from evals.harbor import Trial, by_task
from evals.metrics import compute_metrics, compute_pass_at_k, is_success


def score(trials: list[Trial]) -> dict[str, Any]:
    """Leaderboard metrics (data-eng-bench `metrics.py`): errored trials count as 0."""
    rewards = by_task(trials)
    accuracy, stderr = compute_metrics(rewards)
    return {
        "accuracy": accuracy,
        "accuracy_stderr": stderr,
        **compute_pass_at_k(rewards),
        "n_trials": len(trials),
    }


def summary(trials: list[Trial], expected_tasks: list[str]) -> dict[str, Any]:
    """Aggregate scores plus outcome counts; infrastructure errors are kept apart from agent
    failures (a trial that errored has no reward, a failed one ran and scored 0)."""
    errored = sum(1 for t in trials if t.exception_type is not None)
    succeeded = sum(1 for t in trials if is_success(t.reward))
    present = {t.task for t in trials}
    return {
        **score(trials),
        "n_tasks": len(present),
        "n_tasks_missing": len(set(expected_tasks) - present),
        "n_succeeded": succeeded,
        "n_failed": len(trials) - succeeded - errored,
        "n_errored": errored,
        "cost_usd": sum(t.cost_usd for t in trials),
    }


def _per_task(trials: list[Trial]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for task, rewards in sorted(by_task(trials).items()):
        out[task] = {"successes": sum(1 for r in rewards if is_success(r)), "trials": len(rewards)}
    return out


def _trial_rows(trials: list[Trial]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trials:
        events = t.dir / "agent" / "events.jsonl"
        rows.append(
            {
                "task": t.task,
                "trial": t.name,
                "reward": t.reward,
                "exception_type": t.exception_type,
                "cost_usd": t.cost_usd,
                "events": str(events) if events.exists() else None,
            }
        )
    return rows


def write_report(
    trials: list[Trial], manifest: dict[str, Any], split: str, out_dir: Path
) -> dict[str, Any]:
    """Write `out_dir/report.json` for one split of the manifest; dev also gets `trials.json`."""
    tasks: list[str] = manifest[split]
    in_split = [t for t in trials if t.task in set(tasks)]
    report: dict[str, Any] = {"split": split, **summary(in_split, tasks)}
    out_dir.mkdir(parents=True, exist_ok=True)
    if split == "dev":
        report["per_task"] = _per_task(in_split)
        rows = _trial_rows(in_split)
        (out_dir / "trials.json").write_text(json.dumps(rows, indent=2) + "\n")
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
