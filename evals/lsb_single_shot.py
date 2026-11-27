"""`lsb-single-shot`: LiveSQLBench's official single-call baseline on the dev set (ticket 138,
spec D2), as one Harbor job scored by the tasks' own verifier.

    OUT/jobs/single-shot/       the Harbor job (evals/configs/livesqlbench.single-shot.yaml, the
                                agent evals.lsb_single_shot_agent); resumed when it exists
    OUT/dropped/single-shot/    event files of trials a resume deleted, still counted as spend
    OUT/logs/single-shot.log    Harbor's output; OUT/logs/guards.log the spend guard's and scrub's
    OUT/request.json            the tasks and trials the first invocation asked for; another
                                request is refused
    OUT/reports/<UTC time>/     `evals report` of the dev tasks run (report.json, trials.json) and
                                tasks.json, the manifest it read

Only the manifest's LiveSQLBench dev tasks run (`--split dev` is the only choice; a `--task` must
be one of them, ADR-0004), and the agent refuses any other task too. The job is what `evals
compare --baseline <agent job> --candidate OUT/jobs/single-shot --manifest evals/manifest.json
--split dev --benchmark livesqlbench --trials 3` reads.

Spend (`--budget`, in USD billed, evals.spend): before Harbor starts, the spend guard stops at 80%
of it, and the model account's balance must cover what is left (evals.balance); while the job
runs, its trials are watched (evals.ablate `stop_reason`: a config error, a usage limit, a streak
of infrastructure errors) and Harbor is stopped once the billed spend under OUT reaches 80% of the
budget. The trials running then finish their one call, so the overshoot is at most a call per
concurrent trial. A rerun with the same --out resumes the job as ablate does (its errored and
interrupted trials' event files kept under OUT/dropped/ first).

Run from the repo root with the DATA_AGENT_* variables and the verifiers' write credential set
(`da-remote start` sources both):

    uv run python -m evals lsb-single-shot --tasks-dir ../da-benchmarks/livesqlbench/tasks \
        --split dev --out runs/lsb-single-shot-3 --budget 3

Exit 0 with the report; 1 when the spend guard stopped the run; 2 on anything a human must look
at (a refused task, a config error or usage limit, a failed Harbor run, trials left to rerun).
"""

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from data_agent.run_config import REPO_ROOT
from evals.ablate import (
    Stop,
    _log,  # pyright: ignore[reportPrivateUsage]
    check_account_errors,
    check_balance,
    check_config_errors,
    evals_cli,
    guard_spend,
    interrupted,
    keep_spend,
    problem,
    run_harbor,
    spent,
    stop_reason,
)
from evals.harbor import load_trials
from evals.livesqlbench import BENCHMARK as LSB_BENCHMARK
from evals.report import TRIALS_PER_TASK, benchmark_tasks, is_infra_error, verifier_failed
from evals.spend import STOP_FRACTION, STOP_PERCENT

JOB = "single-shot"
JOB_CONFIG = REPO_ROOT / "evals" / "configs" / "livesqlbench.single-shot.yaml"
MANIFEST = REPO_ROOT / "evals" / "manifest.json"
FAIL_FAST = 5


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser(
        "lsb-single-shot", help="LiveSQLBench's single-call baseline on the dev set (ticket 138)"
    )
    parser.add_argument("--tasks-dir", type=Path, required=True, help="the generated tasks")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--split", choices=("dev",), required=True, help="dev only (ADR-0004)")
    parser.add_argument(
        "--task", action="append", default=[], help="a dev task to run (repeatable; default all)"
    )
    parser.add_argument("--trials", type=int, default=TRIALS_PER_TASK)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--budget", required=True, help="stop at 80%% of this billed spend (USD)")
    parser.add_argument("--n-concurrent", type=int, help="trials at once (job config: 3)")
    parser.add_argument("--fail-fast", type=int, default=FAIL_FAST)
    parser.set_defaults(run=run)


def dev_tasks(args: argparse.Namespace) -> list[str]:
    """The tasks to run: the manifest's LiveSQLBench dev tasks, or the `--task`s among them."""
    tasks = benchmark_tasks(json.loads(args.manifest.read_text()), "dev", LSB_BENCHMARK)
    if not args.task:
        return tasks
    others = sum(1 for t in args.task if t not in tasks)
    if others:
        raise Stop(f"{others} of the --task ids are not LiveSQLBench dev tasks of {args.manifest}")
    return list(dict.fromkeys(args.task))


def check_request(out: Path, tasks: list[str], trials: int) -> None:
    """OUT holds one request: a finished job is reused, so other tasks or trials would meet a job
    that does not cover them."""
    path = out / "request.json"
    now: dict[str, Any] = {
        "agent": "lsb-single-shot",
        "benchmark": LSB_BENCHMARK,
        "split": "dev",
        "tasks": tasks,
        "trials": trials,
    }
    if not path.is_file():
        path.write_text(json.dumps(now) + "\n")
        return
    try:
        before = json.loads(path.read_text())
    except ValueError as exc:
        raise Stop(f"cannot read {path}: {exc}; use another --out") from exc
    if before != now:
        raise Stop(f"{out} was run for {before}, this call asks for {now}; use another --out")


