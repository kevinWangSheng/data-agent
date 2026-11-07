"""Ablation runner (ticket 12): 6 configs x k trials on the holdout, one command.

Harbor is replaced at the subprocess seam (`ablate.run_harbor`) by `FakeHarbor`, which writes job
directories shaped like Harbor's (`<job>/config.json`, `<job>/<trial>/result.json`, the agent's
event file), so everything after it (spend guard, scrub, report, compare) runs for real.

The evaluation lifecycle contract (eval-readiness-and-lifecycle ticket 01) is fixed at the same
seam: readiness before the first job, reuse and resume, logical failures kept as scored failures,
and the spend of dropped trials. A test marked `xfail(strict=True)` names the ticket that makes it
pass; that ticket removes the marker and changes nothing else in the test.
"""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from evals import ablate, balance
from evals import report as report_module
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
type StopWhen = Callable[[], str | None]
# A new trial's extra events, written between its version header and its result event, and extra
# fields of its result event; both from (smoke, config, task, attempt).
type Events = Callable[[bool, str, str, int], list[dict[str, Any]]]
type ResultFields = Callable[[bool, str, str, int], dict[str, Any]]


def by_pass_rate(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
    return (1.0 if attempt < round(3 * PASS[config]) else 0.0), None


def smoke_passes(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
    return 1.0, None


class FakeHarbor:
    """Writes what `harbor run` / `harbor job resume` would; `outcome(config, task, attempt)` gives
    each new trial's (reward, exception type). Preflight smoke runs (jobs under OUT/smoke/) are
    recorded in `smoke` rather than `calls` and take `smoke_outcome`."""

    def __init__(self, outcome: Outcome = by_pass_rate, cost: float = 0.01) -> None:
        self.outcome = outcome
        self.smoke_outcome: Outcome = smoke_passes
        self.cost = cost
        self.calls: list[list[str]] = []
        self.smoke: list[list[str]] = []
        self.model: dict[str, str] = {}  # per config; deepseek-flash otherwise
        self.finished: list[str] = []  # trial names in the order they finished
        self.events: Events = lambda smoke, config, task, attempt: []
        self.result_fields: ResultFields = lambda smoke, config, task, attempt: {}

    def __call__(self, args: list[str], log: Path, stop_when: StopWhen) -> tuple[int, str | None]:
        smoke = args[0] == "run" and Path(args[args.index("-o") + 1]).parent.name == "smoke"
        (self.smoke if smoke else self.calls).append(args)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("harbor output\n")
        if args[:2] == ["job", "resume"]:
            job = Path(args[args.index("-p") + 1])
            dropped = {args[i + 1] for i, a in enumerate(args) if a == "-f"}
            for trial in [p for p in job.iterdir() if p.is_dir()]:
                # As Harbor's resume does (harbor/job.py `_maybe_init_existing_job`), a trial
                # directory without result.json (an interrupted trial) is deleted and rerun.
                done = (trial / "result.json").is_file()
                result = json.loads((trial / "result.json").read_text()) if done else {}
                error = (result.get("exception_info") or {}).get("exception_type")
                if not done or error in dropped:
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
        # As ablate's watcher would see them: after each trial Harbor finishes.
        outcome = self.smoke_outcome if smoke else self.outcome
        reason = self.fill(job, config, stop_when, outcome, smoke)
        return (-15, reason) if reason else (0, None)

    def fill(
        self,
        job: Path,
        config: dict[str, Any],
        stop_when: StopWhen,
        outcome: Outcome,
        smoke: bool,
    ) -> str | None:
        for task in config["tasks"]:
            # Trial names carry the attempt they fill: `<task>__a<attempt>c<call>`.
            have = {
                p.parent.name.split("__a")[1].split("c")[0] for p in job.glob(f"{task}__*/*.json")
            }
            for attempt in range(config["k"]):
                if str(attempt) in have:
                    continue
                reward, error = outcome(config["run_config"], task, attempt)
                trial = job / f"{task}__a{attempt}c{len(self.calls) + len(self.smoke)}"
                (trial / "agent" / "events").mkdir(parents=True)
                header = {
                    "type": "version",
                    "config": config["run_config"],
                    **ablate.conditions(config["run_config"]),
                    "model": self.model.get(config["run_config"], "deepseek-flash"),
                }
                result_event = {"type": "result", "cost_usd": self.cost}
                result_event |= self.result_fields(smoke, config["run_config"], task, attempt)
                extra = self.events(smoke, config["run_config"], task, attempt)
                event_lines = [json.dumps(e) for e in (header, *extra, result_event)]
                # A key the model echoed into a tool result: scrub must remove it.
                event_lines.append(json.dumps({"type": "tool_result", "output": FAKE_KEY}))
                (trial / "agent" / "events" / "run.jsonl").write_text("\n".join(event_lines) + "\n")
                result = {
                    "task_name": f"org/{task}",
                    "trial_name": trial.name,
                    "verifier_result": None if reward is None else {"rewards": {"reward": reward}},
                    "exception_info": {"exception_type": error} if error else None,
                    "finished_at": f"2026-10-31T00:{len(self.finished) // 60:02d}:"
                    f"{len(self.finished) % 60:02d}.000000Z",
                    "agent_result": {
                        "n_input_tokens": 100,
                        "n_cache_tokens": 60,
                        "n_output_tokens": 10,
                        "cost_usd": self.cost,
                    },
                }
                (trial / "result.json").write_text(json.dumps(result))
                self.finished.append(trial.name)
                if reason := stop_when():
                    return reason
        return None


@pytest.fixture(autouse=True)
def commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean commit, whatever state this checkout is in while the tests run."""
    monkeypatch.setattr(ablate, "git_commit", lambda: "c0ffee")


@pytest.fixture(autouse=True)
def account(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The model account's balance as DeepSeek would answer it; the real one needs the network."""
    reply: dict[str, Any] = {
        "is_available": True,
        "balance_infos": [{"currency": "USD", "total_balance": "500.00"}],
    }
    monkeypatch.setattr(balance, "fetch", lambda: reply)
    return reply


@pytest.fixture(autouse=True)
def benchmark(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean benchmark clone, the tests' --tasks-dir being no git checkout, on a labelled
    machine."""
    monkeypatch.setattr(ablate, "benchmark_commit", lambda _dir: "be4c4")
    monkeypatch.setenv("DATA_AGENT_MACHINE", "mac-colima")


REAL_BENCHMARK_COMMIT = ablate.benchmark_commit
PREFETCH_WHEELS = ablate.prefetch_wheels


@pytest.fixture(autouse=True)
def prefetched(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, list[str]]]:
    """The wheel cache fills ablate asks for, as (tasks dir, tasks); the real fill needs task
    directories and Docker."""
    calls: list[tuple[Path, list[str]]] = []
    monkeypatch.setattr(ablate, "prefetch_wheels", lambda *call: calls.append(call))
    return calls


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


def test_a_usage_limit_stops_for_a_human_and_a_rerun_redoes_those_trials(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ApiUsageLimitError means the account's limit or balance ran out (dev-3: no-verifiers 90/90
    of it, scored as a real 0%). Once a human tops the account up, the same --out reruns them."""
    topped_up = {"yes": False}

    def outcome(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
        if config == "no-skill" and not topped_up["yes"]:
            return None, "ApiUsageLimitError"
        return by_pass_rate(config, task, attempt)

    harbor = FakeHarbor(outcome)
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out) == 2
    assert len(harbor.calls) == ablate.CONFIGS.index("no-skill") + 1  # no further config ran
    assert report_dirs(out) == []
    err = capsys.readouterr().err
    assert "ApiUsageLimitError" in err and "account" in err and "human" in err
    assert all(task not in err for task in HOLDOUT)
    topped_up["yes"] = True
    assert run(monkeypatch, harbor, manifest, out) == 0
    resume = ["job", "resume", "-p", str(out / "jobs" / "no-skill"), "-f", "ApiUsageLimitError"]
    assert harbor.calls[ablate.CONFIGS.index("no-skill") + 1] == resume
    no_skill = table(out)["configs"][ablate.CONFIGS.index("no-skill")]
    assert no_skill["accuracy"] == pytest.approx(100 / 3)


@pytest.mark.parametrize(("extra", "streak"), [((), 5), (("--fail-fast", "3"), 3)])
def test_a_streak_of_infrastructure_errors_stops_the_batch_mid_job(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extra: tuple[str, ...],
    streak: int,
) -> None:
    """Docker down, the network gone: every trial would error. Stop instead of burning the batch."""
    broken = {"yes": True}

    def outcome(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
        if config == "baseline" and broken["yes"]:
            return None, "ApiConnectionClosedError"
        return by_pass_rate(config, task, attempt)

    harbor = FakeHarbor(outcome)
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, *extra) == 2
    assert len(harbor.calls) == 2  # full, then baseline, stopped
    assert len(list((out / "jobs" / "baseline").glob("*/result.json"))) == streak
    assert report_dirs(out) == []
    err = capsys.readouterr().err
    assert f"{streak} infrastructure errors in a row" in err and "human" in err
    assert all(task not in err for task in HOLDOUT)
    broken["yes"] = False
    assert run(monkeypatch, harbor, manifest, out, *extra) == 0
    assert harbor.calls[2] == [
        *("job", "resume", "-p", str(out / "jobs" / "baseline")),
        *("-f", "ApiConnectionClosedError"),
    ]


@pytest.mark.parametrize("streak", ["0", "-1"])
def test_a_fail_fast_streak_below_one_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    streak: str,
) -> None:
    harbor = FakeHarbor()
    assert run(monkeypatch, harbor, manifest, tmp_path / "out", "--fail-fast", streak) == 2
    assert harbor.calls == harbor.smoke == []
    assert "--fail-fast" in capsys.readouterr().err


def test_infrastructure_errors_between_other_outcomes_do_not_stop_the_job(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    def outcome(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
        if config == "full" and attempt != 1:  # 2 in a row at most: a0, a2 of one task, a0 next
            return None, "ApiConnectionClosedError"
        return by_pass_rate(config, task, attempt)

    harbor = FakeHarbor(outcome)
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full", "--fail-fast", "3") == 2
    assert len(list((out / "jobs" / "full").glob("*/result.json"))) == 6  # the job ran to its end


def test_preflight_smokes_every_config_on_one_dev_task_before_any_job(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    harbor = FakeHarbor()
    smoked_by_first_job: list[int] = []

    def watching(args: list[str], log: Path, stop_when: StopWhen) -> tuple[int, str | None]:
        if not harbor.calls and args[0] == "run" and "/jobs" in args[args.index("-o") + 1]:
            smoked_by_first_job.append(len(harbor.smoke))
        return harbor(args, log, stop_when)

    monkeypatch.setattr(ablate, "run_harbor", watching)
    out = tmp_path / "out"
    assert evals_main(ablate_args(manifest, out)) == 0
    assert smoked_by_first_job == [len(ablate.CONFIGS)]
    for config, call in zip(ablate.CONFIGS, harbor.smoke, strict=True):
        assert f"run_config={config}" in call
        assert [call[i + 1] for i, a in enumerate(call) if a == "-i"] == MANIFEST["dev"]
        assert call[call.index("-k") + 1] == "1"
    # scrub ran on the smoke trials too
    smoke_events = list((out / "smoke").rglob("run.jsonl"))
    assert len(smoke_events) == len(ablate.CONFIGS)
    assert all(FAKE_KEY not in p.read_text() for p in smoke_events)


@pytest.mark.parametrize(
    ("smoke", "says"),
    [
        ((None, "ApiInternalServerError"), "ApiInternalServerError"),
        ((None, "ApiUsageLimitError"), "account"),
        ((None, None), "no reward"),
    ],
)
def test_a_failed_smoke_stops_before_any_job(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    smoke: tuple[float | None, str | None],
    says: str,
) -> None:
    harbor = FakeHarbor()
    harbor.smoke_outcome = lambda config, task, attempt: (
        smoke if config == "baseline" else (1, None)
    )
    assert run(monkeypatch, harbor, manifest, tmp_path / "out") == 2
    assert harbor.calls == []
    assert len(harbor.smoke) == 2  # full passed, baseline failed, nothing after
    err = capsys.readouterr().err
    assert "smoke" in err and "baseline" in err and says in err


def test_a_smoke_scrub_that_cannot_check_everything_stops_before_any_job(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harbor = FakeHarbor()

    def harbor_with_a_symlink(
        args: list[str], log: Path, stop_when: StopWhen
    ) -> tuple[int, str | None]:
        code = harbor(args, log, stop_when)
        job = Path(args[args.index("-o") + 1]) / args[args.index("--job-name") + 1]
        (job / "link").symlink_to(job / "config.json")
        return code

    monkeypatch.setattr(ablate, "run_harbor", harbor_with_a_symlink)
    assert evals_main(ablate_args(manifest, tmp_path / "out")) == 2
    assert harbor.calls == []
    err = capsys.readouterr().err
    assert "smoke" in err and "scrub" in err


def test_skip_smoke_runs_the_jobs_without_it(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    harbor = FakeHarbor()
    assert run(monkeypatch, harbor, manifest, tmp_path / "out", "--skip-smoke") == 0
    assert harbor.smoke == []
    assert len(harbor.calls) == len(ablate.CONFIGS)


@pytest.mark.parametrize(
    ("infos", "available", "extra"),
    [
        ([{"currency": "USD", "total_balance": "50.00"}], True, ()),
        ([{"currency": "USD", "total_balance": "50.00"}], True, ("--skip-smoke",)),
        ([{"currency": "CNY", "total_balance": "700.00"}], True, ()),  # under $100
        ([{"currency": "USD", "total_balance": "500.00"}], False, ()),
    ],
)
def test_a_balance_short_of_the_budget_stops_before_any_harbor_run(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    account: dict[str, Any],
    infos: list[dict[str, str]],
    available: bool,
    extra: tuple[str, ...],
) -> None:
    account["balance_infos"], account["is_available"] = infos, available
    harbor = FakeHarbor()
    assert run(monkeypatch, harbor, manifest, tmp_path / "out", *extra) == 2  # --budget 100
    assert harbor.calls == harbor.smoke == []
    assert "balance" in capsys.readouterr().err


def test_the_balance_needs_to_cover_only_the_budget_left(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path, account: dict[str, Any]
) -> None:
    harbor = FakeHarbor(cost=1.0)
    out = tmp_path / "out"
    # $1 of smoke and 6 trials at $1: $7 of the $100 spent.
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 0
    account["balance_infos"] = [{"currency": "USD", "total_balance": "93.50"}]
    assert run(monkeypatch, harbor, manifest, out, "--config", "full", "--config", "baseline") == 0
    assert len(harbor.calls) == 2


def test_the_budget_left_counts_what_was_spent_at_billed_prices(
    tmp_path: Path, account: dict[str, Any]
) -> None:
    """Ticket 34: $20 at peak prices, run on a Saturday, was billed $10, so $90 of a $100 budget
    is left, not $80."""
    out = tmp_path / "out"
    events = out / "jobs" / "full" / "t__a" / "agent" / "events"
    events.mkdir(parents=True)
    lines = [
        {"type": "version", "ts": "2026-10-31T12:00:00+00:00", "model": "deepseek-flash"},
        {"type": "result", "cost_usd": 20.0, "cost_basis": "price_table"},
    ]
    (events / "run.jsonl").write_text("".join(json.dumps(e) + "\n" for e in lines))
    assert ablate.spent(out) == Decimal(10)
    account["balance_infos"] = [{"currency": "USD", "total_balance": "85.00"}]
    with pytest.raises(ablate.Stop, match=r"below the \$90.00 left"):
        ablate.check_balance(out, Decimal(100))
    account["balance_infos"] = [{"currency": "USD", "total_balance": "90.00"}]
    ablate.check_balance(out, Decimal(100))


def test_an_unreadable_balance_stops_before_any_harbor_run(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fetch() -> Any:
        raise balance.BalanceUnknown("HTTP 401 from the balance endpoint")

    monkeypatch.setattr(balance, "fetch", fetch)
    harbor = FakeHarbor()
    assert run(monkeypatch, harbor, manifest, tmp_path / "out") == 2
    assert harbor.calls == harbor.smoke == []
    assert "HTTP 401" in capsys.readouterr().err


def test_no_preflight_when_no_job_is_to_run(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    harbor = FakeHarbor()
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out) == 0

    def fetch() -> Any:
        raise AssertionError("asked for the balance")

    monkeypatch.setattr(balance, "fetch", fetch)
    assert run(monkeypatch, harbor, manifest, out) == 0
    assert len(harbor.smoke) == len(ablate.CONFIGS)


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
    args = ablate_args(manifest, out, "--skip-smoke")  # smoke would spend $6 first
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

    def harbor_with_a_symlink(
        args: list[str], log: Path, stop_when: StopWhen
    ) -> tuple[int, str | None]:
        code = harbor(args, log, stop_when)
        job = log.parent.parent / "jobs" / log.stem  # the smoke jobs live elsewhere
        if not job.is_dir():
            return code
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

    def failing(args: list[str], log: Path, stop_when: StopWhen) -> tuple[int, str | None]:
        calls.append(args)
        return 1, None

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
    # 6 configs x 2 tasks x 3 trials, the 2 dropped ones, and 6 smoke trials in each of the 2
    # invocations, at $0.01 each.
    assert code == 0 and "(cost_usd $0.5000) over 50 event file(s)" in printed


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


@pytest.mark.parametrize(
    "damage",
    ["no event file", "empty event file", "no git_commit", "no skill_version"],
)
def test_jobs_whose_trials_lack_versions_are_not_reused(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    damage: str,
) -> None:
    """A trial with no recorded code or Skill version cannot be shown to match this checkout."""
    harbor = FakeHarbor()
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 0
    events = next((out / "jobs" / "full").glob("dbt-echo__*/agent/events/run.jsonl"))
    if damage == "no event file":
        events.unlink()
    elif damage == "empty event file":
        events.write_text("")
    else:
        header, *rest = events.read_text().splitlines()
        fields = json.loads(header)
        del fields[damage.removeprefix("no ")]
        events.write_text("\n".join([json.dumps(fields), *rest]) + "\n")
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 2
    assert "version" in capsys.readouterr().err
    assert len(harbor.calls) == 1
    assert len(report_dirs(out)) == 1


def test_an_infrastructure_error_without_events_is_still_redone(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    """A trial that broke before the agent wrote its event file is dropped by the resume anyway."""
    broken = {"flaky": True}

    def outcome(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
        if attempt == 0 and broken["flaky"]:
            return None, "ApiInternalServerError"
        return by_pass_rate(config, task, attempt)

    harbor = FakeHarbor(outcome)
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 2
    for events in (out / "jobs" / "full").glob("*__a0c*/agent/events/run.jsonl"):
        events.unlink()
    broken["flaky"] = False
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 0
    assert harbor.calls[-1][:2] == ["job", "resume"]
    # Its result stays under dropped/, so `evals accept` still sees the round had an error.
    assert list((out / "dropped" / "full").glob("*__a0c*/result.json"))


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


@pytest.mark.parametrize(
    ("first", "second"),
    [((), ("--model", "deepseek-pro")), (("--model", "deepseek-pro"), ())],
)
def test_another_model_on_the_same_out_stops(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    first: tuple[str, ...],
    second: tuple[str, ...],
) -> None:
    """Finished jobs are reused, so a call for model B would otherwise print model A's table."""
    harbor = FakeHarbor()
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full", *first) == 0
    assert run(monkeypatch, harbor, manifest, out, "--config", "full", *second) == 2
    assert "another --out" in capsys.readouterr().err
    assert len(harbor.calls) == 1
    assert len(report_dirs(out)) == 1
    # The same request again still reuses the jobs.
    assert run(monkeypatch, harbor, manifest, out, "--config", "full", *first) == 0
    assert len(harbor.calls) == 1


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ((), ("--agent-kwarg", "agent_timeout_sec=1500")),
        (("--agent-kwarg", "agent_timeout_sec=1500"), ()),
        (("--agent-kwarg", "agent_timeout_sec=1500"), ("--agent-kwarg", "agent_timeout_sec=900")),
    ],
)
def test_other_agent_kwargs_on_the_same_out_stop(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    first: tuple[str, ...],
    second: tuple[str, ...],
) -> None:
    """Ticket 54: finished jobs are reused, so a call with another agent kwarg (a shorter
    timeout) would otherwise report trials run under the first one."""
    harbor = FakeHarbor()
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full", *first) == 0
    assert run(monkeypatch, harbor, manifest, out, "--config", "full", *second) == 2
    assert "another --out" in capsys.readouterr().err
    assert len(harbor.calls) == 1
    assert run(monkeypatch, harbor, manifest, out, "--config", "full", *first) == 0
    assert len(harbor.calls) == 1


def test_a_request_file_from_before_agent_kwargs_is_still_reused(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    """A run without agent kwargs records the request as before, so an OUT written before them
    stays reusable."""
    out = tmp_path / "out"
    assert run(monkeypatch, FakeHarbor(), manifest, out, "--config", "full") == 0
    assert set(json.loads((out / "request.json").read_text())) == {"model"}


def test_the_wheel_cache_is_filled_once_before_the_first_harbor_job(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    prefetched: list[tuple[Path, list[str]]],
) -> None:
    harbor = FakeHarbor()
    fills_seen: list[int] = []

    def harbor_after_fill(
        args: list[str], log: Path, stop_when: StopWhen
    ) -> tuple[int, str | None]:
        fills_seen.append(len(prefetched))
        return harbor(args, log, stop_when)

    monkeypatch.setattr(ablate, "run_harbor", harbor_after_fill)
    out = tmp_path / "out"
    assert evals_main(ablate_args(manifest, out)) == 0
    assert fills_seen == [1] * 2 * len(ablate.CONFIGS)  # smoke and job per config
    assert prefetched == [(Path("tasks"), HOLDOUT + MANIFEST["dev"])]  # the smoke task too
    # Every job finished: a rerun starts no Harbor job, so it needs no wheels.
    assert evals_main(ablate_args(manifest, out)) == 0
    assert len(prefetched) == 1


@pytest.fixture
def tasks_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The holdout tasks on one base image, the agent's artifacts faked, the real fill wired in."""
    from evals import harbor_agent

    tasks = tmp_path / "tasks"
    for task in [*HOLDOUT, *MANIFEST["dev"]]:
        (tasks / task / "environment").mkdir(parents=True)
        (tasks / task / "task.toml").write_text("[environment]\ncpus = 2\n")
        (tasks / task / "environment" / "Dockerfile").write_text("FROM base:1\n")
    requirements = tmp_path / "requirements.txt"
    monkeypatch.setattr(harbor_agent, "build_artifacts", lambda: (tmp_path / "a.whl", requirements))
    monkeypatch.setattr(ablate, "prefetch_wheels", PREFETCH_WHEELS)
    return tasks


def run_on(
    monkeypatch: pytest.MonkeyPatch, harbor: FakeHarbor, manifest: Path, out: Path, tasks: Path
) -> int:
    argv = ablate_args(manifest, out)
    argv[argv.index("--tasks-dir") + 1] = str(tasks)
    monkeypatch.setattr(ablate, "run_harbor", harbor)
    return evals_main(argv)


def test_the_fill_covers_the_images_of_the_holdout_tasks(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    tasks_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    filled: list[set[str]] = []

    def fill(images: set[str], requirements: Path, lock: Path) -> list[str]:
        filled.append(set(images))
        assert requirements == tmp_path / "requirements.txt"
        return ["CPython 3.12, glibc 2.36, x86_64: 3 wheels in cache, 0.1 s"]

    monkeypatch.setattr(ablate.wheels, "fill", fill)
    assert run_on(monkeypatch, FakeHarbor(), manifest, tmp_path / "out", tasks_dir) == 0
    assert filled == [{"base:1"}]
    assert "wheels: CPython 3.12, glibc 2.36, x86_64: 3 wheels" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (RuntimeError("cannot probe base:1: Cannot connect to the Docker daemon"), "Docker daemon"),
        (RuntimeError("x.whl: sha256 00, expected 11"), "sha256"),
        (RuntimeError("no locked wheel of duckdb==1.4.0 installs on"), "no locked wheel"),
        (subprocess.CalledProcessError(6, ["curl"]), "curl"),
        (KeyError("markers"), "markers"),  # a probe that printed other JSON
    ],
)
def test_a_failed_fill_stops_before_any_harbor_job(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    tasks_dir: Path,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    reason: str,
) -> None:
    def fill(images: set[str], requirements: Path, lock: Path) -> list[str]:
        raise failure

    monkeypatch.setattr(ablate.wheels, "fill", fill)
    harbor = FakeHarbor()
    assert run_on(monkeypatch, harbor, manifest, tmp_path / "out", tasks_dir) == 2
    assert harbor.calls == []
    err = capsys.readouterr().err
    assert "cannot fill the wheel cache" in err and reason in err


def test_a_task_without_a_known_image_stops_before_any_harbor_job(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    tasks_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tasks_dir / HOLDOUT[1] / "environment" / "Dockerfile").unlink()
    harbor = FakeHarbor()
    assert run_on(monkeypatch, harbor, manifest, tmp_path / "out", tasks_dir) == 2
    assert harbor.calls == []
    assert "cannot fill the wheel cache" in capsys.readouterr().err


DEV = ["dbt-alpha", "dbt-bravo", "dbt-charlie"]


@pytest.fixture
def dev_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "dev-manifest.json"
    path.write_text(json.dumps({"seed": 1, "dev": DEV, "holdout": HOLDOUT}))
    return path


def test_the_dev_split_runs_the_chosen_dev_tasks_with_per_task_reports(
    monkeypatch: pytest.MonkeyPatch, dev_manifest: Path, tmp_path: Path
) -> None:
    harbor = FakeHarbor()
    out = tmp_path / "out"
    configs = ("--config", "full", "--config", "baseline")
    tasks = ("--task", "dbt-charlie", "--task", "dbt-alpha")
    assert run(monkeypatch, harbor, dev_manifest, out, "--split", "dev", *tasks, *configs) == 0
    for call in harbor.calls:
        assert [call[i + 1] for i, a in enumerate(call) if a == "-i"] == [
            "dbt-alpha",
            "dbt-charlie",
        ]
    result = table(out)
    assert (result["split"], result["n_tasks"]) == ("dev", 2)
    (reports,) = report_dirs(out)
    full = json.loads((reports / "full" / "report.json").read_text())
    assert full["per_task"] == {
        "dbt-alpha": {"successes": 3, "trials": 3},
        "dbt-charlie": {"successes": 3, "trials": 3},
    }
    assert full["process"]["per_task"]["dbt-alpha"]["cost_usd"] == pytest.approx(0.03)
    diverged = json.loads((reports / "diverge" / "baseline" / "diverge.json").read_text())
    assert diverged["labels"] == {"baseline": "baseline", "candidate": "full"}
    assert diverged["wins"] == {"baseline": 0, "candidate": 2}


def test_the_dev_split_without_tasks_runs_the_whole_dev_list(
    monkeypatch: pytest.MonkeyPatch, dev_manifest: Path, tmp_path: Path
) -> None:
    harbor = FakeHarbor()
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, dev_manifest, out, "--split", "dev", "--config", "full") == 0
    assert [harbor.calls[0][i + 1] for i, a in enumerate(harbor.calls[0]) if a == "-i"] == DEV


@pytest.mark.parametrize(
    "extra",
    [
        ("--split", "dev", "--task", "dbt-delta"),  # a holdout task
        ("--split", "dev", "--task", "dbt-zulu"),  # in no list
        ("--task", "dbt-alpha"),  # the holdout is run whole
    ],
)
def test_tasks_outside_the_dev_list_are_refused(
    monkeypatch: pytest.MonkeyPatch, dev_manifest: Path, tmp_path: Path, extra: tuple[str, ...]
) -> None:
    harbor = FakeHarbor()
    assert run(monkeypatch, harbor, dev_manifest, tmp_path / "out", *extra) == 2
    assert harbor.calls == []


def test_a_dev_run_on_a_manifest_whose_dev_and_holdout_overlap_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0004: a dev report names its tasks, so a holdout task listed under dev too would have
    its per-task results written; fail closed before any job."""
    overlapping = tmp_path / "overlap.json"
    overlapping.write_text(json.dumps({"seed": 1, "dev": [*DEV, HOLDOUT[0]], "holdout": HOLDOUT}))
    harbor = FakeHarbor()
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, overlapping, out, "--split", "dev", "--config", "full") == 2
    assert f"{overlapping} holds out" in capsys.readouterr().err
    assert harbor.calls == harbor.smoke == []
    assert not out.exists()


@pytest.mark.parametrize(
    "real", [{"dev": [], "holdout": ["dbt-charlie"]}, {"holdout": "dbt-charlie"}, None]
)
def test_a_dev_run_on_a_task_the_repos_manifest_holds_out_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    dev_manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    real: dict[str, Any] | None,
) -> None:
    """A manifest the run brings may list a holdout task under dev, without overlap of its own;
    the repo's evals/manifest.json (on the server, the real one) decides. Unreadable: refused."""
    repo_manifest = tmp_path / "repo-manifest.json"
    if real is not None:
        repo_manifest.write_text(json.dumps(real))
    monkeypatch.setattr(report_module, "REPO_MANIFEST", repo_manifest)
    harbor = FakeHarbor()
    out = tmp_path / "out"
    dev = ("--split", "dev", "--config", "full", "--task", "dbt-alpha", "--task", "dbt-charlie")
    assert run(monkeypatch, harbor, dev_manifest, out, *dev) == 2
    assert str(repo_manifest) in capsys.readouterr().err
    assert harbor.calls == harbor.smoke == []
    assert not out.exists()


def test_another_split_or_task_list_on_the_same_out_stops(
    monkeypatch: pytest.MonkeyPatch,
    dev_manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harbor = FakeHarbor()
    out = tmp_path / "out"
    dev = ("--split", "dev", "--config", "full")
    assert run(monkeypatch, harbor, dev_manifest, out, *dev, "--task", "dbt-alpha") == 0
    assert run(monkeypatch, harbor, dev_manifest, out, *dev, "--task", "dbt-bravo") == 2
    assert run(monkeypatch, harbor, dev_manifest, out, "--config", "full") == 2
    assert "another --out" in capsys.readouterr().err
    assert len(harbor.calls) == 1


SLEEPER = "import signal, sys, time; print('started', flush=True); {setup}; time.sleep(60)"


@pytest.mark.parametrize(
    ("setup", "code"),
    [("pass", -15), ("signal.signal(signal.SIGTERM, signal.SIG_IGN)", -9)],
)
def test_the_watcher_stops_its_own_child_with_sigterm_then_kills_it(
    tmp_path: Path, setup: str, code: int
) -> None:
    """Harbor's own stop is SIGTERM (it cancels its trials); a child that ignores it is killed."""
    asks: list[int] = []

    def stop_when() -> str | None:
        asks.append(1)
        return "stop now" if len(asks) >= 2 else None

    log = tmp_path / "child.log"
    command = [sys.executable, "-c", SLEEPER.format(setup=setup)]
    result = ablate.run_watched(command, log, dict(os.environ), stop_when, 0.2, grace=1.0)
    assert result == (code, "stop now")
    assert "started" in log.read_text()


def test_the_watcher_returns_the_exit_code_of_a_child_that_ends(tmp_path: Path) -> None:
    log = tmp_path / "child.log"
    command = [sys.executable, "-c", "print('done'); raise SystemExit(3)"]
    assert ablate.run_watched(command, log, dict(os.environ), lambda: None, 0.05) == (3, None)
    assert log.read_text() == "done\n"


def records(out: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted((out / "records").glob("*.json"))]


def test_every_invocation_writes_a_run_record(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    """ADR-0008 reuses an earlier result only on the same benchmark commit and machine."""
    bench = tmp_path / "data-eng-bench"
    (bench / "tasks").mkdir(parents=True)
    (bench / "tasks" / "README").write_text("tasks\n")
    git = ["git", "-C", str(bench), "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "tasks"], check=True)
    head = subprocess.run(
        [*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    monkeypatch.setenv("DATA_AGENT_MACHINE", "mac-colima")
    monkeypatch.setattr(ablate, "benchmark_commit", REAL_BENCHMARK_COMMIT)
    monkeypatch.setattr(ablate, "run_harbor", FakeHarbor())
    out = tmp_path / "out"
    argv = ablate_args(manifest, out, "--config", "full")
    argv[argv.index("--tasks-dir") + 1] = str(bench / "tasks")
    assert evals_main(argv) == 0
    (record,) = records(out)
    assert record["benchmark_commit"] == head
    assert record["machine"] == "mac-colima"
    assert record["git_commit"] == "c0ffee"
    assert datetime.fromisoformat(record["started_at"]).tzinfo is not None
    assert evals_main(argv) == 0
    assert len(records(out)) == 2
    (bench / "tasks" / "README").write_text("changed\n")
    other = tmp_path / "other"
    argv[argv.index("--out") + 1] = str(other)
    assert evals_main(argv) == 0
    assert [r["benchmark_commit"] for r in records(other)] == [f"{head}-dirty"]


def test_a_run_record_without_git_or_a_machine_label_says_so(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DATA_AGENT_MACHINE", raising=False)
    monkeypatch.setattr(ablate, "benchmark_commit", REAL_BENCHMARK_COMMIT)
    out = tmp_path / "out"
    argv = ablate_args(manifest, out, "--config", "full")
    argv[argv.index("--tasks-dir") + 1] = str(tmp_path)  # not in a git checkout
    monkeypatch.setattr(ablate, "run_harbor", FakeHarbor())
    assert evals_main(argv) == 0
    (record,) = records(out)
    assert record["benchmark_commit"] is None and record["machine"] is None
    assert "DATA_AGENT_MACHINE" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"benchmark": "0ther"}, "benchmark commit 'be4c4' there, '0ther' now"),
        ({"machine": "server-1"}, "machine 'mac-colima' there, 'server-1' now"),
        ({"benchmark": "be4c4-dirty"}, "uncommitted changes"),
        ({"benchmark": None}, "uncommitted changes"),
        ({"machine": None}, "DATA_AGENT_MACHINE is not set"),
        ({"records": "deleted"}, "no run record"),
    ],
)
def test_jobs_from_another_benchmark_or_machine_are_not_reused(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    change: dict[str, Any],
    message: str,
) -> None:
    """ADR-0008: a job resumed or skipped on another benchmark checkout or machine would mix
    tasks of two benchmark versions, or two machines, in one result."""
    monkeypatch.setenv("DATA_AGENT_MACHINE", "mac-colima")
    harbor = FakeHarbor()
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 0
    if "benchmark" in change:
        monkeypatch.setattr(ablate, "benchmark_commit", lambda _dir: change["benchmark"])
    if change.get("machine"):
        monkeypatch.setenv("DATA_AGENT_MACHINE", change["machine"])
    elif "machine" in change:
        monkeypatch.delenv("DATA_AGENT_MACHINE")
    if "records" in change:
        for path in (out / "records").iterdir():
            path.unlink()
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 2
    assert message in capsys.readouterr().err
    assert len(harbor.calls) == 1
    assert len(report_dirs(out)) == 1
    # The refused call leaves no record of its own, so the checkout OUT was run on still matches.
    assert len(records(out)) == (0 if "records" in change else 1)


def test_a_refused_benchmark_change_does_not_block_going_back(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    harbor = FakeHarbor()
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 0
    monkeypatch.setattr(ablate, "benchmark_commit", lambda _dir: "0ther")
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 2
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 2
    monkeypatch.setattr(ablate, "benchmark_commit", lambda _dir: "be4c4")
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 0


def test_a_new_out_records_whatever_benchmark_it_runs_on(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    """Records from calls that made no job do not bind OUT."""
    harbor = FakeHarbor()
    out = tmp_path / "out"
    monkeypatch.setattr(balance, "fetch", lambda: {"is_available": False, "balance_infos": []})
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 2
    assert not harbor.calls
    funded = {"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "500"}]}
    monkeypatch.setattr(balance, "fetch", lambda: funded)
    monkeypatch.setattr(ablate, "benchmark_commit", lambda _dir: "0ther")
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 0


def ablation_row(config: str, timing: dict[str, Any] | None) -> dict[str, Any]:
    row: dict[str, Any] = {"config": config, "accuracy": 50.0, "accuracy_stderr": 1.0}
    row |= {"n_trials": 3, "cost_usd": 0.1, "uncached_input_tokens": 1, "cached_input_tokens": 1}
    row |= {"output_tokens": 1}
    if timing is not None:
        row["process"] = {"timing": timing}
    return row


def test_the_markdown_reports_timing_per_config_and_marks_what_is_missing() -> None:
    timed = {
        "wall_s": {"p50": 900.0, "p90": 1200.0, "p95": 1300.0, "p99": 1390.0},
        "verifier_wall_s": {"p50": 150.0, "p90": 200.0, "p95": 210.0, "p99": 219.0},
        "verifier_unclosed": 1,
        "tool_calls": 140,
        "trials": 3,
        "trials_timed": 3,
        "timeouts": 2,
        "untimed_calls": 0,
        "tools": {
            "Bash": {"calls": 40, "p50": 2.0, "p90": 30.0, "p95": 45.0, "p99": 60.0}
            | {"timeouts": 0, "untimed": 0}
        },
    }
    old_files = timed | {"tools": None, "timeouts": None, "untimed_calls": None, "trials_timed": 0}
    rows = [
        ablation_row("full", timed),
        ablation_row("baseline", old_files),
        ablation_row("no-skill", None),  # a holdout report: no process metrics
    ]
    text = ablate.markdown(
        {"split": "dev", "n_tasks": 1, "trials_per_task": 3}
        | {"reference": "full", "configs": rows, "contributions": []}
    )
    walls = "| 900.0 / 1200.0 / 1300.0 / 1390.0 | 150.0 / 200.0 / 210.0 / 219.0 |"
    assert f"| full {walls} 1 | 140 | 3/3 | 2 | 0 |" in text
    assert f"| baseline {walls} 1 | 140 | 0/3 | missing | missing |" in text
    assert "| no-skill |" + " missing |" * 7 in text
    assert "| full | Bash | 40 | 2.0 | 30.0 | 45.0 | 60.0 | 0 | 0 |" in text
    assert "| baseline | missing | | | | | | | |" in text


def test_a_dev_ablation_report_carries_timing(
    monkeypatch: pytest.MonkeyPatch, dev_manifest: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    args = ("--split", "dev", "--task", "dbt-alpha", "--config", "full")
    assert run(monkeypatch, FakeHarbor(), dev_manifest, out, *args) == 0
    (reports,) = report_dirs(out)
    full = json.loads((reports / "full" / "report.json").read_text())
    assert full["process"]["timing"]["trials"] == 3
    assert full["process"]["timing"]["tools"] is None  # the fake events predate tool_timing
    assert "| full | missing | | | | | | | |" in (reports / "ablation.md").read_text()


def agent_kwargs(call: list[str]) -> list[str]:
    return [call[i + 1] for i, a in enumerate(call) if a == "--ak"]


def test_agent_kwargs_reach_every_harbor_run_as_is(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    """Ticket 54: `--agent-kwarg KEY=VALUE` (repeatable) becomes Harbor's `--ak KEY=VALUE`, on
    the smoke run and every job alike, after the config's own `run_config`."""
    harbor = FakeHarbor()
    out = tmp_path / "out"
    extra = ("--config", "full", "--agent-kwarg", "agent_timeout_sec=1500")
    assert run(monkeypatch, harbor, manifest, out, *extra, "--agent-kwarg", "note=a=b") == 0
    assert len(harbor.smoke) == len(harbor.calls) == 1
    for call in harbor.smoke + harbor.calls:
        assert agent_kwargs(call) == ["run_config=full", "agent_timeout_sec=1500", "note=a=b"]


def test_without_agent_kwargs_harbor_gets_only_the_run_config(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    harbor = FakeHarbor()
    assert run(monkeypatch, harbor, manifest, tmp_path / "out", "--config", "full") == 0
    calls = harbor.smoke + harbor.calls
    assert calls and all(agent_kwargs(call) == ["run_config=full"] for call in calls)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("run_config=baseline", "--config sets run_config"),
        (" run_config=baseline", "--config sets run_config"),
        ("agent_timeout_sec", "KEY=VALUE"),
        ("=1500", "KEY=VALUE"),
    ],
)
def test_an_agent_kwarg_that_is_malformed_or_overrides_the_config_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    value: str,
    message: str,
) -> None:
    harbor = FakeHarbor()
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch, harbor, manifest, tmp_path / "out", "--agent-kwarg", value)
    assert exc.value.code == 2
    assert message in capsys.readouterr().err
    assert harbor.calls == harbor.smoke == []


# --- Evaluation lifecycle contract (eval-readiness-and-lifecycle, ticket 01) ----------------------

# The models of a dbt project as a verifier dispatch lists them (`verifier_dispatch`, ticket 59):
# in stage 1, pos-operations re-dispatched all 21 of its models.
ALL_MODELS = [f"model_{i:02d}" for i in range(21)]


def dispatches(*counts: int) -> list[dict[str, Any]]:
    """`verifier_dispatch` events listing the first `count` models, each followed by its
    verifier's start."""
    events: list[dict[str, Any]] = []
    for n, count in enumerate(counts, 1):
        source = "first" if n == 1 else "again"
        events += [
            {"type": "verifier_dispatch", "n": n, "models": ALL_MODELS[:count], "source": source},
            {"type": "verifier_start", "verifier": "verifier", "agent_id": f"v{n}"},
        ]
    return events


@pytest.mark.xfail(
    strict=True, reason="ticket 02: readiness blocks a smoke stopped at its deadline"
)
def test_readiness_a_smoke_trial_stopped_at_its_deadline_stops_before_any_job(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A smoke trial the agent stopped itself at the trial deadline (the result event's
    `terminal_reason` is `deadline`, ticket 54) ends with a reward and no Harbor error, so today's
    smoke passes it. A dev task running out of time before any job is a systemic timeout risk for
    the whole batch: readiness blocks it before the first real job, as a failed smoke does."""
    harbor = FakeHarbor()
    harbor.result_fields = lambda smoke, config, task, attempt: (
        {"terminal_reason": "deadline"} if smoke and config == "baseline" else {}
    )
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full", "--config", "baseline") == 2
    assert harbor.calls == []
    assert report_dirs(out) == []
    err = capsys.readouterr().err
    assert "deadline" in err and "baseline" in err


@pytest.mark.xfail(strict=True, reason="ticket 02: readiness warnings reach the ablation table")
def test_readiness_a_full_model_redispatch_is_a_warning_the_table_carries(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A smoke trial whose second verifier dispatch re-sent every model of the first is an
    isolated efficiency finding, not a broken environment: the batch runs, the scores are the
    trials' own, and the table says the run was not clean and why. The check reads the smoke's
    event files: one smoke trial per config, no extra probe."""
    harbor = FakeHarbor()
    harbor.events = lambda smoke, config, task, attempt: (
        dispatches(21, 21) if smoke and config == "full" else []
    )
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full", "--config", "baseline") == 0
    printed = capsys.readouterr()
    assert len(harbor.smoke) == len(harbor.calls) == 2
    result = table(out)
    assert {c["config"]: c["accuracy"] for c in result["configs"]} == pytest.approx(
        {"full": 100.0, "baseline": 0.0}
    )
    readiness = result["readiness"]
    assert readiness["clean"] is False
    (warning,) = readiness["warnings"]
    assert {"check", "reason"} <= set(warning)
    assert "redispatch" in f"{warning['check']} {warning['reason']}".lower()
    assert "warning" in (printed.out + printed.err).lower()
    markdown = (report_dirs(out)[0] / "ablation.md").read_text().lower()
    assert "warning" in markdown and "redispatch" in markdown


@pytest.mark.xfail(strict=True, reason="ticket 02: the ablation table states readiness")
def test_readiness_without_findings_is_reported_clean(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    """Later dispatches that narrow the model list are the expected shape: no warning."""
    harbor = FakeHarbor()
    harbor.events = lambda smoke, config, task, attempt: dispatches(21, 6, 5)
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 0
    assert table(out)["readiness"] == {"clean": True, "warnings": []}


def test_logical_failures_stay_scored_and_a_rerun_does_not_retry_them(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    """A wrong answer (reward 0) and the agent running out of time (AgentTimeoutError, which
    Harbor does not retry) are the agent's results: the job is complete, a rerun reuses it, and
    the failures stay in the score. Only a new --out runs them again."""

    def outcome(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
        if task == "dbt-echo" and attempt == 0:
            return 0.0, None
        if task == "dbt-echo" and attempt == 1:
            return None, "AgentTimeoutError"
        return 1.0, None

    harbor = FakeHarbor(outcome)
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 0
    job = out / "jobs" / "full"
    failed = sorted(p.name for p in job.glob("dbt-echo__a[01]c*"))
    assert len(failed) == 2
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 0
    assert len(harbor.calls) == 1
    assert sorted(p.name for p in job.glob("dbt-echo__a[01]c*")) == failed
    assert not (out / "dropped").exists()
    for report in report_dirs(out):
        (full,) = json.loads((report / "ablation.json").read_text())["configs"]
        assert full["accuracy"] == pytest.approx(100 * 4 / 6)


def test_a_resume_reruns_infrastructure_errors_and_keeps_logical_failures(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    """In one job, the resume deletes and reruns the infrastructure error under the job's own
    request (`harbor job resume -p`, `-f` its exception type only); the logical failures are left
    as they were and scored."""
    broken = {"yes": True}

    def outcome(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
        if task == "dbt-delta" and attempt == 0 and broken["yes"]:
            return None, "ApiInternalServerError"
        if task == "dbt-echo" and attempt == 0:
            return 0.0, None
        if task == "dbt-echo" and attempt == 1:
            return None, "AgentTimeoutError"
        return 1.0, None

    harbor = FakeHarbor(outcome)
    out = tmp_path / "out"
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 2
    job = out / "jobs" / "full"
    failed = sorted(p.name for p in job.glob("dbt-echo__a[01]c*"))
    (errored,) = [p.name for p in job.glob("dbt-delta__a0c*")]
    broken["yes"] = False
    assert run(monkeypatch, harbor, manifest, out, "--config", "full") == 0
    assert harbor.calls[-1] == ["job", "resume", "-p", str(job), "-f", "ApiInternalServerError"]
    assert sorted(p.name for p in job.glob("dbt-echo__a[01]c*")) == failed
    assert not (job / errored).exists()
    assert sorted(p.name for p in (out / "dropped" / "full").iterdir()) == [errored]
    (full,) = table(out)["configs"]
    assert (full["n_trials"], full["accuracy"]) == (6, pytest.approx(100 * 4 / 6))


def test_a_dropped_trial_is_counted_once_however_many_resumes_follow(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    """The same slot errors in two rounds: both dropped trials are kept and each is counted once,
    next to the six scored trials; a later call that runs nothing copies nothing again."""
    rounds = {"errors_left": 2}

    def outcome(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
        if task == "dbt-delta" and attempt == 0 and rounds["errors_left"]:
            rounds["errors_left"] -= 1
            return None, "ApiInternalServerError"
        return 1.0, None

    harbor = FakeHarbor(outcome)
    out = tmp_path / "out"
    full = ("--config", "full", "--skip-smoke")
    assert [run(monkeypatch, harbor, manifest, out, *full) for _ in range(4)] == [2, 2, 0, 0]
    assert len(harbor.calls) == 3
    assert len(list((out / "dropped" / "full").iterdir())) == 2
    code, printed, _ = ablate.evals_cli(["spend", str(out), "--budget", "100"])
    assert code == 0 and "(cost_usd $0.0800) over 8 event file(s)" in printed
    for report in report_dirs(out):
        assert json.loads((report / "full" / "report.json").read_text())["n_trials"] == 6


@pytest.mark.xfail(strict=True, reason="ticket 03: an interrupted trial's spend survives resume")
def test_the_spend_of_an_interrupted_trial_survives_its_resume(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    """Harbor's resume deletes a trial directory without result.json (harbor/job.py
    `_maybe_init_existing_job`), the agent's event file with it. What that trial cost was billed
    all the same, so the spend guard must still count it, kept under OUT/dropped/ as an
    infrastructure error's is; the rerun trial is counted on its own, once."""
    harbor = FakeHarbor(cost=1.0)
    out = tmp_path / "out"
    full = ("--config", "full", "--skip-smoke")
    assert run(monkeypatch, harbor, manifest, out, *full) == 0
    job = out / "jobs" / "full"
    victim = next(job.glob("dbt-echo__*"))
    (victim / "result.json").unlink()  # the agent ended; Harbor was stopped before the result
    assert run(monkeypatch, harbor, manifest, out, *full) == 0
    assert harbor.calls[-1] == ["job", "resume", "-p", str(job)]
    assert not victim.exists()
    code, printed, _ = ablate.evals_cli(["spend", str(out), "--budget", "100"])
    assert code == 0 and "(cost_usd $7.0000) over 7 event file(s)" in printed


@pytest.mark.xfail(strict=True, reason="ticket 04: the table states infrastructure exclusions")
def test_the_table_states_the_infrastructure_errors_it_excluded_and_their_spend(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, tmp_path: Path
) -> None:
    """Dropped trials are out of the score but not out of the record: each config's row gives how
    many trials resumes dropped and what they cost, as counts (no task, so holdout-safe)."""
    broken = {"yes": True}

    def outcome(config: str, task: str, attempt: int) -> tuple[float | None, str | None]:
        if config == "full" and task == "dbt-delta" and attempt == 0 and broken["yes"]:
            return None, "ApiInternalServerError"
        return 1.0, None

    harbor = FakeHarbor(outcome)
    out = tmp_path / "out"
    configs = ("--config", "full", "--config", "baseline")
    assert run(monkeypatch, harbor, manifest, out, *configs) == 2
    broken["yes"] = False
    assert run(monkeypatch, harbor, manifest, out, *configs) == 0
    rows = {row["config"]: row for row in table(out)["configs"]}
    assert rows["full"]["n_trials"] == rows["baseline"]["n_trials"] == 6
    assert rows["full"]["infra_excluded"] == {"trials": 1, "cost_usd": pytest.approx(0.01)}
    assert rows["baseline"]["infra_excluded"] == {"trials": 0, "cost_usd": 0}
