"""`ablate`: the ablation table in one command (ADR-0004, plan §7).

Each run config is one Harbor job on one split of the manifest, k trials per task: by default
the holdout, always whole; with `--split dev`, the dev set or the `--task`s chosen from it:

    OUT/jobs/<config>/        the Harbor job (resumed when it exists)
    OUT/dropped/<config>/     result.json and event files of trials a resume deleted, kept for the
                              spend guard and `evals accept`
    OUT/logs/<config>.log     Harbor's output; OUT/logs/guards.log the spend guard's and scrub's
    OUT/request.json          the model (dev: also the split and tasks) the first invocation
                              asked for; another request is refused
    OUT/records/<UTC time>.json  new on every invocation check_record lets through: start
                              time, benchmark commit, machine
    OUT/smoke/<UTC time>/<config>/  the preflight's smoke job, one dev task once
    OUT/reports/<UTC time>/   new on every invocation:
        <config>/report.json  `evals report` (holdout: aggregates only)
        ablation.json, ablation.md
        tasks.json            dev only: the tasks run, the manifest the reports read
        diverge/<config>/     dev only: `evals diverge` of the config against full

Before each job the spend guard (`evals spend` on OUT) stops the batch at 80% of --budget; a job
runs to its end once started, so the overshoot is at most one job. Before the first job that runs
comes the preflight (ADR-0008): the model account's balance must cover what is left of --budget
(evals.balance); the wheel cache is filled for the platforms of the tasks' images (evals.wheels;
with a full cache no network); each config runs the manifest's first dev task once, which must
end with a reward, no error and versioned events, and scrub must check its files (--skip-smoke
leaves this out when resuming). Any failure stops the batch before a job. While a job runs, its
new trials are watched: a usage limit or config error, or --fail-fast infrastructure errors in a
row, stop Harbor and the batch. A finished job is not rerun; an
interrupted one, or one with infrastructure errors, is resumed with `harbor job resume`, which
deletes the errored trials and runs what is missing (plan §7: infrastructure errors are rerun,
never scored). An existing job is reused only from a clean checkout at the commit and Skill version
its trials recorded, on the clean benchmark commit and the machine of OUT's last run record, and
under the --model OUT was first run with (the recorded model names need not match Harbor's -m, so
the request itself is compared), and the table needs one commit and one model
across its configs. A config error (the model's key, name or parameters) stops the batch for a
human: Harbor does not retry it and a report would score it as a real 0. So does a usage limit
(the account ran out), but its trials are infrastructure errors, rerun once the account is topped
up. Once every job is
complete, `evals scrub` cleans this table's jobs in place, `evals report` writes each config's
report and `evals compare --manifest --split <split>` gives each layer's contribution: full minus
the config without the layer, in accuracy points, with a paired bootstrap 95% interval. Per-task
holdout results are never printed or written (ADR-0004); the guards' and Harbor's own output,
which name tasks, go to logs.

Run from the repo root with the DATA_AGENT_* variables set, as for `harbor run`:

    uv run python -m evals ablate --tasks-dir <data-eng-bench>/tasks \
        --manifest evals/manifest.json --out runs/ablation --budget 300 --n-concurrent 2

plan §7's cuts: `--trials 2`, or `--config` repeated to drop configs (full is always needed).
On the dev set (ADR-0008: read the trajectories where configs differ), for example one task once:

    uv run python -m evals ablate --tasks-dir <data-eng-bench>/tasks \
        --manifest evals/manifest.json --out runs/dev-4 --budget 5 \
        --split dev --task dbt-fix-division-by-zero --trials 1 --config full

Exit 0 with the table; 1 when the spend guard stopped the batch; 2 on anything a human must look at
(a failed preflight, a config error or usage limit, a streak of infrastructure errors, a failed
Harbor run, infrastructure errors left, a scrub that could not check everything).
"""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from data_agent.agent import git_commit, skill_version
from data_agent.run_config import REPO_ROOT, load_config
from evals import balance, wheels
from evals.harbor import Trial, event_files, load_trials
from evals.report import (
    TRIALS_PER_TASK,
    check_complete,
    check_dev_list,
    is_infra_error,
    split_trials,
)
from evals.spend import SpendUnknown, file_spend
from evals.walk import walk_files

