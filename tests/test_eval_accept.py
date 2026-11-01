"""`python -m evals accept`: does a change pass ADR-0008; can the old results be reused."""

import json
from pathlib import Path
from typing import Any

import pytest

from evals.__main__ import main

DEV = ["t1", "t2", "t3", "t4"]
HOLDOUT = ["h1"]
VERSION = {
    "config": "full",
    "layers": {"skill": True, "verifiers": True, "context": True},
    "model": "m1",
    "sdk_version": "0.2",
    "cli_version": "2.1",
}
RECORD = {"benchmark_commit": "b" * 40, "machine": "box"}


def write_job(
    root: Path,
    name: str,
    successes: dict[str, int],
    *,
    commit: str,
    trials: int = 3,
    started: str = "2026-10-20T10:00:00Z",
    version: dict[str, Any] | None = None,
    record: dict[str, Any] | None = RECORD,
    errors: dict[str, str] | None = None,
) -> Path:
    """OUT `root/name` with one job, `jobs/full`: `successes[task]` of `trials` pass per task;
    `errors[task]` gives that task's last trial an exception (and no reward)."""
    out = root / name
    job = out / "jobs" / "full"
    for task, wins in successes.items():
        for i in range(trials):
            trial = job / f"{task}__{i}"
            events = trial / "agent" / "events"
            events.mkdir(parents=True)
            header = {"seq": 0, "type": "version", "ts": started, "git_commit": commit}
            header |= {"skill_version": f"s-{commit}", **(version or VERSION)}
            (events / "run-1.jsonl").write_text(json.dumps(header) + "\n")
            result: dict[str, Any] = {
                "task_name": f"org/{task}",
                "trial_name": trial.name,
                "started_at": started,
                "finished_at": started,
                "verifier_result": {"rewards": {"reward": 1.0 if i < wins else 0.0}},
            }
            if errors and task in errors and i == trials - 1:
                result["verifier_result"] = None
                result["exception_info"] = {"exception_type": errors[task]}
            (trial / "result.json").write_text(json.dumps(result))
    (job / "config.json").write_text("{}")
    if record is not None:
        (out / "records").mkdir()
        (out / "records" / "20261020T100000Z.json").write_text(json.dumps(record))
    return job


def all_tasks(wins: int, **overrides: int) -> dict[str, int]:
    return {task: overrides.get(task, wins) for task in DEV}


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"seed": 0, "dev": DEV, "holdout": HOLDOUT}))
    return path


def accept(
    tmp_path: Path,
    manifest: Path,
    old: Path,
    new: Path,
    *,
    targets: tuple[str, ...] = ("t1",),
    regression: list[str] | None = None,
    reruns: tuple[Path, ...] = (),
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict[str, Any]]:
    reg = tmp_path / "regression.json"
    reg.write_text(json.dumps(regression or []))
    argv = ["accept", "--old", str(old), "--new", str(new), "--manifest", str(manifest)]
    argv += ["--regression", str(reg), "--json"]
    argv += [a for t in targets for a in ("--target", t)]
    argv += [a for r in reruns for a in ("--rerun", str(r))]
    code = main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out) if out else {}


