"""Several jobs of one config, merged by report, compare, diverge and accept (ticket 65)."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from evals.__main__ import main

DEV = ["t1", "t2"]
HOLDOUT = ["h-secret"]
RECORD = {"benchmark_commit": "b" * 40, "machine": "box", "model": "m1"}
# OUT/request.json of a dev run (evals.ablate.check_request); merged jobs' requests may differ
# only in their tasks (eval-readiness ticket 04).
REQUEST = {"model": "m1", "split": "dev", "tasks": DEV}
STARTED = "2026-11-01T10:00:00+00:00"


def write_out(
    root: Path,
    name: str,
    rewards: dict[str, list[float]],
    *,
    start: int = 0,
    commit: str = "c1",
    skill: str = "s1",
    config: str = "full",
    record: dict[str, Any] | None = RECORD,
    request: dict[str, Any] | None = REQUEST,
    exception: str | None = None,
) -> Path:
    """OUT `root/name` with one job, `jobs/<config>`; trial `<task>__<i>` from i = `start`.
    `exception` gives every trial that Harbor exception and no reward."""
    job = root / name / "jobs" / config
    for task, values in rewards.items():
        for i, reward in enumerate(values, start):
            trial = job / f"{task}__{i}"
            events = trial / "agent" / "events"
            events.mkdir(parents=True)
            header = {"seq": 0, "type": "version", "ts": STARTED, "git_commit": commit}
            header |= {"skill_version": skill, "config": config, "model": "m1"}
            header |= {"layers": {}, "sdk_version": "0.2", "cli_version": "2.1"}
            end = {"seq": 1, "type": "result", "subtype": "success", "num_turns": 3 + i}
            end |= {"terminal_reason": "completed", "cost_usd": 0.01 * (i + 1)}
            (events / "run-1.jsonl").write_text(json.dumps(header) + "\n" + json.dumps(end) + "\n")
            result: dict[str, Any] = {
                "task_name": f"org/{task}",
                "trial_name": trial.name,
                "started_at": STARTED,
                "finished_at": STARTED,
                "verifier_result": {"rewards": {"reward": reward}},
                "agent_result": {"cost_usd": 0.01 * (i + 1), "n_output_tokens": 10},
            }
            if exception:
                result["verifier_result"] = None
                result["exception_info"] = {"exception_type": exception}
            (trial / "result.json").write_text(json.dumps(result))
    (job / "config.json").write_text("{}")
    if record is not None:
        (root / name / "records").mkdir(exist_ok=True)
        (root / name / "records" / "20261101T100000Z.json").write_text(json.dumps(record))
    if request is not None:
        (root / name / "request.json").write_text(json.dumps(request))
    return job


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"seed": 0, "dev": DEV, "holdout": HOLDOUT}))
    return path


@pytest.fixture(autouse=True)
def repo_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the repo manifest check at one that holds out no dev task of these tests."""
    path = tmp_path / "repo-manifest.json"
    path.write_text(json.dumps({"seed": 0, "dev": [], "holdout": HOLDOUT}))
    monkeypatch.setattr("evals.report.REPO_MANIFEST", path)


def split(
    tmp_path: Path, name: str, rewards: dict[str, list[float]], **kwargs: Any
) -> tuple[list[Path], Path]:
    """The same trials twice: as two jobs (first trial of each task, then the rest) under OUTs
    `<name>-a`, `<name>-b`, and as one job under OUT `<name>`."""
    first = {task: values[:1] for task, values in rewards.items()}
    rest = {task: values[1:] for task, values in rewards.items()}
    a = write_out(tmp_path, f"{name}-a", first, **kwargs)
    b = write_out(tmp_path, f"{name}-b", rest, start=1, **kwargs)
    return [a, b], write_out(tmp_path, name, rewards, **kwargs)


def run_report(
    tmp_path: Path, manifest: Path, jobs: list[Path], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any], str]:
    out = tmp_path / "report-out"
    shutil.rmtree(out, ignore_errors=True)
    argv = ["report", *map(str, jobs), "--manifest", str(manifest), "--split", "dev"]
    code = main([*argv, "--out", str(out)])
    err = capsys.readouterr().err
    report = json.loads((out / "report.json").read_text()) if code == 0 else {}
    return code, report, err


REWARDS = {"t1": [1.0, 0.0, 1.0], "t2": [0.0, 0.0, 1.0]}