# Run order: the reference first, and full-old-skill last, the first config plan §7 drops.
CONFIGS = ("full", "baseline", "no-skill", "no-verifiers", "no-context", "full-old-skill")
REFERENCE = "full"
# What full minus each config measures.
LAYERS = {
    "baseline": "all layers",
    "no-skill": "Skill",
    "no-verifiers": "verifier subagents",
    "no-context": "context strategy",
    "full-old-skill": "failure-attribution loop (new vs old Skills)",
}
# Harbor does not retry these and a report scores them 0 like an agent failure (ticket 10's known
# limitation), though they mean the model settings are wrong: stop for a human instead.
CONFIG_ERRORS = frozenset(
    {"AgentAuthenticationError", "ModelNotFoundError", "UnsupportedModelParametersError"}
)
# A run of this many infrastructure errors in a row stops the batch (--fail-fast). Harbor already
# retried each one's transient API errors 3 times (the job config's retry), so each is a trial
# that failed 4 times; with 2-4 trials at a time, 5 in a row is more than one full round of
# concurrent trials failing: a broken setup (Docker, network, the API) rather than noise, caught
# after a few trials instead of hours.
FAIL_FAST = 5
POLL_SECONDS = 10.0
STOP_GRACE_SECONDS = 300.0
# The model account ran out (evals.report counts it as infrastructure, rerun and never scored):
# every later trial would fail the same way, so stop for a human too.
ACCOUNT_ERRORS = frozenset({"ApiUsageLimitError"})
JOB_CONFIG = REPO_ROOT / "evals" / "configs" / "data-eng-bench-duckdb.data-agent.yaml"
DEFAULT_MODEL = "the job config's default"
# The machine's human-given label for the run record (`da-remote start` sets its host alias);
# never a hostname or address, which scrub would remove (plan §7).
MACHINE_ENV = "DATA_AGENT_MACHINE"


type StopWhen = Callable[[], str | None]