def over_budget(out: Path, budget: Decimal) -> str | None:
    """The reason to stop Harbor once the billed spend under OUT reaches 80% of the budget."""
    try:
        billed = spent(out)
    except Stop as exc:
        return str(exc)
    limit = budget * STOP_FRACTION
    if billed >= limit:
        return (
            f"billed spend ${billed:.4f} reached {STOP_PERCENT} of the ${budget} budget "
            f"(${limit:.4f}); stop and ask a human"
        )
    return None


def harbor_args(args: argparse.Namespace, tasks: list[str]) -> list[str]:
    return [
        *("run", "--config", str(JOB_CONFIG), "--path", str(args.tasks_dir)),
        *("-o", str(args.out / "jobs"), "--job-name", JOB, "-k", str(args.trials)),
        # The agent checks the task against the same manifest (evals.lsb_single_shot_agent).
        *("--ak", f"manifest={args.manifest.resolve()}"),
        *(("-n", str(args.n_concurrent)) if args.n_concurrent else ()),
        *(a for task in tasks for a in ("-i", task)),
        # The tasks' `${LSB_PG_WRITE_PASSWORD}` makes Harbor 0.24 ask before reading the host's
        # environment, and abort without a TTY (evals.ablate `harbor_run_args`).
        "-y",
    ]


def ensure_job(args: argparse.Namespace, tasks: list[str], budget: Decimal) -> None:
    """Run the job, or resume it when it exists and is not ready to score (as evals.ablate
    `ensure_job`, without its run-config checks: this agent has no run config)."""
    job = args.out / "jobs" / JOB
    dropped: list[Path] = []
    own: list[Path] = []
    if (job / "config.json").is_file():
        trials = load_trials(job) if any(job.glob("*/result.json")) else []
        check_config_errors(JOB, job, trials)
        if problem(trials, tasks, "dev", args.trials) is None:
            return
        errored = [t for t in trials if is_infra_error(t)]
        own = sorted({t.dir for t in errored if verifier_failed(t)})
        infra = sorted({str(t.exception_type) for t in errored if t.dir not in set(own)})
        dropped = [*(t.dir for t in errored), *interrupted(job)]
        command = ["job", "resume", "-p", str(job), *(a for e in infra for a in ("-f", e))]
    else:
        command = harbor_args(args, tasks)
    guard_spend(args.out, args.budget)
    check_balance(args.out, budget)
    keep_spend(args.out, JOB, dropped)  # after the guard, which would count them twice
    for trial_dir in own:
        shutil.rmtree(trial_dir)
    log = args.out / "logs" / f"{JOB}.log"
    print(f"{JOB}: harbor {' '.join(command[:2])} -> {log}", flush=True)
    before = {p.parent.name for p in job.glob("*/result.json")}

    def stop_when() -> str | None:
        return stop_reason(JOB, job, before, args.fail_fast) or over_budget(args.out, budget)

    code, reason = run_harbor(command, log, stop_when)
    reason = reason or stop_reason(JOB, job, before, args.fail_fast)
    if reason:
        raise Stop(reason, code=1 if over_budget(args.out, budget) else 2)
    if code != 0:
        raise Stop(f"harbor exited {code} on the {JOB} job; see {log}")
    try:
        trials = load_trials(job)
    except ValueError as exc:
        raise Stop(str(exc)) from exc
    check_config_errors(JOB, job, trials)
    check_account_errors(JOB, trials)


def write_report(args: argparse.Namespace, tasks: list[str]) -> Path:
    job = args.out / "jobs" / JOB
    reason = problem(load_trials(job), tasks, "dev", args.trials)
    if reason:
        raise Stop(f"not ready to score ({reason}); rerun with the same --out to redo them")
    code, printed, err = evals_cli(["scrub", str(job)])
    _log(args.out / "logs" / "guards.log", printed, err)
    if code != 0:
        raise Stop(f"scrub could not check every file of the job, so no report; see {job}")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    reports = args.out / "reports" / stamp
    reports.mkdir(parents=True)
    manifest = reports / "tasks.json"  # the tasks run, maybe a subset of the dev set
    manifest.write_text(json.dumps({"dev": tasks}, indent=2) + "\n")
    argv = [
        *("report", str(job), "--manifest", str(manifest), "--split", "dev"),
        *("--trials", str(args.trials), "--benchmark", LSB_BENCHMARK, "--out", str(reports)),
    ]
    code, printed, err = evals_cli(argv)
    if code != 0:
        raise Stop(f"report on the job: {err.strip()}")
    print(printed.strip())
    return reports


def run(args: argparse.Namespace) -> int:
    try:
        budget = Decimal(args.budget)
    except InvalidOperation:
        budget = None
    if budget is None or not budget.is_finite() or budget <= 0:
        print(
            f"lsb-single-shot: budget must be a positive number, got {args.budget!r}",
            file=sys.stderr,
        )
        return 2
    try:
        tasks = dev_tasks(args)
        (args.out / "logs").mkdir(parents=True, exist_ok=True)
        check_request(args.out, tasks, args.trials)
        ensure_job(args, tasks, budget)
        reports = write_report(args, tasks)
    except Stop as exc:
        print(f"lsb-single-shot: {exc}", file=sys.stderr)
        return exc.code
    print(f"-> {reports}")
    return 0
