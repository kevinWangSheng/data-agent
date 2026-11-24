"""`python -m evals accept --screening`: ADR-0008's targets, regression and reuse checks on a new
job that ran only the change's tasks (.scratch/skill-improve/spec.md "初筛与判定", ticket 126)."""

import json
from pathlib import Path
from typing import Any

import pytest
from test_eval_accept import DEV, HOLDOUT, RECORD, all_tasks, write_job

from evals.__main__ import main


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"seed": 0, "dev": DEV, "holdout": HOLDOUT}))
    return path


def screen(
    tmp_path: Path,
    manifest: Path,
    old: Path,
    new: Path,
    *,
    targets: tuple[str, ...] = ("t1",),
    regression: tuple[str, ...] = ("t2", "t3"),
    reruns: tuple[Path, ...] = (),
    capsys: pytest.CaptureFixture[str],
    as_json: bool = True,
) -> tuple[int, Any]:
    reg = tmp_path / "regression.json"
    reg.write_text(json.dumps(list(regression)))
    argv = ["accept", "--screening", "--old", str(old), "--new", str(new)]
    argv += ["--manifest", str(manifest), "--regression", str(reg)]
    argv += ["--json"] if as_json else []
    argv += [a for t in targets for a in ("--target", t)]
    argv += [a for r in reruns for a in ("--rerun", str(r))]
    code = main(argv)
    captured = capsys.readouterr()
    if not as_json:
        return code, captured.out
    return code, json.loads(captured.out) if captured.out else captured.err


def old_job(tmp_path: Path, **kw: Any) -> Path:
    return write_job(tmp_path, "old", all_tasks(3, t1=1), commit="a", **kw)


def test_a_change_whose_targets_gained_and_regression_held_passes(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The new job ran only t1 (target) and t2, t3 (regression); t4 is not needed.
    new = write_job(tmp_path, "new", {"t1": 2, "t2": 3, "t3": 3}, commit="c")
    code, result = screen(tmp_path, manifest, old_job(tmp_path), new, capsys=capsys)
    assert (code, result["verdict"]) == (0, "pass")
    assert result["screening"] is True
    assert "point_estimate" not in result["conditions"]
    assert result["conditions"]["targets"] == {"old": "1/3", "new": "2/3", "passed": True}


def test_targets_no_higher_than_the_old_version_fail(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    new = write_job(tmp_path, "new", {"t1": 1, "t2": 3, "t3": 3}, commit="c")
    code, result = screen(tmp_path, manifest, old_job(tmp_path), new, capsys=capsys)
    assert (code, result["verdict"]) == (1, "fail")
    assert result["conditions"]["targets"]["passed"] is False


def test_a_regression_task_below_three_needs_a_rerun_then_is_judged_with_it(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = old_job(tmp_path)
    new = write_job(tmp_path, "new", {"t1": 3, "t2": 2, "t3": 3}, commit="c")
    code, result = screen(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"], result["rerun_tasks"]) == (1, "rerun-regression", ["t2"])

    recovered = write_job(tmp_path, "rerun-ok", {"t2": 2}, commit="c")
    code, result = screen(tmp_path, manifest, old, new, reruns=(recovered,), capsys=capsys)
    assert (code, result["verdict"]) == (0, "pass")

    regressed = write_job(tmp_path, "rerun-bad", {"t2": 1}, commit="c")
    code, result = screen(tmp_path, manifest, old, new, reruns=(regressed,), capsys=capsys)
    assert (code, result["verdict"]) == (1, "fail")
    assert result["conditions"]["regression"]["passed"] is False


def test_an_old_version_that_cannot_be_reused_needs_a_rerun_of_it(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = old_job(tmp_path, started="2026-09-01T10:00:00Z")
    new = write_job(
        tmp_path, "new", {"t1": 3, "t2": 3, "t3": 3}, commit="c", record={**RECORD, "machine": "x"}
    )
    code, result = screen(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"], result["old_reusable"]) == (1, "rerun-old", False)
    problems = " | ".join(result["reuse_problems"])
    assert "days before the new one ended" in problems
    assert "machine" in problems


def test_a_regression_fails_even_when_the_old_version_cannot_be_reused(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = old_job(tmp_path, started="2026-09-01T10:00:00Z")
    new = write_job(tmp_path, "new", {"t1": 3, "t2": 2, "t3": 3}, commit="c")
    regressed = write_job(tmp_path, "rerun", {"t2": 0}, commit="c")
    code, result = screen(tmp_path, manifest, old, new, reruns=(regressed,), capsys=capsys)
    assert (code, result["verdict"]) == (1, "fail")


def test_a_new_job_missing_one_of_the_changes_tasks_is_refused(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    new = write_job(tmp_path, "new", {"t1": 3, "t2": 3}, commit="c")  # no t3
    code, err = screen(tmp_path, manifest, old_job(tmp_path), new, capsys=capsys)
    assert code == 2
    assert "1 of the change's 3 tasks" in err


def test_a_new_job_with_too_few_trials_of_a_task_is_refused(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    new = write_job(tmp_path, "new", {"t1": 2, "t2": 2, "t3": 2}, commit="c", trials=2)
    code, err = screen(tmp_path, manifest, old_job(tmp_path), new, capsys=capsys)
    assert code == 2
    assert "exactly 3 trials" in err


def test_the_old_version_still_covers_the_whole_dev_set(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", {"t1": 1, "t2": 3, "t3": 3}, commit="a")  # no t4
    new = write_job(tmp_path, "new", {"t1": 3, "t2": 3, "t3": 3}, commit="c")
    code, err = screen(tmp_path, manifest, old, new, capsys=capsys)
    assert code == 2
    assert "every task in the split is needed" in err


def test_old_trials_outside_the_dev_set_are_left_out_unnamed(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", {**all_tasks(3, t1=1), "h1": 0}, commit="a")
    new = write_job(tmp_path, "new", {"t1": 2, "t2": 3, "t3": 3}, commit="c")
    code, result = screen(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"]) == (0, "pass")
    assert "h1" not in json.dumps(result)


def test_an_old_infrastructure_error_leaves_targets_unscored_and_needs_the_old_rerun(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write_job(tmp_path, "old", all_tasks(3, t1=1), commit="a", errors={"t4": "Boom"})
    new = write_job(tmp_path, "new", {"t1": 3, "t2": 3, "t3": 3}, commit="c")
    code, result = screen(tmp_path, manifest, old, new, capsys=capsys)
    assert (code, result["verdict"]) == (1, "rerun-old")
    assert "targets" not in result["conditions"]


def test_text_output_has_no_point_estimate_line(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    new = write_job(tmp_path, "new", {"t1": 2, "t2": 3, "t3": 3}, commit="c")
    code, text = screen(tmp_path, manifest, old_job(tmp_path), new, capsys=capsys, as_json=False)
    assert code == 0
    assert text.startswith("verdict: pass")
    assert "point_estimate" not in text
    assert "targets: pass, old 1/3, new 2/3" in text
