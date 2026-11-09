"""The leaderboard run (leaderboard-run ticket 01): one config on the registry's data-eng-bench.

Harbor is replaced at ablate's subprocess seam (`ablate.run_harbor`) by `BoardHarbor`, ablate's
FakeHarbor taught `harbor run -d`: it strips the registry's org from the `-i` patterns and writes
the job config's `datasets` as Harbor does. Docker (`board.docker`), the model account's balance,
readiness's machine probes and the wheel fill are replaced as in the ablate tests; everything
after Harbor (spend guard, scrub, merge, report) runs for real.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_eval_ablate import FakeHarbor, StopWhen, by_pass_rate

from evals import ablate, balance, board, readiness
from evals.__main__ import main as evals_main

GIB = readiness.GIB
DEV = "dbt-alpha"
OTHER = "dbt-other"  # in neither split of the manifest
HOLDOUT = "dbt-delta"
ZERO = board.ZERO_TASK
REGISTRY_TASKS = {
    DEV: "easy",
    OTHER: "hard",
    HOLDOUT: "hard",
    ZERO: "medium",
}
REPO_REGISTRY, REPO_MANIFEST = board.REGISTRY, board.MANIFEST
REGISTRY_REF = "sha256:de31878dba7e5a9aad85dc8276820b0a20ab593aed46ff93dfbbbdb6007ec995"


class BoardHarbor(FakeHarbor):
    """FakeHarbor for `harbor run -d <dataset>@<ref> -i <org>/<task>`; `raw` keeps the calls as
    given, and each trial gets the per-trial config.json Harbor writes."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.raw: list[list[str]] = []

    def __call__(self, args: list[str], log: Path, stop_when: StopWhen) -> tuple[int, str | None]:
        self.raw.append(args)
        if args[0] != "run":
            result = super().__call__(args, log, stop_when)
            self.add_trial_files(Path(args[args.index("-p") + 1]))
            return result
        patterns = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
        plain = [a.split("/", 1)[1] if a in patterns else a for a in args]
        result = super().__call__(plain, log, stop_when)
        job = Path(args[args.index("-o") + 1]) / args[args.index("--job-name") + 1]
        config = json.loads((job / "config.json").read_text())
        name, ref = args[args.index("-d") + 1].split("@", 1)
        config["datasets"] = [{"name": name, "ref": ref, "task_names": patterns}]
        (job / "config.json").write_text(json.dumps(config))
        self.add_trial_files(job)
        return result

    def add_trial_files(self, job: Path) -> None:
        """What Harbor's trials also record: the start time (each trial here took 60 seconds)
        and the trial's config.json with the registry task it ran."""
        for path in job.glob("*/result.json"):
            result = json.loads(path.read_text())
            if "started_at" not in result:
                end = datetime.fromisoformat(result["finished_at"])
                result["started_at"] = (end - timedelta(seconds=60)).isoformat()
                path.write_text(json.dumps(result))
            task = {"name": result["task_name"], "ref": "sha256:fake", "source": board.DATASET}
            (path.parent / "config.json").write_text(json.dumps({"task": task}))


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """A clean commit on a labelled machine with Docker, money, the base image and wheels; the
    registry and manifest of this test's tasks."""
    monkeypatch.setattr(ablate, "git_commit", lambda: "c0ffee")
    monkeypatch.setattr(board, "git_commit", lambda: "c0ffee")
    monkeypatch.setenv("DATA_AGENT_MACHINE", "mac-colima")
    reply = {"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "500"}]}
    monkeypatch.setattr(balance, "fetch", lambda: reply)
    root = tmp_path / "docker-root"
    root.mkdir()
    monkeypatch.setattr(readiness, "docker_info", lambda: (16 * GIB, str(root)))
    monkeypatch.setattr(readiness, "free_disk", lambda path: 100 * GIB)
    found: dict[str, Any] = {"fills": [], "docker": [], "image": True, "fail": None, "order": []}
    monkeypatch.setattr(ablate, "fill_wheels", lambda images: found["fills"].append(images))

    def docker(*args: str) -> tuple[int, str]:
        found["docker"].append(list(args))
        found["order"].append(("docker", args[0]))
        if args[:2] == ("image", "inspect"):
            return (0 if found["image"] else 1), ""
        return (1, "denied") if found["fail"] == args[0] else (0, "")

    monkeypatch.setattr(board, "docker", docker)
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "dataset": board.DATASET,
                "ref": board.DATASET_REF,
                "tasks": {
                    task: {"ref": f"sha256:{i:064x}", "difficulty": level}
                    for i, (task, level) in enumerate(REGISTRY_TASKS.items())
                },
            }
        )
    )
    monkeypatch.setattr(board, "REGISTRY", registry)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"seed": 1, "dev": [DEV, ZERO], "holdout": [HOLDOUT]}))
    monkeypatch.setattr(board, "MANIFEST", manifest)
    return found


