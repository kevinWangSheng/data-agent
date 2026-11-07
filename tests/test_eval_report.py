"""Edge cases of `python -m evals score|report` beyond the seam C acceptance tests."""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from lifecycle_events import dispatches

from evals.__main__ import main as evals_main
from evals.harbor import load_trials
from evals.report import billed_usd

REPO_ROOT = Path(__file__).resolve().parents[1]


def evals(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "evals", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def write_trial(job: Path, name: str, result: dict[str, Any]) -> None:
    (job / name).mkdir(parents=True)
    (job / name / "result.json").write_text(json.dumps({"task_name": "t1", **result}))


def report(job: Path, tmp_path: Path) -> dict[str, Any]:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"seed": 0, "dev": ["t1"], "holdout": []}))
    out = tmp_path / "report"
    proc = evals(
        "report", str(job), "--manifest", str(manifest), "--split", "dev", "--out", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads((out / "report.json").read_text())


def test_outcomes_are_disjoint(tmp_path: Path) -> None:
    job = tmp_path / "job"
    # Harbor still runs the verifier after an agent timeout, so a trial can pass and error.
    write_trial(
        job,
        "t1__a",
        {
            "verifier_result": {"rewards": {"reward": 1.0}},
            "exception_info": {"exception_type": "AgentTimeoutError"},
        },
    )
    write_trial(job, "t1__b", {"verifier_result": {"rewards": {"reward": 0.0}}})
    # Harbor does not retry a timeout: it is the agent's failure and scores 0.
    write_trial(job, "t1__c", {"exception_info": {"exception_type": "AgentTimeoutError"}})
    summary = report(job, tmp_path)
    assert (summary["n_succeeded"], summary["n_failed"]) == (1, 2)
    assert summary["accuracy"] == pytest.approx(100 / 3)


def holdout_report(
    job: Path, tmp_path: Path, holdout: list[str]
) -> subprocess.CompletedProcess[str]:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"seed": 0, "dev": [], "holdout": holdout}))
    return evals(
        *("report", str(job), "--manifest", str(manifest)),
        *("--split", "holdout", "--out", str(tmp_path / "report")),
    )


def test_report_refuses_infrastructure_errors(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "t1__a", {"verifier_result": {"rewards": {"reward": 1.0}}})
    write_trial(job, "t1__b", {"verifier_result": {"rewards": {"reward": 0.0}}})
    # Harbor retries a provider 5xx: infrastructure, to be rerun rather than scored.
    write_trial(job, "t1__c", {"exception_info": {"exception_type": "ApiInternalServerError"}})
    proc = holdout_report(job, tmp_path, ["t1"])
    assert proc.returncode != 0
    assert "1 infrastructure error" in proc.stderr
    assert "t1" not in proc.stderr
    assert not (tmp_path / "report" / "report.json").exists()


def test_dev_trials_point_at_the_event_file_harbor_keeps(tmp_path: Path) -> None:
    """The agent writes `agent/events/run-<stamp>-<id>.jsonl` (evals.harbor.event_files); the
    adapter reads the last one by name."""
    job = tmp_path / "job"
    for name in ("t1__a", "t1__b", "t1__c"):
        write_trial(job, name, {"verifier_result": {"rewards": {"reward": 1.0}}})
    events = job / "t1__a" / "agent" / "events"
    events.mkdir(parents=True)
    for name in ("run-20261020T100000Z-a.jsonl", "run-20261020T110000Z-b.jsonl"):
        (events / name).write_text("{}\n")
    report(job, tmp_path)
    rows = json.loads((tmp_path / "report" / "trials.json").read_text())
    assert [row["events"] for row in rows] == [
        str(events / "run-20261020T110000Z-b.jsonl"),
        None,
        None,
    ]


