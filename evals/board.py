"""`board`: the leaderboard run, one config on the registry's data-eng-bench (leaderboard-run 01).

Named for the leaderboard it feeds. `ablate` runs local task directories (`--path`), and its
trials record `"source": "tasks"`, which the leaderboard's CI filters out; the trials of this
command come from the registry's `DATASET@DATASET_REF` (Harbor's `-d`), so their `config.task`
carries the registry's name, digest and source, as data-eng-bench's leaderboard
(`leaderboard/core/hub.py`, `ci/static_analysis.py`) requires. The job config is ablate's
(`evals/configs/data-eng-bench-duckdb.data-agent.yaml`: no timeout multiplier, no timeout or
resource override, `DB_TYPE=duckdb` through `environment.env`), written to
`OUT/job-config.json` with its agent named and modelled as the leaderboard's CI matches it
(`job_config`).

The run can be split into batches with `--task` (Harbor's `-i`); each batch is one Harbor job,
`OUT/jobs/<batch>/`, named by its task set (`all`, or `batch-<hash>`, so no job name names a
task), resumed when the same tasks are asked for again. A batch whose tasks another batch of OUT
already has is refused, as is any batch run under other conditions: OUT holds one request (the
config, model and trials per task, `OUT/request.json`), one benchmark and machine (the run records
of `ablate`), and its trials one git commit and Skill version (their event files' version
headers). Spend guard, resume (the spend of dropped and interrupted trials kept once, under
`OUT/dropped/<batch>/`), `--fail-fast`, config and account errors, and the preflight (base image,
balance, readiness, wheel cache, smoke on the manifest's first dev task) behave as in `ablate`.

Before the first Harbor run the base image the registry's Dockerfiles start from
(`FROM dbt-bench-base`, all 103) must be on this machine; when it is not, it is pulled from GHCR
(`BASE_IMAGE`) and tagged `dbt-bench-base`. Task files are never changed.

Each call ends, once its batch is complete, with a report over every batch of OUT merged
(`OUT/reports/<UTC time>/board.json`, `board.md`): the leaderboard's accuracy ± stderr and pass@k
(`evals.metrics`, data-eng-bench's algorithm) over the 103 tasks, which tasks or trials are
still missing, accuracy by difficulty, cost, tokens and wall clock, infrastructure exclusions,
and how the registry's v1.0 differs from data-eng-bench a3278ad (`DIFFERENCES`) with the accuracy
without `ZERO_TASK`. The registry holds the 30 holdout tasks of `evals/manifest.json`: the
per-task table lists the other tasks only; the holdout gets aggregates (ADR-0004).

    uv run python -m evals board --config no-verifiers --out runs/board --budget 20

Exit 0 with the report; 1 when the spend guard stopped the batch; 2 on anything a human must look
at (as `ablate`).
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from data_agent.agent import git_commit
from data_agent.run_config import REPO_ROOT
from evals import ablate, readiness
from evals import report as report_module
from evals.ablate import Stop
from evals.harbor import Trial, load_trials, task_id
from evals.metrics import is_success
from evals.report import TRIALS_PER_TASK, is_infra_error, score, summary

# The dataset data-eng-bench's leaderboard tracks (leaderboard/src/leaderboard/core/hub.py
# DATASET, DATASET_REF: snowflake-labs/data-eng-bench@v1.0).
DATASET = "snowflake-labs/data-eng-bench"
DATASET_REF = "sha256:de31878dba7e5a9aad85dc8276820b0a20ab593aed46ff93dfbbbdb6007ec995"
DATASET_VERSION = f"{DATASET}@{DATASET_REF}"
# The registry's tasks, each with its digest and difficulty (read from the registry 2026-11-09).
REGISTRY = REPO_ROOT / "evals" / "registry.json"
MANIFEST = report_module.REPO_MANIFEST
# Every registry Dockerfile starts `FROM dbt-bench-base`, an image no registry serves; it is
# data-eng-bench's published base image under its local build name (data-eng-bench base-image/).
BASE_TAG = "dbt-bench-base"
BASE_IMAGE = "ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0"
DOCKER_TIMEOUT_S = 1800.0
# v1.0's tests/test_outputs.py of this task returns `final_score`, which it never defines
# (a3278ad returns `final`): every trial scores 0.
ZERO_TASK = "dbt-cart-abandonment-recovery"
DIFFERENCES = (
    f"`{ZERO_TASK}` scores 0 on every trial of v1.0: its tests/test_outputs.py writes "
    '`{"reward": final_score}` and `final_score` is undefined (a3278ad writes `final`), so 102 '
    "of 103 is the best possible score.",
    "97 task instructions of v1.0 keep the hint that the SQL must work on both DuckDB and "
    "Snowflake (a3278ad removed it).",
    "v1.0's Dockerfiles start `FROM dbt-bench-base` (a3278ad: `FROM "
    f"{BASE_IMAGE}`), tagged locally from that image; its task.toml has no "
    "[environment.healthcheck] and its tests and solutions name another Snowflake host, which "
    "DuckDB runs do not use.",
    "The leaderboard accepts v1.0's digests only; every submission runs the same tasks.",
)
JOBS_ALL = "all"
# The job config board runs Harbor with (`job_config`), under OUT.
JOB_CONFIG_FILE = "job-config.json"


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser(
        "board", help="run one config on the registry's data-eng-bench, for the leaderboard"
    )
    parser.add_argument("--config", required=True, choices=ablate.CONFIGS, help="the run config")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--budget", required=True, help="stop before a job at 80%% of this (USD)")
    parser.add_argument("--trials", type=int, default=TRIALS_PER_TASK, help="trials per task")
    parser.add_argument("--n-concurrent", type=int, help="Harbor's concurrent trials")
    parser.add_argument("--model", help="Harbor's model name (default: the job config's)")
    parser.add_argument(
        "--task", action="append", help="repeatable: run only these registry tasks (a batch)"
    )
    parser.add_argument(
        "--fail-fast",
        type=int,
        default=ablate.FAIL_FAST,
        metavar="N",
        help=f"stop after N infrastructure errors in a row (default {ablate.FAIL_FAST})",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="no preflight smoke run (resuming a batch whose smoke passed)",
    )
    parser.set_defaults(run=run)


def read_registry(path: Path) -> dict[str, dict[str, str]]:
    """{task: {"ref", "difficulty"}} of the registry list, which must be of DATASET@DATASET_REF."""
    registry = json.loads(path.read_text())
    if registry.get("dataset") != DATASET or registry.get("ref") != DATASET_REF:
        raise ValueError(f"{path} lists another dataset than {DATASET_VERSION}")
    tasks: dict[str, dict[str, str]] = registry["tasks"]
    if not tasks:
        raise ValueError(f"{path} lists no task")
    return tasks


def job_config(model: str | None) -> dict[str, Any]:
    """ablate's job config, its agent as the leaderboard's CI finds it in a job's config.json.

    `ci/static_analysis.py` `matching_agent` wants an entry of `agents` whose `name` is the trials'
    agent name (`DataAgent.name()`), whose `model_name` is their `provider/model`
    (`core/hub.py` `trial_model`, which Harbor records from this same `model_name`) and whose
    `kwargs.reasoning_effort` is the submission's (`flows/filter.py` reads it from this entry). A
    `name` that is no Harbor agent leaves the agent loaded by `import_path` (Harbor 0.24.0
    `agents/factory.py` 242-257). `--model` goes here, not to `-m`: with `--config` and no
    `--agent`, Harbor ignores `-m` (`cli/jobs.py` 1528-1568). No `reasoning_effort`: data-agent
    sets no effort for its model, so the submission's is null ("none")."""
    import yaml  # a Harbor dependency

    from evals.harbor_agent import DataAgent  # imports Harbor

    config: dict[str, Any] = yaml.safe_load(ablate.JOB_CONFIG.read_text())
    (agent,) = config["agents"]
    agent["name"] = DataAgent.name()
    if model:
        agent["model_name"] = model
    return config


