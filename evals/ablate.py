"""`ablate`: the ablation table in one command (ADR-0004, plan §7).

Each run config is one Harbor job on the manifest's holdout tasks, k trials per task:

    OUT/jobs/<config>/        the Harbor job (resumed when it exists)
    OUT/dropped/<config>/     event files of trials a resume deleted, kept for the spend guard
    OUT/logs/<config>.log     Harbor's output; OUT/logs/guards.log the spend guard's and scrub's
    OUT/reports/<UTC time>/   new on every invocation:
        <config>/report.json  `evals report` (holdout: aggregates only)
        ablation.json, ablation.md

Before each job the spend guard (`evals spend` on OUT) stops the batch at 80% of --budget; a job
runs to its end once started, so the overshoot is at most one job. A finished job is not rerun; an
interrupted one, or one with infrastructure errors, is resumed with `harbor job resume`, which
deletes the errored trials and runs what is missing (plan §7: infrastructure errors are rerun,
never scored). An existing job is reused only from a clean checkout at the commit and Skill version
its trials recorded, and the table needs one commit and one model across its configs. A config
error (the model's key, name or parameters) stops the batch for a human: Harbor does not retry it
and a report would score it as a real 0. Once every job is complete, `evals scrub` cleans this
table's jobs in place, `evals report` writes each config's report and `evals compare --manifest
--split holdout` gives each layer's contribution: full minus the config without the layer, in
accuracy points, with a paired bootstrap 95% interval. Per-task holdout results are never printed
or written (ADR-0004); the guards' and Harbor's own output, which name tasks, go to logs.

Run from the repo root with the DATA_AGENT_* variables set, as for `harbor run`:

    uv run python -m evals ablate --tasks-dir <data-eng-bench>/tasks \
        --manifest evals/manifest.json --out runs/ablation --budget 300 --n-concurrent 2

plan §7's cuts: `--trials 2`, or `--config` repeated to drop configs (full is always needed).
Exit 0 with the table; 1 when the spend guard stopped the batch; 2 on anything a human must look at
(a config error, a failed Harbor run, infrastructure errors left, a scrub that could not check
everything).
"""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from data_agent.agent import git_commit, skill_version
from data_agent.run_config import REPO_ROOT, load_config
from evals.harbor import Trial, load_trials
from evals.report import TRIALS_PER_TASK, check_complete, is_infra_error, split_trials

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
JOB_CONFIG = REPO_ROOT / "evals" / "configs" / "data-eng-bench-duckdb.data-agent.yaml"
SPLIT = "holdout"


