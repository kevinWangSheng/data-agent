"""Ablation runner (ticket 12): 6 configs x k trials on the holdout, one command.

Harbor is replaced at the subprocess seam (`ablate.run_harbor`) by `FakeHarbor`, which writes job
directories shaped like Harbor's (`<job>/config.json`, `<job>/<trial>/result.json`, the agent's
event file), so everything after it (spend guard, scrub, report, compare) runs for real.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from evals import ablate
from evals.__main__ import main as evals_main

HOLDOUT = ["dbt-delta", "dbt-echo"]
MANIFEST = {"seed": 1, "dev": ["dbt-alpha"], "holdout": HOLDOUT}
# How each config does, as the fraction of a task's trials that pass: full beats every ablation.
PASS = {
    "full": 1.0,
    "baseline": 0.0,
    "no-skill": 1 / 3,
    "no-verifiers": 2 / 3,
    "no-context": 1.0,
    "full-old-skill": 2 / 3,
}
FAKE_KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"

type Outcome = Callable[[str, str, int], tuple[float | None, str | None]]


def by_pass_rate(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
    return (1.0 if attempt < round(3 * PASS[config]) else 0.0), None


class FakeHarbor:
    """Writes what `harbor run` / `harbor job resume` would; `outcome(config, task, attempt)` gives
    each new trial's (reward, exception type)."""

    def __init__(self, outcome: Outcome = by_pass_rate, cost: float = 0.01) -> None:
        self.outcome = outcome
        self.cost = cost
        self.calls: list[list[str]] = []
        self.model: dict[str, str] = {}  # per config; deepseek-flash otherwise

    def __call__(self, args: list[str], log: Path) -> int:
        self.calls.append(args)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("harbor output\n")
        if args[:2] == ["job", "resume"]:
            job = Path(args[args.index("-p") + 1])
            dropped = {args[i + 1] for i, a in enumerate(args) if a == "-f"}
            for trial in [p for p in job.iterdir() if (p / "result.json").is_file()]:
                result = json.loads((trial / "result.json").read_text())
                if (result.get("exception_info") or {}).get("exception_type") in dropped:
                    for path in sorted(trial.rglob("*"), reverse=True):
                        path.rmdir() if path.is_dir() else path.unlink()
                    trial.rmdir()
            config = json.loads((job / "config.json").read_text())
        else:
            assert args[0] == "run"
            jobs_dir = Path(args[args.index("-o") + 1])
            job = jobs_dir / args[args.index("--job-name") + 1]
            job.mkdir(parents=True)
            run_config = next(a for a in args if a.startswith("run_config=")).split("=", 1)[1]
            tasks = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
            config = {
                "run_config": run_config,
                "tasks": tasks,
                "k": int(args[args.index("-k") + 1]),
                # Harbor keeps absolute paths here, which scrub would redact.
                "path": str(Path.home() / "tasks"),
            }
            (job / "config.json").write_text(json.dumps(config))
        self.fill(job, config)
        return 0

    def fill(self, job: Path, config: dict[str, Any]) -> None:
        for task in config["tasks"]:
            # Trial names carry the attempt they fill: `<task>__a<attempt>c<call>`.
            have = {
                p.parent.name.split("__a")[1].split("c")[0] for p in job.glob(f"{task}__*/*.json")
            }
            for attempt in range(config["k"]):
                if str(attempt) in have:
                    continue
                reward, error = self.outcome(config["run_config"], task, attempt)
                trial = job / f"{task}__a{attempt}c{len(self.calls)}"
                (trial / "agent" / "events").mkdir(parents=True)
                header = {
                    "type": "version",
                    "config": config["run_config"],
                    **ablate.conditions(config["run_config"]),
                    "model": self.model.get(config["run_config"], "deepseek-flash"),
                }
                result_event = {"type": "result", "cost_usd": self.cost}
                event_lines = [json.dumps(header), json.dumps(result_event)]
                # A key the model echoed into a tool result: scrub must remove it.
                event_lines.append(json.dumps({"type": "tool_result", "output": FAKE_KEY}))
                (trial / "agent" / "events" / "run.jsonl").write_text("\n".join(event_lines) + "\n")
                result = {
                    "task_name": f"org/{task}",
                    "trial_name": trial.name,
                    "verifier_result": None if reward is None else {"rewards": {"reward": reward}},
                    "exception_info": {"exception_type": error} if error else None,
                    "agent_result": {
                        "n_input_tokens": 100,
                        "n_cache_tokens": 60,
                        "n_output_tokens": 10,
                        "cost_usd": self.cost,
                    },
                }
                (trial / "result.json").write_text(json.dumps(result))