def write_job_config(out: Path, model: str | None) -> None:
    """`OUT/job-config.json`: the `--config` of every `harbor run` of board (`harbor_run_args`)."""
    (out / JOB_CONFIG_FILE).write_text(json.dumps(job_config(model), indent=2) + "\n")


def harbor_run_args(
    args: argparse.Namespace, jobs: Path, job_name: str, tasks: list[str], trials: int
) -> list[str]:
    """`harbor run` of `--config` on `tasks` of the registry's dataset, `trials` each, as job
    `jobs/<job_name>`. Harbor matches `-i` against the registry's `<org>/<task>` names."""
    org = DATASET.split("/")[0]
    return [
        *("run", "--config", str(args.out / JOB_CONFIG_FILE), "-d", DATASET_VERSION),
        *("-o", str(jobs), "--job-name", job_name, "-k", str(trials)),
        *("--ak", f"run_config={args.config}"),
        *(a for task in tasks for a in ("-i", f"{org}/{task}")),
    ]


def batch_name(tasks: list[str], registry: dict[str, Any]) -> str:
    """The job of a task set: `all` for the whole registry, else a hash of the sorted tasks."""
    if set(tasks) == set(registry):
        return JOBS_ALL
    digest = hashlib.sha256("\n".join(sorted(tasks)).encode()).hexdigest()[:10]
    return f"batch-{digest}"