class Stop(Exception):
    """The batch stops here; `code` is the exit code."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser("ablate", help="run the configs on a split, write the table")
    parser.add_argument("--tasks-dir", type=Path, required=True, help="data-eng-bench tasks/")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--budget", required=True, help="stop before a job at 80%% of this (USD)")
    parser.add_argument(
        "--config", action="append", choices=CONFIGS, help="repeatable; default all six"
    )
    parser.add_argument("--trials", type=int, default=TRIALS_PER_TASK, help="trials per task")
    parser.add_argument("--n-concurrent", type=int, help="Harbor's concurrent trials")
    parser.add_argument("--model", help="Harbor's model name (default: the job config's)")
    parser.add_argument("--split", choices=("holdout", "dev"), default="holdout")
    parser.add_argument(
        "--task", action="append", help="with --split dev, repeatable: run only these dev tasks"
    )
    parser.add_argument(
        "--fail-fast",
        type=int,
        default=FAIL_FAST,
        metavar="N",
        help=f"stop the batch after N infrastructure errors in a row (default {FAIL_FAST})",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="no preflight smoke run (resuming a batch whose smoke passed)",
    )
    parser.set_defaults(run=run)


def run_harbor(args: list[str], log: Path, stop_when: StopWhen) -> tuple[int, str | None]:
    """Harbor from this environment, its output appended to `log`; the repo root on the import
    path, where Harbor finds the agent (evals.harbor_agent). Stopped early when `stop_when`
    gives a reason (run_watched)."""
    path = os.environ.get("PYTHONPATH")
    env = os.environ | {"PYTHONPATH": os.pathsep.join(filter(None, [str(REPO_ROOT), path]))}
    harbor = Path(sys.executable).with_name("harbor")
    return run_watched([str(harbor), *args], log, env, stop_when)


def run_watched(
    command: list[str],
    log: Path,
    env: dict[str, str],
    stop_when: StopWhen,
    interval: float = POLL_SECONDS,
    grace: float = STOP_GRACE_SECONDS,
) -> tuple[int, str | None]:
    """Run `command`, its output appended to `log`, asking `stop_when` every `interval` seconds;
    on a reason, stop it and return its exit code with that reason. Harbor's own stop is SIGTERM,
    which it turns into KeyboardInterrupt to cancel its trials and clean up their containers; only
    a process that ignores it for `grace` seconds is killed. Both go to the child started here."""
    with log.open("a") as f:
        proc = subprocess.Popen(command, stdout=f, stderr=subprocess.STDOUT, env=env)
        while True:
            try:
                return proc.wait(timeout=interval), None
            except subprocess.TimeoutExpired:
                pass
            if reason := stop_when():
                proc.terminate()
                try:
                    return proc.wait(timeout=grace), reason
                except subprocess.TimeoutExpired:
                    proc.kill()
                    return proc.wait(), reason


def evals_cli(argv: list[str]) -> tuple[int, str, str]:
    """An `evals` command in this process: its exit code, stdout and stderr."""
    from evals.__main__ import main  # __main__ imports this module

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as exc:  # argparse
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def _log(path: Path, *texts: str) -> None:
    with path.open("a") as f:
        f.write("".join(texts))


def problem(trials: list[Trial], tasks: list[str], split: str, trials_per_task: int) -> str | None:
    """Why the job is not ready to score, in counts only; None when it is."""
    try:
        check_complete(split_trials(trials, {split: tasks}, split, "this"), split, trials_per_task)
    except ValueError as exc:
        return str(exc)
    return None


def check_config_errors(config: str, job: Path, trials: list[Trial]) -> None:
    found = sorted({t.exception_type for t in trials if t.exception_type in CONFIG_ERRORS})
    if found:
        count = sum(1 for t in trials if t.exception_type in CONFIG_ERRORS)
        raise Stop(
            f"{count} trial(s) of the {config} job failed with {', '.join(found)}: the model "
            "settings are wrong, not the agent. Stop and ask a human; once the settings are fixed, "
            f"remove {job} and rerun"
        )


def check_account_errors(config: str, trials: list[Trial]) -> None:
    """A trial that hit the account's usage limit stops the batch; unlike a config error the job
    is kept: those trials are infrastructure errors, rerun when the same --out runs again."""
    count = sum(1 for t in trials if t.exception_type in ACCOUNT_ERRORS)
    if count:
        raise Stop(
            f"{count} trial(s) of the {config} job failed with {', '.join(sorted(ACCOUNT_ERRORS))}"
            ": the model account's usage limit or balance ran out, not the agent. Stop and ask a "
            "human; once the account is topped up, rerun with the same --out to redo them"
        )


def finished_at(trial: Trial) -> str:
    return str(json.loads((trial.dir / "result.json").read_text()).get("finished_at") or "")


def stop_reason(config: str, job: Path, before: set[str], streak: int) -> str | None:
    """Why the running job must stop now, from the trials it finished since it started (not in
    `before`); None to let it run. Counts only, so no holdout task is named."""
    try:
        trials = [t for t in load_trials(job) if t.dir.name not in before]
        trials.sort(key=lambda t: (finished_at(t), t.dir.name))
    except (OSError, ValueError):  # no trial yet, or a result.json being written
        return None
    try:
        check_config_errors(config, job, trials)
        check_account_errors(config, trials)
    except Stop as exc:
        return str(exc)
    row: list[Trial] = []
    for trial in trials:
        row = [*row, trial] if is_infra_error(trial) else []
        if len(row) >= streak:
            kinds = ", ".join(sorted({str(t.exception_type) for t in row}))
            return (
                f"{len(row)} infrastructure errors in a row in the {config} job ({kinds}): the "
                "setup (Docker, network, the model API) is broken, not the agent. Stop and ask a "
                "human; once it is fixed, rerun with the same --out to redo them"
            )
    return None


def conditions(config: str) -> dict[str, str]:
    """What this checkout would run the config with, as its trials' version headers record it."""
    run_config = load_config(config)
    return {
        "git_commit": git_commit(),
        "skill_version": skill_version(run_config.skills_dir) if run_config.skill else "none",
    }


