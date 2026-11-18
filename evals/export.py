"""`export`: chosen runs as the online site's static data (web UI spec, "导出命令和在线版数据").

    uv run python -m evals export --manifest evals/manifest.json --out runs/site \\
        --run runs/featured/<id> ... --job runs/u1-gate/jobs/full ... \\
        [--conversation <runs dir>/conversations/<id> ...] \\
        [--board board.json] [--dab dab_score.json] [--tasks-dir TASKS] [--hostname H ...]

Inputs:

- `--run RUN_DIR`: a featured run, its one event file (`*.jsonl`, as `data-agent run --events-dir`
  leaves it) and `notes.json` with the maintainer's `title`, `failure_reason` and maybe `grade`
  (and `task`, `task_type`, `data_source` for an event file without a `task` event).
- `--job JOB_DIR`: a Harbor job of the dev set; every trial in it is exported.
- `--conversation DIR`: a recorded conversation (ticket 96) as `data-agent web` keeps it
  (data_agent.web.conversations): `DIR/conversation.json`, its index, and each turn's run in
  `DIR/../../<run_id>/`, beside the conversations' directory; plus the maintainer's
  `DIR/notes.json` with a `title` and where the questions came from, as for a featured run. Only
  the index and each turn's event file are read: the conversation's workspace and the SDK's
  session record are not exported. Every turn must have ended, with its `result` event or as
  cancelled or failed by the server's record (`web-run.json`).
- `--board FILE`: `evals board`'s `board.json`; `--dab FILE`: `evals dab-score`'s JSON report. Only
  their aggregates are copied.

Output (the layout fixed by ticket 73's tests, plus the evaluation page's summary):

    DIR/index.json              {"runs": [notes, ...], "conversations": [conversation, ...]}:
                                featured runs, then trials by name; then the conversations
    DIR/runs/<id>/events.jsonl  the run's events (each turn of a conversation is a run too)
    DIR/runs/<id>/meta.json     the same notes as in the index; a turn's, `turn_notes`
    DIR/eval.json               the evaluation page's summary (`build`)

A conversation in the index is its `id`, `title` (from its notes.json), `created_at` and `turns`
as its `conversation.json` lists them (`turn`, `run_id`, `message`, `steers`;
web/src/chat-view/model.ts, `ConversationIndex`). Its turns are not in `runs`: the site shows
them only in the conversation. Without `--conversation` the index has no `conversations`.

The notes of a run are `id`, `title`, `task`, `task_type`, `data_source`, `grade`
(`{"passed", "detail"}` or null), `failure_reason` and `featured` (web/src/run-view/model.ts,
`RunNotes`). Task, type and data source come from the run's `task` event. A featured run without
one takes them from its notes; a trial without one takes its task from Harbor's task directory
(`--tasks-dir`, `<task>/instruction.md`), else the task id, and is a dbt run (the Harbor adapter
runs every trial with `--type dbt`).

Everything is written to a staging directory beside DIR and scrubbed there with `evals scrub`'s
rules (`--hostname`, `--home`, `--user` as in `scrub`); only a clean scrub is moved to DIR, which
must be new or empty. Nothing turns scrubbing off. Scrubbing changes files, not paths, so a run
id (a run or trial directory's name) the scrub would change is refused, by count, before anything
is written (`check_ids`). A trial of a task outside the manifest's dev
set, or held out by it or by the repo's evals/manifest.json, is refused before anything is
written; the error gives a count, never a task or its outcome (ADR-0004). The holdout and the
whole benchmark reach the site only as the aggregates of `--board`. A featured run's notes.json
says where it came from: `task_id`, checked as a trial's task is, or `local_data: true` for a run
on genuinely local data (`featured_allowed`). One with neither, or with notes.json unreadable, is
refused, and so is the whole export; that error gives a count too, not a run id or directory,
which is often named after its task; a featured run refused later, as malformed, is named by its
position among the `--run`s. These two fields are input only, not in the notes. A conversation's
notes.json is checked the same way (`local_data: true` for questions written by hand rather than
taken from a benchmark task), and its turns' run ids are checked as run ids are.

Exit 0 with the export; 2 on anything refused.
"""

import argparse
import json
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from evals import scrub
from evals.ablate import LAYERS, REFERENCE
from evals.compare import paired_bootstrap
from evals.harbor import Trial, by_task, last_event_file, load_trials, version_header
from evals.metrics import is_success
from evals.report import REPO_MANIFEST, summary