def job_tasks(job: Path, registry: dict[str, Any]) -> set[str]:
    """The tasks a Harbor job was asked to run, from its config.json (`datasets[*].task_names`,
    the `-i` patterns); a dataset without a task list means the whole registry."""
    try:
        datasets = json.loads((job / "config.json").read_text())["datasets"]
        names: set[str] = set()
        for dataset in datasets:
            if not dataset.get("task_names"):
                return set(registry)
            names |= {task_id(name) for name in dataset["task_names"]}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise Stop(
            f"cannot read the tasks of the job {job.name}: {exc}; use another --out"
        ) from exc
    return names


def check_batches(out: Path, batch: str, tasks: list[str], registry: dict[str, Any]) -> list[Path]:
    """The other batches' jobs under OUT; refuses a batch that shares a task with one of them
    (its trials would be counted twice). Counts only, so no holdout task is named."""
    others = sorted(p for p in (out / "jobs").iterdir() if p.is_dir() and p.name != batch)
    shared = sum(len(set(tasks) & job_tasks(job, registry)) for job in others)
    if shared:
        raise Stop(
            f"{shared} of the {len(tasks)} tasks asked for are in another batch of {out}; "
            "ask for the tasks no batch has, or the same tasks as one batch to resume it"
        )
    return others


def check_mixing(config: str, jobs: list[Path]) -> None:
    """Every batch of OUT ran under this checkout's code and Skills (ablate.check_conditions)."""
    for job in jobs:
        trials = load_trials(job) if any(job.glob("*/result.json")) else []
        if trials:
            ablate.check_conditions(config, job, trials)


