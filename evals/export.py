"""`export`: chosen runs as the online site's static data (web UI spec, "导出命令和在线版数据").

    uv run python -m evals export --manifest evals/manifest.json --out runs/site \\
        --run runs/featured/<id> ... --job runs/u1-gate/jobs/full ... \\
        [--board board.json] [--dab dab_score.json] [--tasks-dir TASKS] [--hostname H ...]

Inputs:

- `--run RUN_DIR`: a featured run, its one event file (`*.jsonl`, as `data-agent run --events-dir`
  leaves it) and `notes.json` with the maintainer's `title`, `failure_reason` and maybe `grade`
  (and `task`, `task_type`, `data_source` for an event file without a `task` event).
- `--job JOB_DIR`: a Harbor job of the dev set; every trial in it is exported.
- `--board FILE`: `evals board`'s `board.json`; `--dab FILE`: `evals dab-score`'s JSON report. Only
  their aggregates are copied.

Output (the layout fixed by ticket 73's tests, plus the evaluation page's summary):

    DIR/index.json              {"runs": [notes, ...]}: featured runs, then trials by name
    DIR/runs/<id>/events.jsonl  the run's events
    DIR/runs/<id>/meta.json     the same notes as in the index
    DIR/eval.json               the evaluation page's summary (`build`)

The notes of a run are `id`, `title`, `task`, `task_type`, `data_source`, `grade`
(`{"passed", "detail"}` or null), `failure_reason` and `featured` (web/src/run-view/model.ts,
`RunNotes`). Task, type and data source come from the run's `task` event. A featured run without
one takes them from its notes; a trial without one takes its task from Harbor's task directory
(`--tasks-dir`, `<task>/instruction.md`), else the task id, and is a dbt run (the Harbor adapter
runs every trial with `--type dbt`).

Everything is written to a staging directory beside DIR and scrubbed there with `evals scrub`'s
rules (`--hostname`, `--home`, `--user` as in `scrub`); only a clean scrub is moved to DIR, which
must be new or empty. Nothing turns scrubbing off. A trial of a task held out by the given
manifest or by the repo's evals/manifest.json is refused before anything is written; the error
gives a count, never a task or its outcome (ADR-0004). The holdout and the whole benchmark reach
the site only as the aggregates of `--board`.

Exit 0 with the export; 2 on anything refused.
"""

import argparse
import json
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from evals import scrub
from evals.ablate import LAYERS, REFERENCE
from evals.compare import paired_bootstrap
from evals.harbor import Trial, by_task, last_event_file, load_trials, version_header
from evals.metrics import is_success
from evals.report import REPO_MANIFEST, summary

# The run notes' fields, in the order they are written.
NOTES_FIELDS = (
    "id",
    "title",
    "task",
    "task_type",
    "data_source",
    "grade",
    "failure_reason",
    "featured",
)
# What a config's row of the evaluation page keeps of `evals.report.summary`.
CONFIG_KEYS = (
    "accuracy",
    "accuracy_stderr",
    "pass_at_2",
    "pass_at_3",
    "n_tasks",
    "n_trials",
    "n_succeeded",
    "n_failed",
    "cost_usd",
)
# What the site keeps of `board.json`: the leaderboard over every task, and the holdout's aggregate.
BOARD_LEADERBOARD_KEYS = (
    "accuracy",
    "accuracy_stderr",
    "pass_at_2",
    "pass_at_3",
    "n_tasks",
    "n_trials",
    "n_succeeded",
)
BOARD_HOLDOUT_KEYS = ("n_tasks", "n_trials", "n_succeeded", "accuracy")
# `evals compare`'s defaults, as `evals ablate` compares configs.
BOOTSTRAP_SEED = 0
BOOTSTRAP_N = 10000