def test_dev_report_has_process_metrics_per_task(tmp_path: Path) -> None:
    """Without a second config to diverge from, a dev report still gives each task's process
    metrics (ticket 28), next to the per-task outcomes seam C fixes."""
    job = tmp_path / "job"
    for name, reward, cost in (("t1__a", 1.0, 0.2), ("t1__b", 1.0, 0.4), ("t1__c", 0.0, 0.9)):
        write_trial(job, name, {"verifier_result": {"rewards": {"reward": reward}}})
        events = job / name / "agent" / "events"
        events.mkdir(parents=True)
        result = {"type": "result", "subtype": "success", "num_turns": 10, "cost_usd": cost}
        (events / "run-1.jsonl").write_text(json.dumps(result) + "\n")
    summary = report(job, tmp_path)
    assert summary["per_task"] == {"t1": {"successes": 2, "trials": 3}}
    process = summary["process"]
    assert process["mean_cost_usd_succeeded"] == pytest.approx(0.3)
    assert process["per_task"]["t1"]["mean_turns"] == 10.0
    assert process["per_task"]["t1"]["cost_usd"] == pytest.approx(1.5)


def test_dev_report_has_the_gate_metrics(tmp_path: Path) -> None:
    """Ticket 67: the dev `process` section carries the stage-1 gate's means per trial and each
    task's gate counts."""
    job = tmp_path / "job"
    denied = {"type": "tool_end", "is_error": True, "content": "PreToolUse:Bash hook error: no"}
    trials = (
        ("t1__a", [denied, denied, denied]),
        ("t1__b", [{"type": "ended_disputed"}]),
        ("t1__c", [{"type": "result", "subtype": "success"}]),
    )
    for name, events in trials:
        write_trial(job, name, {"verifier_result": {"rewards": {"reward": 1.0}}})
        directory = job / name / "agent" / "events"
        directory.mkdir(parents=True)
        (directory / "run-1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    process = report(job, tmp_path)["process"]
    gate, task = process["gate"], process["per_task"]["t1"]
    assert (gate["trials"], gate["guard_denials"]) == (3, 1.0)
    assert gate["ended_disputed"] == pytest.approx(1 / 3)
    assert (task["guard_denials"], task["ended_disputed"], task["verifier_dispatches"]) == (3, 1, 0)


def test_a_usage_limit_is_rerun_not_scored(tmp_path: Path) -> None:
    """The account ran out (Harbor does not retry it); that is no agent failure to score as 0."""
    job = tmp_path / "job"
    write_trial(job, "t1__a", {"verifier_result": {"rewards": {"reward": 1.0}}})
    write_trial(job, "t1__b", {"verifier_result": {"rewards": {"reward": 0.0}}})
    write_trial(job, "t1__c", {"exception_info": {"exception_type": "ApiUsageLimitError"}})
    proc = holdout_report(job, tmp_path, ["t1"])
    assert proc.returncode != 0
    assert "1 infrastructure error" in proc.stderr
    assert not (tmp_path / "report" / "report.json").exists()


def test_report_refuses_uneven_trial_counts(tmp_path: Path) -> None:
    job = tmp_path / "job"
    passed = {"verifier_result": {"rewards": {"reward": 1.0}}}
    for name in ("t-secret__a", "t-secret__b"):
        (job / name).mkdir(parents=True)
        (job / name / "result.json").write_text(json.dumps({"task_name": "t-secret", **passed}))
    for name in ("t1__a", "t1__b", "t1__c"):
        write_trial(job, name, passed)
    proc = holdout_report(job, tmp_path, ["t1", "t-secret"])
    assert proc.returncode != 0
    assert "1 of 2 holdout tasks" in proc.stderr
    assert "t-secret" not in proc.stderr
    assert not (tmp_path / "report" / "report.json").exists()


def test_report_refuses_fewer_trials_than_required(tmp_path: Path) -> None:
    # Even counts are not enough: with 2 trials per task pass@3 would silently be 0.
    job = tmp_path / "job"
    passed = {"verifier_result": {"rewards": {"reward": 1.0}}}
    write_trial(job, "t1__a", passed)
    write_trial(job, "t1__b", passed)
    proc = holdout_report(job, tmp_path, ["t1"])
    assert proc.returncode != 0
    assert "1 of 1 holdout tasks do not have exactly 3 trials" in proc.stderr
    assert "t1" not in proc.stderr
    assert not (tmp_path / "report" / "report.json").exists()


def test_report_trials_per_task_is_configurable(tmp_path: Path) -> None:
    # Plan §7's budget fallback cuts the holdout to 2 trials per task.
    job = tmp_path / "job"
    write_trial(job, "t1__a", {"verifier_result": {"rewards": {"reward": 1.0}}})
    write_trial(job, "t1__b", {"verifier_result": {"rewards": {"reward": 0.0}}})
    holdout = tmp_path / "manifest.json"
    holdout.write_text(json.dumps({"seed": 0, "dev": [], "holdout": ["t1"]}))
    proc = evals(
        *("report", str(job), "--manifest", str(holdout), "--split", "holdout"),
        *("--trials", "2", "--out", str(tmp_path / "report")),
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads((tmp_path / "report" / "report.json").read_text())["accuracy"] == 50.0


def test_report_omits_pass_at_k_above_trials_per_task(tmp_path: Path) -> None:
    # With 2 trials per task pass@3 is undefined; reporting it as 0.0 would read as a score.
    job = tmp_path / "job"
    write_trial(job, "t1__a", {"verifier_result": {"rewards": {"reward": 1.0}}})
    write_trial(job, "t1__b", {"verifier_result": {"rewards": {"reward": 0.0}}})
    holdout = tmp_path / "manifest.json"
    holdout.write_text(json.dumps({"seed": 0, "dev": [], "holdout": ["t1"]}))
    proc = evals(
        *("report", str(job), "--manifest", str(holdout), "--split", "holdout"),
        *("--trials", "2", "--out", str(tmp_path / "report")),
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads((tmp_path / "report" / "report.json").read_text())
    assert summary["pass_at_2"] == 1.0
    assert "pass_at_3" not in summary


def test_report_totals_tokens_and_cost(tmp_path: Path) -> None:
    # Harbor's n_input_tokens includes the cached tokens.
    usage = {"n_input_tokens": 120, "n_cache_tokens": 100, "n_output_tokens": 4, "cost_usd": 0.5}
    job = tmp_path / "job"
    for name in ("t1__a", "t1__b", "t1__c"):
        write_trial(job, name, {"agent_result": usage})
    summary = report(job, tmp_path)
    assert summary["uncached_input_tokens"] == 60
    assert summary["cached_input_tokens"] == 300
    assert summary["output_tokens"] == 12
    assert summary["cost_usd"] == 1.5


def billed_job(tmp_path: Path) -> Path:
    """Three trials at $0.40 at peak prices: one run on a Saturday (billed $0.20), one on a
    Monday at peak, one without events (both billed $0.40)."""
    job = tmp_path / "job"
    starts = {"t1__a": "2026-10-31T12:00:00+00:00", "t1__b": "2026-11-02T02:00:00+00:00"}
    for name in ("t1__a", "t1__b", "t1__c"):
        write_trial(job, name, {"agent_result": {"cost_usd": 0.4}})
        if name not in starts:
            continue
        events = job / name / "agent" / "events"
        events.mkdir(parents=True)
        lines = [
            {"type": "version", "ts": starts[name], "model": "deepseek-flash"},
            {"type": "result", "cost_usd": 0.4, "cost_basis": "price_table"},
        ]
        (events / "run-1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in lines))
    return job


def test_dev_report_gives_billed_cost_next_to_cost(tmp_path: Path) -> None:
    summary = report(billed_job(tmp_path), tmp_path)
    assert summary["cost_usd"] == pytest.approx(1.2)
    assert summary["billed_cost_usd"] == pytest.approx(1.0)
    rows = json.loads((tmp_path / "report" / "trials.json").read_text())
    assert [(r["cost_usd"], r["billed_cost_usd"]) for r in rows] == [
        (0.4, pytest.approx(0.2)),
        (0.4, 0.4),
        (0.4, 0.4),
    ]


def test_holdout_report_gives_billed_cost_next_to_cost(tmp_path: Path) -> None:
    job = billed_job(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"seed": 0, "dev": [], "holdout": ["t1"]}))
    out = tmp_path / "report"
    proc = evals(
        "report", str(job), "--manifest", str(manifest), "--split", "holdout", "--out", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads((out / "report.json").read_text())
    assert summary["cost_usd"] == pytest.approx(1.2)
    assert summary["billed_cost_usd"] == pytest.approx(1.0)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_an_unreadable_event_file_is_billed_at_its_full_cost(tmp_path: Path) -> None:
    job = billed_job(tmp_path)
    events = job / "t1__a" / "agent" / "events" / "run-1.jsonl"
    trial = next(t for t in load_trials(job) if t.name == "t1__a")
    events.chmod(0)
    try:
        assert billed_usd(trial) == 0.4
    finally:
        events.chmod(0o644)


def test_missing_or_empty_job_dir_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    for job in (tmp_path / "missing", tmp_path / "empty"):
        proc = evals("score", str(job), "--json")
        assert proc.returncode != 0
        assert "no trial results" in proc.stderr


def test_report_on_a_job_without_the_split_is_an_error(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "t1__a", {"verifier_result": {"rewards": {"reward": 1.0}}})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"seed": 0, "dev": ["t1"], "holdout": ["t2"]}))
    out = tmp_path / "report"
    proc = evals(
        *("report", str(job), "--manifest", str(manifest)),
        *("--split", "holdout", "--out", str(out)),
    )
    assert proc.returncode != 0
    assert "no trial results" in proc.stderr
    assert not (out / "report.json").exists()