def check_request(out: Path, args: argparse.Namespace) -> None:
    """OUT holds one request, whatever tasks each batch asks for: the dataset, config, model
    and trials per task."""
    path = out / "request.json"
    now: dict[str, Any] = {
        "dataset": DATASET_VERSION,
        "config": args.config,
        "model": args.model or ablate.DEFAULT_MODEL,
        "trials": args.trials,
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


def make_record(args: argparse.Namespace) -> dict[str, Any]:
    """ablate's run record, the benchmark being the registry's dataset version, plus the Skill
    version the config runs with (`check_versions`)."""
    return {
        "started_at": datetime.now(UTC).isoformat(),
        "benchmark_commit": DATASET_VERSION,
        "machine": os.environ.get(ablate.MACHINE_ENV) or None,
        "git_commit": git_commit(),
        "skill_version": ablate.conditions(args.config)["skill_version"],
        "model": args.model or ablate.DEFAULT_MODEL,
        "configs": [args.config],
        "smoke": not args.skip_smoke,
    }


def check_versions(out: Path, record: dict[str, Any]) -> None:
    """Jobs under OUT are reused or resumed only from a clean checkout at the git commit and Skill
    version of OUT's last run record: a batch stopped before its first result.json has no trial
    whose version header `check_mixing` could compare (ablate.check_record leaves these out)."""
    if not any((out / "jobs").glob("*/config.json")):
        return
    if str(record["git_commit"]).endswith("-dirty"):
        raise Stop(
            f"this checkout has uncommitted changes, so it cannot be matched to the jobs under "
            f"{out}; commit them, or use another --out"
        )
    paths = sorted((out / "records").glob("*.json"))
    try:
        before = json.loads(paths[-1].read_text()) if paths else {}
    except (OSError, ValueError) as exc:
        raise Stop(f"cannot read {paths[-1]}: {exc}; use another --out") from exc
    changed = [
        f"{key} {before.get(key)!r} there, {record[key]!r} now"
        for key in ("git_commit", "skill_version")
        if before.get(key) != record[key]
    ]
    if changed:
        raise Stop(
            f"the jobs under {out} were run under other conditions ({'; '.join(changed)}); "
            "use another --out"
        )


def docker(*args: str) -> tuple[int, str]:
    """A docker command: its exit code and output."""
    try:
        proc = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=DOCKER_TIMEOUT_S
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def ensure_base_image() -> None:
    """`dbt-bench-base` on this machine, pulled from GHCR and tagged when it is missing."""
    code, _ = docker("image", "inspect", BASE_TAG)
    if code == 0:
        print(f"base image: {BASE_TAG} present", flush=True)
        return
    for step in (("pull", BASE_IMAGE), ("tag", BASE_IMAGE, BASE_TAG)):
        code, output = docker(*step)
        if code != 0:
            tail = output.splitlines()[-1] if output else ""
            raise Stop(f"docker {' '.join(step)} failed ({tail}); no job starts")
    print(f"base image: pulled {BASE_IMAGE}, tagged {BASE_TAG}", flush=True)


def ensure_batch(
    args: argparse.Namespace, batch: str, tasks: list[str], prepare: Callable[[], None]
) -> None:
    """Run the batch's job, or resume it when it exists and is not ready to score (as
    ablate.ensure_job)."""
    jobs, logs = args.out / "jobs", args.out / "logs"
    job = jobs / batch
    dropped: list[Path] = []
    if (job / "config.json").is_file():
        trials = load_trials(job) if any(job.glob("*/result.json")) else []
        ablate.check_config_errors(batch, job, trials)
        if ablate.problem(trials, tasks, "batch", args.trials) is None:
            return
        errored = [t for t in trials if is_infra_error(t)]
        infra = sorted({str(t.exception_type) for t in errored})
        dropped = [*(t.dir for t in errored), *ablate.interrupted(job)]
        harbor_args = ["job", "resume", "-p", str(job), *(a for e in infra for a in ("-f", e))]
    else:
        harbor_args = [
            *harbor_run_args(args, jobs, batch, tasks, args.trials),
            *(("-n", str(args.n_concurrent)) if args.n_concurrent else ()),
        ]
    ablate.guard_spend(args.out, args.budget)
    prepare()
    ablate.keep_spend(args.out, batch, dropped)
    log = logs / f"{batch}.log"
    print(f"{batch}: harbor {' '.join(harbor_args[:2])} -> {log}", flush=True)
    before = {p.parent.name for p in job.glob("*/result.json")}
    code, reason = ablate.run_harbor(
        harbor_args, log, lambda: ablate.stop_reason(batch, job, before, args.fail_fast)
    )
    reason = reason or ablate.stop_reason(batch, job, before, args.fail_fast)
    if reason:
        raise Stop(reason)
    if code != 0:
        raise Stop(f"harbor exited {code} on the {batch} job; see {log}")
    try:
        trials = load_trials(job)
    except ValueError as exc:
        raise Stop(str(exc)) from exc
    ablate.check_config_errors(batch, job, trials)
    ablate.check_account_errors(batch, trials)


def trial_times(trial: Trial) -> tuple[datetime, datetime] | None:
    """When the trial started and finished, from its result.json; None when unrecorded."""
    try:
        result = json.loads((trial.dir / "result.json").read_text())
        return (
            datetime.fromisoformat(result["started_at"]),
            datetime.fromisoformat(result["finished_at"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def wall_clock(trials: list[Trial]) -> dict[str, Any]:
    """The trials' own durations, and each batch's span from its first start to its last end."""
    spans: dict[str, list[tuple[datetime, datetime]]] = {}
    for trial in trials:
        times = trial_times(trial)
        if times is not None:
            spans.setdefault(trial.dir.parent.name, []).append(times)
    durations = [(end - start).total_seconds() for found in spans.values() for start, end in found]
    batches = {
        job: (max(e for _, e in found) - min(s for s, _ in found)).total_seconds()
        for job, found in sorted(spans.items())
    }
    return {
        "trials_timed": len(durations),
        "trial_seconds_total": sum(durations),
        "trial_seconds_mean": sum(durations) / len(durations) if durations else None,
        "batch_seconds": batches,
        "batch_seconds_total": sum(batches.values()),
    }


def aggregate(trials: list[Trial]) -> dict[str, Any]:
    """Counts, accuracy and cost of a set of trials, naming none."""
    succeeded = sum(1 for t in trials if is_success(t.reward))
    return {
        "n_tasks": len({t.task for t in trials}),
        "n_trials": len(trials),
        "n_succeeded": succeeded,
        "accuracy": 100.0 * succeeded / len(trials) if trials else None,
        "cost_usd": sum(t.cost_usd for t in trials),
    }


def build_report(
    out: Path,
    job_dirs: list[Path],
    registry: dict[str, dict[str, str]],
    holdout: set[str],
    trials_per_task: int,
) -> dict[str, Any]:
    """The report over every batch of OUT, merged (report.load_jobs refuses batches of other
    versions, records or requests). Tasks of `holdout` appear in aggregates only. A batch stopped
    before its first result.json has no trial to merge: its tasks are missing until it resumes."""
    with_results = [job for job in job_dirs if any(job.glob("*/result.json"))]
    try:
        trials = report_module.load_jobs(with_results)
    except ValueError as exc:
        raise Stop(f"the batches of {out} cannot be merged: {exc}") from exc
    unknown = sum(1 for t in trials if t.task not in registry)
    if unknown:
        raise Stop(f"{unknown} trial(s) under {out} are of tasks the registry does not list")
    infra = [t for t in trials if is_infra_error(t)]
    scored = [t for t in trials if not is_infra_error(t)]
    counts = Counter(t.task for t in scored)
    short = {
        task: trials_per_task - counts[task] for task in registry if counts[task] < trials_per_task
    }
    over = sum(1 for task in registry if counts[task] > trials_per_task)
    if over:
        raise Stop(f"{over} task(s) under {out} have more than {trials_per_task} trials")
    missing = {
        "tasks": {task: n for task, n in sorted(short.items()) if task not in holdout},
        "holdout_tasks": sum(1 for task in short if task in holdout),
        "holdout_trials": sum(n for task, n in short.items() if task in holdout),
        "infra_errors_to_rerun": len(infra),
    }
    complete = not short and not infra
    succeeded = [t for t in scored if is_success(t.reward)]
    billed = sum(report_module.billed_usd(t) for t in scored)
    by_difficulty: dict[str, Any] = {}
    for level in sorted({entry["difficulty"] for entry in registry.values()}):
        of_level = [t for t in scored if registry[t.task]["difficulty"] == level]
        by_difficulty[level] = aggregate(of_level)
    without_zero = [t for t in scored if t.task != ZERO_TASK]
    excluded = report_module.excluded_trials(job_dirs, set(registry))
    per_task = []
    for task in sorted(registry):
        if task in holdout:
            continue
        of_task = [t for t in scored if t.task == task]
        per_task.append(
            {
                "task": task,
                "difficulty": registry[task]["difficulty"],
                "successes": sum(1 for t in of_task if is_success(t.reward)),
                "trials": len(of_task),
                "cost_usd": sum(t.cost_usd for t in of_task),
            }
        )
    return {
        "dataset": DATASET_VERSION,
        "complete": complete,
        "n_tasks": len(registry),
        "trials_per_task": trials_per_task,
        "missing": missing,
        "leaderboard": {**summary(scored), "n_tasks": len({t.task for t in scored})}
        if scored
        else None,
        "without_zero_task": {
            "task": ZERO_TASK,
            **(score(without_zero) if without_zero else {}),
            "n_tasks": len({t.task for t in without_zero}),
        },
        "by_difficulty": by_difficulty,
        "cost": {
            # Everything run cost (listed): the scored trials, the infrastructure errors still
            # to rerun, and the trials resumes dropped (`excluded`).
            "total_cost_usd": sum(t.cost_usd for t in trials)
            + sum(e["cost_usd"] for e in excluded.values()),
            # The rest is of the scored trials.
            "cost_usd": sum(t.cost_usd for t in scored),
            "billed_cost_usd": billed,
            "per_trial_usd": billed / len(scored) if scored else None,
            "per_success_usd": billed / len(succeeded) if succeeded else None,
            "uncached_input_tokens": sum(t.uncached_input_tokens for t in scored),
            "cached_input_tokens": sum(t.cached_input_tokens for t in scored),
            "output_tokens": sum(t.output_tokens for t in scored),
        },
        "wall_clock": wall_clock(scored),
        **excluded,
        "holdout": aggregate([t for t in scored if t.task in holdout]),
        "per_task": per_task,
        "differences_from_a3278ad": list(DIFFERENCES),
        "batches": [
            {"job": job.name, "n_tasks": len(job_tasks(job, registry))} for job in job_dirs
        ],
        # run_contract reads a request without a split as a holdout one; a board run has none.
        "run": {
            k: v
            for k, v in report_module.run_contract(scored, with_results).items()
            if k != "split"
        },
        "readiness": readiness.summary(out),
    }


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def usd(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def wall_line(w: dict[str, Any]) -> str:
    if not w["trials_timed"]:
        return "- wall clock: unrecorded (no trial result gives its start and end)"
    return (
        f"- wall clock: {w['batch_seconds_total']:.0f} s over the batches; "
        f"{w['trials_timed']} trials took {w['trial_seconds_total']:.0f} s in total, "
        f"{w['trial_seconds_mean']:.0f} s on average"
    )


def markdown(r: dict[str, Any]) -> str:
    k = r["trials_per_task"]
    lb = r["leaderboard"] or {}
    state = "complete" if r["complete"] else "INCOMPLETE (scores below cover the trials so far)"
    lines = [
        f"# Leaderboard run: {r['dataset']}, {r['n_tasks']} tasks x {k} trials, {state}",
        "",
        f"Run: {json.dumps(r['run'])}",
        "",
        "## Score (leaderboard algorithm, data-eng-bench leaderboard/core/metrics.py)",
        "",
        "| tasks | trials | accuracy (%) | ± stderr | pass@2 | pass@3 |",
        "|---:|---:|---:|---:|---:|---:|",
        f"| {lb.get('n_tasks', 0)} | {lb.get('n_trials', 0)} | {pct(lb.get('accuracy'))} | "
        f"{pct(lb.get('accuracy_stderr'))} | {lb.get('pass_at_2', '—')} | "
        f"{lb.get('pass_at_3', '—')} |",
    ]
    m = r["missing"]
    if not r["complete"]:
        lines += ["", "## Missing", ""]
        lines += [f"- {task}: {n} trial(s)" for task, n in m["tasks"].items()]
        lines += [
            f"- holdout: {m['holdout_tasks']} task(s), {m['holdout_trials']} trial(s)",
            f"- infrastructure errors to rerun: {m['infra_errors_to_rerun']}",
        ]
    wz = r["without_zero_task"]
    lines += [
        "",
        "## Differences from a3278ad",
        "",
        *(f"- {d}" for d in r["differences_from_a3278ad"]),
        "",
        f"Without `{wz['task']}` ({wz['n_tasks']} tasks): accuracy {pct(wz.get('accuracy'))}% "
        f"± {pct(wz.get('accuracy_stderr'))}, pass@2 {wz.get('pass_at_2', '—')}, "
        f"pass@3 {wz.get('pass_at_3', '—')}.",
        "",
        "## By difficulty",
        "",
        "| difficulty | tasks | trials | succeeded | accuracy (%) |",
        "|---|---:|---:|---:|---:|",
    ]
    for level, a in r["by_difficulty"].items():
        lines.append(
            f"| {level} | {a['n_tasks']} | {a['n_trials']} | {a['n_succeeded']} | "
            f"{pct(a['accuracy'])} |"
        )
    c, w = r["cost"], r["wall_clock"]
    ie, ii = r["infra_excluded"], r["interrupted_excluded"]
    h = r["holdout"]
    lines += [
        "",
        "## Cost, tokens, time",
        "",
        f"- total cost, excluded trials included: ${usd(c['total_cost_usd'])} listed",
        f"- scored trials: ${usd(c['cost_usd'])} listed, ${usd(c['billed_cost_usd'])} billed; "
        f"${usd(c['per_trial_usd'])} per trial, ${usd(c['per_success_usd'])} per success",
        f"- tokens: {c['uncached_input_tokens']:,} uncached + {c['cached_input_tokens']:,} cached "
        f"input, {c['output_tokens']:,} output",
        wall_line(w),
        f"- excluded: {ie['trials']} infrastructure error(s) (${ie['cost_usd']:.4f}), "
        f"{ii['trials']} interrupted (${ii['cost_usd']:.4f})",
        "",
        "## Holdout (aggregate only, ADR-0004)",
        "",
        f"{h['n_tasks']} tasks, {h['n_trials']} trials, {h['n_succeeded']} succeeded "
        f"({pct(h['accuracy'])}%), ${h['cost_usd']:.4f}",
        "",
        "## Per task (holdout tasks left out)",
        "",
        "| task | difficulty | successes | trials | cost (USD) |",
        "|---|---|---:|---:|---:|",
    ]
    for row in r["per_task"]:
        lines.append(
            f"| {row['task']} | {row['difficulty']} | {row['successes']} | {row['trials']} | "
            f"{row['cost_usd']:.4f} |"
        )
    lines += ["", *readiness.markdown(r.get("readiness"))]
    return "\n".join(lines) + "\n"


def write_report(out: Path, report: dict[str, Any]) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    reports = out / "reports" / stamp
    reports.mkdir(parents=True)
    (reports / "board.json").write_text(json.dumps(report, indent=2) + "\n")
    (reports / "board.md").write_text(markdown(report))
    return reports


def check_args(args: argparse.Namespace) -> Decimal:
    try:
        budget = Decimal(args.budget)
    except InvalidOperation:
        budget = None
    if budget is None or not budget.is_finite() or budget <= 0:
        raise Stop(f"budget must be a positive number, got {args.budget!r}")
    for name in ("fail_fast", "trials", "n_concurrent"):
        value = getattr(args, name)
        if value is not None and value < 1:
            raise Stop(f"--{name.replace('_', '-')} must be at least 1, got {value}")
    return budget


def run(args: argparse.Namespace) -> int:
    try:
        budget = check_args(args)
        try:
            registry = read_registry(REGISTRY)
            manifest = json.loads(MANIFEST.read_text())
            holdout = set(manifest["holdout"])
            dev = [task for task in manifest["dev"] if task in registry]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise Stop(f"cannot read the registry list or the manifest: {exc}") from exc
        unknown = sorted(set(args.task or []) - set(registry))
        if unknown:
            raise Stop(f"not in the registry's {DATASET}: {', '.join(unknown)}")
        tasks = [task for task in registry if not args.task or task in args.task]
        if not dev and not args.skip_smoke:
            raise Stop("the manifest has no dev task in the registry to smoke-test on")
        batch = batch_name(tasks, registry)
        (args.out / "jobs").mkdir(parents=True, exist_ok=True)
        (args.out / "logs").mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        record = make_record(args)
        ablate.check_record(args.out, Path(DATASET), record)
        check_versions(args.out, record)
        others = check_batches(args.out, batch, tasks, registry)
        check_mixing(args.config, [*others, args.out / "jobs" / batch])
        check_request(args.out, args)
        write_job_config(args.out, args.model)
        record_path = ablate.write_record(args.out, record, stamp)
        print(f"record: {record_path}", flush=True)
        print(f"batch: {batch}, {len(tasks)} of {len(registry)} tasks", flush=True)
        prepared: list[bool] = []

        def prepare() -> None:  # once, and only when a job is to run: the preflight
            if prepared:
                return
            ensure_base_image()
            ablate.check_balance(args.out, budget)
            try:
                resources, checked = readiness.check_environment(
                    ablate.concurrent_trials(args, [batch], tasks), []
                )
            except readiness.NotReady as exc:
                raise Stop(f"readiness: {exc}; no job starts") from exc
            if resources["free_disk_bytes"] is None:
                print(
                    f"readiness: Docker's data root {resources['docker_root']} is not on this "
                    "host; its disk is not measured",
                    flush=True,
                )
            ablate.fill_wheels({BASE_TAG})
            prepared.append(True)
            warnings: list[dict[str, str]] = []
            if not args.skip_smoke:
                warnings = ablate.smoke(args, [args.config], dev[0], stamp, harbor_run_args)
                checked.extend(readiness.SMOKE_CHECKS)
            readiness.record(args.out, stamp, checked, warnings, resources)

        ensure_batch(args, batch, tasks, prepare)
        job = args.out / "jobs" / batch
        reason = ablate.problem(load_trials(job), tasks, "batch", args.trials)
        if reason:
            raise Stop(f"not ready to score ({reason}); rerun with the same --out to redo them")
        # Only a finished batch: scrub redacts the paths in a job's Harbor config, so an
        # unfinished one would no longer resume.
        code, printed, err = ablate.evals_cli(["scrub", str(job)])
        with (args.out / "logs" / "guards.log").open("a") as f:
            f.write(printed + err)
        if code != 0:
            raise Stop(
                f"scrub could not check every file of the {batch} job, so no report; "
                f"see {args.out / 'logs' / 'guards.log'}"
            )
        job_dirs = sorted(p for p in (args.out / "jobs").iterdir() if p.is_dir())
        report = build_report(args.out, job_dirs, registry, holdout, args.trials)
        reports = write_report(args.out, report)
    except Stop as exc:
        print(f"board: {exc}", file=sys.stderr)
        return exc.code
    print((reports / "board.md").read_text(), end="")
    print(f"-> {reports}")
    return 0
