"""Scores and split reports for a Harbor job.

Holdout reports carry aggregates only: no task ids, trial names, per-task outcomes or error
text ever reach the output (ADR-0004). Dev reports add per-task outcomes, per-task process
metrics (evals.process) and where each trial's events live, for failure attribution.
"""

import json
from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from evals import spend
from evals.harbor import (
    Trial,
    by_task,
    event_files,
    last_event_file,
    load_trials,
    task_id,
    version_header,
)
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


# What the trials of merged jobs must share: from each event file's version header, and from the
# last run record of each job's OUT (evals.ablate.make_record), with the labels errors use.
MERGE_VERSION_KEYS = (
    ("git_commit", "git commit"),
    ("skill_version", "Skill version"),
    ("config", "config"),
)
MERGE_RECORD_KEYS = (
    ("benchmark_commit", "benchmark commit"),
    ("machine", "machine"),
    ("model", "model"),
)


MERGE_HELP = "one job, or several jobs of one config to merge (evals.report.load_jobs)"


def out_of(job: Path) -> Path | None:
    """The OUT a job of evals.ablate lives in: `OUT/jobs/<config>` -> `OUT`; None for a job
    outside that layout (a standalone Harbor job), whose surroundings are not its OUT."""
    resolved = job.resolve()
    return resolved.parent.parent if resolved.parent.name == "jobs" else None


def distinct(values: Sequence[Any]) -> int:
    """How many different JSON values there are among `values` (lists and dicts compared by
    content)."""
    return len({json.dumps(v, sort_keys=True) for v in values})


def last_record(job: Path) -> dict[str, Any] | None:
    """The last run record of the job's OUT (`OUT/jobs/<config>` -> `OUT/records/*.json`)."""
    out = out_of(job)
    paths = sorted((out / "records").glob("*.json")) if out else []
    return json.loads(paths[-1].read_text()) if paths else None


def load_jobs(job_dirs: Sequence[Path]) -> list[Trial]:
    """The trials of one config's jobs, merged as if run in one job (ticket 65); one job is read
    as `load_trials` reads it.

    Several jobs are merged only when no job is given twice and no trial name repeats (either
    would count a trial twice), every trial that ran records the same code, Skill version and
    config in its event files' version headers, and the last run records of the jobs' OUTs agree
    on the benchmark commit (a clean one), machine and model. Trials with an infrastructure error
    are not version-checked: they may end before the header, and scoring refuses them anyway.
    Errors give counts only, so they name no holdout task.
    """
    if len(job_dirs) == 1:
        return load_trials(job_dirs[0])
    resolved = Counter(job.resolve() for job in job_dirs)
    twice = sum(1 for n in resolved.values() if n > 1)
    if twice:
        raise ValueError(f"{twice} job directory(ies) are given more than once")
    trials = sorted((t for job in job_dirs for t in load_trials(job)), key=lambda t: t.dir.name)
    repeated = sum(1 for n in Counter(t.name for t in trials).values() if n > 1)
    if repeated:
        raise ValueError(
            f"{repeated} trial name(s) appear in more than one of the {len(job_dirs)} jobs; "
            "a trial is counted once"
        )
    check_same_versions(trials, len(job_dirs))
    check_same_records(job_dirs)
    check_same_requests(job_dirs)
    return trials


def check_same_versions(trials: list[Trial], n_jobs: int) -> None:
    """Every trial that ran records one code, Skill version and config; errors give counts."""
    keys = [key for key, _ in MERGE_VERSION_KEYS]
    seen: dict[str, set[str]] = {key: set() for key in keys}
    unversioned = 0
    for trial in trials:
        if is_infra_error(trial):
            continue
        headers = [version_header(events) for events in event_files(trial)]
        if not headers or not all(
            isinstance(h.get(k), str) and h[k] for h in headers for k in keys
        ):
            unversioned += 1
            continue
        for h in headers:
            for key in keys:
                seen[key].add(h[key])
    if unversioned:
        raise ValueError(
            f"{unversioned} trial(s) of the {n_jobs} jobs have no version header with the git "
            "commit, Skill version and config, so the jobs cannot be matched"
        )
    for key, label in MERGE_VERSION_KEYS:
        if len(seen[key]) > 1:
            raise ValueError(
                f"the trials of the {n_jobs} jobs record {len(seen[key])} different {label} "
                "values; merge only jobs of one version and config"
            )