class Stop(Exception):
    """The batch stops here; `code` is the exit code."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser("ablate", help="run the configs on the holdout, write the table")
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
    parser.set_defaults(run=run)


def run_harbor(args: list[str], log: Path) -> int:
    """Harbor from this environment, its output appended to `log`; the repo root on the import
    path, where Harbor finds the agent (evals.harbor_agent)."""
    path = os.environ.get("PYTHONPATH")
    env = os.environ | {"PYTHONPATH": os.pathsep.join(filter(None, [str(REPO_ROOT), path]))}
    harbor = Path(sys.executable).with_name("harbor")
    with log.open("a") as f:
        proc = subprocess.run([str(harbor), *args], stdout=f, stderr=subprocess.STDOUT, env=env)
    return proc.returncode


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


def problem(trials: list[Trial], tasks: list[str], trials_per_task: int) -> str | None:
    """Why the job is not ready to score, in counts only; None when it is."""
    try:
        check_complete(split_trials(trials, {SPLIT: tasks}, SPLIT, "this"), SPLIT, trials_per_task)
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


def guard_spend(out: Path, budget: str) -> None:
    """Spend under OUT: the jobs plus the event files of trials a resume dropped."""
    code, printed, err = evals_cli(["spend", str(out), "--budget", budget])
    _log(out / "logs" / "guards.log", printed, err)
    if code == 1:
        raise Stop(err.strip(), code=1)
    if code != 0:
        raise Stop(f"the spend guard cannot total the spend; see {out / 'logs' / 'guards.log'}")
    print(printed.strip(), flush=True)


def event_files(trial: Trial) -> list[Path]:
    """The trial's event files, where the agent writes them in Harbor's agent logs directory."""
    return sorted((trial.dir / "agent" / "events").glob("*.jsonl"))


def keep_spend(out: Path, config: str, trials: list[Trial]) -> None:
    """Copy the event files of the trials a resume will delete to OUT/dropped/, where the spend
    guard still counts what they cost."""
    for trial in trials:
        for events in event_files(trial):
            kept = out / "dropped" / config / trial.dir.name / events.name
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(events, kept)


def ensure_job(config: str, args: argparse.Namespace, tasks: list[str]) -> None:
    """Run the config's job, or resume it when it exists and is not ready to score."""
    jobs, logs = args.out / "jobs", args.out / "logs"
    job = jobs / config
    dropped: list[Trial] = []
    if (job / "config.json").is_file():
        trials = load_trials(job) if any(job.glob("*/result.json")) else []
        check_config_errors(config, job, trials)
        check_conditions(config, job, trials)
        if problem(trials, tasks, args.trials) is None:
            return
        dropped = [t for t in trials if is_infra_error(t)]
        infra = sorted({str(t.exception_type) for t in dropped})
        harbor_args = ["job", "resume", "-p", str(job), *(a for e in infra for a in ("-f", e))]
    else:
        harbor_args = [
            *("run", "--config", str(JOB_CONFIG), "--path", str(args.tasks_dir)),
            *("-o", str(jobs), "--job-name", config, "-k", str(args.trials)),
            *("--ak", f"run_config={config}"),
            *(("-n", str(args.n_concurrent)) if args.n_concurrent else ()),
            *(("-m", args.model) if args.model else ()),
            *(a for task in tasks for a in ("-i", task)),
        ]
    guard_spend(args.out, args.budget)
    keep_spend(args.out, config, dropped)  # after the guard, which would count them twice
    log = logs / f"{config}.log"
    print(f"{config}: harbor {' '.join(harbor_args[:2])} -> {log}", flush=True)
    code = run_harbor(harbor_args, log)
    if code != 0:
        raise Stop(f"harbor exited {code} on the {config} job; see {log}")
    try:
        trials = load_trials(job)
    except ValueError as exc:
        raise Stop(str(exc)) from exc
    check_config_errors(config, job, trials)


def versions(trials: list[Trial]) -> dict[str, list[str]]:
    """The code, Skill version and model the trials ran with, from their event files' headers."""
    seen: dict[str, set[str]] = {"git_commit": set(), "skill_version": set(), "model": set()}
    for trial in trials:
        for events in event_files(trial):
            with events.open() as f:
                header = json.loads(f.readline() or "{}")
            for key, values in seen.items():
                if header.get(key):
                    values.add(str(header[key]))
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
    common = ["--manifest", str(args.manifest), "--split", SPLIT, "--trials", str(args.trials)]
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
    table = {
        "split": SPLIT,
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
    configs = [c for c in CONFIGS if c in (args.config or CONFIGS)]
    if REFERENCE not in configs:
        print(f"ablate: --config must include {REFERENCE}, the reference", file=sys.stderr)
        return 2
    try:
        tasks: list[str] = json.loads(args.manifest.read_text())[SPLIT]
    except (OSError, ValueError, KeyError) as exc:
        print(f"ablate: cannot read the {SPLIT} tasks from {args.manifest}: {exc}", file=sys.stderr)
        return 2
    if not tasks:
        print(f"ablate: the manifest's {SPLIT} split is empty", file=sys.stderr)
        return 2
    (args.out / "jobs").mkdir(parents=True, exist_ok=True)
    (args.out / "logs").mkdir(exist_ok=True)
    try:
        for config in configs:
            ensure_job(config, args, tasks)
        left = {}
        for config in configs:
            reason = problem(load_trials(args.out / "jobs" / config), tasks, args.trials)
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
