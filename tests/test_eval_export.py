"""`python -m evals export` beyond the acceptance tests (ticket 80): the evaluation page's summary
(`eval.json`), the notes of trials without a `task` event, and what the command refuses."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from evals.export import featured_allowed
from evals.harbor import load_trials
from evals.report import score

REPO_ROOT = Path(__file__).resolve().parents[1]
NEW_FIELDS = REPO_ROOT / "tests" / "fixtures" / "web" / "new-fields" / "events.jsonl"
# A featured run's notes.json on local data, which the holdout check lets through.
LOCAL_NOTES = {"title": "t", "failure_reason": None, "local_data": True}


def export(*args: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("DATA_AGENT_")}
    return subprocess.run(
        [sys.executable, "-m", "evals", "export", *args],
        cwd=REPO_ROOT,
        env=clean,
        capture_output=True,
        text=True,
        timeout=300,
    )


def events(config: str, task: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in NEW_FIELDS.read_text().splitlines():
        event = json.loads(line)
        if event["type"] == "version":
            event["config"] = config
        if event["type"] == "task":
            if task is None:
                continue  # an event file from before the `task` event
            event["task"], event["data_source"] = f"Solve {task}.", f"{task}.duckdb"
        out.append(event)
    return out


def write_trial(
    job: Path,
    task: str,
    suffix: str,
    reward: float,
    config: str = "full",
    started: str = "2026-11-07T21:57:00Z",
    with_task_event: bool = True,
) -> str:
    name = f"{task}__{suffix}"
    trial = job / name
    (trial / "agent" / "events").mkdir(parents=True)
    result = {
        "task_name": f"org/{task}",
        "trial_name": name,
        "verifier_result": {"rewards": {"reward": reward}},
        "exception_info": None,
        "agent_result": {"cost_usd": 0.25},
        "started_at": started,
        "finished_at": started.replace(":00Z", ":30Z"),
    }
    (trial / "result.json").write_text(json.dumps(result))
    lines = events(config, task if with_task_event else None)
    (trial / "agent" / "events" / "run-20261107T215700Z-abc.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in lines)
    )
    return name


def manifest(tmp_path: Path, dev: list[str], holdout: list[str] | None = None) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"seed": 0, "dev": dev, "holdout": holdout or []}))
    return path


def read(path: Path) -> Any:
    return json.loads(path.read_text())


def test_eval_summary_scores_each_config_and_lists_every_trial(tmp_path: Path) -> None:
    jobs = {"full": tmp_path / "full", "baseline": tmp_path / "baseline"}
    rewards = {
        "full": {"a": [1, 1, 0], "b": [1, 0, 0]},
        "baseline": {"a": [1, 0, 0], "b": [0, 0, 0]},
    }
    for config, job in jobs.items():
        for task, rs in rewards[config].items():
            for i, r in enumerate(rs):
                start = f"2026-11-07T2{i}:00:00Z"
                write_trial(job, task, f"{config}{i}", float(r), config=config, started=start)
    out = tmp_path / "site"
    proc = export(
        "--manifest",
        str(manifest(tmp_path, ["a", "b"])),
        "--out",
        str(out),
        *("--job", str(jobs["full"]), "--job", str(jobs["baseline"])),
    )
    assert proc.returncode == 0, proc.stderr

    dev = read(out / "eval.json")["dev"]
    by_config = {c["config"]: c for c in dev["configs"]}
    assert set(by_config) == {"full", "baseline"}
    for config, job in jobs.items():
        expected = score(load_trials(job))
        assert by_config[config]["accuracy"] == expected["accuracy"]
        assert by_config[config]["accuracy_stderr"] == expected["accuracy_stderr"]
        assert by_config[config]["n_tasks"] == 2
        assert by_config[config]["n_trials"] == 6
    [contribution] = dev["contributions"]
    assert contribution["config"] == "baseline"
    assert contribution["layer"] == "all layers"
    assert contribution["diff"] == pytest.approx(100 / 3)  # full minus baseline, per task
    assert set(contribution) >= {"ci_low", "ci_high", "significant"}

    rows = dev["trials"]
    assert len(rows) == 12
    assert {r["id"] for r in rows} == {e["id"] for e in read(out / "index.json")["runs"]}
    row = next(r for r in rows if r["id"] == "a__full2")
    assert row == {
        "id": "a__full2",
        "task": "a",
        "config": "full",
        "attempt": 3,
        "passed": False,
        "reward": 0.0,
        "cost_usd": 0.25,
        "duration_seconds": 30.0,
    }


def test_leaderboard_reports_contribute_aggregates_only(tmp_path: Path) -> None:
    board = tmp_path / "board.json"
    board.write_text(
        json.dumps(
            {
                "complete": True,
                "n_tasks": 103,
                "trials_per_task": 3,
                "leaderboard": {
                    "accuracy": 64.1,
                    "accuracy_stderr": 1.6,
                    "pass_at_2": 0.7,
                    "pass_at_3": 0.75,
                    "n_trials": 309,
                    "n_tasks": 103,
                    "n_succeeded": 198,
                    "cost_usd": 30.3,
                },
                "holdout": {"n_tasks": 30, "n_trials": 90, "n_succeeded": 61, "accuracy": 67.8},
                "per_task": [{"task": "board-task-detail", "successes": 0, "trials": 3}],
                "run": {"config": "no-verifiers", "model": "m"},
            }
        )
    )
    dab = tmp_path / "dab_score.json"
    dab.write_text(
        json.dumps(
            {
                name: {
                    "out": "/somewhere/runs/dab",
                    "pass_at_1": rate,
                    "coverage": {"queries": 2, "clone_queries": 2},
                    "datasets": {
                        "yelp": {
                            "pass_at_1": rate,
                            "queries": {
                                "1": {"passed": 1, "runs": 1},
                                "2": {"passed": 0, "runs": 1},
                            },
                        }
                    },
                    "missing": [{"dataset": "yelp", "query": "dab-query-detail", "run": 0}],
                }
                for name, rate in (("baseline", 0.5), ("candidate", 0.5))
            }
            | {"comparison": {"diff": 0.0, "ci_low": 0.0, "ci_high": 0.0, "significant": False}}
        )
    )
    out = tmp_path / "site"
    proc = export(
        *("--manifest", str(manifest(tmp_path, []))),
        *("--out", str(out), "--board", str(board), "--dab", str(dab)),
    )
    assert proc.returncode == 0, proc.stderr

    summary = read(out / "eval.json")
    deb = summary["data_eng_bench"]
    assert deb["leaderboard"]["accuracy"] == 64.1
    assert deb["leaderboard"]["accuracy_stderr"] == 1.6
    assert deb["leaderboard"]["n_tasks"] == 103
    assert deb["holdout"] == {"n_tasks": 30, "n_trials": 90, "n_succeeded": 61, "accuracy": 67.8}
    assert deb["config"] == "no-verifiers"
    assert summary["dab"]["candidate"]["pass_at_1"] == 0.5
    assert summary["dab"]["candidate"]["datasets"]["yelp"] == {
        "pass_at_1": 0.5,
        "queries": 2,
        "runs": 2,
        "passed": 1,
    }
    assert summary["dab"]["comparison"]["significant"] is False
    text = (out / "eval.json").read_text()
    for detail in ("board-task-detail", "dab-query-detail", "/somewhere"):
        assert detail not in text


def test_without_report_inputs_the_summary_is_empty(tmp_path: Path) -> None:
    out = tmp_path / "site"
    proc = export("--manifest", str(manifest(tmp_path, [])), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    assert read(out / "eval.json") == {
        "dev": {"reference": "full", "configs": [], "contributions": [], "trials": []},
        "data_eng_bench": None,
        "dab": None,
    }
    assert read(out / "index.json") == {"runs": []}


def test_a_trial_without_a_task_event_takes_its_task_from_the_tasks_dir(tmp_path: Path) -> None:
    job = tmp_path / "job"
    name = write_trial(job, "a", "x", 1.0, with_task_event=False)
    tasks = tmp_path / "tasks"
    (tasks / "a").mkdir(parents=True)
    (tasks / "a" / "instruction.md").write_text("# Build model a\n")
    m = manifest(tmp_path, ["a"])

    proc = export("--manifest", str(m), "--out", str(tmp_path / "s1"), "--job", str(job))
    assert proc.returncode == 0, proc.stderr
    notes = read(tmp_path / "s1" / "runs" / name / "meta.json")
    assert notes["task"] == "a"  # no tasks dir: the task id
    assert notes["task_type"] == "dbt"  # the Harbor adapter runs every trial as dbt
    assert notes["data_source"] is None
    assert notes["title"] == "a"
    assert notes["grade"]["passed"] is True
    assert notes["failure_reason"] is None

    proc = export(
        *("--manifest", str(m), "--out", str(tmp_path / "s2")),
        *("--job", str(job), "--tasks-dir", str(tasks)),
    )
    assert proc.returncode == 0, proc.stderr
    assert read(tmp_path / "s2" / "runs" / name / "meta.json")["task"] == "# Build model a\n"


def test_a_featured_run_without_a_task_event_takes_its_task_from_its_notes(
    tmp_path: Path,
) -> None:
    run = tmp_path / "old"
    run.mkdir()
    old = [line for line in NEW_FIELDS.read_text().splitlines() if '"type": "task"' not in line]
    (run / "run-1.jsonl").write_text("\n".join(old) + "\n")
    given = {"task": "Which tier?", "task_type": "query", "data_source": "shop"}
    (run / "notes.json").write_text(json.dumps({**LOCAL_NOTES, **given}))
    out = tmp_path / "site"
    proc = export("--manifest", str(manifest(tmp_path, [])), "--out", str(out), "--run", str(run))
    assert proc.returncode == 0, proc.stderr
    notes = read(out / "runs" / "old" / "meta.json")
    assert {k: notes[k] for k in given} == given


def test_a_featured_runs_task_event_wins_over_its_notes(tmp_path: Path) -> None:
    run = tmp_path / "new"
    run.mkdir()
    (run / "run-1.jsonl").write_text(NEW_FIELDS.read_text())
    stale = {"task": "stale", "task_type": "query", "data_source": "stale"}
    (run / "notes.json").write_text(json.dumps({**LOCAL_NOTES, **stale}))
    out = tmp_path / "site"
    proc = export("--manifest", str(manifest(tmp_path, [])), "--out", str(out), "--run", str(run))
    assert proc.returncode == 0, proc.stderr
    notes = read(out / "runs" / "new" / "meta.json")
    assert (notes["task_type"], notes["data_source"]) == ("dbt", "shop")


def test_a_featured_runs_grade_comes_from_its_notes(tmp_path: Path) -> None:
    run = tmp_path / "r1"
    run.mkdir()
    (run / "run-1.jsonl").write_text(NEW_FIELDS.read_text())
    grade = {"passed": False, "detail": "14 of 15 components passed"}
    (run / "notes.json").write_text(
        json.dumps({"title": "t", "failure_reason": "f", "grade": grade, "local_data": True})
    )
    out = tmp_path / "site"
    proc = export("--manifest", str(manifest(tmp_path, [])), "--out", str(out), "--run", str(run))
    assert proc.returncode == 0, proc.stderr
    notes = read(out / "runs" / "r1" / "meta.json")
    assert notes["grade"] == grade
    assert notes["failure_reason"] == "f"


def test_a_featured_run_without_notes_is_refused(tmp_path: Path) -> None:
    run = tmp_path / "r1"
    run.mkdir()
    (run / "run-1.jsonl").write_text(NEW_FIELDS.read_text())
    out = tmp_path / "site"
    proc = export("--manifest", str(manifest(tmp_path, [])), "--out", str(out), "--run", str(run))
    assert proc.returncode != 0
    assert "notes.json" in proc.stderr
    assert not out.exists()


def test_a_task_the_repo_manifest_holds_out_is_refused(tmp_path: Path) -> None:
    held_out = read(REPO_ROOT / "evals" / "manifest.json")["holdout"][0]
    job = tmp_path / "job"
    write_trial(job, held_out, "x", 0.0)
    out = tmp_path / "site"
    # A temporary manifest that holds out nothing does not let it through.
    proc = export(
        "--manifest", str(manifest(tmp_path, [held_out])), "--out", str(out), "--job", str(job)
    )
    assert proc.returncode != 0
    assert held_out not in proc.stdout + proc.stderr
    assert not out.exists()


def test_an_output_directory_with_files_is_left_alone(tmp_path: Path) -> None:
    out = tmp_path / "site"
    out.mkdir()
    (out / "keep.txt").write_text("mine")
    proc = export("--manifest", str(manifest(tmp_path, [])), "--out", str(out))
    assert proc.returncode != 0
    assert sorted(p.name for p in out.iterdir()) == ["keep.txt"]


def test_two_runs_with_one_id_are_refused(tmp_path: Path) -> None:
    jobs = [tmp_path / "j1", tmp_path / "j2"]
    for job in jobs:
        write_trial(job, "a", "same", 1.0)
    out = tmp_path / "site"
    proc = export(
        *("--manifest", str(manifest(tmp_path, ["a"])), "--out", str(out)),
        *("--job", str(jobs[0]), "--job", str(jobs[1])),
    )
    assert proc.returncode != 0
    assert not out.exists()


def test_a_trial_of_a_task_outside_the_dev_set_is_refused(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "dev-task", "x", 1.0)
    write_trial(job, "other-task", "y", 0.0)
    out = tmp_path / "site"
    proc = export(
        *("--manifest", str(manifest(tmp_path, ["dev-task"])), "--out", str(out)),
        *("--job", str(job)),
    )
    assert proc.returncode == 2
    assert "other-task" not in proc.stdout + proc.stderr
    assert not out.exists()


def test_a_line_separator_inside_a_string_keeps_the_task_event(tmp_path: Path) -> None:
    run = tmp_path / "r1"
    run.mkdir()
    task = {"type": "task", "task": "line one\u2028line two", "task_type": "query"}
    (run / "run-1.jsonl").write_text(json.dumps(task, ensure_ascii=False) + "\n")
    (run / "notes.json").write_text(json.dumps(LOCAL_NOTES))
    out = tmp_path / "site"
    proc = export("--manifest", str(manifest(tmp_path, [])), "--out", str(out), "--run", str(run))
    assert proc.returncode == 0, proc.stderr
    assert read(out / "runs" / "r1" / "meta.json")["task"] == "line one\u2028line two"


def test_trials_are_indexed_by_name_and_the_output_is_readable(tmp_path: Path) -> None:
    jobs = [tmp_path / "j1", tmp_path / "j2"]
    write_trial(jobs[0], "a", "z", 1.0)
    write_trial(jobs[1], "a", "b", 1.0)
    out = tmp_path / "site"
    proc = export(
        *("--manifest", str(manifest(tmp_path, ["a"])), "--out", str(out)),
        *("--job", str(jobs[0]), "--job", str(jobs[1])),
    )
    assert proc.returncode == 0, proc.stderr
    assert [r["id"] for r in read(out / "index.json")["runs"]] == ["a__b", "a__z"]
    assert out.stat().st_mode & 0o777 == 0o755


def test_an_event_file_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    run = tmp_path / "r1"
    run.mkdir()
    (run / "run-1.jsonl").write_bytes(b'{"type": "text", "text": "\xff"}\n')
    (run / "notes.json").write_text(json.dumps(LOCAL_NOTES))
    out = tmp_path / "site"
    proc = export("--manifest", str(manifest(tmp_path, [])), "--out", str(out), "--run", str(run))
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert not out.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["manifest.json", "r1"]  # no staging left


# --- Featured runs and the holdout ------------------------------------------------------------

DEV = {"dbt-alpha"}  # what `exportable` leaves of a manifest with dev [alpha], holdout [delta]


@pytest.mark.parametrize(
    ("given", "allowed"),
    [
        ({"task_id": "dbt-alpha"}, True),  # a dev task
        ({"task_id": "dbt-delta"}, False),  # a holdout task
        ({"task_id": "dbt-omega"}, False),  # a task the manifest does not list
        ({"local_data": True}, True),
        ({}, False),  # neither: not taken as local
        ({"task_id": "dbt-delta", "local_data": True}, False),  # local_data does not override
        ({"local_data": False}, False),
        ({"local_data": "yes"}, False),  # only the boolean true counts
        ({"local_data": 1}, False),
        ({"task_id": 7, "local_data": True}, False),  # a task id that is not a string
        ({"task_id": ["dbt-alpha"]}, False),
    ],
)
def test_a_featured_run_is_let_through_only_from_a_dev_task_or_local_data(
    given: dict[str, Any], allowed: bool
) -> None:
    assert featured_allowed({"title": "t", **given}, DEV) is allowed


def test_a_refused_featured_run_is_reported_as_a_count_only(tmp_path: Path) -> None:
    run = tmp_path / "secret-task-run"
    run.mkdir()
    (run / "run-1.jsonl").write_text(NEW_FIELDS.read_text())
    notes = {"title": "t", "failure_reason": "secret-reason", "local_data": "yes"}
    (run / "notes.json").write_text(json.dumps(notes))
    unreadable = tmp_path / "other-task-run"
    unreadable.mkdir()
    (unreadable / "run-1.jsonl").write_text(NEW_FIELDS.read_text())
    out = tmp_path / "site"
    proc = export(
        *("--manifest", str(manifest(tmp_path, ["dbt-alpha"])), "--out", str(out)),
        *("--run", str(run), "--run", str(unreadable)),
    )
    assert proc.returncode == 2
    printed = proc.stdout + proc.stderr
    for hidden in ("secret-task-run", "other-task-run", "secret-reason", str(tmp_path)):
        assert hidden not in printed
    assert "2 featured run(s)" in printed
    assert not out.exists()


@pytest.mark.parametrize(
    "broken",
    ["no event file", "two event files", "no title", "bad grade", "not utf-8"],
)
def test_a_malformed_featured_run_is_named_by_position_not_by_its_directory(
    tmp_path: Path, broken: str
) -> None:
    good = tmp_path / "good-run"
    good.mkdir()
    (good / "run-1.jsonl").write_text(NEW_FIELDS.read_text())
    (good / "notes.json").write_text(json.dumps(LOCAL_NOTES))
    run = tmp_path / "secret-task-run"
    run.mkdir()
    notes: dict[str, Any] = dict(LOCAL_NOTES)
    if broken != "no event file":
        (run / "run-1.jsonl").write_text(NEW_FIELDS.read_text())
    if broken == "two event files":
        (run / "run-2.jsonl").write_text(NEW_FIELDS.read_text())
    if broken == "no title":
        del notes["title"]
    if broken == "bad grade":
        notes["grade"] = {"passed": "yes"}
    if broken == "not utf-8":
        (run / "run-1.jsonl").write_bytes(b'{"type": "text", "text": "\xff"}\n')
    (run / "notes.json").write_text(json.dumps(notes))
    out = tmp_path / "site"
    proc = export(
        *("--manifest", str(manifest(tmp_path, [])), "--out", str(out)),
        *("--run", str(good), "--run", str(run)),
    )
    assert proc.returncode == 2
    printed = proc.stdout + proc.stderr
    assert "secret-task-run" not in printed and str(tmp_path) not in printed
    assert "--run #2" in printed
    assert "Traceback" not in printed
    assert not out.exists()
