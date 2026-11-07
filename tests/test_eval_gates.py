"""Gates, infrastructure exclusions and the configuration-selection report (eval-readiness
ticket 04): a dev report separates correctness, infrastructure exclusions and efficiency
warnings and records the run contract it was measured under; `evals select` sets the dev
reports of the configs side by side only when they share that contract and task set."""

import json
from pathlib import Path
from typing import Any

import pytest
from lifecycle_events import dispatches

from evals.__main__ import main

DEV = ["t1", "t2"]
HOLDOUT = ["h-secret"]
RECORD = {"benchmark_commit": "b" * 40, "machine": "box", "model": "m1"}
REQUEST = {"model": "m1", "split": "dev", "tasks": DEV, "agent_kwargs": ["agent_timeout_sec=1500"]}


def write_trial(
    job: Path,
    task: str,
    name: str,
    reward: float | None,
    *,
    config: str = "full",
    commit: str = "c1",
    skill: str = "s1",
    events: list[dict[str, Any]] | None = None,
    exception: str | None = None,
    cost: float = 0.01,
) -> None:
    version = {"type": "version", "git_commit": commit, "skill_version": skill, "config": config}
    version["model"] = "m1"
    end = {"type": "result", "subtype": "success", "num_turns": 3, "cost_usd": cost}
    events_dir = job / name / "agent" / "events"
    events_dir.mkdir(parents=True)
    lines = "".join(json.dumps(e) + "\n" for e in (version, *(events or []), end))
    (events_dir / "run-1.jsonl").write_text(lines)
    result: dict[str, Any] = {
        "task_name": f"org/{task}",
        "trial_name": name,
        "verifier_result": None if reward is None else {"rewards": {"reward": reward}},
        "agent_result": {"cost_usd": cost, "n_output_tokens": 10},
    }
    if exception:
        result["exception_info"] = {"exception_type": exception}
    (job / name / "result.json").write_text(json.dumps(result))


def write_out(
    root: Path,
    config: str,
    rewards: dict[str, list[float]],
    *,
    commit: str = "c1",
    skill: str = "s1",
    record: dict[str, Any] = RECORD,
    request: dict[str, Any] = REQUEST,
    dropped: dict[str, float] | None = None,
    interrupted: list[float] | None = None,
    events: dict[str, list[dict[str, Any]]] | None = None,
    start: int = 0,
) -> Path:
    """OUT `root/config` as `evals ablate` leaves it: the job, a run record, the request, and
    the trials a resume deleted, kept as evals.ablate.keep_spend keeps them (result.json and the
    event files moved into the trial's directory under OUT/dropped/<config>/): `dropped`
    infrastructure errors (task -> cost) and `interrupted` trials (their costs; no result.json).
    Trial `<task>__<i>` from i = `start`."""
    out = root / config
    job = out / "jobs" / config
    for task, values in rewards.items():
        for i, reward in enumerate(values, start):
            name = f"{task}__{i}"
            write_trial(job, task, name, reward, config=config, commit=commit, skill=skill,
                        events=(events or {}).get(name))  # fmt: skip
    (job / "config.json").write_text("{}")
    (out / "records").mkdir()
    (out / "records" / "20261107T100000Z.json").write_text(json.dumps(record))
    (out / "request.json").write_text(json.dumps(request))
    kept = out / "dropped" / config
    for i, (task, cost) in enumerate((dropped or {}).items()):
        name = f"{task}__dropped{i}"
        write_trial(kept, task, name, None, config=config,
                    exception="ApiInternalServerError", cost=cost)  # fmt: skip
        flatten_events(kept / name)
    for i, cost in enumerate(interrupted or []):
        name = f"{DEV[0]}__stopped{i}"
        write_trial(kept, DEV[0], name, None, config=config, cost=cost)
        (kept / name / "result.json").unlink()
        flatten_events(kept / name)
    return job


def flatten_events(trial_dir: Path) -> None:
    """Move the trial's event files to its directory, as keep_spend does (ticket 03)."""
    events_dir = trial_dir / "agent" / "events"
    for events in events_dir.iterdir():
        events.rename(trial_dir / events.name)
    events_dir.rmdir()
    (trial_dir / "agent").rmdir()


@pytest.fixture
def manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo-manifest.json"
    repo.write_text(json.dumps({"seed": 0, "dev": [], "holdout": HOLDOUT}))
    monkeypatch.setattr("evals.report.REPO_MANIFEST", repo)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"seed": 0, "dev": DEV, "holdout": HOLDOUT}))
    return path


def report(
    jobs: list[Path], manifest: Path, out: Path, capsys: pytest.CaptureFixture[str], split: str
) -> dict[str, Any]:
    argv = ["report", *map(str, jobs), "--manifest", str(manifest), "--split", split]
    code = main([*argv, "--out", str(out)])
    assert code == 0, capsys.readouterr().err
    return json.loads((out / "report.json").read_text())


