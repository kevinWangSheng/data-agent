# Copied from data-eng-bench (https://github.com/Snowflake-Labs/data-eng-bench),
# leaderboard/src/leaderboard/core/metrics.py at commit a3278ad102829a6084dde086244a0ef665a8011c.
# Copyright 2026 Snowflake Inc. Licensed under the Apache License, Version 2.0
# (http://www.apache.org/licenses/LICENSE-2.0).
# Modified: kept the pure scoring functions (is_success, compute_metrics, compute_pass_at_k)
# unchanged; dropped the hub-backed submission, resource and display helpers, the
# `leaderboard.core.hub` import they need, and the module docstring that described them;
# completed the type annotations (`reward: float | None`, element types of the lists and dicts).
"""metrics: leaderboard metric computation over per-task reward lists."""

from __future__ import annotations

# The pass@k columns the leaderboard metrics_schema requires.
PASS_AT_KS = (2, 3)


def is_success(reward: float | None) -> bool:
    """A trial succeeds iff reward > 0 (errored trials have none -> failure)."""
    return bool(reward) and float(reward) > 0


def compute_metrics(by_task: dict[str, list[float | None]]) -> tuple[float, float]:
    """Return (accuracy, accuracy_stderr) as percentages.

    accuracy = successful / total trials, matching the Harbor importer;
    accuracy_stderr from the per-task standard-error formula
    s^2 = (1/n^2) * sum_i p_i (1 - p_i) / (k_i - 1).
    """
    total = sum(len(rs) for rs in by_task.values())
    successful = sum(1 for rs in by_task.values() for r in rs if is_success(r))
    accuracy = 100.0 * successful / total if total else 0.0

    n = len(by_task)
    variance = 0.0
    for rs in by_task.values():
        k = len(rs)
        if k < 2:
            continue  # p(1-p)/(k-1) undefined for k=1
        p = sum(1 for r in rs if is_success(r)) / k
        variance += p * (1.0 - p) / (k - 1)
    variance = variance / (n * n) if n else 0.0
    return accuracy, 100.0 * variance**0.5


def compute_pass_at_k(
    by_task: dict[str, list[float | None]], ks: tuple[int, ...] = PASS_AT_KS
) -> dict[str, float]:
    """{pass_at_k: value} -- the unbiased per-task estimator
    1 - C(n-c, k)/C(n, k), averaged over tasks.

    A task with fewer than k trials is skipped for that k (the trial-count
    check requires >= 3 per task, so normally none are)."""
    out: dict[str, float] = {}
    for k in ks:
        vals: list[float] = []
        for rs in by_task.values():
            n = len(rs)
            if n < k:
                continue
            c = sum(1 for r in rs if is_success(r))
            if n - c < k:
                vals.append(1.0)
                continue
            miss = 1.0
            for i in range(k):
                miss *= (n - c - i) / (n - i)
            vals.append(1.0 - miss)
        out[f"pass_at_{k}"] = round(sum(vals) / len(vals), 4) if vals else 0.0
    return out