def test_report_with_a_split_task_missing_is_an_error(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "t1__a", {"verifier_result": {"rewards": {"reward": 1.0}}})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"seed": 0, "dev": [], "holdout": ["t1", "t-secret"]}))
    proc = evals(
        *("report", str(job), "--manifest", str(manifest)),
        *("--split", "holdout", "--out", str(tmp_path / "report")),
    )
    assert proc.returncode != 0
    assert "1 of 2 holdout tasks" in proc.stderr
    assert "t-secret" not in proc.stderr


def test_compare_unpaired_jobs_is_an_error_naming_no_task(tmp_path: Path) -> None:
    base, cand = tmp_path / "base", tmp_path / "cand"
    for attempt in "abc":
        write_trial(base, f"t1__{attempt}", {"verifier_result": {"rewards": {"reward": 1.0}}})
        (cand / f"t-secret__{attempt}").mkdir(parents=True)
        (cand / f"t-secret__{attempt}" / "result.json").write_text(
            json.dumps({"task_name": "t-secret", "verifier_result": {"rewards": {"reward": 0.0}}})
        )
    proc = evals("compare", "--baseline", str(base), "--candidate", str(cand), "--json")
    assert proc.returncode != 0
    assert "2 tasks are unpaired" in proc.stderr
    assert "t-secret" not in proc.stderr


