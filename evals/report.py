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


# Harbor's default `RetryConfig.exclude_exceptions` (harbor src/harbor/models/job/config.py):
# errors a rerun would not fix, so they are the agent's (or the task's) and stay in the score.
# Any other exception is infrastructure noise that Harbor retries.
NOT_RETRIED = frozenset(
    {
        "AgentTimeoutError",
        "VerifierTimeoutError",
        "RewardFileNotFoundError",
        "RewardFileEmptyError",
        "VerifierOutputParseError",
        "ApiUsageLimitError",
        "AgentSafetyRefusalError",
        "AgentAuthenticationError",
        "ModelNotFoundError",
        "UnsupportedModelParametersError",
    }
)


def is_infra_error(trial: Trial) -> bool:
    return (
        not is_success(trial.reward)
        and trial.exception_type is not None
        and trial.exception_type not in NOT_RETRIED
    )


def summary(trials: list[Trial]) -> dict[str, Any]:
    """Aggregate scores, outcome counts and usage totals.

    Outcomes are disjoint: a passing trial counts as succeeded even if Harbor also recorded an
    error (the verifier still runs after an agent timeout); of the rest, infrastructure errors
    (see NOT_RETRIED) are kept apart from agent failures. Both still score 0, as on the
    leaderboard; infrastructure errors are the ones to rerun.
    """
    succeeded = [t for t in trials if is_success(t.reward)]
    infra = [t for t in trials if is_infra_error(t)]
    return {
        **score(trials),
        "n_tasks": len({t.task for t in trials}),
        "n_succeeded": len(succeeded),
        "n_failed": len(trials) - len(succeeded) - len(infra),
        "n_infra_errors": len(infra),
        "uncached_input_tokens": sum(t.uncached_input_tokens for t in trials),
        "cached_input_tokens": sum(t.cached_input_tokens for t in trials),
        "output_tokens": sum(t.output_tokens for t in trials),
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
                "infra_error": is_infra_error(t),
                "uncached_input_tokens": t.uncached_input_tokens,
                "cached_input_tokens": t.cached_input_tokens,
                "output_tokens": t.output_tokens,
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
    # Scoring only the tasks present would inflate accuracy; only the count is shown, so a
    # holdout report still names no task.
    missing = set(tasks) - {t.task for t in in_split}
    if missing:
        raise ValueError(
            f"no trial results for {len(missing)} of {len(tasks)} {split} tasks in this job; "
            "a report needs every task in the split"
        )
    report: dict[str, Any] = {"split": split, **summary(in_split)}
    out_dir.mkdir(parents=True, exist_ok=True)
    if split == "dev":
        report["per_task"] = _per_task(in_split)
        rows = _trial_rows(in_split)
        (out_dir / "trials.json").write_text(json.dumps(rows, indent=2) + "\n")
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