def check_conditions(config: str, job: Path, trials: list[Trial]) -> None:
    """A job is reused (skipped or resumed) only under the code and Skills it was made with."""
    now = conditions(config)
    if now["git_commit"].endswith("-dirty"):
        raise Stop(
            f"this checkout has uncommitted changes, so it cannot be matched to the existing "
            f"{config} job; commit them, or use another --out"
        )
    # Infrastructure errors are dropped by the resume; one may end before the agent writes events.
    kept = [t for t in trials if not is_infra_error(t)]
    unversioned = sum(1 for t in kept if not has_versions(t))
    if unversioned:
        raise Stop(
            f"{unversioned} trial(s) of the {config} job have no recorded git_commit or "
            "skill_version in their event files' version headers, so they cannot be matched to "
            "this checkout; use another --out"
        )
    found = versions(trials)
    changed = [
        f"{key} {found[key]} there, {value!r} now"
        for key, value in now.items()
        if found[key] and found[key] != [value]
    ]
    if changed:
        raise Stop(
            f"the {config} job was run under other conditions ({'; '.join(changed)}); "
            "use another --out"
        )


def check_request(out: Path, model: str | None, split: str, tasks: list[str]) -> None:
    """OUT holds one request: finished jobs are reused, so another --model would otherwise get
    the table of the model OUT was first run with, and other tasks would meet jobs that never
    cover them. A holdout request records the model only, since the holdout is always run whole."""
    path = out / "request.json"
    now: dict[str, Any] = {"model": model or DEFAULT_MODEL}
    if split != "holdout":
        now |= {"split": split, "tasks": tasks}
    if not path.is_file():
        path.write_text(json.dumps(now) + "\n")
        return
    try:
        before = json.loads(path.read_text())
    except ValueError as exc:
        raise Stop(f"cannot read {path}: {exc}; use another --out") from exc
    if before != now:
        raise Stop(f"{out} was run for {before}, this call asks for {now}; use another --out")


def guard_spend(out: Path, budget: str) -> None:
    """Spend under OUT: the jobs plus the event files of trials a resume dropped."""
    code, printed, err = evals_cli(["spend", str(out), "--budget", budget])
    _log(out / "logs" / "guards.log", printed, err)
    if code == 1:
        raise Stop(err.strip(), code=1)
    if code != 0:
        raise Stop(f"the spend guard cannot total the spend; see {out / 'logs' / 'guards.log'}")
    print(printed.strip(), flush=True)


def keep_spend(out: Path, config: str, trials: list[Trial]) -> None:
    """Copy the event files of the trials a resume will delete to OUT/dropped/, where the spend
    guard still counts what they cost, and their result.json, which marks that the round had an
    infrastructure error even for a trial that broke before writing events (`evals accept`)."""
    for trial in trials:
        kept_dir = out / "dropped" / config / trial.dir.name
        kept_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(trial.dir / "result.json", kept_dir / "result.json")
        for events in event_files(trial):
            shutil.copyfile(events, kept_dir / events.name)


def prefetch_wheels(tasks_dir: Path, tasks: list[str]) -> None:
    """Fill the wheel cache for the platforms of the tasks' containers (evals.wheels), so that no
    trial downloads the agent's dependencies within Harbor's agent setup limit."""
    from evals.harbor_agent import build_artifacts  # imports Harbor

    try:
        images = {wheels.task_image(tasks_dir / task) for task in tasks}
        _, requirements = build_artifacts()
        lines = wheels.fill(images, requirements, REPO_ROOT / "uv.lock")
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise Stop(f"cannot fill the wheel cache for the tasks' containers: {exc}") from exc
    for line in lines:
        print(f"wheels: {line}", flush=True)


def harbor_run_args(
    args: argparse.Namespace, jobs: Path, config: str, tasks: list[str], trials: int
) -> list[str]:
    """`harbor run` of the config on `tasks`, `trials` each, as job `jobs/<config>`."""
    return [
        *("run", "--config", str(JOB_CONFIG), "--path", str(args.tasks_dir)),
        *("-o", str(jobs), "--job-name", config, "-k", str(trials)),
        *("--ak", f"run_config={config}"),
        *(("-m", args.model) if args.model else ()),
        *(a for task in tasks for a in ("-i", task)),
    ]