@pytest.fixture(autouse=True)
def commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean commit, whatever state this checkout is in while the tests run."""
    monkeypatch.setattr(ablate, "git_commit", lambda: "c0ffee")


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(MANIFEST))
    return path


def ablate_args(manifest: Path, out: Path, *extra: str) -> list[str]:
    return [
        *("ablate", "--tasks-dir", "tasks", "--manifest", str(manifest)),
        *("--out", str(out), "--budget", "100", *extra),
    ]


def run(
    monkeypatch: pytest.MonkeyPatch, harbor: FakeHarbor, manifest: Path, out: Path, *extra: str
) -> int:
    monkeypatch.setattr(ablate, "run_harbor", harbor)
    return evals_main(ablate_args(manifest, out, *extra))


def report_dirs(out: Path) -> list[Path]:
    return sorted((out / "reports").iterdir()) if (out / "reports").exists() else []


def table(out: Path) -> dict[str, Any]:
    (report,) = report_dirs(out)
    return json.loads((report / "ablation.json").read_text())


def test_runs_every_config_on_the_holdout_and_writes_the_ablation_table(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    harbor = FakeHarbor()
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out) == 0
    configs = [next(a for a in call if a.startswith("run_config=")) for call in harbor.calls]
    assert configs == [f"run_config={name}" for name in ablate.CONFIGS]
    for call in harbor.calls:
        assert [call[i + 1] for i, a in enumerate(call) if a == "-i"] == HOLDOUT
        assert call[call.index("-k") + 1] == "3"
        assert call[call.index("--path") + 1] == "tasks"
    result = table(out)
    assert result["split"] == "holdout"
    assert result["trials_per_task"] == 3
    assert {c["config"]: c["accuracy"] for c in result["configs"]} == pytest.approx(
        {name: 100 * rate for name, rate in PASS.items()}
    )
    contributions = {c["config"]: c for c in result["contributions"]}
    assert set(contributions) == set(ablate.CONFIGS) - {"full"}
    # full minus the config without the layer, in points, with its paired bootstrap interval.
    assert contributions["baseline"]["diff"] == pytest.approx(100.0)
    assert contributions["no-skill"]["diff"] == pytest.approx(100 * 2 / 3)
    assert contributions["no-context"]["diff"] == pytest.approx(0.0)
    assert contributions["no-context"]["significant"] is False
    assert {"ci_low", "ci_high", "layer"} <= set(contributions["baseline"])
    markdown = (report_dirs(out)[0] / "ablation.md").read_text()
    assert "| no-skill |" in markdown


def test_holdout_results_appear_only_as_aggregates(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "out"
    assert run(monkeypatch, FakeHarbor(), manifest, out) == 0
    printed = capsys.readouterr()
    (report,) = report_dirs(out)
    files = sorted(p for p in report.rglob("*") if p.is_file())
    assert {p.name for p in files} == {"report.json", "ablation.json", "ablation.md"}
    for text in [printed.out, printed.err, *(p.read_text() for p in files)]:
        for task in HOLDOUT:
            assert task not in text


def test_compare_is_called_with_the_manifest_and_split(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    real = ablate.evals_cli

    def spy(argv: list[str]) -> tuple[int, str, str]:
        calls.append(argv)
        return real(argv)

    monkeypatch.setattr(ablate, "evals_cli", spy)
    assert run(monkeypatch, FakeHarbor(), manifest, tmp_path / "out") == 0
    compares = [argv for argv in calls if argv[0] == "compare"]
    assert len(compares) == len(ablate.CONFIGS) - 1
    for argv in compares:
        assert argv[argv.index("--manifest") + 1] == str(manifest)
        assert argv[argv.index("--split") + 1] == "holdout"
        assert argv[argv.index("--candidate") + 1].endswith("full")


def test_every_report_goes_to_a_new_empty_directory(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    harbor = FakeHarbor()
    assert run(monkeypatch, harbor, manifest, out) == 0
    first = report_dirs(out)
    # Rerun: every job is complete, so nothing runs again, but the report is written anew.
    assert run(monkeypatch, harbor, manifest, out) == 0
    assert len(harbor.calls) == len(ablate.CONFIGS)
    second = [d for d in report_dirs(out) if d not in first]
    assert len(first) == len(second) == 1
    assert (second[0] / "ablation.json").read_text() == (first[0] / "ablation.json").read_text()


@pytest.mark.parametrize(
    "error", ["AgentAuthenticationError", "ModelNotFoundError", "UnsupportedModelParametersError"]
)
def test_a_config_error_stops_for_a_human_without_a_report(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    error: str,
) -> None:
    """These score 0 as agent failures in a report, so a wrong key would read as a real 0%."""
    out = tmp_path / "out"
    harbor = FakeHarbor(lambda config, task, attempt: (None, error))
    assert run(monkeypatch, harbor, manifest, out) == 2
    assert len(harbor.calls) == 1  # no further config ran
    assert report_dirs(out) == []
    err = capsys.readouterr().err
    assert error in err and "human" in err
    assert all(task not in err for task in HOLDOUT)
    # A rerun does not paper over it: the job stays refused until a human removes it.
    assert run(monkeypatch, harbor, manifest, out) == 2
    assert len(harbor.calls) == 1


def test_spend_guard_stops_before_the_next_job(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # One job: 2 tasks x 3 trials x $1 = $6, at 80% of a $7.5 budget.
    harbor = FakeHarbor(cost=1.0)
    out = tmp_path / "out"
    monkeypatch.setattr(ablate, "run_harbor", harbor)
    args = ablate_args(manifest, out)
    args[args.index("--budget") + 1] = "7.5"
    assert evals_main(args) == 1
    assert len(harbor.calls) == 1
    assert report_dirs(out) == []
    assert "80%" in capsys.readouterr().err


def test_infrastructure_errors_block_the_report_and_a_rerun_redoes_them(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    broken = {"flaky": True}

    def outcome(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
        if config == "no-skill" and attempt == 0 and broken["flaky"]:
            return None, "ApiInternalServerError"
        return by_pass_rate(config, task, attempt)

    harbor = FakeHarbor(outcome)
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out) == 2
    assert report_dirs(out) == []
    assert "rerun" in capsys.readouterr().err
    broken["flaky"] = False
    assert run(monkeypatch, harbor, manifest, out) == 0
    resumed = harbor.calls[len(ablate.CONFIGS) :]
    assert resumed == [
        ["job", "resume", "-p", str(out / "jobs" / "no-skill"), "-f", "ApiInternalServerError"]
    ]
    assert table(out)["configs"][ablate.CONFIGS.index("no-skill")]["accuracy"] == pytest.approx(
        100 / 3
    )


def test_an_interrupted_job_is_resumed(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    harbor = FakeHarbor()
    assert run(monkeypatch, harbor, manifest, out) == 0
    victim = next((out / "jobs" / "baseline").glob("dbt-echo__*"))
    for path in sorted(victim.rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    victim.rmdir()
    assert run(monkeypatch, harbor, manifest, out) == 0
    assert harbor.calls[-1] == ["job", "resume", "-p", str(out / "jobs" / "baseline")]


def test_trajectories_are_scrubbed_before_the_report(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    assert run(monkeypatch, FakeHarbor(), manifest, out) == 0
    events = list((out / "jobs").rglob("run.jsonl"))
    assert len(events) == len(ablate.CONFIGS) * len(HOLDOUT) * 3
    assert all(FAKE_KEY not in p.read_text() for p in events)


def test_a_scrub_that_cannot_check_everything_stops_the_report(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harbor = FakeHarbor()

    def harbor_with_a_symlink(args: list[str], log: Path) -> int:
        code = harbor(args, log)
        job = log.parent.parent / "jobs" / log.stem
        (job / "link").symlink_to(job / "config.json")
        return code

    monkeypatch.setattr(ablate, "run_harbor", harbor_with_a_symlink)
    out = tmp_path / "out"
    args = ablate_args(manifest, out, "--config", "full")
    assert evals_main(args) == 2
    assert report_dirs(out) == []
    assert "scrub" in capsys.readouterr().err


def test_a_subset_of_configs_runs_in_the_fixed_order_and_needs_full(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """plan §7's cuts drop configs (full-old-skill first); full is what each one is compared to."""
    harbor = FakeHarbor()
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "no-skill", "--config", "full") == 0
    assert [c["config"] for c in table(out)["configs"]] == ["full", "no-skill"]
    assert run(monkeypatch, harbor, manifest, tmp_path / "out2", "--config", "no-skill") == 2
    assert "full" in capsys.readouterr().err


