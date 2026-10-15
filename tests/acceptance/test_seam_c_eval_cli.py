"""Seam C: the evaluation CLI, run as `python -m evals <command>` from the repo root.

External interface fixed here:

- `sample --tasks-dir DIR --dev-list FILE --size N --seed S --out FILE` writes a manifest
  `{"seed": S, "dev": [...], "holdout": [...]}`: dev = the tasks named in the dev list, holdout =
  N of the remaining tasks, stratified by `[metadata] difficulty` in each `<task>/task.toml`.
- `score JOB_DIR --json` prints `{"accuracy", "accuracy_stderr", "pass_at_2", "pass_at_3",
  "n_trials"}` for a Harbor job directory (`<trial>/result.json`), computed the way data-eng-bench's
  `leaderboard/core/metrics.py` does: percentages, errored trials count as 0.
- `compare --baseline JOB_DIR --candidate JOB_DIR --seed S --n-boot N --json` prints the paired
  per-task bootstrap `{"diff", "ci_low", "ci_high", "significant"}` in percentage points.
- `report JOB_DIR --manifest FILE --split dev|holdout --out DIR` writes `DIR/report.json` (plus any
  other files). Holdout: aggregate scores only. Dev: also `per_task` = {task: {successes, trials}}.
- `scrub DIR [--hostname NAME ...]` rewrites files in place, removing API keys (the value of
  DATA_AGENT_API_KEY and anything key-shaped), the DATA_AGENT_BASE_URL endpoint and its host,
  IPv4 addresses and the given hostnames.
- `spend RUNS_DIR --budget USD` sums `cost_usd` over the result events of every `*.jsonl` event
  file under RUNS_DIR; exit 0 below 80% of the budget, non-zero at or above it.

Expected scores below were computed by running the upstream metrics.py (data-eng-bench) on the
fixture rewards in tests/fixtures/harbor_job/.
"""

import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from seam_support import EVAL_MANIFEST, HARBOR_JOB, REPO_ROOT

DEV_TASKS = ["dbt-alpha", "dbt-bravo", "dbt-charlie"]
HOLDOUT_TASKS = ["dbt-delta", "dbt-echo", "dbt-foxtrot", "dbt-golf"]


