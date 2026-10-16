"""Paired per-task bootstrap of the accuracy difference between two configs (plan §4)."""

import random
import statistics

from evals.metrics import is_success


def task_success_rates(by_task: dict[str, list[float | None]]) -> dict[str, float]:
    return {
        task: sum(1 for r in rewards if is_success(r)) / len(rewards)
        for task, rewards in by_task.items()
    }


def paired_bootstrap(
    baseline: dict[str, list[float | None]],
    candidate: dict[str, list[float | None]],
    seed: int,
    n_boot: int,
) -> dict[str, float | bool]:
    """Candidate minus baseline, in percentage points, with a 95% percentile interval.

    Tasks are resampled with replacement and both configs keep their own result for each
    drawn task, so per-task difficulty cancels out of the difference. The difference is
    significant only when the interval excludes 0. Each task weighs the same; with the same
    number of trials per task (k=3 here) that equals the difference in leaderboard accuracy.
    """
    if set(baseline) != set(candidate):
        # Count only: these may be holdout jobs, whose task ids stay out of the output.
        unpaired = len(set(baseline) ^ set(candidate))
        raise ValueError(f"jobs must cover the same tasks; {unpaired} tasks are unpaired")
    base, cand = task_success_rates(baseline), task_success_rates(candidate)
    diffs = [100.0 * (cand[task] - base[task]) for task in sorted(base)]
    rng = random.Random(seed)
    n = len(diffs)
    boots = [statistics.fmean(rng.choices(diffs, k=n)) for _ in range(n_boot)]
    # n=40 cut points sit at 2.5%, 5%, ..., 97.5%: the first and last bound the 95% interval.
    cuts = statistics.quantiles(boots, n=40, method="inclusive")
    ci_low, ci_high = cuts[0], cuts[-1]
    return {
        "diff": statistics.fmean(diffs),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "significant": ci_low > 0 or ci_high < 0,
    }
