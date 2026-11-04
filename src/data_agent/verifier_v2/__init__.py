"""Verifier v2 (ADR-0009, `.scratch/verifier-v2/spec.md`): the hard facts of verification are
computed by tools and the harness; the model only judges changes against the task.

Ticket 44 fixes the seams here as stubs that raise NotImplementedError; the acceptance tests in
`tests/acceptance/test_verifier_v2.py` define the behavior and the return structures. Tickets 45
(snapshot and tools) and 46 (hooks) fill them in. Proposed module split, re-exported from here:

- `snapshot.py`: `take_snapshot`
- `model_diff.py`: `model_diff`
- `contract.py`: `contract_check`
- `grain.py`: `grain_facts`
- `compare.py`: `compare_by_key`
- `report.py`: `parse_report`, `summarize_transcript`
- `dispatch.py`: `rewrite_dispatch`
- `stop.py`: `record_report`, `stop_decision`
- `hooks.py`: `run_hooks` (the thin SDK wrapper around the pure functions above)

Every function here is pure (or a thin wrapper): the MCP tools and the SDK hooks only adapt
inputs and outputs. All SQL reaches the database through the read-only gateway.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import HookEvent

from data_agent.gateway import Gateway
from data_agent.run_config import RunConfig
from data_agent.verifiers import Emit


def take_snapshot(workspace: Path, dest: Path) -> Path:
    """Copy the logic files of every dbt project under `workspace` into `dest` (outside the
    workspace), mirroring each project's path relative to the workspace. Returns `dest`."""
    raise NotImplementedError


def model_diff(workspace: Path, snapshot: Path, dialect: str = "duckdb") -> list[dict[str, Any]]:
    """New and changed models between `snapshot` and `workspace`, with their changes by category.
    `dialect` is the gateway's: the SQL is parsed in it (spec "工具": 解析方法)."""
    raise NotImplementedError


def contract_check(
    workspace: Path,
    snapshot: Path,
    gateway: Gateway,
    models: Sequence[str],
    task_columns: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Existence, column contract and changed-window ordering, per model."""
    raise NotImplementedError


def grain_facts(
    workspace: Path, snapshot: Path, gateway: Gateway, model: str, key_columns: Sequence[str]
) -> dict[str, Any]:
    """NULL and duplicate counts of the grain key, and any exemption the snapshot YML grants."""
    raise NotImplementedError


def compare_by_key(
    gateway: Gateway,
    model: str,
    key_columns: Sequence[str],
    reference_sql: str,
    changed_models: Sequence[str] = (),
) -> dict[str, Any]:
    """Full-key comparison of `model` against `reference_sql`, with the spec's equality rules."""
    raise NotImplementedError


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