def test_accepts_a_change_that_meets_all_three_conditions(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(2, t1=1), commit="a")
    new = write_job(tmp_path, "new", all_tasks(2, t1=3), commit="c")
    code, result = accept(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"]) == (0, "pass")
    assert result["old_reusable"] is True


def test_rejects_a_lower_point_estimate(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(2, t1=1), commit="a")
    # The target gains 2 successes, two other tasks lose 3: total down by 1.
    new = write_job(tmp_path, "new", all_tasks(2, t1=3, t2=0, t3=1), commit="c")
    code, result = accept(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"]) == (1, "fail")
    assert result["conditions"]["point_estimate"]["passed"] is False
    assert result["conditions"]["targets"]["passed"] is True


def test_an_unchanged_total_passes_the_point_estimate(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(2, t1=1), commit="a")
    # The target gains 2, another task loses 2: the same total, so condition 1 holds.
    new = write_job(tmp_path, "new", all_tasks(2, t1=3, t2=0), commit="c")
    code, result = accept(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"]) == (0, "pass")
    assert result["conditions"]["point_estimate"]["diff_points"] == 0


def test_rejects_a_change_whose_targets_did_not_gain(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(2, t1=1, t2=3), commit="a")
    # Total up by 1, but the targets t1 and t2 together stay at 4.
    new = write_job(tmp_path, "new", all_tasks(2, t1=2, t2=2, t3=3), commit="c")
    code, result = accept(tmp_path, manifest, old, new, targets=("t1", "t2"), capsys=capsys)
    assert (code, result["verdict"]) == (1, "fail")
    assert result["conditions"]["point_estimate"]["passed"] is True
    assert result["conditions"]["targets"] == {"old": "4/6", "new": "4/6", "passed": False}


def test_a_regression_task_below_3_of_3_needs_a_rerun(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(3, t1=1), commit="a")
    new = write_job(tmp_path, "new", all_tasks(3, t1=3, t3=2), commit="c")
    code, result = accept(tmp_path, manifest, old, new, regression=["t2", "t3"], capsys=capsys)
    assert (code, result["verdict"]) == (1, "rerun-regression")
    assert result["rerun_tasks"] == ["t3"]
    assert result["conditions"]["regression"]["passed"] is None


def test_a_rerun_above_1_of_3_clears_the_regression_task(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(3, t1=1), commit="a")
    new = write_job(tmp_path, "new", all_tasks(3, t1=3, t3=2), commit="c")
    rerun = write_job(tmp_path, "rerun", {"t3": 2}, commit="c")
    code, result = accept(
        tmp_path, manifest, old, new, regression=["t2", "t3"], reruns=(rerun,), capsys=capsys
    )
    assert (code, result["verdict"]) == (0, "pass")
    rows = result["conditions"]["regression"]["tasks"]
    assert rows[1] == {"task": "t3", "round": "2/3", "rerun": "2/3", "status": "recovered"}


def test_a_rerun_at_1_of_3_is_a_regression(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(3, t1=1), commit="a")
    new = write_job(tmp_path, "new", all_tasks(3, t1=3, t3=2), commit="c")
    rerun = write_job(tmp_path, "rerun", {"t3": 1}, commit="c")
    code, result = accept(
        tmp_path, manifest, old, new, regression=["t3"], reruns=(rerun,), capsys=capsys
    )
    assert (code, result["verdict"]) == (1, "fail")
    assert result["conditions"]["regression"]["tasks"][0]["status"] == "regressed"
    assert result["conditions"]["regression"]["passed"] is False


def test_a_regression_fails_the_change_even_when_the_old_version_needs_a_rerun(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The regression set is judged on the new version alone; rerunning the old cannot save it.
    old = write_job(tmp_path, "old", all_tasks(3, t1=1), commit="a", record=None)
    new = write_job(tmp_path, "new", all_tasks(3, t1=3, t3=2), commit="c")
    rerun = write_job(tmp_path, "rerun", {"t3": 0}, commit="c")
    code, result = accept(
        tmp_path, manifest, old, new, regression=["t3"], reruns=(rerun,), capsys=capsys
    )
    assert (code, result["verdict"], result["old_reusable"]) == (1, "fail", False)


def test_refuses_a_rerun_of_another_version(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(3, t1=1), commit="a")
    new = write_job(tmp_path, "new", all_tasks(3, t1=3, t3=2), commit="c")
    rerun = write_job(tmp_path, "rerun", {"t3": 3}, commit="a")
    reg = tmp_path / "regression.json"
    reg.write_text(json.dumps(["t3"]))
    argv = ["accept", "--old", str(old), "--new", str(new), "--manifest", str(manifest)]
    argv += ["--regression", str(reg), "--target", "t1", "--rerun", str(rerun)]
    assert main(argv) == 2
    assert "version headers differ" in capsys.readouterr().err


def test_an_infrastructure_error_in_a_rerun_is_reported_as_one(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(3, t1=1), commit="a")
    new = write_job(tmp_path, "new", all_tasks(3, t1=3, t3=2), commit="c")
    rerun = write_job(tmp_path, "rerun", {"t3": 3}, commit="c", errors={"t3": "RuntimeError"})
    # The error ended the trial before the agent wrote its version header.
    (rerun / "t3__2" / "agent" / "events" / "run-1.jsonl").write_text("")
    reg = tmp_path / "regression.json"
    reg.write_text(json.dumps(["t3"]))
    argv = ["accept", "--old", str(old), "--new", str(new), "--manifest", str(manifest)]
    argv += ["--regression", str(reg), "--target", "t1", "--rerun", str(rerun)]
    assert main(argv) == 2
    assert "1 infrastructure error in the rerun trials" in capsys.readouterr().err


def test_an_old_infrastructure_error_without_a_header_adds_no_field_problems(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(2, t1=1), commit="a", errors={"t2": "RuntimeError"})
    (old / "t2__2" / "agent" / "events" / "run-1.jsonl").write_text("")
    new = write_job(tmp_path, "new", all_tasks(2, t1=3), commit="c")
    code, result = accept(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"]) == (1, "rerun-old")
    assert result["reuse_problems"] == ["the old round had infrastructure errors"]


def rerun_old_because(
    tmp_path: Path,
    manifest: Path,
    capsys: pytest.CaptureFixture[str],
    old_kwargs: dict[str, Any],
    new_kwargs: dict[str, Any] | None = None,
) -> list[str]:
    """A change that meets all three conditions, but whose old results fail a reuse condition."""
    old = write_job(tmp_path, "old", all_tasks(2, t1=1), commit="a", **old_kwargs)
    new = write_job(tmp_path, "new", all_tasks(2, t1=3), commit="c", **(new_kwargs or {}))
    code, result = accept(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"], result["old_reusable"]) == (1, "rerun-old", False)
    assert result["conditions"]["targets"]["passed"] is True
    return result["reuse_problems"]


def test_reuse_needs_the_same_version_fields(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    problems = rerun_old_because(tmp_path, manifest, capsys, {"version": VERSION | {"model": "m0"}})
    assert problems == ["version field model: ['\"m0\"'] old, ['\"m1\"'] new"]


def test_reuse_needs_the_same_benchmark_commit(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = RECORD | {"benchmark_commit": "d" * 40}
    problems = rerun_old_because(tmp_path, manifest, capsys, {"record": other})
    assert len(problems) == 1 and problems[0].startswith("benchmark commit")


def test_reuse_needs_the_same_machine(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    problems = rerun_old_because(tmp_path, manifest, capsys, {"record": RECORD | {"machine": "x"}})
    assert problems == ["machine: 'x' old, 'box' new"]


def test_reuse_needs_a_run_record(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    problems = rerun_old_because(tmp_path, manifest, capsys, {"record": None})
    assert problems == ["the old job has no run record"]


def test_reuse_needs_a_run_record_for_the_new_job_too(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    problems = rerun_old_because(tmp_path, manifest, capsys, {}, {"record": None})
    assert problems == ["the new job has no run record"]


def test_reuse_needs_a_known_machine(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # DATA_AGENT_MACHINE unset on both runs: equal, but the machine cannot be matched.
    unknown = {"record": RECORD | {"machine": None}}
    problems = rerun_old_because(tmp_path, manifest, capsys, unknown, unknown)
    assert problems == ["machine: None old, None new"]


def test_reuse_needs_one_value_per_version_field(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(2, t1=1), commit="a")
    new = write_job(tmp_path, "new", all_tasks(2, t1=3), commit="c")
    # One old trial ran under another model: the old job mixes two.
    events = old / "t2__0" / "agent" / "events" / "run-1.jsonl"
    events.write_text(json.dumps(json.loads(events.read_text()) | {"model": "m0"}) + "\n")
    code, result = accept(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"]) == (1, "rerun-old")
    assert result["reuse_problems"] == [
        "version field model: ['\"m0\"', '\"m1\"'] old, ['\"m1\"'] new"
    ]


@pytest.mark.parametrize("side", ["old", "new"])
def test_reuse_needs_every_version_field_on_every_trial(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str], side: str
) -> None:
    old = write_job(tmp_path, "old", all_tasks(2, t1=1), commit="a")
    new = write_job(tmp_path, "new", all_tasks(2, t1=3), commit="c")
    # One trial's header lacks `model`; every other trial has the same one.
    events = (old if side == "old" else new) / "t2__0" / "agent" / "events" / "run-1.jsonl"
    header = json.loads(events.read_text())
    del header["model"]
    events.write_text(json.dumps(header) + "\n")
    code, result = accept(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"]) == (1, "rerun-old")
    with_gap, without = "['\"m1\"', '(missing)']", "['\"m1\"']"
    before, after = (with_gap, without) if side == "old" else (without, with_gap)
    assert result["reuse_problems"] == [f"version field model: {before} old, {after} new"]


def test_text_output_lists_the_tasks_to_rerun(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(3, t1=1), commit="a", record=None)
    new = write_job(tmp_path, "new", all_tasks(3, t1=3, t3=2), commit="c")
    reg = tmp_path / "regression.json"
    reg.write_text(json.dumps(["t3"]))
    argv = ["accept", "--old", str(old), "--new", str(new), "--manifest", str(manifest)]
    argv += ["--regression", str(reg), "--target", "t1"]
    assert main(argv) == 1
    assert capsys.readouterr().out.splitlines() == [
        "verdict: rerun-old",
        "old results: rerun the old version",
        "  - the old job has no run record",
        "point_estimate: pass, old 10/12, new 11/12 (+8.3 points)",
        "targets: pass, old 1/3, new 3/3",
        "regression: rerun needed",
        "  t3: 2/3 -> rerun",
        "rerun these tasks on the new version: t3",
    ]


def test_refuses_a_regression_file_that_is_no_list_of_names(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(2, t1=1), commit="a")
    new = write_job(tmp_path, "new", all_tasks(2, t1=3), commit="c")
    reg = tmp_path / "regression.json"
    reg.write_text(json.dumps({"tasks": ["t1"]}))
    argv = ["accept", "--old", str(old), "--new", str(new), "--manifest", str(manifest)]
    argv += ["--regression", str(reg), "--target", "t1"]
    assert main(argv) == 2
    assert "JSON list of task names" in capsys.readouterr().err


def test_reuse_needs_results_within_two_weeks(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    problems = rerun_old_because(
        tmp_path,
        manifest,
        capsys,
        {"started": "2026-10-01T09:59:00Z"},
        {"started": "2026-10-15T10:00:00Z"},
    )
    assert problems == ["the old job started 14 days before the new one ended (over 14)"]


def test_results_exactly_two_weeks_old_are_reused(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(2, t1=1), commit="a", started="2026-10-01T10:00:00Z")
    new = write_job(tmp_path, "new", all_tasks(2, t1=3), commit="c", started="2026-10-15T10:00:00Z")
    code, result = accept(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"]) == (0, "pass")


def test_reuse_needs_a_round_without_infrastructure_errors(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(2, t1=1), commit="a", errors={"t2": "RuntimeError"})
    new = write_job(tmp_path, "new", all_tasks(2, t1=3), commit="c")
    code, result = accept(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"]) == (1, "rerun-old")
    assert result["reuse_problems"] == ["the old round had infrastructure errors"]
    # Infrastructure errors are never scored (plan §7), so conditions 1 and 2 are left out.
    assert set(result["conditions"]) == {"regression"}


def test_reuse_counts_infrastructure_errors_a_resume_dropped(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A trial that broke before writing events leaves only its result.json (ablate.keep_spend).
    dropped = tmp_path / "old" / "dropped" / "full" / "t2__x"
    dropped.mkdir(parents=True)
    (dropped / "result.json").write_text("{}")
    old = write_job(tmp_path, "old", all_tasks(2, t1=1), commit="a")
    new = write_job(tmp_path, "new", all_tasks(2, t1=3), commit="c")
    code, result = accept(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"]) == (1, "rerun-old")
    assert result["reuse_problems"] == ["the old round had infrastructure errors"]
    assert set(result["conditions"]) == {"regression"}


@pytest.mark.parametrize("side", ["old", "new"])
def test_reuse_needs_a_version_header_on_every_trial(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str], side: str
) -> None:
    old = write_job(tmp_path, "old", all_tasks(2, t1=1), commit="a")
    new = write_job(tmp_path, "new", all_tasks(2, t1=3), commit="c")
    (old if side == "old" else new).joinpath("t2__0", "agent", "events", "run-1.jsonl").unlink()
    code, result = accept(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"]) == (1, "rerun-old")
    assert result["reuse_problems"] == [f"1 {side} trial(s) have no version header"]


def test_an_agent_timeout_is_no_infrastructure_error(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Harbor does not retry a timeout: it is the agent's failure, scored 0 (evals.report).
    old = write_job(
        tmp_path, "old", all_tasks(2, t1=1), commit="a", errors={"t2": "AgentTimeoutError"}
    )
    new = write_job(tmp_path, "new", all_tasks(2, t1=3), commit="c")
    code, result = accept(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"]) == (0, "pass")


@pytest.mark.parametrize("where", ["old", "new", "rerun", "target", "regression"])
def test_refuses_tasks_outside_the_dev_set(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    holdout = {"h1": 3}
    old = write_job(
        tmp_path, "old", all_tasks(2, t1=1) | (holdout if where == "old" else {}), commit="a"
    )
    new = write_job(
        tmp_path, "new", all_tasks(2, t1=3) | (holdout if where == "new" else {}), commit="c"
    )
    reg = tmp_path / "regression.json"
    reg.write_text(json.dumps(["h1"] if where == "regression" else []))
    argv = ["accept", "--old", str(old), "--new", str(new), "--manifest", str(manifest)]
    argv += ["--regression", str(reg), "--target", "t1"]
    argv += ["--target", "h1"] if where == "target" else []
    if where == "rerun":
        argv += ["--rerun", str(write_job(tmp_path, "rerun", holdout, commit="c"))]
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not in the manifest's dev set" in captured.err
    assert "h1" not in captured.err  # holdout tasks are never named (ADR-0004)


def test_refuses_a_job_missing_dev_tasks(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(2, t1=1), commit="a")
    new = write_job(tmp_path, "new", {"t1": 3}, commit="c")
    code, _ = accept(tmp_path, manifest, old, new, capsys=capsys)
    assert code == 2
