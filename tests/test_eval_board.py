"""The leaderboard run (leaderboard-run ticket 01): one config on the registry's data-eng-bench.

Harbor is replaced at ablate's subprocess seam (`ablate.run_harbor`) by `BoardHarbor`, ablate's
FakeHarbor taught `harbor run -d`: it strips the registry's org from the `-i` patterns and writes
the job config, each trial's config and the agent that ran with Harbor 0.24.0's own code (the
`--config` file and `--ak` merged as `harbor run` does, `JobConfig`, `JobPlan`, `AgentFactory`),
so the leaderboard CI's checks (`leaderboard_ci`) run on what Harbor would record. Docker
(`board.docker`), the model account's balance, readiness's machine probes and the wheel fill are
replaced as in the ablate tests; everything after Harbor (spend guard, scrub, merge, report)
runs for real.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import leaderboard_ci
import pytest
import yaml
from harbor.agents.factory import AgentFactory
from harbor.cli.config_sources import load_config_source
from harbor.job_plan import JobPlan
from harbor.models.job.config import JobConfig
from harbor.models.trial.config import TaskConfig
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


def harbor_job_config(args: list[str]) -> JobConfig:
    """The job config `harbor run` builds of these arguments: the `--config` file, each `--ak`
    added to every agent's kwargs (Harbor 0.24.0 cli/jobs.py 1586-1588), `-d` with `-i` as one
    dataset."""
    data = load_config_source(args[args.index("--config") + 1])
    for i, arg in enumerate(args):
        if arg == "--ak":
            key, value = args[i + 1].split("=", 1)
            for agent in data["agents"]:
                agent.setdefault("kwargs", {})[key] = value
    name, ref = args[args.index("-d") + 1].split("@", 1)
    patterns = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
    data["datasets"] = [{"name": name, "ref": ref, "task_names": patterns}]
    data["job_name"] = args[args.index("--job-name") + 1]
    data["jobs_dir"] = args[args.index("-o") + 1]
    data["n_attempts"] = int(args[args.index("-k") + 1])
    return JobConfig.model_validate(data)


class BoardHarbor(FakeHarbor):
    """FakeHarbor for `harbor run -d <dataset>@<ref> -i <org>/<task>`; `raw` keeps the calls as
    given. The job's config.json is Harbor's job config with FakeHarbor's own keys on top (its
    `tasks` replaces Harbor's empty one, for its resume), and each trial gets the agent that ran
    and its per-trial config.json, as Harbor writes them."""

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
        fake = json.loads((job / "config.json").read_text())
        harbor = harbor_job_config(args).model_dump(mode="json")
        (job / "config.json").write_text(
            json.dumps(harbor | fake | {"datasets": harbor["datasets"]})
        )
        self.add_trial_files(job)
        return result

    def add_trial_files(self, job: Path) -> None:
        """What Harbor's trials also record: the start time (each trial here took 60 seconds),
        the agent that ran (`agent_info`, of the agent `AgentFactory` creates of the trial's
        agent config) and the trial's config.json (`JobPlan`'s) with the registry task it ran."""
        config = json.loads((job / "config.json").read_text()) | {"tasks": []}
        harbor = JobConfig.model_validate(config).model_copy(update={"n_attempts": 1})
        for path in job.glob("*/result.json"):
            result = json.loads(path.read_text())
            if "started_at" not in result:
                end = datetime.fromisoformat(result["finished_at"])
                result["started_at"] = (end - timedelta(seconds=60)).isoformat()
            task = TaskConfig(name=result["task_name"], ref="sha256:fake", source=board.DATASET)
            (trial,) = JobPlan.build_trial_configs(harbor, [task], job_id=uuid4())
            agent = AgentFactory.create_agent_from_config(
                trial.agent, logs_dir=path.parent / "agent"
            )
            result["agent_info"] = agent.to_agent_info().model_dump(mode="json")
            path.write_text(json.dumps(result))
            (path.parent / "config.json").write_text(trial.model_dump_json())


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


@pytest.mark.parametrize("change", ["commit", "skill_version", "dirty"])
def test_an_interrupted_batch_without_results_is_not_resumed_under_other_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, change: str
) -> None:
    """A batch stopped before its first result.json has no trial to compare versions with; the
    run record of the call that started it does."""
    out = tmp_path / "out"
    harbor = BoardHarbor()
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", DEV) == 0
    for result in (out / "jobs").glob("*/*/result.json"):
        result.unlink()  # as if Harbor was stopped before any trial finished
    real = ablate.conditions
    if change == "skill_version":
        monkeypatch.setattr(
            ablate, "conditions", lambda config: real(config) | {"skill_version": "other"}
        )
    else:
        commit = "beef" if change == "commit" else "c0ffee-dirty"
        monkeypatch.setattr(ablate, "git_commit", lambda: commit)
        monkeypatch.setattr(board, "git_commit", lambda: commit)
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", DEV) == 2
    assert len(harbor.raw) == 1


def test_a_refused_request_writes_no_run_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The last run record stays the jobs' own (ablate.check_record's contract)."""
    out = tmp_path / "out"
    harbor = BoardHarbor()
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", DEV) == 0
    records = sorted((out / "records").glob("*.json"))
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", OTHER, "--model", "x/y") == 2
    assert sorted((out / "records").glob("*.json")) == records


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
    assert final["cost"]["total_cost_usd"] == pytest.approx(0.04)  # the dropped trial too
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