def run(monkeypatch: pytest.MonkeyPatch, harbor: FakeHarbor, out: Path, *extra: str) -> int:
    def recording(args: list[str], log: Path, stop_when: StopWhen) -> tuple[int, str | None]:
        return harbor(args, log, stop_when)

    monkeypatch.setattr(ablate, "run_harbor", recording)
    argv = ["board", "--config", "no-verifiers", "--out", str(out), "--budget", "100", *extra]
    return evals_main(argv)


def report(out: Path) -> dict[str, Any]:
    latest = sorted((out / "reports").iterdir())[-1]
    return json.loads((latest / "board.json").read_text())


def all_files_text(path: Path) -> str:
    return "\n".join(
        f"{p.relative_to(path)}\n{p.read_text(errors='replace')}"
        for p in sorted(path.rglob("*"))
        if p.is_file()
    )


def test_the_dataset_is_the_one_the_leaderboard_tracks() -> None:
    """data-eng-bench leaderboard/src/leaderboard/core/hub.py:26-27."""
    assert board.DATASET == "snowflake-labs/data-eng-bench"
    assert board.DATASET_REF == REGISTRY_REF


def test_the_repo_registry_lists_the_103_v1_tasks_with_digests_and_the_holdout() -> None:
    tasks = board.read_registry(REPO_REGISTRY)
    assert len(tasks) == 103
    assert all(t["ref"].startswith("sha256:") and len(t["ref"]) == 71 for t in tasks.values())
    assert {t["difficulty"] for t in tasks.values()} == {"easy", "medium", "hard", "very_hard"}
    manifest = json.loads(REPO_MANIFEST.read_text())
    assert set(manifest["holdout"]) <= set(tasks)
    assert set(manifest["dev"]) <= set(tasks)
    assert board.ZERO_TASK in tasks
    assert tasks["cohort-retention-matrix"]["ref"] == (
        "sha256:90ba6b3940bf2725a1b3724319c86560593aa937501e4f7541f1e6eb07b2d039"
    )


def test_harbor_runs_the_registry_dataset_without_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    harbor = BoardHarbor(by_pass_rate)
    assert run(monkeypatch, harbor, tmp_path / "out", "--skip-smoke") == 0
    (call,) = harbor.raw
    assert call[:2] == ["run", "--config"]
    assert call[call.index("-d") + 1] == f"snowflake-labs/data-eng-bench@{REGISTRY_REF}"
    assert "--path" not in call and "-p" not in call
    assert call[call.index("-k") + 1] == "3"
    assert call[call.index("--ak") + 1] == "run_config=no-verifiers"
    includes = sorted(call[i + 1] for i, a in enumerate(call) if a == "-i")
    assert includes == sorted(f"snowflake-labs/{t}" for t in REGISTRY_TASKS)
    config = yaml.safe_load(Path(call[call.index("--config") + 1]).read_text())
    assert config.get("timeout_multiplier") in (None, 1.0)
    forbidden = {
        "agent_timeout_multiplier",
        "verifier_timeout_multiplier",
        "agent_setup_timeout_multiplier",
        "environment_build_timeout_multiplier",
        "override_timeout_sec",
        "override_setup_timeout_sec",
        "max_timeout_sec",
        "override_cpus",
        "override_gpus",
        "override_memory_mb",
        "override_storage_mb",
    }

    def keys(node: Any) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {k for v in node.values() for k in keys(v)}
        if isinstance(node, list):
            return {k for v in node for k in keys(v)}
        return set()

    assert not keys(config) & forbidden
    assert config["environment"]["env"] == {"DB_TYPE": "duckdb"}