def compare_jobs(
    tmp_path: Path, candidate: list[dict[str, Any]]
) -> subprocess.CompletedProcess[str]:
    base, cand = tmp_path / "base", tmp_path / "cand"
    for attempt in "abc":
        write_trial(base, f"t1__{attempt}", {"verifier_result": {"rewards": {"reward": 1.0}}})
    for attempt, result in zip("abc", candidate, strict=False):
        write_trial(cand, f"t1__{attempt}", result)
    return evals("compare", "--baseline", str(base), "--candidate", str(cand), "--json")


def test_compare_refuses_infrastructure_errors(tmp_path: Path) -> None:
    passed = {"verifier_result": {"rewards": {"reward": 1.0}}}
    infra = {"exception_info": {"exception_type": "ApiInternalServerError"}}
    proc = compare_jobs(tmp_path, [passed, passed, infra])
    assert proc.returncode != 0
    assert "1 infrastructure error in the candidate trials" in proc.stderr
    assert "t1" not in proc.stderr


def test_compare_refuses_fewer_trials_than_required(tmp_path: Path) -> None:
    passed = {"verifier_result": {"rewards": {"reward": 1.0}}}
    proc = compare_jobs(tmp_path, [passed, passed])
    assert proc.returncode != 0
    assert "1 of 1 candidate tasks do not have exactly 3 trials" in proc.stderr
    assert "t1" not in proc.stderr