# What `data-agent web` writes (data_agent.web.conversations, data_agent.web.runs): a
# conversation's index, and the server's record of a run it started, with the run's `status`.
CONVERSATION_INDEX = "conversation.json"
RUN_RECORD = "web-run.json"

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
    parser.add_argument(
        "--conversation",
        dest="conversations",
        type=Path,
        action="append",
        default=[],
        help="a recorded conversation's directory, with notes.json (repeatable)",
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


def task_list(manifest: Any, split: str, name: Path) -> set[str]:
    tasks = manifest.get(split) if isinstance(manifest, dict) else None
    if not isinstance(tasks, list) or not all(isinstance(t, str) for t in tasks):
        raise Refused(f"cannot read the {split} of {name}: not a list of task ids")
    return set(tasks)


def exportable(manifest_path: Path) -> set[str]:
    """The manifest's dev tasks, less any the manifest or the repo's manifest holds out (a
    temporary manifest may hold out nothing, as `evals.report.check_dev_list` reasons)."""
    manifest = read_json(manifest_path, "the manifest")
    repo = read_json(REPO_MANIFEST, "the repo manifest")
    held_out = task_list(manifest, "holdout", manifest_path)
    held_out |= task_list(repo, "holdout", REPO_MANIFEST)
    return task_list(manifest, "dev", manifest_path) - held_out


def read_events(path: Path) -> list[dict[str, Any]]:
    """The events of a file, lines that do not parse aside (a run killed mid-line)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Refused(f"cannot read the event file {path}: {exc}") from exc
    events: list[dict[str, Any]] = []
    # Not splitlines(): it also splits at U+2028 and the like inside a string (evals.attribute).
    for line in text.split("\n"):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def task_event(path: Path) -> dict[str, Any] | None:
    return next((e for e in read_events(path) if e.get("type") == "task"), None)


def featured_allowed(given: Any, dev: set[str]) -> bool:
    """Whether a featured run's notes.json lets it be exported: a `task_id` in `dev` (a task id
    wins over `local_data`, so a holdout run cannot pass as local), or else `local_data` being
    the boolean true; neither is refused, not taken as local."""
    if not isinstance(given, dict):
        return False
    if "task_id" in given:
        return isinstance(given["task_id"], str) and given["task_id"] in dev
    return given.get("local_data") is True


def given_notes(run_dir: Path) -> Any:
    """A featured run's notes.json, None when it cannot be read."""
    try:
        return json.loads((run_dir / "notes.json").read_text())
    except (OSError, ValueError):
        return None


def check_featured(run_dirs: list[Path], dev: set[str]) -> list[dict[str, Any]]:
    """Each featured run's notes.json, read once; refused, with a count only, when any is not
    let through (`featured_allowed`): a run's id and directory are often its task's name, so
    neither is printed (ADR-0004)."""
    given = [given_notes(r) for r in run_dirs]
    refused = sum(1 for g in given if not featured_allowed(g, dev))
    if refused:
        raise Refused(
            f"{refused} featured run(s) refused: each needs a readable notes.json with a "
            "`task_id` of the dev set or `local_data: true`; holdout and unlisted tasks are not "
            "exported (ADR-0004)"
        )
    return given


def featured_run(
    run_dir: Path, given: dict[str, Any], position: int
) -> tuple[dict[str, Any], Path]:
    """The notes and event file of a featured run whose notes.json `given` `check_featured` let
    through; its id is the directory name. An error names it by its `position` among the
    `--run`s, not by its directory, which is often its task's name."""
    which = f"the featured run given as --run #{position}"
    files = sorted(run_dir.glob("*.jsonl"))
    if len(files) != 1:
        raise Refused(f"{which} must hold one event file (*.jsonl), not {len(files)}")
    if not isinstance(given.get("title"), str):
        raise Refused(f"{which}: its notes.json gives no title")
    grade = given.get("grade")
    if grade is not None and not (
        isinstance(grade, dict) and isinstance(grade.get("passed"), bool)
    ):
        raise Refused(f"{which}: its grade must be {{'passed': bool, 'detail'}} or null")
    try:
        found = task_event(files[0])
    except Refused:
        # read_events's message names the file; say what failed without it.
        raise Refused(f"{which}: its event file cannot be read, or is not UTF-8") from None
    # An event file from before the `task` event: the notes give task, type and data source.
    task = found or given
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