class Refused(Exception):
    pass


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser(
        "export", help="the online site's static data from chosen runs; always scrubbed"
    )
    parser.add_argument(
        "--manifest", type=Path, required=True, help="trials it holds out are refused"
    )
    parser.add_argument("--out", type=Path, required=True, help="output directory, new or empty")
    parser.add_argument(
        "--run",
        dest="runs",  # `evals` dispatches on `args.run`
        type=Path,
        action="append",
        default=[],
        help="a featured run: its event file and notes.json (repeatable)",
    )
    parser.add_argument(
        "--job", type=Path, action="append", default=[], help="a dev Harbor job (repeatable)"
    )
    parser.add_argument("--board", type=Path, help="`evals board`'s board.json: aggregates only")
    parser.add_argument("--dab", type=Path, help="`evals dab-score`'s JSON report: aggregates only")
    parser.add_argument(
        "--tasks-dir", type=Path, help="Harbor task directories, for trials without a task event"
    )
    parser.add_argument(
        "--hostname", action="append", default=[], help="extra hostname to remove (repeatable)"
    )
    parser.add_argument(
        "--home",
        action="append",
        default=[],
        help="home directory of the machine that ran the job (repeatable; this one's is included)",
    )
    parser.add_argument(
        "--user",
        action="append",
        default=[],
        help="user name of the machine that ran the job (repeatable; this one's is included)",
    )
    parser.set_defaults(run=run)


