"""The committed dev/holdout split (ADR-0004): fast-30 as dev, 30 other tasks as holdout."""

import json
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / "evals" / "manifest.json"


def test_committed_manifest_splits_30_dev_and_30_disjoint_holdout() -> None:
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["seed"] == 20261015
    dev, holdout = manifest["dev"], manifest["holdout"]
    assert len(dev) == len(set(dev)) == 30
    assert len(holdout) == len(set(holdout)) == 30
    assert not set(dev) & set(holdout)