def evals(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "evals", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def evals_ok(*args: str, env: dict[str, str] | None = None) -> str:
    proc = evals(*args, env=env)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def write_trial(job: Path, task: str, attempt: int, reward: float | None) -> None:
    """One Harbor trial directory; reward None means the trial errored."""
    trial = job / f"{task}__t{attempt}"
    trial.mkdir(parents=True)
    result = {
        "task_name": task,
        "trial_name": trial.name,
        "verifier_result": None if reward is None else {"rewards": {"reward": reward}},
        "exception_info": None
        if reward is not None
        else {"exception_type": "AgentTimeoutError", "exception_message": "timed out"},
    }
    (trial / "result.json").write_text(json.dumps(result))


def write_job(job: Path, rewards: dict[str, list[float | None]]) -> Path:
    for task, task_rewards in rewards.items():
        for attempt, reward in enumerate(task_rewards):
            write_trial(job, task, attempt, reward)
    return job


# --- Sampling -------------------------------------------------------------------------------


def write_tasks(root: Path) -> tuple[Path, Path, dict[str, str]]:
    """25 tasks: 5 in the dev list, 20 left over (10 medium, 8 hard, 2 very_hard)."""
    difficulty: dict[str, str] = {}
    for i in range(5):
        difficulty[f"dev-{i}"] = "hard"
    for i in range(10):
        difficulty[f"medium-{i}"] = "medium"
    for i in range(8):
        difficulty[f"hard-{i}"] = "hard"
    for i in range(2):
        difficulty[f"very-hard-{i}"] = "very_hard"
    tasks = root / "tasks"
    for name, level in difficulty.items():
        (tasks / name).mkdir(parents=True)
        (tasks / name / "task.toml").write_text(
            f'version = "1.0.0"\n\n[metadata]\nname = "{name}"\ndifficulty = "{level}"\n'
        )
    dev_list = root / "fast-5.txt"
    dev_list.write_text("".join(f"dev-{i}\n" for i in range(5)))
    return tasks, dev_list, difficulty


@pytest.mark.xfail(strict=True, reason="ticket 10")
def test_sample_is_reproducible_and_stratified(tmp_path: Path) -> None:
    tasks, dev_list, difficulty = write_tasks(tmp_path)

    def sample(seed: int, out: str) -> dict[str, list[str]]:
        evals_ok(
            "sample",
            *("--tasks-dir", str(tasks), "--dev-list", str(dev_list)),
            *("--size", "10", "--seed", str(seed), "--out", str(tmp_path / out)),
        )
        return json.loads((tmp_path / out).read_text())

    first, again, other = sample(7, "a.json"), sample(7, "b.json"), sample(8, "c.json")
    assert first == again
    assert sorted(first["dev"]) == [f"dev-{i}" for i in range(5)]
    holdout = first["holdout"]
    assert len(holdout) == len(set(holdout)) == 10
    assert not set(holdout) & set(first["dev"])
    assert Counter(difficulty[t] for t in holdout) == {"medium": 5, "hard": 4, "very_hard": 1}
    assert set(other["holdout"]) != set(holdout)


# --- Scoring --------------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="ticket 10")
def test_score_matches_leaderboard_metrics() -> None:
    score = json.loads(evals_ok("score", str(HARBOR_JOB), "--json"))
    assert score["n_trials"] == 21
    assert score["accuracy"] == pytest.approx(52.380952, abs=1e-4)
    assert score["accuracy_stderr"] == pytest.approx(9.523810, abs=1e-4)
    assert score["pass_at_2"] == pytest.approx(0.7143, abs=1e-4)
    assert score["pass_at_3"] == pytest.approx(0.8571, abs=1e-4)


@pytest.mark.xfail(strict=True, reason="ticket 10")
def test_errored_trials_count_as_zero(tmp_path: Path) -> None:
    job = write_job(tmp_path / "job", {"t1": [1.0, None, None], "t2": [1.0, 1.0, 1.0]})
    score = json.loads(evals_ok("score", str(job), "--json"))
    assert score["n_trials"] == 6
    assert score["accuracy"] == pytest.approx(100 * 4 / 6, abs=1e-4)


# --- Paired bootstrap -----------------------------------------------------------------------


def compare(baseline: Path, candidate: Path) -> dict[str, float | bool]:
    out = evals_ok(
        "compare",
        *("--baseline", str(baseline), "--candidate", str(candidate)),
        *("--seed", "0", "--n-boot", "2000", "--json"),
    )
    return json.loads(out)


@pytest.mark.xfail(strict=True, reason="ticket 10")
def test_bootstrap_interval_on_known_data(tmp_path: Path) -> None:
    tasks = [f"task-{i}" for i in range(10)]
    fail = write_job(tmp_path / "fail", {t: [0.0, 0.0, 0.0] for t in tasks})
    passes = write_job(tmp_path / "pass", {t: [1.0, 1.0, 1.0] for t in tasks})
    half = write_job(
        tmp_path / "half",
        {t: [1.0, 1.0, 1.0] if i < 5 else [0.0, 0.0, 0.0] for i, t in enumerate(tasks)},
    )

    same = compare(fail, fail)
    assert same["diff"] == pytest.approx(0)
    assert same["ci_low"] == pytest.approx(0) and same["ci_high"] == pytest.approx(0)
    assert same["significant"] is False

    best = compare(fail, passes)
    assert best["diff"] == pytest.approx(100)
    assert best["ci_low"] == pytest.approx(100) and best["ci_high"] == pytest.approx(100)
    assert best["significant"] is True

    mixed = compare(fail, half)
    assert mixed["diff"] == pytest.approx(50)
    assert 0 < mixed["ci_low"] < 50 < mixed["ci_high"] <= 100
    assert mixed["significant"] is True


# --- Reports --------------------------------------------------------------------------------


def report(tmp_path: Path, split: str) -> tuple[dict[str, object], str]:
    out = tmp_path / f"report-{split}"
    proc = evals(
        "report",
        str(HARBOR_JOB),
        *("--manifest", str(EVAL_MANIFEST), "--split", split, "--out", str(out)),
    )
    assert proc.returncode == 0, proc.stderr
    files = [p for p in sorted(out.rglob("*")) if p.is_file()]
    everything = proc.stdout + proc.stderr + "".join(p.read_text() for p in files)
    return json.loads((out / "report.json").read_text()), everything


@pytest.mark.xfail(strict=True, reason="ticket 10")
def test_holdout_report_has_aggregates_only(tmp_path: Path) -> None:
    summary, everything = report(tmp_path, "holdout")
    assert summary["accuracy"] == pytest.approx(58.333333, abs=1e-4)
    assert summary["n_trials"] == 12
    for task in HOLDOUT_TASKS + DEV_TASKS:
        assert task not in everything, f"task id {task} leaked into the holdout report"
    trial_suffixes = ("Qm3xT9a", "Lp8vR2c", "Zk4nW7e")
    for detail in (*trial_suffixes, "timed out", "AgentTimeoutError", "Traceback", "per_task"):
        assert detail not in everything, f"per-trial detail {detail!r} leaked"


@pytest.mark.xfail(strict=True, reason="ticket 10")
def test_dev_report_has_per_task_outcomes(tmp_path: Path) -> None:
    summary, everything = report(tmp_path, "dev")
    assert summary["accuracy"] == pytest.approx(44.444444, abs=1e-4)
    assert summary["per_task"] == {
        "dbt-alpha": {"successes": 3, "trials": 3},
        "dbt-bravo": {"successes": 1, "trials": 3},
        "dbt-charlie": {"successes": 0, "trials": 3},
    }
    for task in HOLDOUT_TASKS:
        assert task not in everything


# --- Scrub ----------------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="ticket 11")
def test_scrub_removes_keys_endpoints_ips_and_hostnames(tmp_path: Path) -> None:
    job = tmp_path / "job"
    shutil.copytree(HARBOR_JOB, job)
    # Key-shaped values are assembled here so the repository never contains a literal one.
    env_key = "sk-" + "e2e7" * 8
    stray_key = "sk-" + "0123456789abcdef" * 2
    anthropic_key = "sk-ant-api03-" + "Xy7_" * 12
    endpoint = "https://llm-gw.corp.example:8443/anthropic"
    secrets = [
        env_key,
        stray_key,
        anthropic_key,
        endpoint,
        "llm-gw.corp.example",
        "203.0.113.7",
        "10.20.30.40",
        "build-box-7",
    ]
    events = job / "dbt-alpha__Qm3xT9a" / "agent" / "events.jsonl"
    leak = {
        "seq": 4,
        "type": "tool_end",
        "tool": "Bash",
        "output": (
            f"POST {endpoint}/v1/messages x-api-key={env_key} (fallback key {stray_key}, "
            f"{anthropic_key}) from 10.20.30.40 via 203.0.113.7 on build-box-7; "
            "built customer_orders in 3.2s"
        ),
    }
    with events.open("a") as f:
        f.write(json.dumps(leak) + "\n")
    result_path = job / "dbt-echo__Lp8vR2c" / "result.json"
    result = json.loads(result_path.read_text())
    result["exception_info"]["exception_message"] = f"connection to 203.0.113.7 ({endpoint}) reset"
    result_path.write_text(json.dumps(result, indent=2))

    env = {**os.environ, "DATA_AGENT_API_KEY": env_key, "DATA_AGENT_BASE_URL": endpoint}
    evals_ok("scrub", str(job), "--hostname", "build-box-7", env=env)

    for path in (p for p in job.rglob("*") if p.is_file()):
        text = path.read_text()
        for secret in secrets:
            assert secret not in text, f"{secret!r} survived in {path.relative_to(job)}"
        if path.suffix == ".json":
            json.loads(text)
        elif path.suffix == ".jsonl":
            for line in text.splitlines():
                json.loads(line)
    assert "built customer_orders in 3.2s" in events.read_text()


# --- Cumulative spend guard -----------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="ticket 11")
def test_spend_stops_at_80_percent_of_budget() -> None:
    # The fixture's event files record 19 finished runs at $0.125 each: $2.375 in total.
    assert evals("spend", str(HARBOR_JOB), "--budget", "3.5").returncode == 0
    assert evals("spend", str(HARBOR_JOB), "--budget", "2.96875").returncode != 0  # exactly 80%
    assert evals("spend", str(HARBOR_JOB), "--budget", "2.5").returncode != 0
