"""`select REPORT_DIR... --out DIR`: the configuration-selection table of dev reports.

The product default is chosen on the dev set from `full`, `baseline` and `no-verifiers`
(ADR-0008 统一评估; eval-readiness ticket 04). Each REPORT_DIR holds one config's `report.json`
as `evals report --split dev` writes it, its jobs merged across stage 1 and stage 2 if need be.
The table sets each config's success, cost and process evidence side by side and keeps the gates
apart: correctness (successes of the scored trials), infrastructure errors excluded from the
score (those never rerun to a result fail the infrastructure gate), and efficiency warnings
(a full verifier redispatch), which are not correctness failures.

Reports are compared only under one run contract: the same code commit, model, benchmark
commit, machine, split and run parameters (agent kwargs), one Skill version among the configs
that load Skills, each config once, over the same tasks
with the same trials per task. A holdout report is refused: it carries no per-task or process
evidence to choose by (ADR-0004). Significance comes from `evals compare` on the jobs.

Writes `DIR/selection.json` and `DIR/selection.md` and prints the markdown; exit 2 on a refusal.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from evals.report import distinct

# The run-contract keys (evals.report.run_contract) the configs must share. `config` differs
# by design, and `skill_version` is "none" for a config without Skills (data_agent's version
# header): the configs with Skills must share it.
SHARED_RUN_KEYS = ("git_commit", "model", "benchmark_commit", "machine", "split", "agent_kwargs")
NO_SKILLS = "none"
NONE = "—"


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser(
        "select", help="dev set: success, cost and gates of each config's report side by side"
    )
    parser.add_argument("report_dirs", type=Path, nargs="+", metavar="report_dir")
    parser.add_argument("--out", type=Path, required=True)
    parser.set_defaults(run=run)


def selection_row(config: str, report: dict[str, Any]) -> dict[str, Any]:
    """One config's line of the selection table, from its report (evals.report.write_report).
    A holdout report has no gates or process metrics, and a report written before success and
    billed counts lacks those: they are None."""
    successes, trials = report.get("n_succeeded"), report["n_trials"]
    efficiency = (report.get("gates") or {}).get("efficiency")
    infrastructure = (report.get("gates") or {}).get("infrastructure") or {}
    process = report.get("process")
    gate = (process or {}).get("gate") or {}
    timing = (process or {}).get("timing") or {}
    return {
        "config": config,
        "accuracy": report["accuracy"],
        "accuracy_stderr": report["accuracy_stderr"],
        "successes": successes,
        "trials": trials,
        "cost_usd": report["cost_usd"],
        "billed_cost_usd": report.get("billed_cost_usd"),
        "cost_per_success_usd": report["cost_usd"] / successes if successes else None,
        "infra_excluded": report.get("infra_excluded"),
        "infra_unresolved": infrastructure.get("unresolved"),
        "efficiency": None
        if efficiency is None
        else {"passed": efficiency["passed"], "warnings": len(efficiency["warnings"])},
        "process": None
        if process is None
        else {
            "full_redispatches": gate.get("full_redispatches"),
            "ended_unverified": gate.get("ended_unverified"),
            "ended_with_unresolved": gate.get("ended_with_unresolved"),
            "ended_disputed": gate.get("ended_disputed"),
            "guard_denials": gate.get("guard_denials"),
            "oversize_outputs": gate.get("oversize_outputs"),
            "verifier_wall_p50_s": (timing.get("verifier_wall_s") or {}).get("p50"),
            "trial_wall_p50_s": (timing.get("wall_s") or {}).get("p50"),
        },
    }


def efficiency_cell(efficiency: dict[str, Any] | None) -> str:
    if efficiency is None:
        return NONE
    if efficiency["passed"] is None:
        return "unknown"
    if efficiency["passed"]:
        return "passed"
    n = efficiency["warnings"]
    return f"failed ({n} warning{'' if n == 1 else 's'})"


def number(value: Any, spec: str) -> str:
    return NONE if value is None else format(value, spec)


def selection_markdown(rows: list[dict[str, Any]]) -> list[str]:
    """The selection table: success, cost, the gates and per-trial process means per config."""
    lines = [
        "Gates are kept apart: correctness is the scored trials' successes; infrastructure errors "
        "were excluded from the score (the gate fails only on an unresolved one, a dropped trial "
        "whose slot was never rerun to a result); an efficiency warning "
        "(a verifier dispatch that re-sent every model) is not a correctness failure. Process "
        "columns are per-trial means except full redispatches, a total; walls are the p50 "
        "in seconds.",
        "",
        "| config | accuracy (%) | successes | cost (USD) | billed (USD) | cost per success "
        "| infra excluded (trials, USD) | efficiency | full redispatches (total) | unverified "
        "| unresolved | disputed | guard denials | oversize outputs | verifier wall | trial wall |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        infra = row["infra_excluded"]
        p = row["process"] or {}
        lines.append(
            f"| {row['config']} | {row['accuracy']:.1f} ± {row['accuracy_stderr']:.1f} | "
            f"{number(row['successes'], 'd')}/{row['trials']} | {row['cost_usd']:.4f} | "
            f"{number(row['billed_cost_usd'], '.4f')} | "
            f"{number(row['cost_per_success_usd'], '.4f')} | "
            + (f"{infra['trials']}, {infra['cost_usd']:.4f}" if infra else NONE)
            + (f" ({row['infra_unresolved']} unresolved)" if row["infra_unresolved"] else "")
            + f" | {efficiency_cell(row['efficiency'])} | "
            f"{number(p.get('full_redispatches'), 'd')} | "
            + " | ".join(
                number(p.get(key), ".2f")
                for key in (
                    "ended_unverified",
                    "ended_with_unresolved",
                    "ended_disputed",
                    "guard_denials",
                    "oversize_outputs",
                )
            )
            + f" | {number(p.get('verifier_wall_p50_s'), '.1f')} | "
            f"{number(p.get('trial_wall_p50_s'), '.1f')} |"
        )
    return lines


def load_reports(report_dirs: list[Path]) -> list[dict[str, Any]]:
    """The reports, checked to be dev reports of one run contract, one per config, over the
    same tasks and trials per task. Errors name keys and counts, never a task."""
    if len(report_dirs) < 2:
        raise ValueError("give the dev reports of at least two configs to choose between")
    reports = [json.loads((d / "report.json").read_text()) for d in report_dirs]
    splits = sorted({str(r.get("split")) for r in reports})
    if splits != ["dev"]:
        raise ValueError(f"only dev reports can be compared to choose a config, got {splits}")
    runs = [r.get("run") or {} for r in reports]
    configs = [run.get("config") for run in runs]
    if None in configs or len(set(configs)) < len(configs):
        raise ValueError("each report must record one config, and each config appear once")
    for key in SHARED_RUN_KEYS:
        values = [run.get(key) for run in runs]
        if None in values:
            unknown = values.count(None)
            raise ValueError(
                f"{unknown} of {len(reports)} reports do not record one {key}, so the run "
                "contracts cannot be matched"
            )
        if distinct(values) > 1:
            raise ValueError(
                f"the reports' run contracts differ in {key}; compare only configs run under "
                "one contract"
            )
    skills = [run.get("skill_version") for run in runs]
    if None in skills or distinct([s for s in skills if s != NO_SKILLS]) > 1:
        raise ValueError(
            "the reports' run contracts differ in skill_version among the configs with Skills; "
            "compare only configs run under one contract"
        )
    trial_counts = [
        {task: counts["trials"] for task, counts in r["per_task"].items()} for r in reports
    ]
    if any(counts != trial_counts[0] for counts in trial_counts):
        raise ValueError("the reports cover different tasks or trials per task")
    return reports


def write_selection(report_dirs: list[Path], out: Path) -> dict[str, Any]:
    reports = load_reports(report_dirs)
    run = {key: reports[0]["run"][key] for key in SHARED_RUN_KEYS}
    table = {
        "run": run,
        "n_tasks": len(reports[0]["per_task"]),
        "configs": [selection_row(r["run"]["config"], r) for r in reports],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "selection.json").write_text(json.dumps(table, indent=2) + "\n")
    (out / "selection.md").write_text(markdown(table))
    return table


def markdown(table: dict[str, Any]) -> str:
    run = table["run"]
    lines = [
        f"# Config selection: dev, {table['n_tasks']} tasks, commit {run['git_commit']}, "
        f"model {run['model']}",
        "",
        *selection_markdown(table["configs"]),
        "",
        "Accuracy differences and their 95% intervals: `evals compare` on the configs' jobs.",
    ]
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    try:
        table = write_selection(args.report_dirs, args.out)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"select: {exc}", file=sys.stderr)
        return 2
    print(markdown(table), end="")
    print(f"-> {args.out}")
    return 0
