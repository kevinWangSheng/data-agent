"""Event fixtures shared by the evaluation lifecycle tests (eval-readiness-and-lifecycle ticket 01).

In stage 1 (ADR-0008 补充 2026-11-06), pos-operations' verifier dispatches listed [21, 6, 5],
[21, 17, 3, 1, 21] and [21, 21, 21] models: the later 21s re-sent every model of the first
dispatch, the efficiency line the stage-1 gate failed.
"""

from typing import Any

# The models of a dbt project as a verifier dispatch lists them (`verifier_dispatch`, ticket 59).
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
