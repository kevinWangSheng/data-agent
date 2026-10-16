"""Edge cases of `python -m evals score|report` beyond the seam C acceptance tests."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

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


def test_outcome_counts_are_disjoint_when_a_timed_out_trial_still_passes(tmp_path: Path) -> None:
    # Harbor still runs the verifier after an agent timeout, so a trial can carry both.
    job = tmp_path / "job"
    write_trial(
        job,
        "t1__a",
        {
            "verifier_result": {"rewards": {"reward": 1.0}},
            "exception_info": {"exception_type": "AgentTimeoutError"},
        },
    )
    write_trial(job, "t1__b", {"verifier_result": {"rewards": {"reward": 0.0}}})
    write_trial(job, "t1__c", {"exception_info": {"exception_type": "AgentTimeoutError"}})
    summary = report(job, tmp_path)
    assert (summary["n_succeeded"], summary["n_failed"], summary["n_errored"]) == (1, 1, 1)


def test_report_totals_tokens_and_cost(tmp_path: Path) -> None:
    # Harbor's n_input_tokens includes the cached tokens.
    usage = {"n_input_tokens": 120, "n_cache_tokens": 100, "n_output_tokens": 4, "cost_usd": 0.5}
    job = tmp_path / "job"
    write_trial(job, "t1__a", {"agent_result": usage})
    write_trial(job, "t1__b", {"agent_result": usage})
    summary = report(job, tmp_path)
    assert summary["uncached_input_tokens"] == 40
    assert summary["cached_input_tokens"] == 200
    assert summary["output_tokens"] == 8
    assert summary["cost_usd"] == 1.0


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
