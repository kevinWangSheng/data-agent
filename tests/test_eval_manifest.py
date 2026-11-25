"""The committed dev/holdout split (ADR-0004): fast-30 as dev, 30 other tasks as holdout, for
data-eng-bench; LiveSQLBench's 60/120 (`lsb-` ids, .scratch/livesqlbench/spec.md U2) beside them
in the same top-level lists, each benchmark checked on its own (`benchmark_tasks`)."""

import json
from pathlib import Path

from evals import livesqlbench
from evals.report import benchmark_tasks

MANIFEST = Path(__file__).resolve().parents[1] / "evals" / "manifest.json"


def test_committed_manifest_splits_30_dev_and_30_disjoint_holdout() -> None:
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["seed"] == 20261015
    dev = benchmark_tasks(manifest, "dev", "data-eng-bench")
    holdout = benchmark_tasks(manifest, "holdout", "data-eng-bench")
    assert len(dev) == len(set(dev)) == 30
    assert len(holdout) == len(set(holdout)) == 30
    assert not set(dev) & set(holdout)


def test_committed_manifest_splits_livesqlbench_60_dev_and_120_disjoint_holdout() -> None:
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["livesqlbench_seed"] == 20261124
    assert manifest["livesqlbench_source"] == "birdsql/livesqlbench-base-lite@507d7d9"
    dev = benchmark_tasks(manifest, "dev", livesqlbench.BENCHMARK)
    holdout = benchmark_tasks(manifest, "holdout", livesqlbench.BENCHMARK)
    assert len(dev) == len(set(dev)) == livesqlbench.DEV_SIZE
    assert len(holdout) == len(set(holdout)) == livesqlbench.HOLDOUT_SIZE
    assert not set(dev) & set(holdout)
    assert set(json.loads(livesqlbench.DATABASES.read_text())) == set(dev) | set(holdout)


def test_the_two_benchmarks_together_are_the_whole_lists() -> None:
    manifest = json.loads(MANIFEST.read_text())
    for split in ("dev", "holdout"):
        both = [
            t
            for b in ("data-eng-bench", livesqlbench.BENCHMARK)
            for t in benchmark_tasks(manifest, split, b)
        ]
        assert sorted(both) == sorted(manifest[split])