def check_same_records(job_dirs: Sequence[Path]) -> None:
    """The jobs' OUTs' last run records agree on benchmark commit, machine and model, compared as
    evals.ablate.check_record does before reusing jobs under one OUT."""
    records = [last_record(job) for job in job_dirs]
    present = [r for r in records if r is not None]
    if len(present) < len(records):
        raise ValueError(
            f"{len(records) - len(present)} of {len(job_dirs)} jobs have no run record "
            "(OUT/records/*.json), so they cannot be matched to one benchmark checkout, machine "
            "and model"
        )
    for key, label in MERGE_RECORD_KEYS:
        values = [r.get(key) for r in present]
        unusable = sum(
            1
            for v in values
            if not isinstance(v, str)
            or not v
            or (key == "benchmark_commit" and v.endswith("-dirty"))
        )
        if unusable:
            raise ValueError(
                f"the run record of {unusable} of {len(job_dirs)} jobs gives no usable {label} "
                "(unset, or a benchmark checkout with uncommitted changes), so the jobs cannot "
                "be matched"
            )
        if len(set(values)) > 1:
            raise ValueError(
                f"the run records of the {len(job_dirs)} jobs give {len(set(values))} different "
                f"{label} values; merge only jobs run under the same conditions"
            )


# The request key merged jobs may differ in (evals.ablate.check_request): stage 1 and stage 2
# ask for different tasks by design (ADR-0008 补充 2026-11-06); the task set the merge fixes is
# the split's, each task with exactly the trials per task, which `write_report` checks.
REQUEST_TASKS = "tasks"