def test_tasks_become_include_patterns_and_concurrency_reaches_harbor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    harbor = BoardHarbor(by_pass_rate)
    code = run(
        monkeypatch, harbor, tmp_path / "out", "--skip-smoke", "--task", DEV,
        "--trials", "1", "--n-concurrent", "2",
    )  # fmt: skip
    assert code == 0
    (call,) = harbor.raw
    assert [call[i + 1] for i, a in enumerate(call) if a == "-i"] == [f"snowflake-labs/{DEV}"]
    assert call[call.index("-k") + 1] == "1"
    assert call[call.index("-n") + 1] == "2"


def test_a_task_outside_the_registry_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    harbor = BoardHarbor(by_pass_rate)
    assert run(monkeypatch, harbor, tmp_path / "out", "--task", "dbt-nowhere") == 2
    assert "dbt-nowhere" in capsys.readouterr().err
    assert harbor.raw == []


def test_the_base_image_is_pulled_and_tagged_before_any_harbor_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fakes: dict[str, Any]
) -> None:
    fakes["image"] = False
    harbor = BoardHarbor(by_pass_rate)

    def ordered(args: list[str], log: Path, stop_when: StopWhen) -> tuple[int, str | None]:
        fakes["order"].append(("harbor", args[0]))
        return harbor(args, log, stop_when)

    monkeypatch.setattr(ablate, "run_harbor", ordered)
    argv = ["board", "--config", "no-verifiers", "--out", str(tmp_path / "out"), "--budget", "9"]
    assert evals_main([*argv, "--task", DEV]) == 0
    assert fakes["docker"] == [
        ["image", "inspect", "dbt-bench-base"],
        ["pull", "ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0"],
        ["tag", "ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0", "dbt-bench-base"],
    ]
    assert fakes["order"][:4] == [
        ("docker", "image"),
        ("docker", "pull"),
        ("docker", "tag"),
        ("harbor", "run"),  # the smoke
    ]
    assert fakes["fills"] == [{"dbt-bench-base"}]


def test_a_present_base_image_is_neither_pulled_nor_tagged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fakes: dict[str, Any]
) -> None:
    assert run(monkeypatch, BoardHarbor(), tmp_path / "out", "--skip-smoke", "--task", DEV) == 0
    assert fakes["docker"] == [["image", "inspect", "dbt-bench-base"]]


@pytest.mark.parametrize("step", ["pull", "tag"])
def test_a_failed_pull_or_tag_stops_before_any_harbor_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fakes: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    step: str,
) -> None:
    fakes["image"], fakes["fail"] = False, step
    harbor = BoardHarbor()
    assert run(monkeypatch, harbor, tmp_path / "out", "--task", DEV) == 2
    assert f"docker {step}" in capsys.readouterr().err
    assert harbor.raw == [] and harbor.smoke == []


def test_the_smoke_runs_the_first_registry_dev_task_from_the_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    harbor = BoardHarbor()
    assert run(monkeypatch, harbor, tmp_path / "out", "--task", OTHER) == 0
    smoke, job = harbor.raw
    assert smoke[smoke.index("-d") + 1] == f"snowflake-labs/data-eng-bench@{REGISTRY_REF}"
    assert [smoke[i + 1] for i, a in enumerate(smoke) if a == "-i"] == [f"snowflake-labs/{DEV}"]
    assert smoke[smoke.index("-k") + 1] == "1"
    assert [job[i + 1] for i, a in enumerate(job) if a == "-i"] == [f"snowflake-labs/{OTHER}"]


