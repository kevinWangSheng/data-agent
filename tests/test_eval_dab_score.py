"""DAB scoring (ticket 35): DAB's own validators, DAB's Pass@1, and a paired bootstrap.

DAB itself is replaced by `tests/fixtures/fake_dab` (no DAB code). Each query's `validate.py` here
passes an answer that contains the query's gold word, and counts how often it was called.
"""

import json
import shutil
from pathlib import Path

import pytest

from evals import dab_score
from evals.__main__ import main as evals_main

FAKE_DAB = Path(__file__).parent / "fixtures" / "fake_dab"

CHECKER = """\
from pathlib import Path

def validate(llm_answer):
    calls = Path(__file__).with_name("calls")
    calls.write_text(calls.read_text() + "x" if calls.exists() else "x")
    ok = {gold!r} in llm_answer
    return ok, "gold found" if ok else "gold missing"
"""


def make_dab(root: Path, queries: dict[str, list[str]]) -> Path:
    """A DAB clone with these query ids per dataset; query q of any dataset has gold `gold<q>`."""
    shutil.copytree(FAKE_DAB, root)
    for dataset, ids in queries.items():
        for q in ids:
            query = root / f"query_{dataset}" / f"query{q}"
            query.mkdir(parents=True)
            (query / "validate.py").write_text(CHECKER.format(gold=f"gold{q}"))
    return root


def make_out(out: Path, answers: dict[tuple[str, str, int], str | None]) -> Path:
    """An `evals dab` output: a run directory per key, a submission entry unless the answer is None
    (a run that broke on infrastructure)."""
    entries = []
    out.mkdir(parents=True)
    for (dataset, q, run), answer in answers.items():
        (out / dataset / f"query{q}" / f"run{run}").mkdir(parents=True)
        if answer is not None:
            entries.append({"dataset": dataset, "query": q, "run": str(run), "answer": answer})
    (out / "submission.json").write_text(json.dumps(entries))
    return out


@pytest.fixture
def dab_root(tmp_path: Path) -> Path:
    return make_dab(tmp_path / "dab", {"alpha": ["1", "2", "3"], "beta": ["1"]})


def test_grade_runs_the_query_validator_from_the_clone(dab_root: Path) -> None:
    grade = dab_score.grader(dab_root)
    assert grade("alpha", "2", "The answer is gold2.") is True
    assert grade("alpha", "2", "The answer is gold1.") is False
    assert (dab_root / "query_alpha" / "query2" / "calls").read_text() == "xx"


def test_an_empty_answer_fails_without_running_the_validator(dab_root: Path) -> None:
    assert dab_score.grader(dab_root)("alpha", "1", "") is False
    assert not (dab_root / "query_alpha" / "query1" / "calls").exists()


@pytest.mark.parametrize(
    "checker",
    [
        "def validate(llm_answer):\n    return True\n",  # not (is_valid, reason)
        "def validate(llm_answer):\n    return 'yes', 'reason'\n",  # is_valid not a bool
        "def validate(llm_answer, gold):\n    return True, ''\n",  # another signature
    ],
)
def test_a_validator_of_another_shape_is_an_error_naming_the_query(
    dab_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], checker: str
) -> None:
    (dab_root / "query_alpha" / "query2" / "validate.py").write_text(checker)
    out = make_out(tmp_path / "out", {("alpha", "1", 0): "gold1", ("alpha", "2", 0): "gold2"})
    assert evals_main(["dab-score", str(out), "--dab-root", str(dab_root)]) == 2
    assert "alpha query2" in capsys.readouterr().err
    assert not (out / "dab_score.json").exists()


def test_a_numpy_bool_from_the_validator_counts(dab_root: Path) -> None:
    # numpy is not a dependency here: a class of the same name stands in for numpy.bool_.
    checker = (
        "class bool_:\n"
        "    def __init__(self, v): self.v = v\n"
        "    def __bool__(self): return self.v\n"
        "def validate(llm_answer):\n"
        "    return bool_('gold1' in llm_answer), ''\n"
    )
    (dab_root / "query_alpha" / "query1" / "validate.py").write_text(checker)
    grade = dab_score.grader(dab_root)
    assert grade("alpha", "1", "gold1") is True
    assert grade("alpha", "1", "gold2") is False


