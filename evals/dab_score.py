"""`dab-score`: grade `evals dab` outputs with DAB's own validators and report DAB's Pass@1.

DAB has no license, so its grading is imported from the clone given by `--dab-root`, as the
runner imports its tools: `common_scaffold/validate/validate.py`'s `validate(query_dir, answer)`
fails an empty answer and otherwise runs the query's own `validate.py`.

DAB's Pass@1 is the mean over datasets of each dataset's mean per-query pass rate; a run with no
answer (one that broke on infrastructure) counts as a fail and is listed, or stops the scoring
under `--strict`. Only the output's queries count unless `--all-queries` (DAB's leaderboard rule:
a query never run scores 0); the coverage of the clone's queries is always reported. With
`--baseline`, a paired bootstrap of the difference resamples queries within
each dataset. Run it as

    uv run --with-requirements evals/dab-requirements.txt python -m evals dab-score runs/dab \\
        --dab-root ../DataAgentBench [--baseline runs/dab-baseline]

Exit 0 on a score; 2 on a usage error, a validator of another shape, unpaired outputs, or a
missing run under `--strict`.
"""

import argparse
import json
import random
import re
import statistics
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.compare import percentile_interval
from evals.dab import _import_from

Grader = Callable[[str, str, str], bool]
RunKey = tuple[str, str, int]  # dataset, query id, run
QueryKey = tuple[str, str]  # dataset, query id

_QUERY_DIR = re.compile(r"query(\d+)")
_RUN_DIR = re.compile(r"run(\d+)")


def grader(dab_root: Path) -> Grader:
    """grade(dataset, query, answer): whether DAB's validator passes the answer."""
    root = dab_root.resolve()
    validate = _import_from(root, "common_scaffold.validate.validate").validate

    def grade(dataset: str, query: str, answer: str) -> bool:
        # A validator that fails or answers in another shape stops the scoring (ticket 35: a
        # human decides how to handle it), instead of counting as a failed run.
        where = f"{dataset} query{query}"
        try:
            result = validate(root / f"query_{dataset}" / f"query{query}", answer)
        except Exception as exc:
            raise ValueError(f"DAB's validator for {where} failed: {exc!r}") from exc
        is_valid = result.get("is_valid") if isinstance(result, dict) else None
        # numpy's bool_ is what a validator comparing numpy values returns.
        if not isinstance(is_valid, bool) and type(is_valid).__name__ != "bool_":
            raise ValueError(f"DAB's validator for {where} returned is_valid={is_valid!r}")
        return bool(is_valid)

    return grade


@dataclass(frozen=True)
class Score:
    """One output's graded runs, and the expected runs it has no answer for (counted as fails)."""

    passed: dict[RunKey, bool]
    missing: tuple[RunKey, ...]
    clone_queries: int  # queries in the DAB clone, for coverage

    def coverage(self) -> dict[str, int]:
        return {"queries": len(self.query_rates()), "clone_queries": self.clone_queries}

    def query_rates(self) -> dict[QueryKey, float]:
        """Per query: passing runs over expected runs (a missing run counts as a fail)."""
        runs: dict[QueryKey, list[bool]] = {}
        for (dataset, query, _), ok in self.passed.items():
            runs.setdefault((dataset, query), []).append(ok)
        for dataset, query, _ in self.missing:
            runs.setdefault((dataset, query), []).append(False)
        return {key: sum(oks) / len(oks) for key, oks in sorted(runs.items(), key=_query_order)}

    def dataset_rates(self) -> dict[str, float]:
        return dataset_rates(self.query_rates())

    def pass_at_1(self) -> float:
        return pass_at_1(self.query_rates())


def _query_order(item: tuple[QueryKey, object]) -> tuple[str, int]:
    (dataset, query), _ = item
    return dataset, int(query)


def dataset_rates(query_rates: dict[QueryKey, float]) -> dict[str, float]:
    """Each dataset's mean per-query pass rate."""
    by_dataset: dict[str, list[float]] = {}
    for (dataset, _), rate in query_rates.items():
        by_dataset.setdefault(dataset, []).append(rate)
    return {dataset: statistics.fmean(rates) for dataset, rates in sorted(by_dataset.items())}


def pass_at_1(query_rates: dict[QueryKey, float]) -> float:
    """DAB's Pass@1: the mean over datasets of each dataset's mean per-query pass rate."""
    return statistics.fmean(dataset_rates(query_rates).values())