def test_trials_per_task_and_concurrency_reach_harbor(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    harbor = FakeHarbor()
    out = tmp_path / "out"
    extra = ("--config", "full", "--config", "baseline", "--trials", "2", "--n-concurrent", "2")
    assert run(monkeypatch, harbor, manifest, out, *extra) == 0
    for call in harbor.calls:
        assert call[call.index("-k") + 1] == "2"
        assert call[call.index("-n") + 1] == "2"
    assert table(out)["trials_per_task"] == 2


def test_a_failed_harbor_run_stops_the_batch(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[list[str]] = []

    def failing(args: list[str], log: Path) -> int:
        calls.append(args)
        return 1

    monkeypatch.setattr(ablate, "run_harbor", failing)
    out = tmp_path / "out"
    assert evals_main(ablate_args(manifest, out)) == 2
    assert len(calls) == 1
    assert "harbor" in capsys.readouterr().err


def test_spend_of_trials_a_resume_drops_still_counts(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    """Harbor deletes the errored trials it reruns; what they cost was still paid."""
    broken = {"flaky": True}

    def outcome(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
        if config == "no-skill" and attempt == 0 and broken["flaky"]:
            return None, "ApiInternalServerError"
        return by_pass_rate(config, task, attempt)

    harbor = FakeHarbor(outcome)
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out) == 2
    broken["flaky"] = False
    assert run(monkeypatch, harbor, manifest, out) == 0
    assert list((out / "dropped" / "no-skill").rglob("*.jsonl"))
    code, printed, _ = ablate.evals_cli(["spend", str(out), "--budget", "100"])
    # 6 configs x 2 tasks x 3 trials, plus the 2 dropped ones, at $0.01 each.
    assert code == 0 and "$0.3800 over 38 event file(s)" in printed


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"git_commit": "beef"}, "other conditions"),
        ({"skill_version": "0ld5k1ll"}, "other conditions"),
        ({"git_commit": "c0ffee-dirty"}, "uncommitted"),
    ],
)
def test_jobs_from_other_conditions_are_not_reused(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    change: dict[str, str],
    message: str,
) -> None:
    """A finished job made by other code or Skills must not join this table (ADR-0004: a Skill
    change means rerunning the configs it affects)."""
    harbor = FakeHarbor()
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 0
    if "git_commit" in change:
        monkeypatch.setattr(ablate, "git_commit", lambda: change["git_commit"])
    else:
        monkeypatch.setattr(ablate, "skill_version", lambda _dir: change["skill_version"])
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 2
    assert message in capsys.readouterr().err
    assert len(harbor.calls) == 1
    assert len(report_dirs(out)) == 1


def test_configs_run_with_different_models_are_not_compared(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harbor = FakeHarbor()
    harbor.model["baseline"] = "deepseek-pro"
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full", "--config", "baseline") == 2
    assert "model" in capsys.readouterr().err
    assert report_dirs(out) == []


def test_scrub_leaves_jobs_outside_this_table_alone(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    """An unfinished job of a config left out this time must stay resumable: scrub would redact
    the paths in its Harbor config."""

    def outcome(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
        if config == "baseline":
            return None, "ApiInternalServerError"
        return by_pass_rate(config, task, attempt)

    harbor = FakeHarbor(outcome)
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full", "--config", "baseline") == 2
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 0
    assert str(Path.home()) in (out / "jobs" / "baseline" / "config.json").read_text()
    assert str(Path.home()) not in (out / "jobs" / "full" / "config.json").read_text()
