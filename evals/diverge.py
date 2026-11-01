"""`diverge --baseline JOB --candidate JOB --manifest M --out DIR`: read two jobs on the dev set.

Writes `DIR/diverge.json` and `DIR/diverge.md` and prints the markdown:

- the paired divergence: each dev task both jobs ran, with each job's successes out of its trials,
  listing the tasks where the success counts differ and which job did better (ADR-0008: read
  those trajectories first);
- each job's process metrics per task (evals.process); per-trial rows name each event file, for
  reading.

Only the manifest's dev tasks are shown. A trial of any other task is counted and nothing else of
it is read or written (ADR-0004), and two jobs with no dev trial between them are refused, as is
a manifest whose dev list shares a task with its holdout. So are infrastructure errors in the dev
trials and a dev task the jobs ran a different number of times: an interrupted job is rerun before
it is compared.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from evals.harbor import load_trials
from evals.process import job_metrics
from evals.report import is_infra_error

SIDES = ("baseline", "candidate")


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser(
        "diverge", help="dev set: paired per-task divergence and process metrics of two jobs"
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True, help="its dev list is shown")
    parser.add_argument("--out", type=Path, required=True)
    parser.set_defaults(run=run)


def divergent_tasks(base: dict[str, Any], cand: dict[str, Any]) -> list[dict[str, Any]]:
    """The paired tasks whose success counts differ, as {task, baseline, candidate, better}."""
    rows: list[dict[str, Any]] = []
    for task in sorted(set(base) & set(cand)):
        b, c = base[task], cand[task]
        if b["successes"] != c["successes"]:
            better = "baseline" if b["successes"] > c["successes"] else "candidate"
            rows.append(
                {
                    "task": task,
                    "baseline": [b["successes"], b["trials"]],
                    "candidate": [c["successes"], c["trials"]],
                    "better": better,
                }
            )
    return rows


def divergence_report(baseline: Path, candidate: Path, dev: set[str]) -> dict[str, Any]:
    jobs = dict(zip(SIDES, (load_trials(baseline), load_trials(candidate)), strict=True))
    in_dev = {side: [t for t in trials if t.task in dev] for side, trials in jobs.items()}
    if not any(in_dev.values()):
        raise ValueError("neither job has a dev-set trial; per-task output is for the dev set only")
    infra = sum(1 for trials in in_dev.values() for t in trials if is_infra_error(t))
    if infra:
        raise ValueError(f"{infra} infrastructure error(s) in the dev trials; rerun those first")
    counts = {side: Counter(t.task for t in trials) for side, trials in in_dev.items()}
    uneven = sorted(
        task
        for task in set(counts["baseline"]) & set(counts["candidate"])
        if counts["baseline"][task] != counts["candidate"][task]
    )
    if uneven:
        raise ValueError(
            f"the jobs ran these dev tasks a different number of times: {', '.join(uneven)}; "
            "rerun the interrupted trials"
        )
    metrics = {side: job_metrics(trials) for side, trials in in_dev.items()}
    base_tasks, cand_tasks = (metrics[side]["per_task"] for side in SIDES)
    divergent = divergent_tasks(base_tasks, cand_tasks)
    return {
        "labels": {"baseline": baseline.name, "candidate": candidate.name},
        "paired_tasks": len(set(base_tasks) & set(cand_tasks)),
        "unpaired_tasks": sorted(set(base_tasks) ^ set(cand_tasks)),
        "outside_dev_trials": {side: len(jobs[side]) - len(in_dev[side]) for side in SIDES},
        "divergent": divergent,
        "wins": {side: sum(1 for row in divergent if row["better"] == side) for side in SIDES},
        **metrics,
    }


def _ratio(pair: list[int]) -> str:
    return f"{pair[0]}/{pair[1]}"


def _cost(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def markdown(report: dict[str, Any]) -> str:
    labels = report["labels"]
    base, cand = labels["baseline"], labels["candidate"]
    wins = report["wins"]
    lines = [
        f"# Dev set: {cand} against {base}",
        "",
        f"{report['paired_tasks']} paired tasks; successes differ on {len(report['divergent'])}: "
        f"{base} better on {wins['baseline']}, {cand} better on {wins['candidate']}.",
    ]
    if report["unpaired_tasks"]:
        lines.append(f"Run by one job only: {', '.join(report['unpaired_tasks'])}.")
    outside = report["outside_dev_trials"]
    if any(outside.values()):
        lines.append(
            f"Trials outside the dev set, not shown: {base} {outside['baseline']}, "
            f"{cand} {outside['candidate']}."
        )
    lines += ["", f"| task | {base} | {cand} | better |", "|---|---:|---:|---|"]
    for row in report["divergent"]:
        better = labels[row["better"]]
        lines.append(
            f"| {row['task']} | {_ratio(row['baseline'])} | {_ratio(row['candidate'])} | {better} |"
        )
    for side in SIDES:
        job = report[side]
        lines += [
            "",
            f"## Process metrics: {labels[side]}",
            "",
            f"Mean spend of a successful trial: {_cost(job['mean_cost_usd_succeeded'])} USD.",
            "",
            "| task | successes | mean turns | stopped: budget / turns | verifier calls | "
            "verifier checks | model edits | cost (USD) |",
            "|---|---:|---:|---:|---:|---|---:|---:|",
        ]
        for task, m in job["per_task"].items():
            checks = ", ".join(f"{k} {v}" for k, v in sorted(m["verifier_checks"].items())) or "-"
            lines.append(
                f"| {task} | {m['successes']}/{m['trials']} | {m['mean_turns']:.1f} | "
                f"{m['stopped_by_budget']} / {m['stopped_by_turns']} | {m['verifier_calls']} | "
                f"{checks} | {m['model_edits']} | {m['cost_usd']:.4f} |"
            )
    return "\n".join(lines) + "\n"


def write_divergence(baseline: Path, candidate: Path, manifest: Path, out: Path) -> dict[str, Any]:
    """Build the report and write it under `out`; ValueError when there is nothing to show."""
    splits = json.loads(manifest.read_text())
    dev = set(splits["dev"])
    if dev & set(splits.get("holdout", [])):
        raise ValueError(f"{manifest} lists tasks in both dev and holdout; use the real manifest")
    report = divergence_report(baseline, candidate, dev)
    out.mkdir(parents=True, exist_ok=True)
    (out / "diverge.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "diverge.md").write_text(markdown(report))
    return report


def run(args: argparse.Namespace) -> int:
    try:
        report = write_divergence(args.baseline, args.candidate, args.manifest, args.out)
    except (OSError, KeyError, ValueError) as exc:
        print(f"diverge: {exc}", file=sys.stderr)
        return 2
    print(markdown(report), end="")
    print(f"-> {args.out}")
    return 0
