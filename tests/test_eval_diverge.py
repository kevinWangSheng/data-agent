"""`python -m evals diverge`: per-task process metrics and the paired divergence of two jobs on
the dev set (ticket 28). Holdout tasks are only counted, never named (ADR-0004)."""

import json
from pathlib import Path
from typing import Any

import pytest

import evals.report as report_module
from evals.__main__ import main as evals_main

MANIFEST = {"seed": 0, "dev": ["dbt-alpha", "dbt-bravo", "dbt-charlie"], "holdout": ["dbt-delta"]}


def edit(path: str, tool: str = "Write") -> dict[str, Any]:
    return {"type": "tool_start", "tool": tool, "input": {"file_path": path}}


def verifier(*statuses: str) -> list[dict[str, Any]]:
    checks = [{"model": "m", "check": f"CHECK {i}", "status": s} for i, s in enumerate(statuses)]
    return [
        {"type": "verifier_start", "verifier": "value-verifier"},
        {"type": "verifier_end", "verifier": "value-verifier", "checks": checks},
    ]


def result(turns: int, cost: float, subtype: str = "success", reason: str = "completed") -> dict:
    return {
        "type": "result",
        "subtype": subtype,
        "num_turns": turns,
        "terminal_reason": reason,
        "cost_usd": cost,
    }


def write_trial(job: Path, task: str, attempt: int, reward: float, events: list[dict]) -> None:
    trial = job / f"{task}__t{attempt}"
    (trial / "agent" / "events").mkdir(parents=True)
    lines = [{"type": "version", "config": job.name}, *events]
    text = "".join(json.dumps({"seq": i, **e}) + "\n" for i, e in enumerate(lines))
    (trial / "agent" / "events" / "run-1.jsonl").write_text(text)
    outcome = {
        "task_name": f"org/{task}",
        "trial_name": trial.name,
        "verifier_result": {"rewards": {"reward": reward}},
        "agent_result": {"cost_usd": 0.0},
    }
    (trial / "result.json").write_text(json.dumps(outcome))


def diverge(tmp_path: Path, baseline: Path, candidate: Path) -> tuple[int, dict[str, Any], str]:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(MANIFEST))
    out = tmp_path / "diverge"
    argv = ["diverge", "--baseline", str(baseline), "--candidate", str(candidate)]
    code = evals_main([*argv, "--manifest", str(manifest), "--out", str(out)])
    report = json.loads((out / "diverge.json").read_text()) if code == 0 else {}
    return code, report, (out / "diverge.md").read_text() if code == 0 else ""


@pytest.fixture
def jobs(tmp_path: Path) -> tuple[Path, Path]:
    baseline, full = tmp_path / "baseline", tmp_path / "full"
    # alpha: both pass both trials. bravo: baseline 2/2, full 0/2. charlie: baseline 0/2, full 1/2.
    for attempt in range(2):
        write_trial(baseline, "dbt-alpha", attempt, 1.0, [result(10, 0.10)])
        write_trial(baseline, "dbt-bravo", attempt, 1.0, [result(20, 0.30)])
        write_trial(baseline, "dbt-charlie", attempt, 0.0, [result(30, 0.50)])
    write_trial(
        full,
        "dbt-alpha",
        0,
        1.0,
        [
            edit("/app/models/a.sql"),
            edit("/app/models/a.sql", "Edit"),
            *verifier("PASS", "PASS"),
            result(12, 0.20),
        ],
    )
    write_trial(full, "dbt-alpha", 1, 1.0, [edit("/app/models/marts/b.sql"), result(14, 0.40)])
    # Stopped by the spend cap, after a verifier that found a failing check.
    write_trial(
        full,
        "dbt-bravo",
        0,
        0.0,
        [*verifier("PASS", "FAIL"), result(40, 1.00, "error_max_budget_usd", "budget_exhausted")],
    )
    write_trial(full, "dbt-bravo", 1, 0.0, [result(50, 0.50, "error_max_turns", "max_turns")])
    write_trial(full, "dbt-charlie", 0, 1.0, [edit("/app/README.md"), result(16, 0.60)])
    write_trial(full, "dbt-charlie", 1, 0.0, [result(18, 0.20)])
    return baseline, full


