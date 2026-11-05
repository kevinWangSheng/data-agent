"""Scores and split reports for a Harbor job.

Holdout reports carry aggregates only: no task ids, trial names, per-task outcomes or error
text ever reach the output (ADR-0004). Dev reports add per-task outcomes, per-task process
metrics (evals.process) and where each trial's events live, for failure attribution.
"""

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from evals import spend
from evals.harbor import Trial, by_task, last_event_file
from evals.metrics import PASS_AT_KS, compute_metrics, compute_pass_at_k, is_success
from evals.process import job_metrics

# The repo's own manifest: on the server, the real one (evals.ablate checks a dev run the same way).
REPO_MANIFEST = Path(__file__).resolve().parent / "manifest.json"


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
# Except ApiUsageLimitError, which Harbor also excludes: the model account's limit or balance ran
# out, which says nothing about the agent, so it is rerun once the account is topped up.
NOT_RETRIED = frozenset(
    {
        "AgentTimeoutError",
        "VerifierTimeoutError",
        "RewardFileNotFoundError",
        "RewardFileEmptyError",
        "VerifierOutputParseError",
        "AgentSafetyRefusalError",
        "AgentAuthenticationError",
        "ModelNotFoundError",
        "UnsupportedModelParametersError",
    }
)


def billed_usd(trial: Trial) -> float:
    """The trial's `cost_usd` as the model account is billed it (evals.spend), by its scored
    event file (`last_event_file`); at the full `cost_usd` when that file cannot be read or holds
    no result event."""
    events = last_event_file(trial)
    if events is None:
        return trial.cost_usd
    first: dict[str, Any] | None = None
    version: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    try:
        lines = events.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return trial.cost_usd
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # a blank line, or one cut short by a killed run (as evals.process)
        if not isinstance(event, dict):
            continue
        if first is None:
            first = event
        if version is None and event.get("type") == "version":
            version = event
        if event.get("type") == "result":
            result = event
    if result is None:
        return trial.cost_usd
    cost = Decimal(str(trial.cost_usd))
    return float(spend.billed(cost, result, spend.run_rate(first, version)))


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
        "billed_cost_usd": sum(billed_usd(t) for t in trials),
    }


def _per_task(trials: list[Trial]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for task, rewards in sorted(by_task(trials).items()):
        out[task] = {"successes": sum(1 for r in rewards if is_success(r)), "trials": len(rewards)}
    return out


def _trial_rows(trials: list[Trial]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trials:
        events = last_event_file(t)
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
                "billed_cost_usd": billed_usd(t),
                "events": str(events) if events else None,
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


def check_dev_list(manifest: dict[str, Any], label: Path | str) -> None:
    """Refuse per-task output for a dev list that names a holdout task (ADR-0004 allows task ids
    for the dev set only): one the given manifest (`label` in errors) holds out, or one the repo's
    evals/manifest.json holds out, since a temporary manifest may hold out nothing. A repo
    manifest whose holdout cannot be read as a list of task ids is refused too. Errors name the
    manifests, never a task.
    """
    try:
        held_out = json.loads(REPO_MANIFEST.read_text())["holdout"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot read the holdout of {REPO_MANIFEST}: {exc}") from exc
    if not isinstance(held_out, list) or not all(isinstance(t, str) for t in held_out):
        raise ValueError(f"cannot read the holdout of {REPO_MANIFEST}: not a list of task ids")
    dev = set(manifest["dev"])
    for name, holdout in ((label, manifest.get("holdout") or []), (REPO_MANIFEST, held_out)):
        if dev & set(holdout):
            raise ValueError(f"{name} holds out tasks the dev list names; use the real manifest")


def write_report(
    trials: list[Trial],
    manifest: dict[str, Any],
    split: str,
    out_dir: Path,
    trials_per_task: int = TRIALS_PER_TASK,
    manifest_label: Path | str = "the manifest",
) -> dict[str, Any]:
    """Write `out_dir/report.json` for one split of the manifest; dev also gets `trials.json`.

    Refuses a split that is incomplete: a task without results, or a split `check_complete`
    rejects; and a dev list `check_dev_list` rejects. Errors give counts only, so they name no
    holdout task.
    """
    if split == "dev":
        check_dev_list(manifest, manifest_label)
    in_split = split_trials(trials, manifest, split, "this")
    check_complete(in_split, split, trials_per_task)
    report: dict[str, Any] = {"split": split, **summary(in_split)}
    out_dir.mkdir(parents=True, exist_ok=True)
    if split == "dev":
        report["per_task"] = _per_task(in_split)
        process = job_metrics(in_split)
        keys = ("mean_cost_usd_succeeded", "per_task", "timing")
        report["process"] = {key: process[key] for key in keys}
        rows = _trial_rows(in_split)
        (out_dir / "trials.json").write_text(json.dumps(rows, indent=2) + "\n")
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