def test_pass_at_1_averages_queries_within_a_dataset_then_datasets(
    dab_root: Path, tmp_path: Path
) -> None:
    # alpha: 3 queries, none passes; beta: 1 query, passes in 1 of 2 runs.
    out = make_out(
        tmp_path / "out",
        {
            ("alpha", "1", 0): "no",
            ("alpha", "2", 0): "no",
            ("alpha", "3", 0): "no",
            ("beta", "1", 0): "gold1",
            ("beta", "1", 1): "nope",
        },
    )
    result = dab_score.score(dab_root, out)
    assert result.query_rates() == {
        ("alpha", "1"): 0.0,
        ("alpha", "2"): 0.0,
        ("alpha", "3"): 0.0,
        ("beta", "1"): 0.5,
    }
    assert result.dataset_rates() == {"alpha": 0.0, "beta": 0.5}
    # DAB's Pass@1 is (0 + 0.5) / 2; a flat mean over the 4 queries would be 0.125.
    assert result.pass_at_1() == 0.25


def broken_run_out(tmp_path: Path) -> Path:
    """beta query1 run1 broke on infrastructure: its directory is there, its answer is not."""
    return make_out(
        tmp_path / "out",
        {
            ("alpha", "1", 0): "gold1",
            ("alpha", "1", 1): "gold1",
            ("beta", "1", 0): "gold1",
            ("beta", "1", 1): None,
        },
    )