def test_paired_divergence_lists_the_tasks_where_successes_differ(
    tmp_path: Path, jobs: tuple[Path, Path]
) -> None:
    code, report, markdown = diverge(tmp_path, *jobs)
    assert code == 0
    assert report["labels"] == {"baseline": "baseline", "candidate": "full"}
    assert report["divergent"] == [
        {"task": "dbt-bravo", "baseline": [2, 2], "candidate": [0, 2], "better": "baseline"},
        {"task": "dbt-charlie", "baseline": [0, 2], "candidate": [1, 2], "better": "candidate"},
    ]
    assert report["wins"] == {"baseline": 1, "candidate": 1}
    assert "| dbt-bravo | 2/2 | 0/2 | baseline |" in markdown
    assert "dbt-alpha" in markdown  # in the process-metrics table


def test_process_metrics_per_task_come_from_the_event_files(
    tmp_path: Path, jobs: tuple[Path, Path]
) -> None:
    _, report, _ = diverge(tmp_path, *jobs)
    full = report["candidate"]
    assert full["per_task"]["dbt-alpha"] == {
        "successes": 2,
        "trials": 2,
        "mean_turns": 13.0,
        "stopped_by_budget": 0,
        "stopped_by_turns": 0,
        "verifier_calls": 1,
        "verifier_checks": {"PASS": 2},
        "model_edits": 3,
        "cost_usd": pytest.approx(0.60),
    }
    bravo = full["per_task"]["dbt-bravo"]
    assert (bravo["stopped_by_budget"], bravo["stopped_by_turns"]) == (1, 1)
    assert bravo["verifier_checks"] == {"PASS": 1, "FAIL": 1}
    assert full["per_task"]["dbt-charlie"]["model_edits"] == 0  # README.md is not a dbt model
    # Mean cost of the successful trials: alpha 0.20 and 0.40, charlie 0.60.
    assert full["mean_cost_usd_succeeded"] == pytest.approx(0.40)
    assert report["baseline"]["mean_cost_usd_succeeded"] == pytest.approx(0.20)
    trial = next(t for t in full["trials"] if t["trial"] == "dbt-bravo__t0")
    assert trial["stopped"] == "budget"
    assert Path(trial["events"]).name == "run-1.jsonl"


def test_holdout_tasks_are_counted_but_never_named(tmp_path: Path, jobs: tuple[Path, Path]) -> None:
    baseline, full = jobs
    for job in jobs:
        write_trial(job, "dbt-delta", 0, 1.0 if job == full else 0.0, [result(5, 0.1)])
    code, report, markdown = diverge(tmp_path, baseline, full)
    assert code == 0
    assert report["outside_dev_trials"] == {"baseline": 1, "candidate": 1}
    everything = json.dumps(report) + markdown
    assert "dbt-delta" not in everything


def test_jobs_without_dev_trials_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    for job in (a, b):
        write_trial(job, "dbt-delta", 0, 1.0, [result(5, 0.1)])
    code, _, _ = diverge(tmp_path, a, b)
    captured = capsys.readouterr()
    assert code == 2
    assert "dev" in captured.err
    assert "dbt-delta" not in captured.out + captured.err
    assert not (tmp_path / "diverge").exists()


def test_a_manifest_listing_a_task_in_both_splits_is_refused(
    tmp_path: Path, jobs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The old practice of a temporary manifest that moves holdout tasks into `dev` would print
    per-task holdout rows; a dev list that overlaps the holdout is refused."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"dev": ["dbt-alpha", "dbt-delta"], "holdout": ["dbt-delta"]}))
    argv = ["diverge", "--baseline", str(jobs[0]), "--candidate", str(jobs[1])]
    out = tmp_path / "diverge"
    assert evals_main([*argv, "--manifest", str(manifest), "--out", str(out)]) == 2
    assert "dbt-delta" not in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("real", [{"dev": [], "holdout": ["dbt-charlie"]}, None])
def test_a_dev_list_the_repos_manifest_holds_out_from_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    jobs: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    real: dict[str, list[str]] | None,
) -> None:
    """The given manifest may list a holdout task under dev and hold out nothing (or something
    else); the repo's evals/manifest.json (on the server, the real one) decides. Unreadable:
    refused."""
    repo_manifest = tmp_path / "repo-manifest.json"
    if real is not None:
        repo_manifest.write_text(json.dumps(real))
    monkeypatch.setattr(report_module, "REPO_MANIFEST", repo_manifest)
    code, _, _ = diverge(tmp_path, *jobs)
    assert code == 2
    err = capsys.readouterr().err
    assert str(repo_manifest) in err
    assert "dbt-charlie" not in err
    assert not (tmp_path / "diverge").exists()