def check_conversations(conversation_dirs: list[Path], dev: set[str]) -> list[dict[str, Any]]:
    """Each conversation's notes.json, read once and let through as a featured run's is
    (`featured_allowed`); refused with a count only."""
    given = [given_notes(c) for c in conversation_dirs]
    refused = sum(1 for g in given if not featured_allowed(g, dev))
    if refused:
        raise Refused(
            f"{refused} conversation(s) refused: each needs a readable notes.json with a "
            "`task_id` of the dev set or `local_data: true`; holdout and unlisted tasks are not "
            "exported (ADR-0004)"
        )
    return given


def is_turn(turn: Any) -> bool:
    """A turn as `conversation.json` lists it, its run id a plain directory name."""
    if not isinstance(turn, dict):
        return False
    run_id = turn.get("run_id")
    message = turn.get("message")
    return (
        isinstance(turn.get("turn"), int)
        and isinstance(run_id, str)
        and Path(run_id).name == run_id
        and not run_id.startswith(".")
        and (message is None or isinstance(message, str))
        and isinstance(turn.get("steers"), list)
    )


def turn_notes(turn: dict[str, Any], events: Path, which: str) -> dict[str, Any]:
    """A turn's run notes: a run's fields from its `task` event, titled by the turn's message, and
    `cancelled` or `failed` as the server's record says of a run that wrote no `result` event (as
    web/src/data/source.ts `turnNotes` gives them locally). A turn that has not ended is refused:
    the site would show it running for ever."""
    try:
        found = read_events(events)
    except Refused:
        raise Refused(f"{which}: its event file cannot be read, or is not UTF-8") from None
    status = None
    if not (found and found[-1].get("type") == "result"):
        record = events.parent / RUN_RECORD
        try:
            given = json.loads(record.read_text()) if record.is_file() else None
        except (OSError, ValueError):
            given = None
        status = given.get("status") if isinstance(given, dict) else None
        if status not in ("cancelled", "failure"):
            raise Refused(f"{which} has not ended: no `result` event, and not cancelled or failed")
    task = next((e for e in found if e.get("type") == "task"), {})
    return {
        "id": turn["run_id"],
        "title": turn["message"] if turn["message"] is not None else task.get("task"),
        "task": task.get("task"),
        "task_type": task.get("task_type"),
        "data_source": task.get("data_source"),
        "grade": None,
        "failure_reason": None,
        "featured": False,
        "cancelled": status == "cancelled",
        "failed": status == "failure",
    }


def conversation(
    directory: Path, given: dict[str, Any], position: int
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], Path]]]:
    """A conversation whose notes.json `given` `check_conversations` let through: its index entry,
    and each turn's notes and event file. An error names it by its `position` among the
    `--conversation`s, and a turn by its number."""
    which = f"the conversation given as --conversation #{position}"
    if not isinstance(given.get("title"), str):
        raise Refused(f"{which}: its notes.json gives no title")
    index = read_json(directory / CONVERSATION_INDEX, f"{which}: its index")
    turns = index.get("turns") if isinstance(index, dict) else None
    if (
        not isinstance(index, dict)
        or not isinstance(index.get("id"), str)
        or not isinstance(turns, list)
        or not all(is_turn(t) for t in turns)
    ):
        raise Refused(f"{which}: its {CONVERSATION_INDEX} is not a conversation's index")
    runs_dir = directory.parent.parent
    runs: list[tuple[dict[str, Any], Path]] = []
    for turn in turns:
        turn_which = f"{which}, turn {turn['turn']}"
        run_dir = runs_dir / turn["run_id"]
        files = sorted(run_dir.glob("*.jsonl")) if run_dir.is_dir() else []
        if len(files) != 1:
            raise Refused(f"{turn_which} must have its run, with one event file (*.jsonl)")
        runs.append((turn_notes(turn, files[0], turn_which), files[0]))
    entry = {
        "id": index["id"],
        "title": given["title"],
        "created_at": index.get("created_at"),
        "turns": [{k: t[k] for k in ("turn", "run_id", "message", "steers")} for t in turns],
    }
    return entry, runs


def instruction(tasks_dir: Path | None, task: str) -> str:
    """The task's instruction in Harbor's task directory, else its id."""
    path = tasks_dir / task / "instruction.md" if tasks_dir else None
    if path is None or not path.is_file():
        return task
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Refused(f"cannot read the task instruction {path}: {exc}") from exc


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


def duration_seconds(result: dict[str, Any]) -> float | None:
    """The trial's wall clock from Harbor's result.json, None when unrecorded."""
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
        timed.append((str(result.get("started_at") or ""), trial.dir.name, trial, config, result))
    for _, _, trial, config, result in sorted(timed, key=lambda t: t[:2]):
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
                "duration_seconds": duration_seconds(result),
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