def compare_split(
    tmp_path: Path, base_tasks: list[str], cand_tasks: list[str], holdout: list[str]
) -> subprocess.CompletedProcess[str]:
    base, cand = tmp_path / "base", tmp_path / "cand"
    for job, tasks in ((base, base_tasks), (cand, cand_tasks)):
        for task in tasks:
            for attempt in "abc":
                (job / f"{task}__{attempt}").mkdir(parents=True)
                (job / f"{task}__{attempt}" / "result.json").write_text(
                    json.dumps({"task_name": task, "verifier_result": {"rewards": {"reward": 1.0}}})
                )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"seed": 0, "dev": ["d1"], "holdout": holdout}))
    return evals(
        *("compare", "--baseline", str(base), "--candidate", str(cand)),
        *("--manifest", str(manifest), "--split", "holdout", "--json"),
    )


def test_compare_refuses_a_split_task_missing_from_both_jobs(tmp_path: Path) -> None:
    proc = compare_split(tmp_path, ["t1"], ["t1"], holdout=["t1", "t-secret"])
    assert proc.returncode != 0
    assert "1 of 2 holdout tasks" in proc.stderr
    assert "t-secret" not in proc.stderr


def test_compare_scores_only_the_split(tmp_path: Path) -> None:
    # The dev task outside the split would be unpaired; with --split it is left out.
    proc = compare_split(tmp_path, ["t1", "t2"], ["t1", "t2", "d1"], holdout=["t1", "t2"])
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["diff"] == 0


def test_compare_manifest_and_split_go_together(tmp_path: Path) -> None:
    proc = evals("compare", "--baseline", ".", "--candidate", ".", "--split", "holdout")
    assert proc.returncode != 0
    assert "--manifest" in proc.stderr


def test_sample_rejects_dev_tasks_missing_from_tasks_dir(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    (tasks / "a").mkdir(parents=True)
    (tasks / "a" / "task.toml").write_text('[metadata]\ndifficulty = "hard"\n')
    dev_list = tmp_path / "dev.txt"
    dev_list.write_text("# dev set\n\nnot-a-task\n")
    proc = evals(
        *("sample", "--tasks-dir", str(tasks), "--dev-list", str(dev_list)),
        *("--size", "1", "--seed", "0", "--out", str(tmp_path / "m.json")),
    )
    assert proc.returncode != 0
    assert "not-a-task" in proc.stderr


@pytest.mark.parametrize("real", [{"dev": [], "holdout": ["t1"]}, {"holdout": "t2"}, None])
def test_a_dev_report_on_a_task_the_repos_manifest_holds_out_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real: dict[str, Any] | None
) -> None:
    """A dev report names its tasks; a manifest that lists a holdout task under dev (the old
    temporary-manifest practice) is checked against the repo's evals/manifest.json (on the
    server, the real one), and refused when that cannot be read."""
    from evals import report as report_module
    from evals.harbor import load_trials

    repo_manifest = tmp_path / "repo-manifest.json"
    if real is not None:
        repo_manifest.write_text(json.dumps(real))
    monkeypatch.setattr(report_module, "REPO_MANIFEST", repo_manifest)
    job = tmp_path / "job"
    write_trial(job, "t1__a", {"verifier_result": {"rewards": {"reward": 1.0}}})
    out = tmp_path / "report"
    manifest = {"seed": 0, "dev": ["t1"], "holdout": []}
    with pytest.raises(ValueError, match=str(repo_manifest)) as exc:
        report_module.write_report(load_trials(job), manifest, "dev", out, trials_per_task=1)
    assert "t1" not in str(exc.value).replace(str(tmp_path), "")
    assert not out.exists()


def test_a_dev_report_on_a_task_its_own_manifest_holds_out_is_refused(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "t1__a", {"verifier_result": {"rewards": {"reward": 1.0}}})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"seed": 0, "dev": ["t1"], "holdout": ["t1"]}))
    out = tmp_path / "report"
    proc = evals(
        *("report", str(job), "--manifest", str(manifest), "--split", "dev"),
        *("--out", str(out), "--trials", "1"),
    )
    assert proc.returncode == 2
    assert str(manifest) in proc.stderr
    assert not out.exists()


