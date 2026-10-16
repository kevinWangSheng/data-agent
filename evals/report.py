"""Scores and split reports for a Harbor job.

Holdout reports carry aggregates only: no task ids, trial names, per-task outcomes or error
text ever reach the output (ADR-0004). Dev reports add per-task outcomes and where each
trial's events live, for failure attribution.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any

from evals.harbor import Trial, by_task
from evals.metrics import PASS_AT_KS, compute_metrics, compute_pass_at_k, is_success


def score(trials: list[Trial]) -> dict[str, Any]:
    """Leaderboard metrics (data-eng-bench `metrics.py`): errored trials count as 0.

    pass@k is given only for k up to the fewest trials any task has; above that it is undefined.
    """
    rewards = by_task(trials)
    accuracy, stderr = compute_metrics(rewards)
    fewest = min((len(rs) for rs in rewards.values()), default=0)
    return {
        "accuracy": accuracy,
        "accuracy_stderr": stderr,
        **compute_pass_at_k(rewards, tuple(k for k in PASS_AT_KS if k <= fewest)),
        "n_trials": len(trials),
    }


# Harbor's default `RetryConfig.exclude_exceptions` (harbor src/harbor/models/job/config.py):
# errors a rerun would not fix, so they are the agent's (or the task's) and score 0. Any other
# exception is infrastructure noise that Harbor retries; it is rerun, never scored (plan §7).
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
    error (the verifier still runs after an agent timeout).
    """
    succeeded = [t for t in trials if is_success(t.reward)]
    return {
        **score(trials),
        "n_tasks": len({t.task for t in trials}),
        "n_succeeded": len(succeeded),
        "n_failed": len(trials) - len(succeeded),
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
                "uncached_input_tokens": t.uncached_input_tokens,
                "cached_input_tokens": t.cached_input_tokens,
                "output_tokens": t.output_tokens,
                "cost_usd": t.cost_usd,
                "events": str(events) if events.exists() else None,
            }
        )
    return rows


# Trials per task: the leaderboard's k, used for the holdout and dev sets alike (ADR-0004).
TRIALS_PER_TASK = 3


def check_complete(trials: list[Trial], label: str, trials_per_task: int) -> None:
    """Refuse trials that are not ready to score: some task without exactly `trials_per_task`
    trials (an interrupted job; pass@k would silently drop or zero it) or an infrastructure
    error (to be rerun, never scored). Errors give counts only, so they name no holdout task.
    """
    counts = Counter(t.task for t in trials)
    off = sum(1 for n in counts.values() if n != trials_per_task)
    if off:
        raise ValueError(
            f"{off} of {len(counts)} {label} tasks do not have exactly {trials_per_task} "
            "trials; rerun the interrupted trials"
        )
    infra = sum(1 for t in trials if is_infra_error(t))
    if infra:
        noun = "error" if infra == 1 else "errors"
        raise ValueError(
            f"{infra} infrastructure {noun} in the {label} trials; rerun those trials first"
        )


def split_trials(
    trials: list[Trial], manifest: dict[str, Any], split: str, label: str
) -> list[Trial]:
    """The trials of one manifest split; refuses a job missing any task of the split, since
    scoring only the tasks present would inflate accuracy. Errors give counts only.
    """
    tasks: list[str] = manifest[split]
    in_split = [t for t in trials if t.task in set(tasks)]
    missing = set(tasks) - {t.task for t in in_split}
    if missing:
        raise ValueError(
            f"no trial results for {len(missing)} of {len(tasks)} {split} tasks in {label} "
            "job; every task in the split is needed"
        )
    return in_split


def write_report(
    trials: list[Trial],
    manifest: dict[str, Any],
    split: str,
    out_dir: Path,
    trials_per_task: int = TRIALS_PER_TASK,
) -> dict[str, Any]:
    """Write `out_dir/report.json` for one split of the manifest; dev also gets `trials.json`.

    Refuses a split that is incomplete: a task without results, or a split `check_complete`
    rejects. Errors give counts only, so they name no holdout task.
    """
    in_split = split_trials(trials, manifest, split, "this")
    check_complete(in_split, split, trials_per_task)
    report: dict[str, Any] = {"split": split, **summary(in_split)}
    out_dir.mkdir(parents=True, exist_ok=True)
    if split == "dev":
        report["per_task"] = _per_task(in_split)
        rows = _trial_rows(in_split)
        (out_dir / "trials.json").write_text(json.dumps(rows, indent=2) + "\n")
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