def test_batches_merge_into_one_report_and_the_missing_tasks_are_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    harbor = BoardHarbor()
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", DEV, "--task", OTHER) == 0
    first = report(out)
    assert not first["complete"]
    assert first["missing"] == {
        "tasks": {ZERO: 3},
        "holdout_tasks": 1,
        "holdout_trials": 3,
        "infra_errors_to_rerun": 0,
    }
    assert "## Missing" in (sorted((out / "reports").iterdir())[-1] / "board.md").read_text()
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", HOLDOUT, "--task", ZERO) == 0
    final = report(out)
    assert final["complete"]
    assert len(final["batches"]) == 2
    # by_pass_rate, no-verifiers: 2 of 3 trials of each task pass.
    lb = final["leaderboard"]
    assert (lb["n_tasks"], lb["n_trials"], lb["n_succeeded"]) == (4, 12, 8)
    assert lb["accuracy"] == pytest.approx(200 / 3)
    # Upstream metrics.py on 4 tasks at p=2/3, k=3: sqrt(4 * (2/9) / 2 / 16) * 100.
    assert lb["accuracy_stderr"] == pytest.approx(16.6667, abs=1e-3)
    assert lb["pass_at_2"] == 1.0 and lb["pass_at_3"] == 1.0
    assert final["without_zero_task"]["n_tasks"] == 3
    assert final["by_difficulty"]["hard"]["n_tasks"] == 2
    assert final["by_difficulty"]["easy"]["n_trials"] == 3


def test_a_batch_sharing_a_task_with_another_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    harbor = BoardHarbor()
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", DEV, "--task", OTHER) == 0
    calls = len(harbor.raw)
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", OTHER, "--task", ZERO) == 2
    assert "another batch" in capsys.readouterr().err
    assert len(harbor.raw) == calls


def test_the_same_batch_again_reuses_its_finished_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    harbor = BoardHarbor()
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", DEV) == 0
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", DEV) == 0
    assert len(harbor.raw) == 1


@pytest.mark.parametrize(
    ("change", "extra"),
    [
        ("commit", ()),
        ("config", ("--config", "full")),
        ("model", ("--model", "deepseek/other")),
        ("trials", ("--trials", "2")),
    ],
)
def test_a_batch_under_other_conditions_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, change: str, extra: tuple[str, ...]
) -> None:
    out = tmp_path / "out"
    harbor = BoardHarbor()
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", DEV) == 0
    if change == "commit":
        monkeypatch.setattr(ablate, "git_commit", lambda: "beef")
        monkeypatch.setattr(board, "git_commit", lambda: "beef")
    argv = ["board", "--config", "no-verifiers", "--out", str(out), "--budget", "100"]
    if extra[:1] == ("--config",):
        argv[2] = extra[1]
        extra = ()
    assert evals_main([*argv, "--skip-smoke", "--task", OTHER, *extra]) == 2
    assert len(harbor.raw) == 1


def test_a_skill_version_change_between_batches_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    harbor = BoardHarbor()
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", DEV) == 0
    real = ablate.conditions
    monkeypatch.setattr(
        ablate, "conditions", lambda config: real(config) | {"skill_version": "other"}
    )
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", OTHER) == 2
    assert "skill_version" in capsys.readouterr().err
    assert len(harbor.raw) == 1  # refused before the batch ran


def test_the_spend_guard_stops_before_the_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    harbor = BoardHarbor(cost=30.0)  # 3 trials: $90, past 80% of the $100 budget
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", DEV) == 0
    calls = len(harbor.raw)
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", OTHER) == 1
    assert len(harbor.raw) == calls