def leaderboard_view(job: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict]]:
    """A job as the leaderboard's CI reads it once uploaded: its config, its bulk trial rows (the
    agent and model Harbor's uploader stores of each trial's `agent_info`, upload/uploader.py
    519-536, and the trial's task source) and each trial's config."""
    trials = [p.parent for p in sorted(job.glob("*/result.json"))]
    configs = [json.loads((t / "config.json").read_text()) for t in trials]
    rows = []
    for trial, config in zip(trials, configs, strict=True):
        info = json.loads((trial / "result.json").read_text())["agent_info"]
        model = info["model_info"] or {}
        rows.append(
            {
                "agent_name": info["name"],
                "agent_version": info["version"],
                "model_provider": model.get("provider"),
                "model_name": model.get("name"),
                "source": config["task"]["source"],
            }
        )
    return json.loads((job / "config.json").read_text()), rows, configs


@pytest.mark.parametrize(
    ("extra", "model"),
    [
        ((), "deepseek/deepseek-flash"),
        (("--model", "deepseek/deepseek-pro"), "deepseek/deepseek-pro"),
    ],
)
def test_the_leaderboard_ci_finds_the_agent_of_the_trials_in_the_job_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, extra: tuple[str, ...], model: str
) -> None:
    """data-eng-bench's `lb filter` and static analysis on a board job: one source_filter, its
    agent found in the job config (`matching_agent`), no override in the job or any trial."""
    out = tmp_path / "out"
    harbor = BoardHarbor()
    assert run(monkeypatch, harbor, out, "--skip-smoke", "--task", DEV, *extra) == 0
    (call,) = harbor.raw
    assert "-m" not in call  # Harbor ignores -m with --config and no --agent
    config, rows, trial_configs = leaderboard_view(out / "jobs" / board.batch_name([DEV], {}))
    (sf,) = leaderboard_ci.source_filters(config, rows) or []
    assert sf == {
        "agent": "data-agent",
        "agent_version": "unknown",  # the wheel's git commit in a real trial
        "model_name": model,
        "reasoning_effort": None,
    }
    assert leaderboard_ci.job_config_failures(config, sf) == []
    assert [leaderboard_ci.trial_config_failures(c) for c in trial_configs] == [[]] * 3
    (agent,) = config["agents"]
    assert agent["import_path"] == "evals.harbor_agent:DataAgent"


def test_the_agent_name_is_the_one_its_trials_record() -> None:
    from evals.harbor_agent import DataAgent

    (agent,) = board.job_config(None)["agents"]
    assert agent["name"] == DataAgent.name()


def test_board_shows_in_the_cli_help() -> None:
    with pytest.raises(SystemExit) as exc:
        evals_main(["board", "--help"])
    assert exc.value.code == 0