def test_missing_runs_count_as_fails_and_are_listed(
    dab_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = broken_run_out(tmp_path)
    report = tmp_path / "report.json"
    code = evals_main(["dab-score", str(out), "--dab-root", str(dab_root), "--report", str(report)])
    assert code == 0
    err = capsys.readouterr().err
    assert "1 missing run" in err
    assert "beta query1 run1" in err
    data = json.loads(report.read_text())
    assert data["candidate"]["missing"] == [{"dataset": "beta", "query": "1", "run": 1}]
    # alpha 1.0; beta 1 of 2 runs -> 0.5.
    assert data["candidate"]["pass_at_1"] == 0.75


def test_runs_sets_the_expected_runs_per_query(dab_root: Path, tmp_path: Path) -> None:
    out = make_out(tmp_path / "out", {("alpha", "1", 0): "gold1"})
    result = dab_score.score(dab_root, out, runs=4)
    assert result.missing == (("alpha", "1", 1), ("alpha", "1", 2), ("alpha", "1", 3))
    assert result.pass_at_1() == 0.25


def test_all_queries_counts_queries_the_output_never_ran_as_fails(
    dab_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The clone has alpha 1-3 and beta 1; the output ran alpha query1 twice and nothing else.
    out = make_out(tmp_path / "out", {("alpha", "1", 0): "gold1", ("alpha", "1", 1): "gold1"})
    report = tmp_path / "report.json"
    argv = ["dab-score", str(out), "--dab-root", str(dab_root), "--report", str(report)]
    assert evals_main(argv) == 0
    data = json.loads(report.read_text())
    assert data["candidate"]["pass_at_1"] == 1.0
    assert data["candidate"]["coverage"] == {"queries": 1, "clone_queries": 4}
    assert "1 of the clone's 4 queries" in capsys.readouterr().err

    assert evals_main([*argv, "--all-queries"]) == 0
    data = json.loads(report.read_text())
    # alpha (1 + 0 + 0) / 3, beta 0; the never-run queries are missing for both runs found.
    assert data["candidate"]["pass_at_1"] == pytest.approx(1 / 6)
    assert data["candidate"]["coverage"] == {"queries": 4, "clone_queries": 4}
    assert {"dataset": "beta", "query": "1", "run": 1} in data["candidate"]["missing"]


def test_an_output_without_runs_is_an_error(
    dab_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = make_out(tmp_path / "out", {})
    assert evals_main(["dab-score", str(out), "--dab-root", str(dab_root)]) == 2
    assert "no runs" in capsys.readouterr().err


def test_strict_exits_2_on_missing_runs(
    dab_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = broken_run_out(tmp_path)
    report = tmp_path / "report.json"
    argv = ["dab-score", str(out), "--dab-root", str(dab_root), "--report", str(report)]
    assert evals_main([*argv, "--strict"]) == 2
    assert "beta query1 run1" in capsys.readouterr().err
    assert not report.exists()


ALL_QUERIES = [("alpha", "1"), ("alpha", "2"), ("alpha", "3"), ("beta", "1")]


def pair(tmp_path: Path, base_ok: set[tuple[str, str]], cand_ok: set[tuple[str, str]]) -> tuple:
    """Baseline and candidate outputs, one run per query; listed queries pass."""

    def answers(ok: set[tuple[str, str]]) -> dict[tuple[str, str, int], str | None]:
        return {(d, q, 0): f"gold{q}" if (d, q) in ok else "no" for d, q in ALL_QUERIES}

    return make_out(tmp_path / "base", answers(base_ok)), make_out(
        tmp_path / "cand", answers(cand_ok)
    )


def test_bootstrap_resamples_within_each_dataset(dab_root: Path, tmp_path: Path) -> None:
    # Only beta's single query improves: every resample keeps beta, so the difference in Pass@1
    # is (0 + 100) / 2 points in each one. Resampling all queries together would vary instead.
    base, cand = pair(tmp_path, set(), {("beta", "1")})
    result = dab_score.paired_bootstrap(
        dab_score.score(dab_root, base), dab_score.score(dab_root, cand), seed=0, n_boot=500
    )
    assert result == {"diff": 50.0, "ci_low": 50.0, "ci_high": 50.0, "significant": True}


def test_bootstrap_is_reproducible_with_a_fixed_seed(dab_root: Path, tmp_path: Path) -> None:
    base, cand = pair(tmp_path, {("alpha", "1")}, {("alpha", "2"), ("alpha", "3")})
    scores = dab_score.score(dab_root, base), dab_score.score(dab_root, cand)
    first = dab_score.paired_bootstrap(*scores, seed=7, n_boot=300)
    assert first == dab_score.paired_bootstrap(*scores, seed=7, n_boot=300)
    # alpha: 1/3 -> 2/3, beta: 0 -> 0, so Pass@1 rises by (33.3 + 0) / 2 points.
    assert first["diff"] == pytest.approx(100 / 6)
    low, diff, high = first["ci_low"], first["diff"], first["ci_high"]
    assert low is not None and diff is not None and high is not None
    assert low < diff < high
    assert first["significant"] is False


def test_bootstrap_refuses_outputs_on_different_queries(dab_root: Path, tmp_path: Path) -> None:
    base = make_out(tmp_path / "base", {("alpha", "1", 0): "gold1", ("alpha", "2", 0): "x"})
    cand = make_out(tmp_path / "cand", {("alpha", "1", 0): "gold1", ("beta", "1", 0): "x"})
    with pytest.raises(ValueError, match="2 queries are unpaired"):
        dab_score.paired_bootstrap(
            dab_score.score(dab_root, base), dab_score.score(dab_root, cand), seed=0, n_boot=10
        )


def test_cli_compares_with_a_baseline(
    dab_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base, cand = pair(tmp_path, set(), {("beta", "1")})
    report = tmp_path / "report.json"
    argv = ["dab-score", str(cand), "--baseline", str(base), "--dab-root", str(dab_root)]
    assert evals_main([*argv, "--report", str(report), "--n-boot", "200"]) == 0
    data = json.loads(report.read_text())
    assert data["baseline"]["pass_at_1"] == 0.0
    assert data["candidate"]["pass_at_1"] == 0.5
    assert data["candidate"]["datasets"]["beta"]["queries"]["1"] == {"passed": 1, "runs": 1}
    assert data["comparison"] == {
        "diff": 50.0,
        "ci_low": 50.0,
        "ci_high": 50.0,
        "significant": True,
    }
    out = capsys.readouterr().out
    assert "baseline: Pass@1 0.0000 (over 4 of the clone's 4 queries)" in out
    assert "candidate - baseline: +50.00 points" in out


def test_cli_refuses_unpaired_outputs_by_count(
    dab_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = make_out(tmp_path / "base", {("alpha", "1", 0): "gold1"})
    cand = make_out(tmp_path / "cand", {("beta", "1", 0): "gold1"})
    argv = ["dab-score", str(cand), "--baseline", str(base), "--dab-root", str(dab_root)]
    assert evals_main(argv) == 2
    assert "2 queries are unpaired" in capsys.readouterr().err


def test_cli_reports_no_interval_when_every_dataset_has_a_single_query(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Ticket 62: resampling within a dataset of one query always draws that query, so every
    # resample equals the point estimate; the interval would be a point and look significant.
    dab_root = make_dab(tmp_path / "dab", {"alpha": ["1"], "beta": ["1"]})
    base = make_out(tmp_path / "base", {("alpha", "1", 0): "gold1", ("beta", "1", 0): "no"})
    cand = make_out(tmp_path / "cand", {("alpha", "1", 0): "no", ("beta", "1", 0): "no"})
    report = tmp_path / "report.json"
    argv = ["dab-score", str(cand), "--baseline", str(base), "--dab-root", str(dab_root)]
    assert evals_main([*argv, "--report", str(report), "--n-boot", "200"]) == 0
    assert json.loads(report.read_text())["comparison"] == {
        "diff": -50.0,
        "ci_low": None,
        "ci_high": None,
        "significant": False,
    }
    line = next(
        x for x in capsys.readouterr().out.splitlines() if x.startswith("candidate - baseline")
    )
    assert line == (
        "candidate - baseline: -50.00 points "
        "(every dataset has 1 query: too few to estimate an interval)"
    )