def test_infrastructure_errors_are_resumed_and_their_spend_kept_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    first: set[tuple[str, int]] = set()

    def outcome(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
        if task == DEV and attempt == 0 and (task, attempt) not in first:
            first.add((task, attempt))
            return None, "DockerDaemonError"
        return 1.0, None

    harbor = BoardHarbor(outcome)
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", DEV) == 2
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", DEV) == 0
    assert harbor.raw[1][:2] == ["job", "resume"]
    assert "DockerDaemonError" in harbor.raw[1]
    final = report(out)
    assert final["infra_excluded"]["trials"] == 1
    assert final["infra_excluded"]["cost_usd"] == pytest.approx(0.01)
    assert final["cost"]["cost_usd"] == pytest.approx(0.03)
    assert len(list((out / "dropped").rglob("*.jsonl"))) == 1


def test_a_streak_of_infrastructure_errors_stops_the_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    harbor = BoardHarbor(lambda config, task, attempt: (None, "DockerDaemonError"))
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--fail-fast", "2") == 2
    assert "2 infrastructure errors in a row" in capsys.readouterr().err
    assert len(list((out / "jobs" / "all").glob("*/result.json"))) == 2


def test_readiness_blocks_before_any_harbor_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(readiness, "docker_info", lambda: (1 * GIB, str(tmp_path)))
    harbor = BoardHarbor()
    assert run(monkeypatch, harbor, tmp_path / "out", "--task", DEV) == 2
    assert "readiness" in capsys.readouterr().err
    assert harbor.raw == []


def test_the_report_states_cost_time_differences_and_the_102_task_score(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    harbor = BoardHarbor(lambda config, task, attempt: (0.0 if task == ZERO else 1.0, None))
    assert run(monkeypatch, harbor, out, "--skip-smoke") == 0
    r = report(out)
    assert r["complete"]
    assert r["leaderboard"]["accuracy"] == pytest.approx(75.0)
    assert r["without_zero_task"]["accuracy"] == pytest.approx(100.0)
    assert r["cost"]["cost_usd"] == pytest.approx(0.12)
    assert r["cost"]["per_trial_usd"] == pytest.approx(0.01)
    assert r["cost"]["per_success_usd"] == pytest.approx(0.12 / 9)
    assert r["cost"]["output_tokens"] == 120
    # FakeHarbor finishes a trial a second after the one before; each took 60 seconds.
    assert r["wall_clock"]["trials_timed"] == 12
    assert r["wall_clock"]["trial_seconds_total"] == pytest.approx(720.0)
    assert r["wall_clock"]["batch_seconds"] == {"all": pytest.approx(71.0)}
    assert r["infra_excluded"] == {"trials": 0, "cost_usd": 0.0}
    md = (sorted((out / "reports").iterdir())[-1] / "board.md").read_text()
    assert "## Differences from a3278ad" in md
    assert "final_score" in md and "102 of 103" in md
    assert "DuckDB and Snowflake" in md
    assert r["run"]["config"] == "no-verifiers"
    assert r["run"]["benchmark_commit"] == f"snowflake-labs/data-eng-bench@{REGISTRY_REF}"
    assert "split" not in r["run"]  # no manifest split: the registry's tasks


def test_holdout_tasks_appear_in_aggregates_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    harbor = BoardHarbor()
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", DEV) == 0  # holdout missing
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", HOLDOUT) == 0
    printed = capsys.readouterr()
    assert HOLDOUT not in printed.out + printed.err
    assert HOLDOUT not in all_files_text(out / "reports")
    r = report(out)
    assert DEV in {row["task"] for row in r["per_task"]}
    assert r["holdout"]["n_tasks"] == 1 and r["holdout"]["n_trials"] == 3


def test_trial_results_trajectories_and_spend_stay_in_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    assert run(monkeypatch, BoardHarbor(), out, "--skip-smoke", "--task", DEV) == 0
    trials = [p.parent for p in (out / "jobs").glob("*/*/result.json")]
    assert len(trials) == 3
    for trial in trials:
        assert list((trial / "agent" / "events").glob("*.jsonl"))
        assert json.loads((trial / "config.json").read_text())["task"]["source"] == board.DATASET
        result = json.loads((trial / "result.json").read_text())
        assert result["agent_result"]["cost_usd"] == 0.01


def test_board_shows_in_the_cli_help() -> None:
    with pytest.raises(SystemExit) as exc:
        evals_main(["board", "--help"])
    assert exc.value.code == 0