def test_a_dev_report_on_a_task_the_real_manifest_holds_out_is_refused(tmp_path: Path) -> None:
    """End to end against the repo's own evals/manifest.json; the task id is never printed."""
    held_out = json.loads((REPO_ROOT / "evals" / "manifest.json").read_text())["holdout"][0]
    job = tmp_path / "job"
    (job / "x__a").mkdir(parents=True)
    outcome = {"task_name": held_out, "verifier_result": {"rewards": {"reward": 1.0}}}
    (job / "x__a" / "result.json").write_text(json.dumps(outcome))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"seed": 0, "dev": [held_out], "holdout": []}))
    out = tmp_path / "report"
    proc = evals(
        *("report", str(job), "--manifest", str(manifest), "--split", "dev"),
        *("--out", str(out), "--trials", "1"),
    )
    assert proc.returncode == 2
    assert held_out not in proc.stdout + proc.stderr
    assert not out.exists()


# --- Evaluation lifecycle contract (eval-readiness-and-lifecycle, ticket 01) ----------------------
#
# Stage 1 and Stage 2 of the dev evaluation (ADR-0008 补充 2026-11-06) run on one commit into
# several OUTs and are merged per config by `evals report JOB...` (ticket 65). A test marked
# `xfail(strict=True)` names the ticket that makes it pass; that ticket removes the marker only.

STAGE_DEV = ["t1", "t2"]
STAGE_HOLDOUT = ["h-secret"]
# What `evals ablate` writes to OUT/records/<stamp>.json and OUT/request.json for a dev run.
STAGE_RECORD = {"benchmark_commit": "b" * 40, "machine": "box", "model": "m1"}
STAGE_REQUEST = {"model": "m1", "split": "dev", "tasks": STAGE_DEV}
STAGE_REQUEST |= {"agent_kwargs": ["agent_timeout_sec=1500"]}


def write_versioned_trial(
    job: Path,
    task: str,
    name: str,
    reward: float,
    events: list[dict[str, Any]] | None = None,
    header: dict[str, Any] | None = None,
) -> None:
    """A trial as the agent and Harbor leave it: a version header, `events`, a result event."""
    version = {"type": "version", "git_commit": "c1", "skill_version": "s1", "config": "full"}
    version |= {"model": "m1"} | (header or {})
    end = {"type": "result", "subtype": "success", "num_turns": 3, "cost_usd": 0.01}
    events_dir = job / name / "agent" / "events"
    events_dir.mkdir(parents=True)
    lines = "".join(json.dumps(e) + "\n" for e in (version, *(events or []), end))
    (events_dir / "run-1.jsonl").write_text(lines)
    result = {
        "task_name": f"org/{task}",
        "trial_name": name,
        "verifier_result": {"rewards": {"reward": reward}},
        "agent_result": {"cost_usd": 0.01, "n_output_tokens": 10},
    }
    (job / name / "result.json").write_text(json.dumps(result))


@pytest.fixture
def dev_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], Path]:
    """Writes a manifest with the given dev list; the repo manifest's holdout, which a dev report
    checks too, is pointed at one that holds out none of these tests' dev tasks."""
    repo = tmp_path / "repo-manifest.json"
    repo.write_text(json.dumps({"seed": 0, "dev": [], "holdout": STAGE_HOLDOUT}))
    monkeypatch.setattr("evals.report.REPO_MANIFEST", repo)

    def write(dev: list[str]) -> Path:
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps({"seed": 0, "dev": dev, "holdout": STAGE_HOLDOUT}))
        return path

    return write


def report_on(
    jobs: list[Path],
    manifest: Path,
    out: Path,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict[str, Any] | None, str]:
    """`evals report` of the dev split on `jobs`: exit code, report.json or None, stderr."""
    paths = [str(job) for job in jobs]
    code = evals_main(
        ["report", *paths, "--manifest", str(manifest), "--split", "dev", "--out", str(out)]
    )
    err = capsys.readouterr().err
    report = json.loads((out / "report.json").read_text()) if code == 0 else None
    return code, report, err