def spent(out: Path) -> Decimal:
    """What OUT's event files cost so far, as the spend guard totals it."""
    found, unchecked = walk_files(out)
    if unchecked:
        raise Stop(f"cannot see every event file under {out}: {'; '.join(unchecked)}")
    try:
        return sum((file_spend(p) for p in found if p.suffix == ".jsonl"), Decimal(0))
    except (SpendUnknown, OSError, UnicodeDecodeError) as exc:
        raise Stop(f"cannot total the spend under {out}: {exc}") from exc


def check_balance(out: Path, budget: Decimal) -> None:
    """The model account can pay for what is left of the budget (ADR-0008 preflight)."""
    left = budget - spent(out)
    try:
        found = balance.read()
    except balance.BalanceUnknown as exc:
        raise Stop(f"cannot read the model account's balance ({exc}); no job starts") from exc
    if not found.available:
        raise Stop(
            f"the model account's balance ({found.describe()}) is not available for API calls; "
            "top it up, then rerun"
        )
    if found.usd < left:
        raise Stop(
            f"the model account's balance ({found.describe()}) is below the ${left:.2f} left of "
            "--budget; top it up or lower --budget, then rerun"
        )
    print(f"balance: {found.describe()}, covers the ${left:.2f} left of --budget", flush=True)


def smoke(args: argparse.Namespace, configs: list[str], task: str, stamp: str) -> None:
    """Each config on one dev task, once, then scrub on its files: a broken config, key, image or
    scrub shows here, before the jobs spend hours (ADR-0008 preflight). Dev, not holdout, so the
    output may name the task."""
    jobs = args.out / "smoke" / stamp
    for config in configs:
        log = args.out / "logs" / f"smoke-{config}.log"
        print(f"smoke: {config} on {task} -> {log}", flush=True)
        code, _ = run_harbor(harbor_run_args(args, jobs, config, [task], 1), log, lambda: None)
        if code != 0:
            raise Stop(f"smoke run of {config}: harbor exited {code}, so no job starts; see {log}")
        try:
            (trial,) = load_trials(jobs / config)
        except ValueError as exc:
            raise Stop(f"smoke run of {config}: not one trial result ({exc}); see {log}") from exc
        check_account_errors(f"{config} smoke", [trial])
        failure = trial.exception_type or ("no reward" if trial.reward is None else None)
        if not failure and not has_versions(trial):
            failure = "no version header in its event files"
        if failure:
            raise Stop(
                f"smoke run of {config} on {task} failed ({failure}), so no job starts; "
                f"see {log} and {trial.dir}"
            )
        code, printed, err = evals_cli(["scrub", str(jobs / config)])
        _log(args.out / "logs" / "guards.log", printed, err)
        if code != 0:
            raise Stop(
                f"scrub could not check every file of the {config} smoke run, so no job starts; "
                f"see {args.out / 'logs' / 'guards.log'}"
            )
        print(f"smoke: {config} ok (reward {trial.reward:g}, ${trial.cost_usd:.4f})", flush=True)