def submitted(out: Path) -> dict[RunKey, str]:
    """The answers in `OUT/submission.json`."""
    entries = json.loads((out / "submission.json").read_text())
    return {(e["dataset"], e["query"], int(e["run"])): e["answer"] for e in entries}


def run_dirs(out: Path) -> set[RunKey]:
    """Every `OUT/<dataset>/query<id>/run<k>/` directory: runs that started, answered or not."""
    found = set()
    for path in out.glob("*/query*/run*"):
        query, run = _QUERY_DIR.fullmatch(path.parent.name), _RUN_DIR.fullmatch(path.name)
        if path.is_dir() and query and run:
            found.add((path.parent.parent.name, query.group(1), int(run.group(1))))
    return found


def clone_queries(dab_root: Path) -> set[QueryKey]:
    """Every `query_<dataset>/query<id>/` in the DAB clone."""
    found = set()
    for path in dab_root.glob("query_*/query*"):
        query = _QUERY_DIR.fullmatch(path.name)
        if path.is_dir() and query:
            found.add((path.parent.name.removeprefix("query_"), query.group(1)))
    return found


def score(dab_root: Path, out: Path, runs: int | None = None, all_queries: bool = False) -> Score:
    """Grade every answer in the output. Expected runs are the run directories and submission
    entries found, plus runs 0..`runs`-1 of every query found when `runs` is given. With
    `all_queries`, every query of the clone is expected too (DAB's leaderboard rule: a missing
    query scores 0), with `runs` runs or as many as the output's most-run query."""
    answers = submitted(out)
    expected = run_dirs(out) | set(answers)
    if not expected:
        raise ValueError(f"no runs in {out}")
    in_clone = clone_queries(dab_root)
    if runs is None and all_queries:
        counts: dict[QueryKey, int] = {}
        for d, q, _ in expected:
            counts[(d, q)] = counts.get((d, q), 0) + 1
        runs = max(counts.values())
    if runs is not None:
        queries = {(d, q) for d, q, _ in expected} | (in_clone if all_queries else set())
        expected |= {(d, q, k) for d, q in queries for k in range(runs)}
    grade = grader(dab_root)
    passed = {key: grade(key[0], key[1], answer) for key, answer in sorted(answers.items())}
    missing = tuple(sorted((key for key in expected if key not in answers), key=_run_order))
    return Score(passed=passed, missing=missing, clone_queries=len(in_clone))


def _run_order(key: RunKey) -> tuple[str, int, int]:
    return key[0], int(key[1]), key[2]


def summary(result: Score) -> dict[str, Any]:
    """The JSON form of one output's score: Pass@1, per dataset and per query pass counts, and
    the missing runs."""
    counts: dict[QueryKey, list[int]] = {}  # [passed, runs]
    for (dataset, query, _), ok in result.passed.items():
        count = counts.setdefault((dataset, query), [0, 0])
        count[0] += ok
        count[1] += 1
    for dataset, query, _ in result.missing:
        counts.setdefault((dataset, query), [0, 0])[1] += 1
    datasets: dict[str, Any] = {}
    for dataset, rate in result.dataset_rates().items():
        queries = {
            query: {"passed": passed, "runs": runs}
            for (d, query), (passed, runs) in sorted(counts.items(), key=_query_order)
            if d == dataset
        }
        datasets[dataset] = {"pass_at_1": rate, "queries": queries}
    return {
        "pass_at_1": result.pass_at_1(),
        "coverage": result.coverage(),
        "datasets": datasets,
        "missing": [{"dataset": d, "query": q, "run": k} for d, q, k in result.missing],
    }


def render(name: str, data: dict[str, Any]) -> str:
    covered = data["coverage"]
    lines = [
        f"{name}: Pass@1 {data['pass_at_1']:.4f} "
        f"(over {covered['queries']} of the clone's {covered['clone_queries']} queries)"
    ]
    for dataset, entry in data["datasets"].items():
        lines.append(f"  {dataset}: {entry['pass_at_1']:.4f}")
        for query, count in entry["queries"].items():
            lines.append(f"    query{query}: {count['passed']}/{count['runs']}")
    return "\n".join(lines)