def stage_job(tmp_path: Path, rewards: dict[str, list[float]]) -> Path:
    """A dev job of pos-operations' stage-1 trials and `rewards` for the other tasks."""
    job = tmp_path / "job"
    for name, counts in (("a", (21, 6, 5)), ("b", (21, 17, 3, 1, 21)), ("c", (21, 21, 21))):
        write_versioned_trial(
            job, "pos-operations", f"pos-operations__{name}", 1.0, dispatches(*counts)
        )
    for task, values in rewards.items():
        for i, reward in enumerate(values):
            write_versioned_trial(job, task, f"{task}__{i}", reward)
    return job


@pytest.mark.xfail(strict=True, reason="ticket 04: dev reports separate gates")
def test_a_dev_report_keeps_the_redispatch_warning_out_of_correctness(
    tmp_path: Path, dev_manifest: Callable[[list[str]], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Stage 1: every pos-operations trial passed, but two re-dispatched all 21 models. The report
    shows a failed efficiency line naming those dev trials; correctness counts successes only, so
    a logical failure elsewhere is a correctness result and the redispatch is not one."""
    job = stage_job(tmp_path, {"t1": [1.0, 1.0, 1.0], "t2": [1.0, 1.0, 0.0]})
    manifest = dev_manifest([*STAGE_DEV, "pos-operations"])
    code, report, err = report_on([job], manifest, tmp_path / "report", capsys)
    assert code == 0, err
    assert report is not None
    gates = report["gates"]
    assert (gates["correctness"]["successes"], gates["correctness"]["trials"]) == (8, 9)
    assert "redispatch" not in json.dumps(gates["correctness"]).lower()
    efficiency = gates["efficiency"]
    assert efficiency["passed"] is False
    assert {w["task"] for w in efficiency["warnings"]} == {"pos-operations"}
    assert {w["trial"] for w in efficiency["warnings"]} == {
        "pos-operations__b",
        "pos-operations__c",
    }
    assert all("redispatch" in w["line"] for w in efficiency["warnings"])
    assert report["accuracy"] == pytest.approx(100 * 8 / 9)


@pytest.mark.xfail(strict=True, reason="ticket 04: dev reports separate gates")
def test_a_dev_report_without_full_redispatches_passes_the_efficiency_line(
    tmp_path: Path, dev_manifest: Callable[[list[str]], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Later dispatches that narrow the model list pass the efficiency line, whatever the
    correctness results (here every other trial failed)."""
    job = tmp_path / "job"
    for i, counts in enumerate(((21,), (21, 6, 5), (21, 3))):
        write_versioned_trial(
            job, "pos-operations", f"pos-operations__{i}", 1.0, dispatches(*counts)
        )
    for task in STAGE_DEV:
        for i in range(3):
            write_versioned_trial(job, task, f"{task}__{i}", 0.0)
    manifest = dev_manifest([*STAGE_DEV, "pos-operations"])
    code, report, err = report_on([job], manifest, tmp_path / "report", capsys)
    assert code == 0, err
    assert report is not None
    efficiency = report["gates"]["efficiency"]
    assert (efficiency["passed"], efficiency["warnings"]) == (True, [])
    assert (report["gates"]["correctness"]["successes"], report["accuracy"]) == (
        3,
        pytest.approx(100 / 3),
    )


def write_stage_out(
    root: Path,
    name: str,
    rewards: dict[str, list[float]],
    *,
    start: int = 0,
    header: dict[str, Any] | None = None,
    record: dict[str, Any] = STAGE_RECORD,
    request: dict[str, Any] = STAGE_REQUEST,
) -> Path:
    """OUT `root/name` as `evals ablate` leaves it for one config: `jobs/full`, a run record and
    the request; trial `<task>__<i>` from i = `start`."""
    job = root / name / "jobs" / "full"
    for task, values in rewards.items():
        for i, reward in enumerate(values, start):
            write_versioned_trial(job, task, f"{task}__{i}", reward, header=header)
    (job / "config.json").write_text("{}")
    (root / name / "records").mkdir()
    (root / name / "records" / "20261106T100000Z.json").write_text(json.dumps(record))
    (root / name / "request.json").write_text(json.dumps(request))
    return job


def test_stage_1_and_stage_2_of_one_contract_merge_into_three_trials_per_task(
    tmp_path: Path, dev_manifest: Callable[[list[str]], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Stage 1 ran t1 x 3 in one OUT and t2 x 1 in another; stage 2 ran t2 x 2 (ADR-0008 补充
    2026-11-06: one OUT per request). The requests' task lists differ by design; the task set
    the contract fixes is the split's, each task with exactly 3 merged trials."""
    targets = write_stage_out(
        tmp_path, "s1-targets", {"t1": [1.0, 1.0, 0.0]}, request={**STAGE_REQUEST, "tasks": ["t1"]}
    )
    rest = write_stage_out(
        tmp_path, "s1-rest", {"t2": [1.0]}, request={**STAGE_REQUEST, "tasks": ["t2"]}
    )
    stage_2 = write_stage_out(
        tmp_path, "s2", {"t2": [0.0, 1.0]}, start=1, request={**STAGE_REQUEST, "tasks": ["t2"]}
    )
    manifest = dev_manifest(STAGE_DEV)
    code, report, err = report_on([targets, rest, stage_2], manifest, tmp_path / "r", capsys)
    assert code == 0, err
    assert report is not None
    assert report["per_task"] == {
        "t1": {"successes": 2, "trials": 3},
        "t2": {"successes": 2, "trials": 3},
    }


NOT_YET_COMPARED = pytest.mark.xfail(strict=True, reason="ticket 04: merge compares run requests")


@pytest.mark.parametrize(
    ("stage_2", "what"),
    [
        pytest.param({"header": {"git_commit": "c2"}}, "git commit", id="commit"),
        pytest.param({"header": {"skill_version": "s2"}}, "Skill version", id="skill"),
        pytest.param({"header": {"config": "baseline"}}, "config", id="config"),
        pytest.param(
            {"record": {**STAGE_RECORD, "benchmark_commit": "d" * 40}},
            "benchmark commit",
            id="benchmark",
        ),
        pytest.param({"record": {**STAGE_RECORD, "machine": "other"}}, "machine", id="machine"),
        pytest.param({"record": {**STAGE_RECORD, "model": "m2"}}, "model", id="model"),
        pytest.param(
            {"rewards": {"t1": [1.0, 1.0]}}, "exactly 3 trials", id="task-set-trial-count"
        ),
        pytest.param(
            {"request": {**STAGE_REQUEST, "agent_kwargs": ["agent_timeout_sec=900"]}},
            "agent_kwargs",
            id="run-parameters",
            marks=NOT_YET_COMPARED,
        ),
        pytest.param(
            {"request": {k: v for k, v in STAGE_REQUEST.items() if k != "agent_kwargs"}},
            "agent_kwargs",
            id="run-parameters-dropped",
            marks=NOT_YET_COMPARED,
        ),
        pytest.param(
            {"request": {**STAGE_REQUEST, "split": "holdout"}},
            "split",
            id="split",
            marks=NOT_YET_COMPARED,
        ),
    ],
)
def test_stage_1_and_stage_2_under_different_contracts_are_not_merged(
    tmp_path: Path,
    dev_manifest: Callable[[list[str]], Path],
    capsys: pytest.CaptureFixture[str],
    stage_2: dict[str, Any],
    what: str,
) -> None:
    """Stage 2 merges with stage 1 only under one commit, Skill version, config, benchmark
    commit, machine, model, split, task set (the split's tasks, each with exactly 3 merged
    trials) and run parameters (the agent kwargs, e.g. the agent timeout); one mismatch refuses
    the merge. A new commit makes stage 1 stale: it is rerun, never merged. The whole contract is
    listed here, so the cases ticket 65 already enforces (test_eval_merge) appear again."""
    manifest = dev_manifest(STAGE_DEV)
    stage_1 = write_stage_out(tmp_path, "s1", {t: [1.0] for t in STAGE_DEV})
    rewards = stage_2.get("rewards", {t: [1.0, 0.0] for t in STAGE_DEV})
    conditions = {key: value for key, value in stage_2.items() if key != "rewards"}
    later = write_stage_out(tmp_path, "s2", rewards, start=1, **conditions)
    code, _, err = report_on([stage_1, later], manifest, tmp_path / "r", capsys)
    assert code == 2
    assert what in err
    assert not (tmp_path / "r" / "report.json").exists()
