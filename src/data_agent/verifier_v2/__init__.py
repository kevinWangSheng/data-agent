"""Verifier v2 (ADR-0009, `.scratch/verifier-v2/spec.md`): the hard facts of verification are
computed by tools and the harness; the model only judges changes against the task.

The acceptance tests in `tests/acceptance/test_verifier_v2.py` define the behavior and the return
structures. Modules, re-exported from here:

- `snapshot.py`: `take_snapshot` (ticket 45)
- `model_diff.py`: `model_diff` (ticket 45), on `sql.py` (sqlglot facts) and `project.py`
- `contract.py`: `contract_check` (ticket 45)
- `grain.py`: `grain_facts` (ticket 45)
- `compare.py`: `compare_by_key` (ticket 45)
- `render.py`: the text the MCP tools return (ticket 45)
- `report.py`: `parse_report`, `summarize_transcript` (ticket 46)
- `dispatch.py`: `rewrite_dispatch` (ticket 46)
- `stop.py`: `record_report`, `stop_decision` (ticket 46)
- `hooks.py`: `run_hooks` (ticket 46; the thin SDK wrapper around the pure functions above)

Every function here is pure (or a thin wrapper): the MCP tools and the SDK hooks only adapt
inputs and outputs. All SQL reaches the database through the read-only gateway.
"""

from data_agent.verifier_v2.compare import compare_by_key
from data_agent.verifier_v2.contract import contract_check
from data_agent.verifier_v2.dispatch import rewrite_dispatch
from data_agent.verifier_v2.grain import grain_facts
from data_agent.verifier_v2.hooks import run_hooks
from data_agent.verifier_v2.model_diff import model_diff
from data_agent.verifier_v2.report import parse_report, summarize_transcript
from data_agent.verifier_v2.snapshot import take_snapshot
from data_agent.verifier_v2.stop import record_report, stop_decision

__all__ = [
    "compare_by_key",
    "contract_check",
    "grain_facts",
    "model_diff",
    "parse_report",
    "record_report",
    "rewrite_dispatch",
    "run_hooks",
    "stop_decision",
    "summarize_transcript",
    "take_snapshot",
]
