"""`accept`: does a change pass ADR-0008 on the dev set; can the old version's results be reused.

Inputs are two Harbor jobs on the whole dev set (`--old`, the version before the change; `--new`,
the change), the change's target tasks, the regression set file (a JSON list of dev task names)
and any reruns of regression tasks (`--rerun`, jobs of the new version). The change passes when
all three of ADR-0008's conditions hold:

1. point estimate: the new version's successes over the dev set are not fewer than the old's;
2. targets: the targets' successes add up to more than the old's;
3. regression: every regression task is k/k in the new round; one below k is rerun k times and
   regresses when the rerun is at most 1/3 again.

The old results are reused only when, against the new job, the version headers agree on every
field but the code and Skill versions, the run records (ticket 27's `OUT/records/`) agree on the
benchmark commit and the machine, the old job started at most two weeks before the new one ended,
and the old round had no infrastructure error (none left in the job, none dropped by a resume
into `OUT/dropped/<config>/`). Otherwise the verdict is `rerun-old`, unless a regression task
regressed: that is judged on the new version alone, so the verdict is `fail` either way.

Dev set only: any job, target or regression task outside the manifest's dev set is refused, by
count, so no holdout task is named (ADR-0004).

Exit 0 on pass; 1 on fail, rerun-regression (rerun the listed tasks, add them with --rerun) or
rerun-old; 2 on input it refuses.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from evals.ablate import has_versions, header
from evals.harbor import Trial, event_files, load_trials
from evals.metrics import is_success
from evals.report import TRIALS_PER_TASK, check_complete, is_infra_error, split_trials

MAX_AGE = timedelta(days=14)
# Version header fields that a change is expected to alter, or that differ per event file.
CHANGED_BY_DESIGN = frozenset({"git_commit", "skill_version"})
PER_FILE = frozenset({"seq", "type", "ts"})
# Written by every run (data_agent.agent); one no trial has cannot be matched either.
REQUIRED = frozenset({"config", "layers", "model", "sdk_version", "cli_version"})
MISSING = "(missing)"  # not JSON, so no header value collides with it
RECORD_KEYS = (("benchmark_commit", "benchmark commit"), ("machine", "machine"))


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    p = commands.add_parser("accept", help="ADR-0008: does a change pass on the dev set")
    p.add_argument("--old", type=Path, required=True, help="job of the version before the change")
    p.add_argument("--new", type=Path, required=True, help="job of the change")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--target", action="append", required=True, help="target task; repeatable")
    p.add_argument("--regression", type=Path, required=True, help="JSON list of dev task names")
    p.add_argument("--rerun", type=Path, action="append", default=[], help="rerun job; repeatable")
    p.add_argument("--trials", type=int, default=TRIALS_PER_TASK, help="trials per task")
    p.add_argument("--json", action="store_true")
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    try:
        manifest = json.loads(args.manifest.read_text())
        regression = json.loads(args.regression.read_text())
        if not isinstance(regression, list) or not all(isinstance(t, str) for t in regression):
            raise ValueError(f"{args.regression} must be a JSON list of task names")
        result = evaluate(
            args.old, args.new, manifest["dev"], args.target, regression, args.rerun, args.trials
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else render(result))
    return 0 if result["verdict"] == "pass" else 1


def check_dev(tasks: set[str], dev: set[str], label: str) -> None:
    outside = len(tasks - dev)
    if outside:
        raise ValueError(
            f"{outside} {label} task(s) are not in the manifest's dev set; accept works on the "
            "dev set only (ADR-0004)"
        )


def successes(trials: list[Trial]) -> Counter[str]:
    return Counter(t.task for t in trials if is_success(t.reward))


def evaluate(
    old_job: Path,
    new_job: Path,
    dev_tasks: list[str],
    targets: list[str],
    regression: list[str],
    rerun_jobs: list[Path],
    trials: int,
) -> dict[str, Any]:
    dev = set(dev_tasks)
    old, new = load_trials(old_job), load_trials(new_job)
    reruns = [t for job in rerun_jobs for t in load_trials(job)]
    for label, tasks in (
        ("old job", {t.task for t in old}),
        ("new job", {t.task for t in new}),
        ("rerun", {t.task for t in reruns}),
        ("target", set(targets)),
        ("regression", set(regression)),
    ):
        check_dev(tasks, dev, label)
    manifest = {"dev": dev_tasks}
    split_trials(old, manifest, "dev", "the old")
    split_trials(new, manifest, "dev", "the new")
    check_complete(new, "new", trials)

    old_infra = had_infra_errors(old_job, old)
    problems = reuse_problems(old_job, new_job, old, new, old_infra)
    conditions: dict[str, Any] = {}
    if not old_infra:  # the old round cannot be scored at all
        check_complete(old, "old", trials)
        old_wins, new_wins = successes(old), successes(new)
        old_total, new_total = sum(old_wins.values()), sum(new_wins.values())
        old_target = sum(old_wins[t] for t in targets)
        new_target = sum(new_wins[t] for t in targets)
        n = len(old)
        conditions["point_estimate"] = {
            "old": f"{old_total}/{n}",
            "new": f"{new_total}/{n}",
            "diff_points": 100.0 * (new_total - old_total) / n,
            "passed": new_total >= old_total,
        }
        conditions["targets"] = {
            "old": f"{old_target}/{trials * len(targets)}",
            "new": f"{new_target}/{trials * len(targets)}",
            "passed": new_target > old_target,
        }
    conditions["regression"] = regression_check(new, reruns, regression, trials)

    rerun_tasks = [r["task"] for r in conditions["regression"]["tasks"] if r["status"] == "rerun"]
    if conditions["regression"]["passed"] is False:
        verdict = "fail"  # judged on the new version alone: rerunning the old cannot change it
    elif problems:
        verdict = "rerun-old"
    elif any(c["passed"] is False for c in conditions.values()):
        verdict = "fail"
    elif rerun_tasks:
        verdict = "rerun-regression"
    else:
        verdict = "pass"
    return {
        "verdict": verdict,
        "old_reusable": not problems,
        "reuse_problems": problems,
        "rerun_tasks": rerun_tasks,
        "conditions": conditions,
    }


def regression_check(
    new: list[Trial], reruns: list[Trial], regression: list[str], trials: int
) -> dict[str, Any]:
    """Each regression task's status: ok (k/k), recovered (rerun above 1/3), regressed (rerun at
    most 1/3) or rerun (below k/k, no rerun given). `passed` is None while reruns are missing."""
    new_wins = successes(new)
    below = [task for task in sorted(set(regression)) if new_wins[task] < trials]
    given = [t for t in reruns if t.task in below]
    if given:
        if fields(given) != fields(new):
            raise ValueError("the reruns' version headers differ from the new job's")
        check_complete(given, "rerun", trials)
    rerun_wins, rerun_tasks = successes(given), {t.task for t in given}
    rows: list[dict[str, Any]] = []
    for task in sorted(set(regression)):
        row: dict[str, Any] = {"task": task, "round": f"{new_wins[task]}/{trials}"}
        if task not in below:
            row["status"] = "ok"
        elif task not in rerun_tasks:
            row["status"] = "rerun"
        else:
            row["rerun"] = f"{rerun_wins[task]}/{trials}"
            row["status"] = "regressed" if 3 * rerun_wins[task] <= trials else "recovered"
        rows.append(row)
    statuses = {row["status"] for row in rows}
    passed = False if "regressed" in statuses else None if "rerun" in statuses else True
    return {"tasks": rows, "passed": passed}


def fields(trials: list[Trial]) -> dict[str, set[str]]:
    """Every version header field's values over the event files of the trials that ran; a file
    whose header lacks a field another file has, or a REQUIRED one, adds MISSING to that field's
    values. Trials with
    an infrastructure error are left out: never scored, they may end before the header."""
    ran = [t for t in trials if not is_infra_error(t)]
    headers = [header(events) for trial in ran for events in event_files(trial)]
    keys = ({key for h in headers for key in h} | REQUIRED) - PER_FILE
    return {
        key: {json.dumps(h[key], sort_keys=True) if key in h else MISSING for h in headers}
        for key in keys
    }


def last_record(job: Path) -> dict[str, Any] | None:
    """The last run record of the job's OUT (`OUT/jobs/<config>` -> `OUT/records/*.json`)."""
    paths = sorted((job.parent.parent / "records").glob("*.json"))
    return json.loads(paths[-1].read_text()) if paths else None


