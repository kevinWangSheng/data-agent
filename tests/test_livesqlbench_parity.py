"""Ticket 136: the oracle and the parity of our soft EX with upstream's evaluator
(evals.livesqlbench_parity), with a stand-in for docker and git: no database, no network."""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from evals import livesqlbench
from evals import livesqlbench_parity as lp
from evals.__main__ import main as evals_main
from evals.livesqlbench_parity import Side

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "livesqlbench"
PASSWORD = "parityTestWrite" + "Credential0002"
GOLD = {
    "orchard_q1": ["SELECT tree_id FROM orchard_trees WHERE planted_year > 2001"],
    "tide_q1": ["SELECT gauge_id, max(level) FROM tide_readings GROUP BY gauge_id"],
}
DEV, HOLDOUT = "lsb-orchard_q1", "lsb-tide_q1"

# What a fake side says about a case: (reward, extra) where extra is our diagnostics' fields or
# upstream's log text; None as the reward writes no reward file.
type Verdict = Callable[[str, bytes, int], tuple[float | None, dict[str, Any] | str]]


def ours_by_default(task: str, pred: bytes, call: int) -> tuple[float | None, dict[str, Any]]:
    right = json.loads(pred)["predicted_sql"] == GOLD[task.removeprefix("lsb-")]
    return (1 if right else 0), {"outcome": "match" if right else "mismatch", "predicted_rows": 2}


def upstream_by_default(task: str, pred: bytes, call: int) -> tuple[float | None, str]:
    right = json.loads(pred)["predicted_sql"] == GOLD[task.removeprefix("lsb-")]
    # As upstream logs: every query, the gold one too (db_utils.py:302-304).
    sql = GOLD[task.removeprefix("lsb-")][0]
    log = f"[evaluator] Executing query 1/1: {sql}\n[evaluator] Reward written: {int(right)}\n"
    if not right:
        log += "[test.sh] ERROR: Evaluator crashed\n"  # its exit 1 on a 0
    return (1 if right else 0.0), log


class Fake:
    """docker and git as the parity run calls them; each scoring container writes what `ours` or
    `upstream` says into its /logs/verifier mount, from its /app/pred.json and /tests payload."""

    def __init__(self) -> None:
        self.ours: Verdict = ours_by_default  # pyright: ignore[reportAttributeAccessIssue]
        self.upstream: Verdict = upstream_by_default  # pyright: ignore[reportAttributeAccessIssue]
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str] | None] = []
        self.images: set[str] = set()
        self.head = lp.UPSTREAM_COMMIT
        self.scored: dict[tuple[str, str], int] = {}

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        self.envs.append(kwargs.get("env"))
        code, stdout = 0, ""
        if cmd[0] == "git":
            repo = Path(cmd[2])
            if cmd[3] == "rev-parse":
                code, stdout = (0, self.head + "\n") if (repo / ".git").is_dir() else (128, "")
            elif cmd[3] == "checkout":
                (repo / ".git").mkdir()
                tests = repo / lp.UPSTREAM_TESTS
                tests.mkdir(parents=True)
                (tests / "test.sh").write_text("#!/bin/bash\n")
                (tests / "evaluator.py").write_text("print('upstream')\n")
                (repo / lp.UPSTREAM_SRC).mkdir(parents=True)
                (repo / lp.UPSTREAM_SRC / "db_utils.py").write_text("# upstream\n")
                (repo / lp.UPSTREAM_REQUIREMENTS).parent.mkdir(parents=True)
                (repo / lp.UPSTREAM_REQUIREMENTS).write_text("psycopg2-binary==2.9.9\n")
        elif cmd[:3] == ["docker", "image", "inspect"]:
            code = 0 if cmd[3] in self.images else 1
        elif cmd[:2] == ["docker", "build"]:
            self.images.add(cmd[cmd.index("-t") + 1])
        elif cmd[:2] == ["docker", "run"] and cmd[-2:] == ["bash", "/tests/test.sh"]:
            self.verify(cmd)
        return subprocess.CompletedProcess(cmd, code, stdout, "")

    def verify(self, cmd: list[str]) -> None:
        mounts = {
            arg.split(":")[1]: Path(arg.split(":")[0])
            for i, arg in enumerate(cmd)
            if i and cmd[i - 1] == "-v"
        }
        task = json.loads((mounts["/tests"] / "task_payload.json").read_text())["instance_id"]
        pred = (mounts["/app"] / "pred.json").read_bytes()
        out = mounts["/logs/verifier"]
        side = "ours" if cmd[-3] == livesqlbench.main_image() else "upstream"
        key = (side, f"lsb-{task}")
        self.scored[key] = call = self.scored.get(key, 0) + 1
        reward, extra = (self.ours if side == "ours" else self.upstream)(f"lsb-{task}", pred, call)
        if reward is not None:
            (out / "reward.txt").write_text(f"{reward}\n")
        if side == "ours":
            assert isinstance(extra, dict)
            (out / "diagnostics.json").write_text(json.dumps({"reward": reward, **extra}))
        else:
            assert isinstance(extra, str)
            (out / "evaluator_stdout.txt").write_text(extra)
            (out / "debug.txt").write_text(pred.decode())  # test.sh cats pred.json


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """ROOT with the fixture's two Query tasks and their gold answers; the first one is dev."""
    monkeypatch.setenv(livesqlbench.WRITE_PASSWORD_ENV, PASSWORD)
    monkeypatch.setattr(lp, "dev_set", lambda: {DEV})
    answers = tmp_path / "answers.jsonl"
    answers.write_text(
        "".join(
            json.dumps({"instance_id": i, "sol_sql": s, "test_cases": [], "external_knowledge": []})
            + "\n"
            for i, s in GOLD.items()
        )
    )
    root = tmp_path / "root"
    livesqlbench.generate_tasks(
        FIXTURE / "public.jsonl",
        answers,
        FIXTURE / "db_assets",
        root / "tasks",
        write_password=PASSWORD,
    )
    return root