def test_a_dev_list_the_real_manifest_holds_out_from_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end against the repo's own evals/manifest.json; the task id is never printed."""
    real = Path(report_module.__file__).parent / "manifest.json"
    held_out = json.loads(real.read_text())["holdout"][0]
    baseline, full = tmp_path / "baseline", tmp_path / "full"
    write_trial(baseline, held_out, 0, 1.0, [result(10, 0.10)])
    write_trial(full, held_out, 0, 0.0, [result(10, 0.10)])
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"dev": [held_out], "holdout": []}))
    out = tmp_path / "diverge"
    argv = ["diverge", "--baseline", str(baseline), "--candidate", str(full)]
    assert evals_main([*argv, "--manifest", str(manifest), "--out", str(out)]) == 2
    captured = capsys.readouterr()
    assert held_out not in captured.out + captured.err
    assert not out.exists()


def test_only_the_last_event_file_counts(tmp_path: Path, jobs: tuple[Path, Path]) -> None:
    """The Harbor adapter scores and reports a trial by its last event file (a trial runs
    `data-agent run` once); an older file left in the directory is not part of the result."""
    baseline, full = jobs
    old = full / "dbt-alpha__t0" / "agent" / "events" / "run-0.jsonl"
    old.write_text(json.dumps({"seq": 0, **result(99, 9.0, "error_max_turns")}) + "\n")
    _, report, _ = diverge(tmp_path, baseline, full)
    trial = next(t for t in report["candidate"]["trials"] if t["trial"] == "dbt-alpha__t0")
    assert (trial["turns"], trial["stopped"]) == (12, None)
    assert Path(trial["events"]).name == "run-1.jsonl"


def test_unequal_trial_counts_are_refused(
    tmp_path: Path, jobs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    baseline, full = jobs
    write_trial(full, "dbt-charlie", 2, 1.0, [result(10, 0.1)])
    code, _, _ = diverge(tmp_path, baseline, full)
    assert code == 2
    assert "dbt-charlie" in capsys.readouterr().err


def test_infrastructure_errors_are_refused(
    tmp_path: Path, jobs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    baseline, full = jobs
    path = full / "dbt-bravo__t1" / "result.json"
    outcome = json.loads(path.read_text())
    outcome["verifier_result"] = None
    outcome["exception_info"] = {"exception_type": "ApiInternalServerError"}
    path.write_text(json.dumps(outcome))
    code, _, _ = diverge(tmp_path, baseline, full)
    assert code == 2
    assert "infrastructure" in capsys.readouterr().err


def test_a_line_cut_short_by_a_killed_run_is_skipped(
    tmp_path: Path, jobs: tuple[Path, Path]
) -> None:
    """A run killed mid-write leaves half a line at the end of its event file; the Harbor adapter
    skips it (`evals.harbor_agent.parse_events`), and so do the process metrics."""
    baseline, full = jobs
    events = full / "dbt-alpha__t0" / "agent" / "events" / "run-1.jsonl"
    with events.open("a") as f:
        f.write('{"seq": 99, "type": "tool_st')
    code, report, _ = diverge(tmp_path, baseline, full)
    assert code == 0
    trial = next(t for t in report["candidate"]["trials"] if t["trial"] == "dbt-alpha__t0")
    assert (trial["turns"], trial["model_edits"], trial["verifier_calls"]) == (12, 2, 1)


def test_a_line_cut_inside_a_multibyte_character_is_skipped(
    tmp_path: Path, jobs: tuple[Path, Path]
) -> None:
    baseline, full = jobs
    events = full / "dbt-alpha__t0" / "agent" / "events" / "run-1.jsonl"
    with events.open("ab") as f:
        f.write('{"seq": 99, "type": "text", "text": "数'.encode()[:-1])
    code, report, _ = diverge(tmp_path, baseline, full)
    assert code == 0
    trial = next(t for t in report["candidate"]["trials"] if t["trial"] == "dbt-alpha__t0")
    assert trial["turns"] == 12