GOOD = {"t1": [1.0, 1.0, 0.0], "t2": [1.0, 1.0, 1.0]}


def test_a_dev_report_states_the_infrastructure_errors_it_excluded(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The scored trials are complete; a trial a resume dropped is out of the score, counted
    with its cost, and fails the infrastructure gate (ADR-0008: infra_error must be 0)."""
    job = write_out(tmp_path, "full", GOOD, dropped={"t1": 0.02})
    r = report([job], manifest, tmp_path / "r", capsys, "dev")
    assert r["n_trials"] == 6
    assert r["infra_excluded"] == {"trials": 1, "cost_usd": pytest.approx(0.02)}
    gates = r["gates"]
    assert gates["infrastructure"] == {
        "passed": False,
        "trials": 1,
        "cost_usd": pytest.approx(0.02),
        "interrupted": {"trials": 0, "cost_usd": 0},
    }
    assert gates["correctness"] == {"successes": 5, "failures": 1, "trials": 6}


def test_without_dropped_trials_the_infrastructure_gate_passes(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    job = write_out(tmp_path, "full", GOOD)
    r = report([job], manifest, tmp_path / "r", capsys, "dev")
    assert r["infra_excluded"] == {"trials": 0, "cost_usd": 0}
    assert r["gates"]["infrastructure"]["passed"] is True


def test_interrupted_trials_are_stated_apart_and_do_not_fail_the_gate(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A trial Harbor was stopped in (kept without result.json, ticket 03) was rerun by the
    resume: it is out of the score and stated with what it cost, but it is not an
    infrastructure error, so the gate still passes."""
    job = write_out(tmp_path, "full", GOOD, interrupted=[0.3, 0.0])
    r = report([job], manifest, tmp_path / "r", capsys, "dev")
    assert r["n_trials"] == 6
    assert r["infra_excluded"] == {"trials": 0, "cost_usd": 0}
    assert r["interrupted_excluded"] == {"trials": 2, "cost_usd": pytest.approx(0.3)}
    infrastructure = r["gates"]["infrastructure"]
    assert infrastructure["passed"] is True
    assert infrastructure["interrupted"] == {"trials": 2, "cost_usd": pytest.approx(0.3)}


def test_merged_jobs_total_the_exclusions_of_every_out(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = write_out(tmp_path / "s1", "full", {"t1": [1.0], "t2": [1.0]}, dropped={"t1": 0.01})
    rest = {"t1": [1.0, 0.0], "t2": [1.0, 1.0]}
    b = write_out(tmp_path / "s2", "full", rest, dropped={"t2": 0.03}, start=1)
    r = report([a, b], manifest, tmp_path / "r", capsys, "dev")
    assert r["infra_excluded"] == {"trials": 2, "cost_usd": pytest.approx(0.04)}


def test_efficiency_is_unknown_when_no_trial_records_dispatches(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Event files that predate dispatch events (a verifier ran, no dispatch recorded) cannot
    show a full redispatch, so the efficiency line is neither passed nor failed."""
    old = [{"type": "verifier_start", "verifier": "verifier", "agent_id": "v1"}]
    events = {f"{t}__{i}": old for t in DEV for i in range(3)}
    job = write_out(tmp_path, "full", GOOD, events=events)
    r = report([job], manifest, tmp_path / "r", capsys, "dev")
    assert (r["gates"]["efficiency"]["passed"], r["gates"]["efficiency"]["warnings"]) == (None, [])


def test_a_dev_report_records_the_run_contract(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    job = write_out(tmp_path, "full", GOOD)
    r = report([job], manifest, tmp_path / "r", capsys, "dev")
    assert r["run"] == {
        "config": "full",
        "git_commit": "c1",
        "skill_version": "s1",
        "model": "m1",
        "benchmark_commit": "b" * 40,
        "machine": "box",
        "split": "dev",
        "agent_kwargs": ["agent_timeout_sec=1500"],
    }


def test_a_holdout_report_gives_exclusions_as_counts_and_no_gates(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Holdout (ADR-0004): the exclusions are counts and a cost; no gate names a trial."""
    request = {"model": "m1", "agent_kwargs": REQUEST["agent_kwargs"]}
    job = write_out(
        tmp_path, "full", {"h-secret": [1.0, 0.0, 1.0]}, request=request, dropped={"h-secret": 0.5}
    )
    out = tmp_path / "r"
    r = report([job], manifest, out, capsys, "holdout")
    assert r["infra_excluded"] == {"trials": 1, "cost_usd": pytest.approx(0.5)}
    assert "gates" not in r
    assert "h-secret" not in (out / "report.json").read_text()


# --- evals select ------------------------------------------------------------------------------

CONFIGS = ("full", "baseline", "no-verifiers")


def dev_reports(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str], **changes: Any
) -> list[Path]:
    """Each config's dev report directory; `changes[config]` overrides that config's OUT."""
    rewards = {
        "full": GOOD,
        "baseline": {"t1": [1.0, 0.0, 0.0], "t2": [1.0, 1.0, 0.0]},
        "no-verifiers": {"t1": [1.0, 1.0, 0.0], "t2": [1.0, 0.0, 1.0]},
    }
    events = {
        "full": {"t1__0": dispatches(21, 21), "t1__1": dispatches(21, 6)},
        "baseline": {},
        "no-verifiers": {},
    }
    dirs = []
    for config in CONFIGS:
        # baseline loads no Skills: its version header records "none" (data_agent.agent).
        skill = {"skill": "none"} if config == "baseline" else {}
        kwargs: dict[str, Any] = {"events": events[config], **skill} | changes.get(config, {})
        job = write_out(tmp_path / "outs", config, rewards[config], **kwargs)
        out = tmp_path / "reports" / config
        report([job], manifest, out, capsys, "dev")
        dirs.append(out)
    return dirs


def select(dirs: list[Path], out: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = main(["select", *map(str, dirs), "--out", str(out)])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_select_sets_the_three_configs_side_by_side(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Success, cost and process evidence per config, and the gates kept apart: full's
    redispatch is an efficiency warning, not a correctness failure."""
    dirs = dev_reports(tmp_path, manifest, capsys, full={"dropped": {"t2": 0.04}})
    code, printed = select(dirs, tmp_path / "sel", capsys)
    assert code == 0, printed
    table = json.loads((tmp_path / "sel" / "selection.json").read_text())
    assert table["run"]["git_commit"] == "c1"
    rows = {row["config"]: row for row in table["configs"]}
    assert list(rows) == list(CONFIGS)
    full = rows["full"]
    assert (full["successes"], full["trials"]) == (5, 6)
    assert full["accuracy"] == pytest.approx(100 * 5 / 6)
    assert full["cost_usd"] == pytest.approx(0.06)
    assert full["cost_per_success_usd"] == pytest.approx(0.06 / 5)
    assert full["infra_excluded"] == {"trials": 1, "cost_usd": pytest.approx(0.04)}
    assert full["efficiency"] == {"passed": False, "warnings": 1}
    assert "full_redispatches" in full["process"]
    assert rows["baseline"]["efficiency"] == {"passed": True, "warnings": 0}
    md = (tmp_path / "sel" / "selection.md").read_text()
    for config in CONFIGS:
        assert f"| {config} |" in md
    assert "failed (1 warning)" in md
    assert md in printed


@pytest.mark.parametrize(
    ("change", "what"),
    [
        pytest.param({"baseline": {"commit": "c2"}}, "git_commit", id="commit"),
        pytest.param({"no-verifiers": {"skill": "s2"}}, "skill_version", id="skill"),
        pytest.param({"baseline": {"record": {**RECORD, "machine": "x"}}}, "machine", id="machine"),
        pytest.param(
            {"no-verifiers": {"request": {**REQUEST, "agent_kwargs": []}}},
            "agent_kwargs",
            id="run-parameters",
        ),
    ],
)
def test_select_refuses_reports_of_different_contracts(
    tmp_path: Path,
    manifest: Path,
    capsys: pytest.CaptureFixture[str],
    change: dict[str, Any],
    what: str,
) -> None:
    dirs = dev_reports(tmp_path, manifest, capsys, **change)
    code, printed = select(dirs, tmp_path / "sel", capsys)
    assert code == 2
    assert what in printed
    assert not (tmp_path / "sel").exists()


def test_select_refuses_two_reports_of_one_config(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dirs = dev_reports(tmp_path, manifest, capsys)
    code, printed = select([dirs[0], dirs[0], dirs[1]], tmp_path / "sel", capsys)
    assert code == 2
    assert "config" in printed


def test_select_refuses_reports_over_other_tasks(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dirs = dev_reports(tmp_path, manifest, capsys)
    data = json.loads((dirs[1] / "report.json").read_text())
    data["per_task"]["t3"] = {"successes": 1, "trials": 3}
    (dirs[1] / "report.json").write_text(json.dumps(data))
    code, printed = select(dirs, tmp_path / "sel", capsys)
    assert code == 2
    assert "tasks" in printed


def test_select_refuses_a_holdout_report(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The product default is chosen on the dev set; holdout reports carry no per-task data or
    process evidence to choose by (ADR-0004)."""
    request = {"model": "m1", "agent_kwargs": REQUEST["agent_kwargs"]}
    job = write_out(tmp_path / "h", "full", {"h-secret": [1.0, 1.0, 1.0]}, request=request)
    holdout = tmp_path / "holdout-report"
    report([job], manifest, holdout, capsys, "holdout")
    dirs = dev_reports(tmp_path, manifest, capsys)
    code, printed = select([holdout, *dirs[1:]], tmp_path / "sel", capsys)
    assert code == 2
    assert "dev" in printed
    assert "h-secret" not in printed
