"""Edge cases of `python -m evals score|report` beyond the seam C acceptance tests."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

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