def load_dev_trials(job_dirs: list[Path], dev: set[str]) -> list[tuple[Trial, Path, str | None]]:
    """Every trial of the jobs by name, with its event file and config; refused, with a count
    only, when any is of a task outside `dev` (held out, or not in the manifest's dev set), or
    has no event file."""
    trials: list[Trial] = []
    for job in job_dirs:
        try:
            trials.extend(load_trials(job))
        except OSError as exc:
            # Not `exc`: it names the trial's file, and a trial is named after its task, maybe a
            # held-out one; the holdout check below has not run yet (ADR-0004).
            raise Refused(f"cannot read the job {job}: {exc.strerror}") from exc
        except (ValueError, KeyError) as exc:
            raise Refused(f"cannot read the job {job}: {exc}") from exc
    refused = sum(1 for t in trials if t.task not in dev)
    if refused:
        raise Refused(
            f"{refused} trial(s) of holdout tasks or tasks outside the dev set; only dev trials "
            "are exported, the holdout and the whole benchmark reach the site as aggregates "
            "only (ADR-0004)"
        )
    out: list[tuple[Trial, Path, str | None]] = []
    for trial in sorted(trials, key=lambda t: t.dir.name):
        events = last_event_file(trial)
        if events is None:
            raise Refused(f"the trial {trial.dir} has no event file")
        config = version_header(events).get("config")
        out.append((trial, events, config if isinstance(config, str) else None))
    return out


def check_ids(ids: list[str], args: argparse.Namespace) -> None:
    """Refused, with a count only, when `evals scrub`'s rules would change a run id: scrubbing
    rewrites the files, not their paths, so `runs/<id>/` would keep the hostname, IP or user name
    the notes lose, and the site would not find the run. The id is not printed; it may be the
    very value to hide. Not renamed automatically: the run's directory is renamed by hand."""
    rules = scrub.build_rules(args.hostname, args.home, args.user)
    changed = 0
    for run_id in ids:
        try:
            scrubbed = scrub.scrub_string(run_id, rules, Counter())
        except scrub.Unchecked:
            scrubbed = None  # a `\uXXXX` escape hides a name: refused, not exported unchecked
        if scrubbed != run_id:
            changed += 1
    if changed:
        raise Refused(
            f"{changed} run id(s) hold a value scrubbing removes (a hostname, IP, home or user "
            "name); rename the run or trial directory and export again"
        )


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def build(args: argparse.Namespace, stage: Path) -> int:
    """Write the export into `stage`; the number of runs."""
    dev = exportable(args.manifest)
    given = check_featured(args.runs, dev)
    given_conversations = check_conversations(args.conversations, dev)
    trials = load_dev_trials(args.job, dev)
    runs: list[tuple[dict[str, Any], Path]] = [
        featured_run(r, g, i) for i, (r, g) in enumerate(zip(args.runs, given, strict=True), 1)
    ]
    runs += [(trial_notes(t, events, args.tasks_dir), events) for t, events, _ in trials]
    conversations: list[dict[str, Any]] = []
    turns: list[tuple[dict[str, Any], Path]] = []
    for i, (c, g) in enumerate(zip(args.conversations, given_conversations, strict=True), 1):
        entry, turn_runs = conversation(c, g, i)
        conversations.append(entry)
        turns += turn_runs
    ids = [notes["id"] for notes, _ in runs + turns]
    if len(set(ids)) < len(ids):
        raise Refused(f"{len(ids) - len(set(ids))} run id(s) repeat; each run needs its own id")
    check_ids(ids, args)
    for notes, events in runs + turns:
        run_dir = stage / "runs" / notes["id"]
        run_dir.mkdir(parents=True)
        shutil.copyfile(events, run_dir / "events.jsonl")
        write_json(run_dir / "meta.json", notes)
    index: dict[str, Any] = {"runs": [notes for notes, _ in runs]}
    if conversations:
        index["conversations"] = conversations
    write_json(stage / "index.json", index)
    evaluation = {
        "dev": dev_summary([(t, config) for t, _, config in trials]),
        "data_eng_bench": board_summary(args.board) if args.board else None,
        "dab": dab_summary(args.dab) if args.dab else None,
    }
    write_json(stage / "eval.json", evaluation)
    return len(runs) + len(turns)


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
        except (Refused, OSError) as exc:
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
        stage.chmod(0o755)  # mkdtemp's 0700 would follow the directory to the site
        stage.rename(out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(f"export: {n} run(s), {len(args.conversations)} conversation(s) -> {out}")
    return 0