def paired_bootstrap(
    baseline: Score, candidate: Score, seed: int, n_boot: int
) -> dict[str, float | bool]:
    """Candidate minus baseline Pass@1, in percentage points, with a 95% percentile interval.

    As `evals.compare`: queries are paired, and the difference is significant only when the
    interval excludes 0. Each resample draws queries with replacement within each dataset, so
    every dataset is in every resample and the statistic stays DAB's Pass@1 (mean over datasets
    of the per-query mean).
    """
    base, cand = baseline.query_rates(), candidate.query_rates()
    if set(base) != set(cand):
        # Count only, as `evals.compare`.
        unpaired = len(set(base) ^ set(cand))
        raise ValueError(f"outputs must cover the same queries; {unpaired} queries are unpaired")
    diffs: dict[str, list[float]] = {}
    for key in base:
        diffs.setdefault(key[0], []).append(100.0 * (cand[key] - base[key]))
    groups = [diffs[dataset] for dataset in sorted(diffs)]
    rng = random.Random(seed)
    boots = [
        statistics.fmean(statistics.fmean(rng.choices(g, k=len(g))) for g in groups)
        for _ in range(n_boot)
    ]
    return percentile_interval(statistics.fmean(statistics.fmean(g) for g in groups), boots)


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser(
        "dab-score", help="grade `evals dab` outputs with DAB's validators; DAB's Pass@1"
    )
    parser.add_argument("out", type=Path, help="an `evals dab` output directory (the candidate)")
    parser.add_argument("--baseline", type=Path, help="an output to compare against (paired)")
    parser.add_argument("--dab-root", type=Path, required=True, help="local DataAgentBench clone")
    parser.add_argument("--runs", type=int, help="expected runs per query (default: as found)")
    parser.add_argument(
        "--all-queries",
        action="store_true",
        help="expect every query of the clone; never-run ones score 0 (DAB's leaderboard rule)",
    )
    parser.add_argument("--strict", action="store_true", help="exit 2 when a run is missing")
    parser.add_argument("--report", type=Path, help="JSON report (default: OUT/dab_score.json)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--n-boot", type=int, default=10000)
    parser.set_defaults(run=run_cli)


def _error(message: str) -> int:
    print(f"dab-score: {message}", file=sys.stderr)
    return 2


def report_missing(name: str, result: Score) -> None:
    if not result.missing:
        return
    runs = ", ".join(f"{d} query{q} run{k}" for d, q, k in result.missing)
    print(
        f"dab-score: {name}: {len(result.missing)} missing run(s), counted as fails: {runs}",
        file=sys.stderr,
    )


def run_cli(args: argparse.Namespace) -> int:
    if not (args.dab_root / "common_scaffold").is_dir():
        return _error(f"not a DataAgentBench clone: {args.dab_root}")
    if args.runs is not None and args.runs < 1:
        return _error(f"--runs must be at least 1, got {args.runs}")
    if args.n_boot < 1:
        return _error(f"--n-boot must be at least 1, got {args.n_boot}")
    outputs = {"candidate": args.out}
    if args.baseline is not None:
        outputs = {"baseline": args.baseline, **outputs}
    try:
        scores = {
            name: score(args.dab_root, out, args.runs, args.all_queries)
            for name, out in outputs.items()
        }
        comparison = None
        if "baseline" in scores:
            comparison = paired_bootstrap(
                scores["baseline"], scores["candidate"], args.seed, args.n_boot
            )
    except (ValueError, OSError) as exc:
        return _error(str(exc))
    for name, result in scores.items():
        report_missing(name, result)
        covered = result.coverage()
        if covered["queries"] < covered["clone_queries"]:
            print(
                f"dab-score: {name} covers {covered['queries']} of the clone's "
                f"{covered['clone_queries']} queries; its Pass@1 is over those only, not "
                "comparable with DAB's leaderboard (--all-queries scores the others as 0)",
                file=sys.stderr,
            )
    if args.strict and any(result.missing for result in scores.values()):
        return _error("--strict: missing runs; redo them, then score again")
    data: dict[str, Any] = {
        name: {"out": str(outputs[name]), **summary(result)} for name, result in scores.items()
    }
    for name in scores:
        print(render(name, data[name]))
    if comparison is not None:
        data["comparison"] = comparison
        print(
            f"candidate - baseline: {comparison['diff']:+.2f} points "
            f"(95% CI {comparison['ci_low']:+.2f} to {comparison['ci_high']:+.2f}; "
            f"{'significant' if comparison['significant'] else 'not significant'})"
        )
    path = args.report or args.out / "dab_score.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"report: {path}")
    return 0