def times(trials: list[Trial], key: str) -> tuple[list[datetime], int]:
    """The trials' `key` times, and how many trials that ran have none. Infrastructure error
    trials are not counted: they may end before Harbor writes the times, an old one forces a
    rerun anyway, and the new job may have none (check_complete)."""
    out: list[datetime] = []
    missing = 0
    for trial in trials:
        value = json.loads((trial.dir / "result.json").read_text()).get(key)
        if value:
            out.append(datetime.fromisoformat(value))
        elif not is_infra_error(trial):
            missing += 1
    return out, missing


def reuse_problems(
    old_job: Path, new_job: Path, old: list[Trial], new: list[Trial], old_infra: bool
) -> list[str]:
    """Why the old results cannot be reused (ADR-0008); empty when they can."""
    problems: list[str] = []
    for side, trials in (("old", old), ("new", new)):
        # An infrastructure error may end before the agent writes its events.
        unversioned = sum(1 for t in trials if not is_infra_error(t) and not has_versions(t))
        if unversioned:
            problems.append(f"{unversioned} {side} trial(s) have no version header")
    old_fields, new_fields = fields(old), fields(new)
    for key in sorted((old_fields.keys() | new_fields.keys()) - CHANGED_BY_DESIGN):
        before, after = old_fields.get(key, set()), new_fields.get(key, set())
        if len(before) != 1 or before != after or MISSING in before:
            problems.append(f"version field {key}: {sorted(before)} old, {sorted(after)} new")

    records = {"old": last_record(old_job), "new": last_record(new_job)}
    for side, record in records.items():
        if record is None:
            problems.append(f"the {side} job has no run record")
    if records["old"] is not None and records["new"] is not None:
        for key, label in RECORD_KEYS:
            before, after = records["old"].get(key), records["new"].get(key)
            # An empty or non-string value (e.g. DATA_AGENT_MACHINE unset) cannot be matched.
            if not isinstance(before, str) or not before or before != after:
                problems.append(f"{label}: {before!r} old, {after!r} new")

    started, old_missing = times(old, "started_at")
    finished, new_missing = times(new, "finished_at")
    untimed = [
        f"{missing} {side} trial(s) have no {key}, so the old results' age is unknown"
        for side, key, missing in (
            ("old", "started_at", old_missing),
            ("new", "finished_at", new_missing),
        )
        if missing
    ]
    if untimed:
        problems += untimed
    elif not started or not finished:
        problems.append("trial times are missing, so the old results' age is unknown")
    elif max(finished) - min(started) > MAX_AGE:
        days = (max(finished) - min(started)).days
        problems.append(
            f"the old job started {days} days before the new one ended (over {MAX_AGE.days})"
        )

    if old_infra:
        problems.append("the old round had infrastructure errors")
    return problems


