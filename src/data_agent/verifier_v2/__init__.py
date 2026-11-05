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
inputs and outputs. All SQL reaches the database through the read-only gateway. Ticket 46's
seams are still the stubs below, raising NotImplementedError.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import HookEvent

from data_agent.run_config import RunConfig
from data_agent.verifier_v2.compare import compare_by_key
from data_agent.verifier_v2.contract import contract_check
from data_agent.verifier_v2.grain import grain_facts
from data_agent.verifier_v2.model_diff import model_diff
from data_agent.verifier_v2.snapshot import take_snapshot
from data_agent.verifiers import Emit

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


def parse_report(report: str, hit_max_turns: bool = False) -> dict[str, Any]:
    """Per-model check statuses from a verifier report in the v2 format."""
    raise NotImplementedError


def summarize_transcript(path: Path) -> dict[str, int]:
    """Model calls (`turns`) and tool calls in one verifier call's transcript."""
    raise NotImplementedError


def rewrite_dispatch(
    tool_name: str,
    tool_input: Mapping[str, Any],
    *,
    task_file: Path,
    project_dir: Path,
    models: Sequence[str],
    dispatches_so_far: int,
) -> dict[str, Any]:
    """PreToolUse output that replaces a verifier dispatch's prompt; `{}` for any other call."""
    raise NotImplementedError


def record_report(state: Mapping[str, Any], report: str, workspace: Path) -> dict[str, Any]:
    """The Stop state after one verifier report: the models it names get their statuses and
    current file hashes; the rest keep theirs."""
    raise NotImplementedError


def stop_decision(state: Mapping[str, Any], workspace: Path, snapshot: Path) -> dict[str, Any]:
    """Whether the agent may stop, and per model which of the three rules blocks it."""
    raise NotImplementedError


def run_hooks(
    config: RunConfig,
    *,
    workspace: Path,
    snapshot: Path,
    task_file: Path,
    emit: Emit,
) -> dict[HookEvent, list[HookMatcher]]:
    """The v2 hooks for one run: PreToolUse (dispatch rewrite), SubagentStop (report and turn
    count into `verifier_end` and the Stop state), Stop (block, at most twice per run). Empty
    when `config.verifiers` is off."""
    raise NotImplementedError
