"""Seam 3 of the web UI (.scratch/web-ui/spec.md "Testing Decisions"): the export command that turns
chosen runs into the online site's static data, run as `python -m evals export` from the repo root.

Written by ticket 73; the featured-run holdout check added by ticket 84. Every test is
xfail(strict) until ticket 80 makes it pass and removes the marker, changing nothing else. The
manifest and job here are fakes; no real holdout data is read.

External interface fixed here:

- `export --manifest FILE --out DIR [--run RUN_DIR ...] [--job JOB_DIR ...] [--hostname NAME ...]`
  - `--run RUN_DIR`: a featured run: its event file (`*.jsonl`, as `data-agent run --events-dir`
    leaves it) and `notes.json` with the maintainer's `title` and `failure_reason` (and a `grade`
    when there is one). Its id is the directory name. `notes.json` also says where the run came
    from, with at least one of: `task_id` (the leaderboard task it ran) or `local_data: true` (a
    run on genuinely local data). A run with neither is refused, not taken as local. These two
    fields are input only; the output notes are the same as for any other run.
  - `--job JOB_DIR`: a Harbor job; every trial in it (`<trial>/result.json`, events under
    `<trial>/agent/events/`) is exported. Its id is the trial directory name.
- Output: `DIR/index.json` = `{"runs": [notes, ...]}` and per run `DIR/runs/<id>/events.jsonl` and
  `DIR/runs/<id>/meta.json` (the same notes). Notes fields: `id`, `title`, `task`, `task_type`,
  `data_source`, `grade` (`{"passed", "detail"}` or null), `failure_reason`, `featured`
  (web/src/run-view/model.ts, `RunNotes`). Other files (the evaluation page's summary) are
  ticket 80's to define.
- Every output file is scrubbed with the `evals scrub` rules; nothing turns that off.
- A trial of a task in the manifest's holdout is refused: non-zero exit, nothing exported, and no
  failure detail of that trial printed (ADR-0004).
- A featured run with a `task_id` gets the same check as a trial: only a task in the manifest's
  dev set is let through; a holdout task, or one the manifest does not list, is refused. On any
  refusal (this one or a run with neither field) the exit is non-zero, nothing is exported, and
  stdout and stderr give only counts: no task id, failure reason or result.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from seam_support import FIXTURES, REPO_ROOT

pytestmark = pytest.mark.xfail(strict=True, reason="ticket 80: the export command")

NEW_FIELDS = FIXTURES / "web" / "new-fields" / "events.jsonl"
NOTES_FIELDS = {
    "id",
    "title",
    "task",
    "task_type",
    "data_source",
    "grade",
    "failure_reason",
    "featured",
}

# Key-shaped values are assembled here so the repository never contains a literal one.
FAKE_KEY = "sk-" + "ab12" * 8
ENDPOINT_HOST = "198.51.100.9"
ENDPOINT = f"http://{ENDPOINT_HOST}:8080/v1"
STRAY_IP = "203.0.113.7"
HOSTNAME = "build-box-7"


def export(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("DATA_AGENT_")}
    return subprocess.run(
        [sys.executable, "-m", "evals", "export", *args],
        cwd=REPO_ROOT,
        env={**clean, **env},
        capture_output=True,
        text=True,
        timeout=300,
    )


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def task_events(task: str) -> list[dict[str, Any]]:
    """The new-fields run, retold as a run of `task`."""
    events = read_events(NEW_FIELDS)
    for event in events:
        if event["type"] == "task":
            event["task"] = f"Solve {task}."
            event["data_source"] = f"{task}.duckdb"
    return events


def write_events(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


def write_featured(
    root: Path, run_id: str, notes: dict[str, Any], events: list[dict[str, Any]]
) -> Path:
    run = root / run_id
    write_events(run / "run-20261115T090000Z-abc123.jsonl", events)
    (run / "notes.json").write_text(json.dumps(notes))
    return run


def write_trial(job: Path, task: str, suffix: str, reward: float, **result: Any) -> str:
    name = f"{task}__{suffix}"
    trial = job / name
    trial.mkdir(parents=True)
    body = {
        "task_name": task,
        "trial_name": name,
        "verifier_result": {"rewards": {"reward": reward}},
        "exception_info": None,
        **result,
    }
    (trial / "result.json").write_text(json.dumps(body))
    write_events(
        trial / "agent" / "events" / "run-20261115T090000Z-def456.jsonl", task_events(task)
    )
    return name


def write_manifest(path: Path, dev: list[str], holdout: list[str]) -> Path:
    path.write_text(json.dumps({"seed": 0, "dev": dev, "holdout": holdout}))
    return path


def all_text(root: Path) -> str:
    return "\n".join(p.read_text() for p in sorted(root.rglob("*")) if p.is_file())


# --- Index and run notes -------------------------------------------------------------------


def test_every_run_is_exported_with_complete_notes(tmp_path: Path) -> None:
    featured = write_featured(
        tmp_path / "featured",
        "orders-mart",
        {"title": "dbt: orders mart", "failure_reason": None, "local_data": True},
        read_events(NEW_FIELDS),
    )
    job = tmp_path / "job"
    passed = write_trial(job, "dbt-alpha", "Lp8vR2c", 1.0)
    failed = write_trial(job, "dbt-alpha", "Qm3xT9a", 0.0)
    manifest = write_manifest(tmp_path / "manifest.json", ["dbt-alpha"], ["dbt-delta"])
    out = tmp_path / "site"

    proc = export(
        "--manifest", str(manifest), "--out", str(out), "--run", str(featured), "--job", str(job)
    )
    assert proc.returncode == 0, proc.stderr

    index = json.loads((out / "index.json").read_text())
    notes = {entry["id"]: entry for entry in index["runs"]}
    assert set(notes) == {"orders-mart", passed, failed}
    for run_id, entry in notes.items():
        assert set(entry) >= NOTES_FIELDS, f"{run_id} lacks {NOTES_FIELDS - set(entry)}"
        assert json.loads((out / "runs" / run_id / "meta.json").read_text()) == entry
        assert read_events(out / "runs" / run_id / "events.jsonl")

    assert notes["orders-mart"]["title"] == "dbt: orders mart"
    assert notes["orders-mart"]["featured"] is True
    # Task, type and data source come from the run's `task` event.
    assert notes["orders-mart"]["task"] == "Build the orders mart from raw.orders."
    assert notes["orders-mart"]["task_type"] == "dbt"
    assert notes["orders-mart"]["data_source"] == "shop"
    assert read_events(out / "runs" / "orders-mart" / "events.jsonl") == read_events(NEW_FIELDS)

    for trial, outcome in ((passed, True), (failed, False)):
        assert notes[trial]["featured"] is False
        assert notes[trial]["grade"]["passed"] is outcome
        assert notes[trial]["task"] == "Solve dbt-alpha."
        assert notes[trial]["data_source"] == "dbt-alpha.duckdb"


# --- Scrubbing -----------------------------------------------------------------------------


def test_keys_endpoints_addresses_and_hostnames_are_scrubbed(tmp_path: Path) -> None:
    leaky = read_events(NEW_FIELDS)
    for event in leaky:
        if event["type"] == "text":
            event["text"] = (
                f"Used key {FAKE_KEY} at {ENDPOINT}; the database is at {STRAY_IP} on {HOSTNAME}."
            )
    featured = write_featured(
        tmp_path / "featured",
        "leaky",
        {
            "title": f"Run on {HOSTNAME} ({STRAY_IP})",
            "failure_reason": f"{ENDPOINT} timed out",
            "local_data": True,
        },
        leaky,
    )
    before = all_text(featured)
    manifest = write_manifest(tmp_path / "manifest.json", [], [])
    out = tmp_path / "site"

    proc = export(
        "--manifest",
        str(manifest),
        "--out",
        str(out),
        "--run",
        str(featured),
        "--hostname",
        HOSTNAME,
        DATA_AGENT_API_KEY=FAKE_KEY,
        DATA_AGENT_BASE_URL=ENDPOINT,
    )
    assert proc.returncode == 0, proc.stderr

    text = all_text(out)
    for secret in (FAKE_KEY, ENDPOINT, ENDPOINT_HOST, STRAY_IP, HOSTNAME):
        assert secret not in text, f"{secret} reached the export"
    # Still valid event lines, one per event.
    assert len(read_events(out / "runs" / "leaky" / "events.jsonl")) == len(leaky)
    # The inputs are left as they were; only the output is scrubbed.
    assert all_text(featured) == before


def test_scrubbing_cannot_be_turned_off(tmp_path: Path) -> None:
    help_text = export("--help").stdout
    options = set(re.findall(r"--[a-z][a-z-]*", help_text))
    assert "--manifest" in options
    assert not {o for o in options if re.search(r"scrub|redact|raw|unsafe|skip", o)}

    featured = write_featured(
        tmp_path / "featured",
        "r1",
        {"title": "t", "failure_reason": None, "local_data": True},
        read_events(NEW_FIELDS),
    )
    manifest = write_manifest(tmp_path / "manifest.json", [], [])
    out = tmp_path / "site"
    proc = export(
        "--manifest", str(manifest), "--out", str(out), "--run", str(featured), "--no-scrub"
    )
    assert proc.returncode != 0
    assert not (out / "index.json").exists()


# --- Holdout -------------------------------------------------------------------------------


def test_holdout_trials_are_refused(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "dbt-alpha", "Lp8vR2c", 1.0)
    manifest = write_manifest(tmp_path / "manifest.json", ["dbt-alpha"], ["dbt-delta"])
    # The dev trials alone export.
    dev_only = export(
        "--manifest", str(manifest), "--out", str(tmp_path / "dev"), "--job", str(job)
    )
    assert dev_only.returncode == 0, dev_only.stderr

    detail = "holdout-failure-detail-" + "q9z"
    write_trial(
        job,
        "dbt-delta",
        "Zk4nW7e",
        0.0,
        exception_info={"exception_type": "AgentTimeoutError", "exception_message": detail},
    )
    out = tmp_path / "site"
    proc = export("--manifest", str(manifest), "--out", str(out), "--job", str(job))
    assert proc.returncode != 0
    assert not (out / "index.json").exists()
    assert not (out / "runs").exists()
    assert detail not in proc.stdout + proc.stderr


# --- Featured runs and the holdout ---------------------------------------------------------


def write_task_run(root: Path, run_id: str, task: str, **notes: Any) -> Path:
    """A featured run of `task`, with `notes` added to its `notes.json`."""
    return write_featured(
        root,
        run_id,
        {"title": f"Featured {task}", "failure_reason": None, **notes},
        task_events(task),
    )


def write_local_run(root: Path, run_id: str) -> Path:
    return write_featured(
        root,
        run_id,
        {"title": "dbt: orders mart", "failure_reason": None, "local_data": True},
        read_events(NEW_FIELDS),
    )


# What a refused featured run must not print: its failure reason and its result.
REFUSED_REASON = "refused-featured-reason-" + "v7k"
REFUSED_RESULT = "refused-featured-grade-" + "m2w"
REFUSED_NOTES = {
    "failure_reason": REFUSED_REASON,
    "grade": {"passed": False, "detail": REFUSED_RESULT},
}


def assert_refused(proc: subprocess.CompletedProcess[str], out: Path, *hidden: str) -> None:
    assert proc.returncode != 0
    # Nothing exported: no index, no runs, no other file.
    assert not out.exists() or not any(p.is_file() for p in out.rglob("*"))
    for text in (REFUSED_REASON, REFUSED_RESULT, *hidden):
        assert text not in proc.stdout + proc.stderr, f"{text} was printed"


def test_featured_run_of_a_dev_task_is_exported(tmp_path: Path) -> None:
    featured = write_task_run(tmp_path / "featured", "dev-run", "dbt-alpha", task_id="dbt-alpha")
    manifest = write_manifest(tmp_path / "manifest.json", ["dbt-alpha"], ["dbt-delta"])
    out = tmp_path / "site"

    proc = export("--manifest", str(manifest), "--out", str(out), "--run", str(featured))
    assert proc.returncode == 0, proc.stderr

    (entry,) = json.loads((out / "index.json").read_text())["runs"]
    assert entry["id"] == "dev-run"
    assert set(entry) >= NOTES_FIELDS, f"lacks {NOTES_FIELDS - set(entry)}"
    # `task_id` is input only; the output notes are the usual ones.
    assert "task_id" not in entry
    assert entry["featured"] is True
    assert entry["task"] == "Solve dbt-alpha."
    assert json.loads((out / "runs" / "dev-run" / "meta.json").read_text()) == entry


def test_featured_run_with_local_data_is_exported(tmp_path: Path) -> None:
    featured = write_local_run(tmp_path / "featured", "local-run")
    manifest = write_manifest(tmp_path / "manifest.json", ["dbt-alpha"], ["dbt-delta"])
    out = tmp_path / "site"

    proc = export("--manifest", str(manifest), "--out", str(out), "--run", str(featured))
    assert proc.returncode == 0, proc.stderr

    (entry,) = json.loads((out / "index.json").read_text())["runs"]
    assert entry["id"] == "local-run"
    assert set(entry) >= NOTES_FIELDS, f"lacks {NOTES_FIELDS - set(entry)}"
    # `local_data` is input only; the output notes are the usual ones.
    assert "local_data" not in entry
    assert entry["featured"] is True
    assert json.loads((out / "runs" / "local-run" / "meta.json").read_text()) == entry


def test_featured_run_of_a_holdout_task_is_refused(tmp_path: Path) -> None:
    local = write_local_run(tmp_path / "featured", "local-run")
    manifest = write_manifest(tmp_path / "manifest.json", ["dbt-alpha"], ["dbt-delta"])
    # The local run alone exports.
    local_only = export(
        "--manifest", str(manifest), "--out", str(tmp_path / "local"), "--run", str(local)
    )
    assert local_only.returncode == 0, local_only.stderr

    held = write_task_run(
        tmp_path / "featured", "held-run", "dbt-delta", task_id="dbt-delta", **REFUSED_NOTES
    )
    out = tmp_path / "site"
    proc = export(
        "--manifest", str(manifest), "--out", str(out), "--run", str(local), "--run", str(held)
    )
    assert_refused(proc, out, "dbt-delta")

    # `local_data: true` does not let a run with a holdout `task_id` through.
    both = write_task_run(
        tmp_path / "featured",
        "both-run",
        "dbt-delta",
        task_id="dbt-delta",
        local_data=True,
        **REFUSED_NOTES,
    )
    out = tmp_path / "site-both"
    proc = export(
        "--manifest", str(manifest), "--out", str(out), "--run", str(local), "--run", str(both)
    )
    assert_refused(proc, out, "dbt-delta")


def test_featured_run_of_an_unlisted_task_is_refused(tmp_path: Path) -> None:
    local = write_local_run(tmp_path / "featured", "local-run")
    manifest = write_manifest(tmp_path / "manifest.json", ["dbt-alpha"], ["dbt-delta"])
    local_only = export(
        "--manifest", str(manifest), "--out", str(tmp_path / "local"), "--run", str(local)
    )
    assert local_only.returncode == 0, local_only.stderr

    unlisted = write_task_run(
        tmp_path / "featured", "unlisted-run", "dbt-omega", task_id="dbt-omega", **REFUSED_NOTES
    )
    out = tmp_path / "site"
    proc = export(
        "--manifest", str(manifest), "--out", str(out), "--run", str(local), "--run", str(unlisted)
    )
    assert_refused(proc, out, "dbt-omega")


def test_featured_run_with_neither_task_id_nor_local_data_is_refused(tmp_path: Path) -> None:
    local = write_local_run(tmp_path / "featured", "local-run")
    manifest = write_manifest(tmp_path / "manifest.json", ["dbt-alpha"], ["dbt-delta"])
    local_only = export(
        "--manifest", str(manifest), "--out", str(tmp_path / "local"), "--run", str(local)
    )
    assert local_only.returncode == 0, local_only.stderr

    unmarked = write_featured(
        tmp_path / "featured",
        "unmarked-run",
        {"title": "t", **REFUSED_NOTES},
        read_events(NEW_FIELDS),
    )
    out = tmp_path / "site"
    proc = export(
        "--manifest", str(manifest), "--out", str(out), "--run", str(local), "--run", str(unmarked)
    )
    assert_refused(proc, out)