def write_trial(runs: Path, job: str, trial: str, task: str, pred: dict[str, Any] | None) -> None:
    path = runs / "jobs" / job / trial
    (path / "agent").mkdir(parents=True)
    (path / "result.json").write_text(json.dumps({"task_name": task}))
    if pred is not None:
        (path / "agent" / "pred.json").write_text(json.dumps(pred))


def parity(root: Path, tmp_path: Path, fake: Fake, trials: list[Path] | None = None) -> Any:
    return lp.parity(root, tmp_path / "out", trials or [], tmp_path / "scratch", jobs=2, run=fake)


def test_the_oracle_and_both_parity_checks_agree_and_leave_no_sql(
    setup: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trials = tmp_path / "lsb-smoke"
    wrong = {"instance_id": "orchard_q1", "predicted_sql": ["SELECT 1"]}
    write_trial(trials, "baseline", f"{DEV}__aaa", DEV, wrong)
    write_trial(trials, "baseline", f"{DEV}__bbb", DEV, None)
    write_trial(trials, "baseline", f"{HOLDOUT}__ccc", HOLDOUT, wrong)
    (trials / "jobs" / "baseline" / "result.json").write_text(json.dumps({"stats": {}}))
    fake = Fake()
    summary = parity(setup, tmp_path, fake, [trials])
    assert lp.passed(summary)
    assert summary["oracle"] == {
        "right": 2,
        "of": 2,
        "wrong_by_category": {},
        "dev_wrong": [],
        "holdout_wrong_by_category": {},
    }
    assert (summary["parity_gold"]["agree"], summary["parity_gold"]["cases"]) == (2, 2)
    assert (summary["parity_trials"]["agree"], summary["parity_trials"]["cases"]) == (1, 1)
    assert (summary["trials_without_pred_json"], summary["holdout_trials_skipped"]) == (1, 1)
    assert summary["scratch_removed"] and not (tmp_path / "scratch").exists()
    # The holdout trial's pred.json was never scored.
    assert fake.scored == {
        ("ours", DEV): 2,
        ("upstream", DEV): 2,
        ("ours", HOLDOUT): 1,
        ("upstream", HOLDOUT): 1,
    }
    report = (tmp_path / "out" / "reports" / "parity.json").read_text()
    assert json.loads(report) == summary
    printed = capsys.readouterr().out
    for sql in (s for sqls in GOLD.values() for s in sqls):
        assert sql not in report and sql not in printed
    assert HOLDOUT not in report and HOLDOUT not in printed
    assert "oracle: 2/2" in printed and "parity on gold SQL: 2/2 agree" in printed


def test_a_disagreement_names_the_dev_task_and_counts_the_holdout(
    setup: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = Fake()

    def ours(task: str, pred: bytes, call: int) -> tuple[float | None, dict[str, Any]]:
        if task == DEV:  # the verifier failed itself: no reward
            return None, {"outcome": "verifier_error", "error": "OperationalError"}
        return 0, {"outcome": "mismatch", "predicted_rows": 3}

    fake.ours = ours  # pyright: ignore[reportAttributeAccessIssue]
    summary = parity(setup, tmp_path, fake)
    assert not lp.passed(summary)
    assert summary["oracle"]["right"] == 0
    assert summary["oracle"]["dev_wrong"] == [
        {"task": DEV, "category": "verifier_error", "error": "OperationalError"}
    ]
    assert summary["oracle"]["holdout_wrong_by_category"] == {"different_result": 1}
    gold = summary["parity_gold"]
    assert gold["by_status"] == {"disagree": 1, "ours_no_reward": 1}
    ((only,),) = [gold["dev_disagree"]]
    assert (only["task"], only["status"], only["ours_outcome"]) == (
        DEV,
        "ours_no_reward",
        "verifier_error",
    )
    assert gold["holdout_disagree"] == 1
    printed = capsys.readouterr().out
    assert HOLDOUT not in printed and HOLDOUT not in json.dumps(summary)
    assert f"dev {DEV}: ours_no_reward" in printed
    assert "holdout: 1 not agreeing" in printed


@pytest.mark.parametrize(
    ("ours", "rows", "category"),
    [
        (Side(reward=1, outcome="match"), None, None),
        (
            Side(reward=0, outcome="predicted_error", error="UndefinedTable"),
            None,
            "execution_error",
        ),
        (Side(reward=0, outcome="gold_error", error="QueryCanceled"), None, "execution_error"),
        (Side(reward=0, outcome="mismatch"), 0, "empty_result"),
        (Side(reward=0, outcome="mismatch"), 4, "different_result"),
        (Side(reward=None, outcome="verifier_error"), None, "verifier_error"),
        (Side(reward=0, outcome="no_prediction"), None, "no_prediction"),
    ],
)
def test_the_oracle_classes_a_gold_sql_that_did_not_score_1(
    ours: Side, rows: int | None, category: str | None
) -> None:
    ours.predicted_rows = rows
    assert lp.oracle_category(ours) == category


@pytest.mark.parametrize(
    ("ours", "upstream", "status"),
    [
        (Side(reward=1), Side(reward=1.0, flags=["reward_written"]), "agree"),
        (Side(reward=0), Side(reward=0.0, flags=["reward_written"]), "agree"),
        (Side(reward=0), Side(reward=0.0, flags=["no_prediction"]), "agree"),
        (Side(reward=1), Side(reward=0.0, flags=["reward_written"]), "disagree"),
        (Side(reward=None), Side(reward=1.0), "ours_no_reward"),
        # evaluator.py swallows an exception into reward 1: no score, never an agreement.
        (Side(reward=1), Side(reward=1.0, flags=["unexpected_exception"]), "upstream_failed"),
        (Side(reward=0), Side(reward=0.0, flags=["evaluator_crashed"]), "upstream_failed"),
        (Side(reward=0), Side(reward=None), "upstream_failed"),
    ],
)
def test_compare_tells_an_upstream_failure_from_its_0(
    ours: Side, upstream: Side, status: str
) -> None:
    assert lp.compare(ours, upstream) == status


def test_upstream_flags_take_a_0s_exit_for_no_crash() -> None:
    scored_0 = "[evaluator] Reward written: 0\n[test.sh] ERROR: Evaluator crashed\n"
    assert lp.upstream_flags(scored_0) == ["reward_written"]
    assert lp.upstream_flags("[test.sh] ERROR: Evaluator crashed\n") == ["evaluator_crashed"]
    swallowed = (
        "[evaluator][error] Unexpected evaluation exception: x\n[evaluator] Reward written: 1"
    )
    assert lp.upstream_flags(swallowed) == ["reward_written", "unexpected_exception"]


def test_a_timeout_disagreement_is_rerun_once(setup: Path, tmp_path: Path) -> None:
    fake = Fake()

    def ours(task: str, pred: bytes, call: int) -> tuple[float | None, dict[str, Any]]:
        if task == DEV and call == 1:
            return 0, {"outcome": "gold_error", "error": "QueryCanceled"}
        return ours_by_default(task, pred, call)

    fake.ours = ours  # pyright: ignore[reportAttributeAccessIssue]
    summary = parity(setup, tmp_path, fake)
    assert lp.passed(summary)
    assert summary["parity_gold"]["reruns"] == 1
    assert fake.scored[("ours", DEV)] == 2 and fake.scored[("ours", HOLDOUT)] == 1


def test_the_credential_reaches_docker_by_name_only(setup: Path, tmp_path: Path) -> None:
    fake = Fake()
    parity(setup, tmp_path, fake)
    assert not any(PASSWORD in arg for call in fake.calls for arg in call)
    scoring = [
        (call, env)
        for call, env in zip(fake.calls, fake.envs, strict=True)
        if call[-2:] == ["bash", "/tests/test.sh"]
    ]
    assert len(scoring) == 4
    for call, env in scoring:
        assert "PGPASSWORD" in call and env is not None and env["PGPASSWORD"] == PASSWORD
        assert "--rm" in call
        # Named by database: memory.log records container names, a task id could be a holdout's.
        name = call[call.index("--name") + 1]
        assert "q1" not in name and name.startswith("da-lsb-parity-")


def test_one_database_container_per_database_and_upstreams_image_built_once(
    setup: Path, tmp_path: Path
) -> None:
    fake = Fake()
    parity(setup, tmp_path, fake)
    started = [c for c in fake.calls if c[:3] == ["docker", "run", "-d"]]
    assert sorted(c[-1] for c in started) == sorted(
        livesqlbench.db_image(db) for db in ("orchard_ledger", "tide_gauges")
    )
    assert [c[:2] for c in fake.calls].count(["docker", "build"]) == 1
    # The checkout is kept and checked against the commit on the next run; the image is reused.
    parity(setup, tmp_path, fake)
    assert [c[:2] for c in fake.calls].count(["docker", "build"]) == 1
    assert [c[3] for c in fake.calls if c[0] == "git"].count("fetch") == 1


def test_an_upstream_checkout_at_another_commit_is_refused(setup: Path, tmp_path: Path) -> None:
    fake = Fake()
    parity(setup, tmp_path, fake)
    fake.head = "0" * 40
    with pytest.raises(SystemExit, match="is not at"):
        parity(setup, tmp_path, fake)


def test_tasks_this_checkout_cannot_score_are_refused_by_count(setup: Path, tmp_path: Path) -> None:
    task = setup / "tasks" / HOLDOUT
    (task / "tests" / "livesqlbench_verifier.py").write_text("# an older verifier\n")
    with pytest.raises(SystemExit) as stop:
        parity(setup, tmp_path, Fake())
    message = str(stop.value)
    assert "1 with another verifier than this checkout's" in message
    assert "rerun da-remote benchmarks" in message and HOLDOUT not in message


def test_tasks_without_a_gold_answer_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(livesqlbench.WRITE_PASSWORD_ENV, PASSWORD)
    empty = tmp_path / "none.jsonl"
    empty.write_text("")
    root = tmp_path / "root"
    livesqlbench.generate_tasks(
        FIXTURE / "public.jsonl",
        empty,
        FIXTURE / "db_assets",
        root / "tasks",
        write_password=PASSWORD,
    )
    with pytest.raises(SystemExit, match="2 without a gold answer"):
        parity(root, tmp_path, Fake())


def test_the_scratch_is_never_under_runs_or_out_nor_a_directory_it_did_not_make(
    setup: Path, tmp_path: Path
) -> None:
    for scratch in (REPO_ROOT / "runs" / "x" / "scratch", tmp_path / "out" / "scratch"):
        with pytest.raises(SystemExit, match="must not be under"):
            lp.parity(setup, tmp_path / "out", [], scratch, run=Fake())
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "keep.txt").write_text("a human's file\n")
    with pytest.raises(SystemExit, match="no scratch of this command"):
        lp.parity(setup, tmp_path / "out", [], mine, run=Fake())
    assert (mine / "keep.txt").is_file()


def test_the_scratch_is_removed_when_scoring_fails(setup: Path, tmp_path: Path) -> None:
    fake = Fake()

    def broken(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["docker", "run", "-d"]:
            return subprocess.CompletedProcess(cmd, 1, "", "")
        return fake(cmd, **kwargs)

    with pytest.raises(SystemExit, match="did not start"):
        lp.parity(setup, tmp_path / "out", [], tmp_path / "scratch", run=broken)
    assert not (tmp_path / "scratch").exists()


def test_leaks_counts_gold_statements_raw_and_cleaned_and_prints_the_count_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sql = "SELECT DISTINCT tree_id  FROM orchard_trees\n WHERE planted_year > 2001"
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"instance_id": "a", "sol_sql": [sql, "SELECT 1"]}) + "\n")
    run = tmp_path / "run"
    (run / "reports").mkdir(parents=True)
    (run / "reports" / "parity.json").write_text(json.dumps({"right": 1}))
    assert (
        evals_main(["livesqlbench-parity", "leaks", "--answers", str(answers), "--dir", str(run)])
        == 0
    )
    assert "gold SQL: 0 occurrence(s)" in capsys.readouterr().out
    (run / "log.txt").write_text("q: SELECT tree_id FROM orchard_trees WHERE planted_year > 2001\n")
    (run / "raw.txt").write_text(sql)
    assert (
        evals_main(["livesqlbench-parity", "leaks", "--answers", str(answers), "--dir", str(run)])
        == 1
    )
    printed = capsys.readouterr().out
    assert "gold SQL: 2 occurrence(s)" in printed and "orchard_trees" not in printed