def test_report_of_two_jobs_equals_the_report_of_one_job(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    jobs, single = split(tmp_path, "full", REWARDS)
    code, merged, err = run_report(tmp_path, manifest, jobs, capsys)
    assert code == 0, err
    assert run_report(tmp_path, manifest, [single], capsys)[1] == merged
    assert merged["n_trials"] == 6


@pytest.mark.parametrize(
    ("kwargs", "what"),
    [
        ({"commit": "c2"}, "git commit"),
        ({"skill": "s2"}, "Skill version"),
        ({"record": {**RECORD, "benchmark_commit": "d" * 40}}, "benchmark commit"),
        ({"record": {**RECORD, "machine": "other"}}, "machine"),
        ({"record": {**RECORD, "model": "m2"}}, "model"),
    ],
)
def test_jobs_run_under_other_conditions_are_refused(
    tmp_path: Path,
    manifest: Path,
    capsys: pytest.CaptureFixture[str],
    kwargs: dict[str, Any],
    what: str,
) -> None:
    a = write_out(tmp_path, "a", {t: [1.0] for t in DEV})
    b = write_out(tmp_path, "b", {t: [1.0, 0.0] for t in DEV}, start=1, **kwargs)
    code, _, err = run_report(tmp_path, manifest, [a, b], capsys)
    assert code == 2
    assert what in err


def test_jobs_of_another_config_are_refused(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = write_out(tmp_path, "a", {t: [1.0] for t in DEV})
    b = write_out(tmp_path, "b", {t: [1.0, 0.0] for t in DEV}, start=1, config="baseline")
    code, _, err = run_report(tmp_path, manifest, [a, b], capsys)
    assert code == 2
    assert "config" in err


@pytest.mark.parametrize(
    "record",
    [None, {**RECORD, "machine": None}, {**RECORD, "benchmark_commit": "b" * 40 + "-dirty"}],
)
def test_jobs_whose_conditions_cannot_be_matched_are_refused(
    tmp_path: Path,
    manifest: Path,
    capsys: pytest.CaptureFixture[str],
    record: dict[str, Any] | None,
) -> None:
    a = write_out(tmp_path, "a", {t: [1.0] for t in DEV}, record=record)
    b = write_out(tmp_path, "b", {t: [1.0, 0.0] for t in DEV}, start=1, record=record)
    code, _, err = run_report(tmp_path, manifest, [a, b], capsys)
    assert code == 2
    assert "run record" in err


def test_a_trial_without_a_version_header_is_refused(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = write_out(tmp_path, "a", {t: [1.0] for t in DEV})
    b = write_out(tmp_path, "b", {t: [1.0, 0.0] for t in DEV}, start=1)
    (b / "t1__1" / "agent" / "events" / "run-1.jsonl").write_text("")
    code, _, err = run_report(tmp_path, manifest, [a, b], capsys)
    assert code == 2
    assert "1 trial(s)" in err and "version" in err


def test_merged_tasks_without_3_trials_are_refused_by_count(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rewards = {"t1": [1.0], "t2": [1.0], "h-secret": [1.0]}
    a = write_out(tmp_path, "a", rewards)
    b = write_out(tmp_path, "b", {**rewards, "t2": [1.0, 0.0]}, start=1)
    code, _, err = run_report(tmp_path, manifest, [a, b], capsys)
    assert code == 2
    assert "1 of 2 dev tasks do not have exactly 3 trials" in err
    assert "t1" not in err and "h-secret" not in err


def test_the_same_job_given_twice_is_refused(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = write_out(tmp_path, "a", {t: [1.0, 1.0, 0.0] for t in DEV})
    code, _, err = run_report(tmp_path, manifest, [a, a.parent / ".." / "jobs" / "full"], capsys)
    assert code == 2
    assert "more than once" in err


def test_a_trial_name_in_two_jobs_is_refused_by_count(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rewards = {"t1": [1.0, 0.0], "h-secret": [1.0]}
    a = write_out(tmp_path, "a", rewards)
    b = write_out(tmp_path, "b", {**rewards, "t1": [1.0]}, start=1)  # h-secret__1 twice
    code, _, err = run_report(tmp_path, manifest, [a, b], capsys)
    assert code == 2
    assert "1 trial name(s)" in err
    assert "h-secret" not in err


def test_one_job_reads_as_before(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # No run record and no version header: a single job needs neither, as before.
    job = write_out(tmp_path, "a", {t: [1.0, 0.0, 1.0] for t in DEV}, record=None)
    for path in job.glob("*/agent/events/*.jsonl"):
        path.write_text("")
    code, report, err = run_report(tmp_path, manifest, [job], capsys)
    assert code == 0, err
    assert report["n_trials"] == 6


def test_compare_merges_jobs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base_jobs, base = split(tmp_path, "baseline", REWARDS, config="baseline")
    cand_jobs, cand = split(tmp_path, "full", {"t1": [1.0, 1.0, 1.0], "t2": [0.0, 1.0, 1.0]})

    def compare(baseline: list[Path], candidate: list[Path]) -> dict[str, Any]:
        argv = ["compare", "--baseline", *map(str, baseline), "--candidate", *map(str, candidate)]
        assert main([*argv, "--n-boot", "200", "--json"]) == 0
        return json.loads(capsys.readouterr().out)

    assert compare(base_jobs, cand_jobs) == compare([base], [cand])


def test_diverge_merges_jobs(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base_jobs, base = split(tmp_path, "baseline", REWARDS, config="baseline")
    cand_jobs, cand = split(tmp_path, "full", {"t1": [1.0, 1.0, 1.0], "t2": [0.0, 1.0, 1.0]})

    def diverge(baseline: list[Path], candidate: list[Path]) -> dict[str, Any]:
        out = tmp_path / "diverge-out"
        argv = ["diverge", "--baseline", *map(str, baseline), "--candidate", *map(str, candidate)]
        assert main([*argv, "--manifest", str(manifest), "--out", str(out)]) == 0
        capsys.readouterr()
        report = json.loads((out / "diverge.json").read_text())
        for side in ("baseline", "candidate"):
            for row in report[side]["trials"]:
                row.pop("events", None)
        return {k: v for k, v in report.items() if k != "labels"}

    merged = diverge(base_jobs, cand_jobs)
    assert merged == diverge([base], [cand])
    assert merged["wins"] == {"baseline": 0, "candidate": 2}


def test_diverge_refuses_jobs_of_two_versions(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = write_out(tmp_path, "base", REWARDS, config="baseline")
    a = write_out(tmp_path, "a", {t: [1.0] for t in DEV})
    b = write_out(tmp_path, "b", {t: [1.0, 0.0] for t in DEV}, start=1, commit="c2")
    argv = ["diverge", "--baseline", str(base), "--candidate", str(a), str(b)]
    assert main([*argv, "--manifest", str(manifest), "--out", str(tmp_path / "d")]) == 2
    assert "git commit" in capsys.readouterr().err


def test_accept_merges_jobs(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old_jobs, old = split(tmp_path, "old", REWARDS, commit="c0", skill="s0")
    new_jobs, new = split(tmp_path, "new", {"t1": [1.0, 1.0, 1.0], "t2": [0.0, 1.0, 1.0]})
    regression = tmp_path / "regression.json"
    regression.write_text(json.dumps(["t1"]))

    def accept(old: list[Path], new: list[Path]) -> tuple[int, dict[str, Any]]:
        argv = ["accept", "--old", *map(str, old), "--new", *map(str, new)]
        argv += ["--manifest", str(manifest), "--target", "t2"]
        code = main([*argv, "--regression", str(regression), "--json"])
        return code, json.loads(capsys.readouterr().out)

    merged = accept(old_jobs, new_jobs)
    assert merged == accept([old], [new])
    assert merged[0] == 0, merged[1]


def test_accept_counts_infrastructure_errors_in_any_old_job(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old_a = write_out(tmp_path, "old-a", {t: [1.0] for t in DEV}, commit="c0")
    old_b = write_out(tmp_path, "old-b", {t: [1.0, 1.0] for t in DEV}, start=1, commit="c0")
    dropped = tmp_path / "old-b" / "dropped" / "full" / "t1__9"
    dropped.mkdir(parents=True)
    new = write_out(tmp_path, "new", {t: [1.0, 1.0, 1.0] for t in DEV})
    regression = tmp_path / "regression.json"
    regression.write_text("[]")
    argv = ["accept", "--old", str(old_a), str(old_b), "--new", str(new)]
    argv += ["--manifest", str(manifest), "--target", "t1", "--regression", str(regression)]
    assert main([*argv, "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["verdict"] == "rerun-old"
    assert "the old round had infrastructure errors" in result["reuse_problems"]


def test_jobs_without_a_request_are_refused(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without OUT/request.json the run parameters (agent kwargs, split) cannot be matched, so
    the jobs are not merged (eval-readiness ticket 04)."""
    a = write_out(tmp_path, "a", {t: [1.0] for t in DEV})
    b = write_out(tmp_path, "b", {t: [1.0, 0.0] for t in DEV}, start=1, request=None)
    code, _, err = run_report(tmp_path, manifest, [a, b], capsys)
    assert code == 2
    assert "request" in err


def test_requests_that_differ_only_in_their_tasks_merge(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stage 1 and stage 2 ask for different task lists by design; the split's tasks with
    exactly 3 trials each are what the merged report checks."""
    a = write_out(tmp_path, "a", {"t1": [1.0, 1.0, 1.0]}, request={**REQUEST, "tasks": ["t1"]})
    b = write_out(tmp_path, "b", {"t2": [1.0, 0.0, 0.0]}, request={**REQUEST, "tasks": ["t2"]})
    code, report, err = run_report(tmp_path, manifest, [a, b], capsys)
    assert code == 0, err
    assert report["per_task"] == {
        "t1": {"successes": 3, "trials": 3},
        "t2": {"successes": 1, "trials": 3},
    }