def had_infra_errors(job: Path, trials: list[Trial]) -> bool:
    """An infrastructure error left in the job, or one a resume dropped (ablate.keep_spend keeps
    each dropped trial under `OUT/dropped/<config>/`)."""
    dropped = job.parent.parent / "dropped" / job.name
    return any(is_infra_error(t) for t in trials) or (dropped.is_dir() and any(dropped.iterdir()))


def render(result: dict[str, Any]) -> str:
    lines = [f"verdict: {result['verdict']}"]
    if result["old_reusable"]:
        lines.append("old results: reusable")
    else:
        lines.append("old results: rerun the old version")
        lines += [f"  - {p}" for p in result["reuse_problems"]]
    conditions = result["conditions"]
    for name in ("point_estimate", "targets"):
        if name in conditions:
            c = conditions[name]
            mark = "pass" if c["passed"] else "FAIL"
            extra = f" ({c['diff_points']:+.1f} points)" if "diff_points" in c else ""
            lines.append(f"{name}: {mark}, old {c['old']}, new {c['new']}{extra}")
        else:
            lines.append(f"{name}: not scored (the old job has infrastructure errors)")
    reg = conditions["regression"]
    mark = {True: "pass", False: "FAIL", None: "rerun needed"}[reg["passed"]]
    lines.append(f"regression: {mark}")
    for row in reg["tasks"]:
        rerun = f", rerun {row['rerun']}" if "rerun" in row else ""
        lines.append(f"  {row['task']}: {row['round']}{rerun} -> {row['status']}")
    if result["rerun_tasks"]:
        lines.append(f"rerun these tasks on the new version: {' '.join(result['rerun_tasks'])}")
    return "\n".join(lines)