def benchmark_commit(tasks_dir: Path) -> str | None:
    """HEAD of the clone holding the tasks, `-dirty` with tracked changes; None outside git."""
    try:
        head = subprocess.run(
            ["git", "-C", str(tasks_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        status = subprocess.run(
            ["git", "-C", str(tasks_dir), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if head.returncode != 0 or status.returncode != 0:
        return None
    return head.stdout.strip() + ("-dirty" if status.stdout.strip() else "")


def make_record(args: argparse.Namespace, configs: list[str]) -> dict[str, Any]:
    """What ADR-0008 compares before reusing results, beyond the version headers: the benchmark's
    commit and the machine, under a human-given label."""
    return {
        "started_at": datetime.now(UTC).isoformat(),
        "benchmark_commit": benchmark_commit(args.tasks_dir),
        "machine": os.environ.get(MACHINE_ENV) or None,
        "git_commit": git_commit(),
        "model": args.model or DEFAULT_MODEL,
        "configs": configs,
        "smoke": not args.skip_smoke,
    }


def check_record(out: Path, tasks_dir: Path, record: dict[str, Any]) -> None:
    """Jobs under OUT are reused (skipped or resumed) only on the benchmark commit and machine of
    OUT's last run record: another checkout of the tasks or another machine would mix two versions
    in one job. A call this check refuses writes no record, so the last one is the jobs' own."""
    if not any((out / "jobs").glob("*/config.json")):
        return
    paths = sorted((out / "records").glob("*.json"))
    if not paths:
        raise Stop(
            f"{out} has jobs but no run record, so they cannot be matched to this benchmark "
            "checkout and machine; use another --out"
        )
    try:
        before = json.loads(paths[-1].read_text())
    except (OSError, ValueError) as exc:
        raise Stop(f"cannot read {paths[-1]}: {exc}; use another --out") from exc
    commit = record["benchmark_commit"]
    if commit is None or commit.endswith("-dirty"):
        raise Stop(
            f"the benchmark checkout of {tasks_dir} has uncommitted changes or is no git "
            f"clone, so it cannot be matched to the jobs under {out}; use a clean clone, "
            "or another --out"
        )
    if record["machine"] is None:
        raise Stop(
            f"{MACHINE_ENV} is not set, so this machine cannot be matched to the jobs under "
            f"{out}; set it to the label of the machine they ran on, or use another --out"
        )
    changed = [
        f"{label} {before.get(key)!r} there, {record[key]!r} now"
        for key, label in (("benchmark_commit", "benchmark commit"), ("machine", "machine"))
        if before.get(key) != record[key]
    ]
    if changed:
        raise Stop(
            f"the jobs under {out} were run under other conditions ({'; '.join(changed)}, "
            f"see {paths[-1]}); use another --out"
        )


def write_record(out: Path, record: dict[str, Any], stamp: str) -> Path:
    """OUT/records/<stamp>.json, one per call that `check_record` lets through."""
    path = out / "records" / f"{stamp}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n")
    if record["machine"] is None:
        print(f"ablate: {MACHINE_ENV} is not set; the run record has no machine", file=sys.stderr)
    return path


def ensure_job(
    config: str, args: argparse.Namespace, tasks: list[str], prepare: Callable[[], None]
) -> None:
    """Run the config's job, or resume it when it exists and is not ready to score; `prepare`
    runs before Harbor does."""
    jobs, logs = args.out / "jobs", args.out / "logs"
    job = jobs / config
    dropped: list[Trial] = []
    if (job / "config.json").is_file():
        trials = load_trials(job) if any(job.glob("*/result.json")) else []
        check_config_errors(config, job, trials)
        check_conditions(config, job, trials)
        if problem(trials, tasks, args.split, args.trials) is None:
            return
        dropped = [t for t in trials if is_infra_error(t)]
        infra = sorted({str(t.exception_type) for t in dropped})
        harbor_args = ["job", "resume", "-p", str(job), *(a for e in infra for a in ("-f", e))]
    else:
        harbor_args = [
            *harbor_run_args(args, jobs, config, tasks, args.trials),
            *(("-n", str(args.n_concurrent)) if args.n_concurrent else ()),
        ]
    guard_spend(args.out, args.budget)
    prepare()
    keep_spend(args.out, config, dropped)  # after the guard, which would count them twice
    log = logs / f"{config}.log"
    print(f"{config}: harbor {' '.join(harbor_args[:2])} -> {log}", flush=True)
    before = {p.parent.name for p in job.glob("*/result.json")}
    code, reason = run_harbor(
        harbor_args, log, lambda: stop_reason(config, job, before, args.fail_fast)
    )
    # The watcher looks every few seconds; a job that ended in between is judged here.
    reason = reason or stop_reason(config, job, before, args.fail_fast)
    if reason:
        raise Stop(reason)
    if code != 0:
        raise Stop(f"harbor exited {code} on the {config} job; see {log}")
    try:
        trials = load_trials(job)
    except ValueError as exc:
        raise Stop(str(exc)) from exc
    check_config_errors(config, job, trials)
    check_account_errors(config, trials)


def header(events: Path) -> dict[str, Any]:
    with events.open() as f:
        return json.loads(f.readline() or "{}")


def has_versions(trial: Trial) -> bool:
    """The trial has event files, and each one's version header records the code and Skills."""
    files = event_files(trial)
    return bool(files) and all(
        header(events).get(key) for events in files for key in ("git_commit", "skill_version")
    )


def versions(trials: list[Trial]) -> dict[str, list[str]]:
    """The code, Skill version and model the trials ran with, from their event files' headers."""
    seen: dict[str, set[str]] = {"git_commit": set(), "skill_version": set(), "model": set()}
    for trial in trials:
        for events in event_files(trial):
            fields = header(events)
            for key, values in seen.items():
                if fields.get(key):
                    values.add(str(fields[key]))
    return {key: sorted(values) for key, values in seen.items()}


def write_reports(args: argparse.Namespace, configs: list[str], tasks: list[str]) -> Path:
    jobs = args.out / "jobs"
    ran_with = {config: versions(load_trials(jobs / config)) for config in configs}
    for key in ("git_commit", "model"):
        values = sorted({value for found in ran_with.values() for value in found[key]})
        if len(values) > 1:
            raise Stop(f"the configs ran with different values of {key} ({values}); no table")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    reports = args.out / "reports" / stamp
    reports.mkdir(parents=True)  # never an earlier report's directory
    manifest = args.manifest
    if args.split == "dev":  # the reports and comparisons cover the tasks run, maybe a subset
        manifest = reports / "tasks.json"
        manifest.write_text(json.dumps({"dev": tasks}, indent=2) + "\n")
    common = ["--manifest", str(manifest), "--split", args.split, "--trials", str(args.trials)]
    rows: list[dict[str, Any]] = []
    for config in configs:
        argv = ["report", str(jobs / config), *common, "--out", str(reports / config)]
        code, _, err = evals_cli(argv)
        if code != 0:
            raise Stop(f"report on the {config} job: {err.strip()}")
        report = json.loads((reports / config / "report.json").read_text())
        rows.append({"config": config, **report, **ran_with[config]})
    contributions: list[dict[str, Any]] = []
    for config in configs:
        if config == REFERENCE:
            continue
        pair = ["--baseline", str(jobs / config), "--candidate", str(jobs / REFERENCE)]
        code, out, err = evals_cli(["compare", *pair, *common, "--json"])
        if code != 0:
            raise Stop(f"compare {REFERENCE} with {config}: {err.strip()}")
        contributions.append({"layer": LAYERS[config], "config": config, **json.loads(out)})
        if args.split == "dev":
            argv = ["diverge", *pair, "--manifest", str(manifest)]
            code, _, err = evals_cli([*argv, "--out", str(reports / "diverge" / config)])
            if code != 0:
                raise Stop(f"diverge of {config} against {REFERENCE}: {err.strip()}")
    table = {
        "split": args.split,
        "n_tasks": len(tasks),
        "trials_per_task": args.trials,
        "reference": REFERENCE,
        "configs": rows,
        "contributions": contributions,
    }
    (reports / "ablation.json").write_text(json.dumps(table, indent=2) + "\n")
    (reports / "ablation.md").write_text(markdown(table))
    return reports


def markdown(table: dict[str, Any]) -> str:
    rows: list[dict[str, Any]] = table["configs"]
    passes = sorted({key for row in rows for key in row if key.startswith("pass_at_")})
    lines = [
        f"# Ablation: {table['split']}, {table['n_tasks']} tasks x {table['trials_per_task']} "
        "trials",
        "",
        "| config | accuracy (%) | ± stderr | "
        + "".join(f"pass@{key.removeprefix('pass_at_')} | " for key in passes)
        + "trials | cost (USD) | input tokens (uncached / cached) | output tokens |",
        "|---|---:|---:|" + "---:|" * len(passes) + "---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['config']} | {row['accuracy']:.1f} | {row['accuracy_stderr']:.1f} | "
            + "".join(f"{row[key]:.3f} | " for key in passes)
            + f"{row['n_trials']} | {row['cost_usd']:.4f} | "
            f"{row['uncached_input_tokens']:,} / {row['cached_input_tokens']:,} | "
            f"{row['output_tokens']:,} |"
        )
    lines += [
        "",
        f"Contribution of each layer: {table['reference']} minus the config without it, in "
        "accuracy points, paired bootstrap 95% interval over tasks.",
        "",
        "| layer | config | contribution (points) | 95% interval | significant |",
        "|---|---|---:|---|---|",
    ]
    for c in table["contributions"]:
        lines.append(
            f"| {c['layer']} | {c['config']} | {c['diff']:+.1f} | "
            f"[{c['ci_low']:+.1f}, {c['ci_high']:+.1f}] | {'yes' if c['significant'] else 'no'} |"
        )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    try:
        budget = Decimal(args.budget)
    except InvalidOperation:
        budget = None
    if budget is None or not budget.is_finite() or budget <= 0:
        print(f"ablate: budget must be a positive number, got {args.budget!r}", file=sys.stderr)
        return 2
    if args.fail_fast < 1:
        print(f"ablate: --fail-fast must be at least 1, got {args.fail_fast}", file=sys.stderr)
        return 2
    configs = [c for c in CONFIGS if c in (args.config or CONFIGS)]
    if REFERENCE not in configs:
        print(f"ablate: --config must include {REFERENCE}, the reference", file=sys.stderr)
        return 2
    try:
        manifest = json.loads(args.manifest.read_text())
        tasks: list[str] = manifest[args.split]
        dev: list[str] = manifest.get("dev") or []
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        print(
            f"ablate: cannot read the {args.split} tasks from {args.manifest}: {exc}",
            file=sys.stderr,
        )
        return 2
    if args.split == "dev":
        # A dev run's reports name their tasks (ADR-0004 allows that for dev only): no task the
        # given manifest or the repo's (on the server, the real one) holds out. evals.report and
        # evals.diverge check the same way; here it comes before any spend.
        try:
            check_dev_list(manifest, args.manifest)
        except (ValueError, KeyError, TypeError) as exc:
            print(f"ablate: {exc}", file=sys.stderr)
            return 2
    if args.task:
        if args.split != "dev":
            print("ablate: --task needs --split dev; the holdout is run whole", file=sys.stderr)
            return 2
        unknown = sorted(set(args.task) - set(tasks))
        if unknown:
            print(f"ablate: not in the manifest's dev list: {', '.join(unknown)}", file=sys.stderr)
            return 2
        tasks = [task for task in tasks if task in args.task]
    if not tasks:
        print(f"ablate: the manifest's {args.split} split is empty", file=sys.stderr)
        return 2
    if not dev and not args.skip_smoke:
        print("ablate: the manifest has no dev task to smoke-test on", file=sys.stderr)
        return 2
    (args.out / "jobs").mkdir(parents=True, exist_ok=True)
    (args.out / "logs").mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    try:
        record = make_record(args, configs)
        check_record(args.out, args.tasks_dir, record)
        record_path = write_record(args.out, record, stamp)
        check_request(args.out, args.model, args.split, tasks)
        print(f"record: {record_path}", flush=True)
        prepared: list[bool] = []

        def prepare() -> None:  # once, and only when a job is to run: the preflight
            if prepared:
                return
            check_balance(args.out, budget)
            smoke_tasks = [] if args.skip_smoke else dev[:1]
            prefetch_wheels(args.tasks_dir, [*tasks, *smoke_tasks])
            prepared.append(True)
            if smoke_tasks:
                smoke(args, configs, smoke_tasks[0], stamp)

        for config in configs:
            ensure_job(config, args, tasks, prepare)
        left = {}
        for config in configs:
            trials = load_trials(args.out / "jobs" / config)
            reason = problem(trials, tasks, args.split, args.trials)
            if reason:
                left[config] = reason
        if left:
            details = "; ".join(f"{config}: {reason}" for config, reason in left.items())
            raise Stop(f"not ready to score ({details}). rerun with the same --out to redo them")
        # Only this table's jobs, all finished: scrub redacts the paths in a job's Harbor config,
        # so another config's unfinished job would no longer resume.
        for config in configs:
            code, out, err = evals_cli(["scrub", str(args.out / "jobs" / config)])
            _log(args.out / "logs" / "guards.log", out, err)
            if code != 0:
                raise Stop(
                    f"scrub could not check every file of the {config} job, so no report; "
                    f"see {args.out / 'logs' / 'guards.log'}"
                )
        reports = write_reports(args, configs, tasks)
    except Stop as exc:
        print(f"ablate: {exc}", file=sys.stderr)
        return exc.code
    print((reports / "ablation.md").read_text(), end="")
    print(f"-> {reports}")
    return 0