def read_json(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read {what} {path}: {exc}") from exc


def held_out(manifest: dict[str, Any]) -> set[str]:
    """The tasks the manifest or the repo's manifest holds out (a temporary manifest may hold out
    nothing, as `evals.report.check_dev_list` reasons)."""
    tasks: set[str] = set()
    for name, m in (("the manifest", manifest), (str(REPO_MANIFEST), None)):
        holdout = (m if m is not None else read_json(REPO_MANIFEST, "the repo manifest")).get(
            "holdout"
        )
        if not isinstance(holdout, list) or not all(isinstance(t, str) for t in holdout):
            raise Refused(f"cannot read the holdout of {name}: not a list of task ids")
        tasks |= set(holdout)
    return tasks


def read_events(path: Path) -> list[dict[str, Any]]:
    """The events of a file, lines that do not parse aside (a run killed mid-line)."""
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def task_event(path: Path) -> dict[str, Any] | None:
    return next((e for e in read_events(path) if e.get("type") == "task"), None)


def featured_run(run_dir: Path) -> tuple[dict[str, Any], Path]:
    """The notes and event file of a featured run; its id is the directory name."""
    files = sorted(run_dir.glob("*.jsonl"))
    if len(files) != 1:
        raise Refused(f"{run_dir} must hold one event file (*.jsonl), not {len(files)}")
    given = read_json(run_dir / "notes.json", "the featured run's notes.json")
    if not isinstance(given, dict) or not isinstance(given.get("title"), str):
        raise Refused(f"{run_dir / 'notes.json'} gives no title")
    grade = given.get("grade")
    if grade is not None and not (
        isinstance(grade, dict) and isinstance(grade.get("passed"), bool)
    ):
        raise Refused(
            f"{run_dir / 'notes.json'}: grade must be {{'passed': bool, 'detail'}} or null"
        )
    # An event file from before the `task` event: the notes give task, type and data source.
    task = task_event(files[0]) or given
    notes = {
        "id": run_dir.name,
        "title": given["title"],
        "task": task.get("task"),
        "task_type": task.get("task_type"),
        "data_source": task.get("data_source"),
        "grade": None
        if grade is None
        else {"passed": grade["passed"], "detail": grade.get("detail")},
        "failure_reason": given.get("failure_reason"),
        "featured": True,
    }
    return notes, files[0]


def instruction(tasks_dir: Path | None, task: str) -> str:
    """The task's instruction in Harbor's task directory, else its id."""
    path = tasks_dir / task / "instruction.md" if tasks_dir else None
    return path.read_text() if path and path.is_file() else task


def trial_notes(trial: Trial, events: Path, tasks_dir: Path | None) -> dict[str, Any]:
    task = task_event(events)
    detail = f"reward {trial.reward}" if trial.reward is not None else "no reward"
    if trial.exception_type:
        detail += f"; {trial.exception_type}"
    return {
        "id": trial.dir.name,
        "title": trial.task,
        "task": task.get("task") if task else instruction(tasks_dir, trial.task),
        "task_type": task.get("task_type") if task else "dbt",
        "data_source": task.get("data_source") if task else None,
        "grade": {"passed": is_success(trial.reward), "detail": detail},
        "failure_reason": None,  # failure attribution is shown only where it exists
        "featured": False,
    }


def duration_seconds(trial: Trial) -> float | None:
    """The trial's wall clock from Harbor's result.json, None when unrecorded."""
    result = read_json(trial.dir / "result.json", "a trial's result.json")
    try:
        start = datetime.fromisoformat(result["started_at"])
        end = datetime.fromisoformat(result["finished_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return (end - start).total_seconds()


def trial_rows(trials: list[tuple[Trial, str | None]]) -> list[dict[str, Any]]:
    """One row per dev trial; `attempt` numbers a task's trials of one config by start time."""
    rows: list[dict[str, Any]] = []
    seen: dict[tuple[str, str | None], int] = defaultdict(int)
    timed = []
    for trial, config in trials:
        result = read_json(trial.dir / "result.json", "a trial's result.json")
        timed.append((str(result.get("started_at") or ""), trial.dir.name, trial, config))
    for _, _, trial, config in sorted(timed, key=lambda t: t[:2]):
        seen[(trial.task, config)] += 1
        rows.append(
            {
                "id": trial.dir.name,
                "task": trial.task,
                "config": config,
                "attempt": seen[(trial.task, config)],
                "passed": is_success(trial.reward),
                "reward": trial.reward,
                "cost_usd": trial.cost_usd,
                "duration_seconds": duration_seconds(trial),
            }
        )
    return sorted(rows, key=lambda r: r["id"])


def dev_summary(trials: list[tuple[Trial, str | None]]) -> dict[str, Any]:
    """Each config's pass rate (`evals.report.summary`) and each layer's contribution, the
    reference minus the config without it (`evals.compare.paired_bootstrap`, as `evals ablate`)."""
    grouped: dict[str, list[Trial]] = defaultdict(list)
    for trial, config in trials:
        grouped[config or "unknown"].append(trial)
    configs = [
        {"config": config, **{k: v for k, v in summary(ts).items() if k in CONFIG_KEYS}}
        for config, ts in sorted(grouped.items())
    ]
    contributions: list[dict[str, Any]] = []
    if REFERENCE in grouped:
        for config, ts in sorted(grouped.items()):
            if config == REFERENCE:
                continue
            try:
                diff = paired_bootstrap(
                    by_task(ts), by_task(grouped[REFERENCE]), BOOTSTRAP_SEED, BOOTSTRAP_N
                )
            except ValueError as exc:
                raise Refused(f"cannot compare {REFERENCE} with {config}: {exc}") from exc
            contributions.append({"layer": LAYERS.get(config, config), "config": config, **diff})
    return {
        "reference": REFERENCE,
        "configs": configs,
        "contributions": contributions,
        "trials": trial_rows(trials),
    }


def pick(source: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: source[k] for k in keys if k in source} if isinstance(source, dict) else {}


def board_summary(path: Path) -> dict[str, Any]:
    """data-eng-bench's leaderboard score and the holdout's aggregate, nothing per task."""
    board = read_json(path, "the board report")
    if not isinstance(board, dict) or not isinstance(board.get("leaderboard"), dict):
        raise Refused(f"{path} is not a board.json of `evals board`")
    run_info = pick(board.get("run"), ("config", "model"))
    return {
        "complete": board.get("complete"),
        "trials_per_task": board.get("trials_per_task"),
        "config": run_info.get("config"),
        "model": run_info.get("model"),
        "leaderboard": pick(board["leaderboard"], BOARD_LEADERBOARD_KEYS),
        "holdout": pick(board.get("holdout"), BOARD_HOLDOUT_KEYS),
    }


def dab_summary(path: Path) -> dict[str, Any]:
    """DAB's Pass@1 of each output and per dataset, and the comparison; no query or path."""
    report = read_json(path, "the DAB score report")
    if not isinstance(report, dict) or not any(
        isinstance(v, dict) and "pass_at_1" in v for v in report.values()
    ):
        raise Refused(f"{path} is not a JSON report of `evals dab-score`")
    out: dict[str, Any] = {}
    for name, entry in report.items():
        if name == "comparison":
            out[name] = pick(entry, ("diff", "ci_low", "ci_high", "significant"))
            continue
        if not isinstance(entry, dict) or "pass_at_1" not in entry:
            continue
        datasets: dict[str, Any] = {}
        for dataset, d in (entry.get("datasets") or {}).items():
            queries = list((d.get("queries") or {}).values())
            datasets[dataset] = {
                "pass_at_1": d.get("pass_at_1"),
                "queries": len(queries),
                "runs": sum(int(q.get("runs", 0)) for q in queries),
                "passed": sum(int(q.get("passed", 0)) for q in queries),
            }
        out[name] = {
            "pass_at_1": entry["pass_at_1"],
            "coverage": pick(entry.get("coverage"), ("queries", "clone_queries")),
            "missing_runs": len(entry.get("missing") or []),
            "datasets": datasets,
        }
    return out


def load_dev_trials(
    job_dirs: list[Path], holdout: set[str]
) -> list[tuple[Trial, Path, str | None]]:
    """Every trial of the jobs with its event file and config; refused, with a count only, when
    any is a holdout task's, or has no event file."""
    trials: list[Trial] = []
    for job in job_dirs:
        try:
            trials.extend(load_trials(job))
        except (OSError, ValueError, KeyError) as exc:
            raise Refused(f"cannot read the job {job}: {exc}") from exc
    refused = sum(1 for t in trials if t.task in holdout)
    if refused:
        raise Refused(
            f"{refused} trial(s) of holdout tasks; only dev trials are exported, the holdout "
            "reaches the site as aggregates only (ADR-0004)"
        )
    out: list[tuple[Trial, Path, str | None]] = []
    for trial in trials:
        events = last_event_file(trial)
        if events is None:
            raise Refused(f"the trial {trial.dir} has no event file")
        config = version_header(events).get("config")
        out.append((trial, events, config if isinstance(config, str) else None))
    return out


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def build(args: argparse.Namespace, stage: Path) -> int:
    """Write the export into `stage`; the number of runs."""
    manifest = read_json(args.manifest, "the manifest")
    if not isinstance(manifest, dict):
        raise Refused(f"{args.manifest} is not a manifest")
    trials = load_dev_trials(args.job, held_out(manifest))
    runs: list[tuple[dict[str, Any], Path]] = [featured_run(r) for r in args.runs]
    runs += [(trial_notes(t, events, args.tasks_dir), events) for t, events, _ in trials]
    ids = [notes["id"] for notes, _ in runs]
    if len(set(ids)) < len(ids):
        raise Refused(f"{len(ids) - len(set(ids))} run id(s) repeat; each run needs its own id")
    for notes, events in runs:
        run_dir = stage / "runs" / notes["id"]
        run_dir.mkdir(parents=True)
        shutil.copyfile(events, run_dir / "events.jsonl")
        write_json(run_dir / "meta.json", notes)
    write_json(stage / "index.json", {"runs": [notes for notes, _ in runs]})
    summary_ = {
        "dev": dev_summary([(t, config) for t, _, config in trials]),
        "data_eng_bench": board_summary(args.board) if args.board else None,
        "dab": dab_summary(args.dab) if args.dab else None,
    }
    write_json(stage / "eval.json", summary_)
    return len(runs)


def run(args: argparse.Namespace) -> int:
    out: Path = args.out
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        print(f"export: {out} exists and is not an empty directory", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{out.name}.export-", dir=out.parent))
    try:
        try:
            n = build(args, stage)
        except Refused as exc:
            print(f"export: {exc}", file=sys.stderr)
            return 2
        cleaned = scrub.run(
            argparse.Namespace(dir=stage, hostname=args.hostname, home=args.home, user=args.user)
        )
        if cleaned != 0:
            print("export: scrubbing left the problems above; nothing exported", file=sys.stderr)
            return 2
        if out.exists():
            out.rmdir()  # empty, checked above
        stage.rename(out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(f"export: {n} run(s) -> {out}")
    return 0