def check_same_requests(job_dirs: Sequence[Path]) -> None:
    """The jobs' OUTs' requests (`OUT/request.json`) agree on everything but their tasks: the
    model, the split and the run parameters (`agent_kwargs`, e.g. the agent timeout; one request
    with them and one without do not agree). A job without a readable request cannot be
    matched, nor a dev request (one with a split) without a non-empty task list: its requested
    task set would be unknown. Errors name the keys, never a task."""
    requests: list[dict[str, Any]] = []
    for job in job_dirs:
        request = read_request(job)
        if request is None:
            raise ValueError(
                f"a job of the {len(job_dirs)} has no readable request (OUT/request.json), so "
                "its model, split and run parameters cannot be matched"
            )
        tasks = request.get(REQUEST_TASKS)
        listed = isinstance(tasks, list) and tasks and all(isinstance(t, str) for t in tasks)
        if "split" in request and not listed:
            raise ValueError(
                f"a dev request of the {len(job_dirs)} jobs has no task list, so the task set it "
                "asked for cannot be known"
            )
        requests.append({k: v for k, v in request.items() if k != REQUEST_TASKS})
    keys = sorted({key for request in requests for key in request})
    differ = [key for key in keys if distinct([r.get(key) for r in requests]) > 1]
    if differ:
        raise ValueError(
            f"the requests of the {len(job_dirs)} jobs differ in {', '.join(differ)}; merge "
            "only jobs run under the same request"
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


def read_request(job: Path) -> dict[str, Any] | None:
    """The request of the job's OUT (`OUT/jobs/<config>` -> `OUT/request.json`), or None."""
    out = out_of(job)
    if out is None:
        return None
    try:
        request = json.loads((out / "request.json").read_text())
    except (OSError, ValueError):
        return None
    return request if isinstance(request, dict) else None


def kept_cost(kept_dir: Path) -> float:
    """The `cost_usd` of a dropped trial's event files, which evals.ablate.keep_spend moved into
    its directory under OUT/dropped/ (what the spend guard counts). Errors name no trial."""
    try:
        return float(sum(spend.file_spend(p).listed for p in sorted(kept_dir.glob("*.jsonl"))))
    except (spend.SpendUnknown, OSError, UnicodeDecodeError) as exc:
        raise ValueError(
            "a dropped trial's event file cannot be totalled; run `evals spend` on its OUT"
        ) from exc


def kept_trials(job: Path) -> list[tuple[Path, str | None]]:
    """The trials resumes of the job dropped, kept by evals.ablate under `OUT/dropped/<config>/`
    (each once, by its trial name): each directory with its task, or None for a trial Harbor was
    stopped in (no result.json, so no task). The config is the job's real directory name, as
    its OUT is found from its real path (`out_of`), not the name of a link or `.` it is given by."""
    out = out_of(job)
    if out is None:
        return []
    found: list[tuple[Path, str | None]] = []
    for kept in sorted(p for p in (out / "dropped" / job.resolve().name).glob("*") if p.is_dir()):
        result = kept / "result.json"
        task = task_id(json.loads(result.read_text())["task_name"]) if result.is_file() else None
        found.append((kept, task))
    return found


def excluded_trials(job_dirs: Sequence[Path], tasks: set[str]) -> dict[str, dict[str, Any]]:
    """The trials resumes of the jobs dropped from the score (`kept_trials`), as counts and
    `cost_usd` alone (no task or trial, so a holdout report may carry them, ADR-0004):
    `infra_excluded`, the infrastructure errors (a kept result.json) of `tasks`, and
    `interrupted_excluded`, trials Harbor was stopped in (no task to filter by: the job's own)."""
    out = {key: {"trials": 0, "cost_usd": 0.0} for key in ("infra", "interrupted")}
    for job in job_dirs:
        for kept, task in kept_trials(job):
            if task is not None and task not in tasks:
                continue
            key = "interrupted" if task is None else "infra"
            out[key]["trials"] += 1
            out[key]["cost_usd"] += kept_cost(kept)
    return {"infra_excluded": out["infra"], "interrupted_excluded": out["interrupted"]}


def planned_attempts(job: Path) -> int:
    """The trials per task the job's Harbor config plans (`n_attempts`, Harbor's default 1)."""
    try:
        config = json.loads((job / "config.json").read_text())
        attempts = config.get("n_attempts", 1)
    except (OSError, ValueError, AttributeError) as exc:
        raise ValueError(
            "a job's config.json cannot be read, so its planned trials per task are unknown"
        ) from exc
    if not isinstance(attempts, int) or attempts < 1:
        raise ValueError("a job's config.json plans no usable number of trials per task")
    return attempts


def unresolved_infra(trials: list[Trial], job_dirs: Sequence[Path], tasks: set[str]) -> int:
    """The infrastructure errors resumes dropped (`kept_trials`) of `tasks` that were never rerun
    to a result (human decision 2026-11-07). A rerun gets a new trial name (Harbor plans
    `n_attempts` per task and runs the missing ones), so slots are counted per job and task: of
    the planned trials, those without a scored trial (result.json) in the same job are open, and
    each dropped error fills at most one open slot. Another job's trial of the task fills that
    job's own planned slot, never this one."""
    unresolved = 0
    for job in job_dirs:
        dropped = Counter(task for _, task in kept_trials(job) if task in tasks)
        if not dropped:
            continue
        planned = planned_attempts(job)
        present = Counter(t.task for t in trials if t.dir.parent.resolve() == job.resolve())
        unresolved += sum(min(n, max(0, planned - present[task])) for task, n in dropped.items())
    return unresolved


# The run contract a report records (`run`): from the trials' version headers, the last run
# record of the jobs' OUTs, and their requests. `evals select` compares reports by it.
RUN_HEADER_KEYS = ("config", "git_commit", "skill_version", "model")
RUN_RECORD_KEYS = ("benchmark_commit", "machine")


def run_contract(trials: list[Trial], job_dirs: Sequence[Path]) -> dict[str, Any]:
    """What the trials were run under; a key is None when it is unrecorded or not one value
    (several jobs agree on it already, `load_jobs`). `agent_kwargs` is [] for a request
    without any."""

    def one(values: list[Any]) -> Any:
        return values[0] if values and None not in values and distinct(values) == 1 else None

    headers = [
        version_header(events) for t in trials if not is_infra_error(t) for events in event_files(t)
    ]
    records = [last_record(job) or {} for job in job_dirs]
    requests = [read_request(job) for job in job_dirs]
    run: dict[str, Any] = {key: one([h.get(key) for h in headers]) for key in RUN_HEADER_KEYS}
    run |= {key: one([r.get(key) for r in records]) for key in RUN_RECORD_KEYS}
    # evals.ablate.check_request leaves the split out of a holdout request (the holdout is run
    # whole) and `agent_kwargs` out of a request without any.
    run["split"] = one([r.get("split", "holdout") if r else None for r in requests])
    run["agent_kwargs"] = one([r.get("agent_kwargs", []) if r else None for r in requests])
    return run


# The efficiency line of a dev report's gates (ADR-0008 补充 2026-11-06): a later verifier
# dispatch that re-sent every model (evals.process.is_full_redispatch).
FULL_REDISPATCH_LINE = "full_redispatches"


def gates(
    in_split: list[Trial],
    process: dict[str, Any],
    excluded: dict[str, dict[str, Any]],
    unresolved: int,
) -> dict[str, Any]:
    """A dev report's gates, kept apart: correctness counts the scored trials' successes and
    failures; infrastructure states the infrastructure errors excluded from the score and passes
    only with none of them `unresolved` (`unresolved_infra`; human decision 2026-11-07: one
    rerun to a result is reported, not failed), and states the interrupted trials resumes
    reran, which do not fail it (the operator or Harbor stopped them); efficiency lists each
    trial with a full redispatch, a warning that is not a correctness failure. Efficiency fails
    on any warning; without one it passes only when every trial's event file records
    dispatches, else it is None (unknown). Correctness has no pass line here: the success
    lines are the human's, per evaluation (ADR-0008)."""
    successes = sum(1 for t in in_split if is_success(t.reward))
    warnings = [
        {
            "task": row["task"],
            "trial": row["trial"],
            "line": FULL_REDISPATCH_LINE,
            "count": row["full_redispatches"],
        }
        for row in process["trials"]
        if row.get("full_redispatches")
    ]
    every_trial_recorded = all(row["full_redispatches"] is not None for row in process["trials"])
    return {
        "correctness": {
            "successes": successes,
            "failures": len(in_split) - successes,
            "trials": len(in_split),
        },
        "infrastructure": {
            "passed": unresolved == 0,
            **excluded["infra_excluded"],
            "unresolved": unresolved,
            "interrupted": excluded["interrupted_excluded"],
        },
        "efficiency": {
            "passed": False if warnings else (True if every_trial_recorded else None),
            "warnings": warnings,
        },
    }


def write_report(
    trials: list[Trial],
    manifest: dict[str, Any],
    split: str,
    out_dir: Path,
    trials_per_task: int = TRIALS_PER_TASK,
    manifest_label: Path | str = "the manifest",
    job_dirs: Sequence[Path] = (),
) -> dict[str, Any]:
    """Write `out_dir/report.json` for one split of the manifest; dev also gets `trials.json`.

    Refuses a split that is incomplete: a task without results, or a split `check_complete`
    rejects; and a dev list `check_dev_list` rejects. Errors give counts only, so they name no
    holdout task. `job_dirs`, the jobs the trials come from, give the run contract (`run`) and
    the trials resumes dropped (`infra_excluded`, `interrupted_excluded`); a dev report adds
    `gates`.
    """
    if split == "dev":
        check_dev_list(manifest, manifest_label)
    in_split = split_trials(trials, manifest, split, "this")
    check_complete(in_split, split, trials_per_task)
    tasks = {t.task for t in in_split}
    excluded = excluded_trials(job_dirs, tasks)
    report: dict[str, Any] = {"split": split, **summary(in_split), **excluded}
    report["run"] = run_contract(in_split, job_dirs)
    out_dir.mkdir(parents=True, exist_ok=True)
    if split == "dev":
        report["per_task"] = _per_task(in_split)
        process = job_metrics(in_split)
        keys = ("mean_cost_usd_succeeded", "per_task", "timing", "gate")
        report["process"] = {key: process[key] for key in keys}
        unresolved = unresolved_infra(in_split, job_dirs, tasks)
        report["gates"] = gates(in_split, process, excluded, unresolved)
        rows = _trial_rows(in_split)
        (out_dir / "trials.json").write_text(json.dumps(rows, indent=2) + "\n")
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
