"""`diverge --baseline JOB --candidate JOB --manifest M --out DIR`: read two jobs on the dev set.

Writes `DIR/diverge.json` and `DIR/diverge.md` and prints the markdown:

- the paired divergence: each dev task both jobs ran, with each job's successes out of its trials,
  listing the tasks where the success counts differ and which job did better (ADR-0008: read
  those trajectories first);
- each job's process metrics per task, read from the trials' event files (`data_agent.agent`
  writes them): turns, runs stopped by the spend cap or the turn limit (the SDK result's
  subtype), verifier calls and their check statuses, edits to dbt model files, spend; plus the
  mean spend of the job's successful trials. Per-trial rows name each event file, for reading.

Only the manifest's dev tasks are shown. A trial of any other task is counted and nothing else of
it is read or written (ADR-0004), and two jobs with no dev trial between them are refused. So
are infrastructure errors in the dev trials and a dev task the jobs ran a different number of
times: an interrupted job is rerun before it is compared.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from evals.harbor import Trial, last_event_file, load_trials
from evals.metrics import is_success
from evals.report import is_infra_error

# Claude Code's file-writing tools; a call on a `.sql` file under a `models/` directory edits a
# dbt model.
EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})
# The SDK ResultMessage subtypes of a run cut off by a limit (data_agent.agent's `Spend` writes the
# budget one too when it stops the run itself).
STOPS = {"error_max_budget_usd": "budget", "error_max_turns": "turns"}


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser(
        "diverge", help="dev set: paired per-task divergence and process metrics of two jobs"
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True, help="its dev list is shown")
    parser.add_argument("--out", type=Path, required=True)
    parser.set_defaults(run=run)


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
        rows.setdefault(trial.task, []).append({"task": trial.task, **trial_metrics(trial)})
    succeeded = [r["cost_usd"] for rs in rows.values() for r in rs if r["success"]]
    return {
        "mean_cost_usd_succeeded": sum(succeeded) / len(succeeded) if succeeded else None,
        "per_task": {task: task_metrics(rs) for task, rs in sorted(rows.items())},
        "trials": [r for _, rs in sorted(rows.items()) for r in rs],
    }


def divergence(base: dict[str, Any], cand: dict[str, Any]) -> list[dict[str, Any]]:
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


def build(baseline: Path, candidate: Path, dev: set[str]) -> dict[str, Any]:
    jobs = {"baseline": load_trials(baseline), "candidate": load_trials(candidate)}
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
    divergent = divergence(metrics["baseline"]["per_task"], metrics["candidate"]["per_task"])
    paired = set(metrics["baseline"]["per_task"]) & set(metrics["candidate"]["per_task"])
    return {
        "labels": {"baseline": baseline.name, "candidate": candidate.name},
        "paired_tasks": len(paired),
        "unpaired_tasks": sorted(
            set(metrics["baseline"]["per_task"]) ^ set(metrics["candidate"]["per_task"])
        ),
        "outside_dev_trials": {
            side: len(jobs[side]) - len(in_dev[side]) for side in ("baseline", "candidate")
        },
        "divergent": divergent,
        "wins": {
            side: sum(1 for row in divergent if row["better"] == side)
            for side in ("baseline", "candidate")
        },
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
    for side in ("baseline", "candidate"):
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


def write(baseline: Path, candidate: Path, manifest: Path, out: Path) -> dict[str, Any]:
    """Build the report and write it under `out`; ValueError when there is nothing to show."""
    dev = set(json.loads(manifest.read_text())["dev"])
    report = build(baseline, candidate, dev)
    out.mkdir(parents=True, exist_ok=True)
    (out / "diverge.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "diverge.md").write_text(markdown(report))
    return report


def run(args: argparse.Namespace) -> int:
    try:
        report = write(args.baseline, args.candidate, args.manifest, args.out)
    except (OSError, KeyError, ValueError) as exc:
        print(f"diverge: {exc}", file=sys.stderr)
        return 2
    print(markdown(report), end="")
    print(f"-> {args.out}")
    return 0
