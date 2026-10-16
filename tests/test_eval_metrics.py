"""The scoring functions copied from data-eng-bench carry full type annotations."""

import importlib.util
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import get_type_hints

import pytest

# `evals/` sits beside the package rather than in it, so load the module by path.
_spec = importlib.util.spec_from_file_location(
    "evals_metrics", Path(__file__).resolve().parents[1] / "evals" / "metrics.py"
)
assert _spec and _spec.loader
metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(metrics)


SCORING = [metrics.is_success, metrics.compute_metrics, metrics.compute_pass_at_k]


@pytest.mark.parametrize("fn", SCORING, ids=lambda fn: fn.__name__)
def test_scoring_functions_are_fully_annotated(fn: Callable[..., object]) -> None:
    hints = get_type_hints(fn)
    assert set(inspect.signature(fn).parameters) | {"return"} <= set(hints)
    # Bare containers leave element types to inference.
    nested = [arg for hint in hints.values() for arg in getattr(hint, "__args__", ())]
    for hint in [*hints.values(), *nested]:
        assert hint not in (list, dict, tuple), f"bare container in {fn.__name__}"
