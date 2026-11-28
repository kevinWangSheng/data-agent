"""Evaluations read none of the user's config (.scratch/product-config/spec.md "Testing Decisions":
评测隔离; ADR-0011; decisions.md Q4, Q8).

Written by ticket 141. These tests pass now and must keep passing through tickets 143-148; no
ticket edits them. Today no code reads these files or the variable, so they pass trivially; they
guard against an evaluation entry picking up the user's config once tickets 143-148 teach the
product to read it.

The user has `~/.data-agent/config.toml` (every layer off, other limits and prompts, an MCP
server, Skill, plugin and instructions directories and files, `claude_code_config = true`) and a
Claude Code MCP config in the same home (`~/.claude.json`); a project-level
`.data-agent/config.toml` is in the run's workspace and in the working directory; and
`DATA_AGENT_CONFIG` names the user's file. Each evaluation entry, driven as
tests/acceptance/test_config_snapshot.py drives it, still gives the recorded snapshot: the entries
pass their own `--config`, which wins over `DATA_AGENT_CONFIG` (decisions.md Q8), and an explicit
config file means neither the user-level nor the project-level file is read (spec Q1).
"""

from pathlib import Path

import pytest
from product_config_support import (
    MCP_TOKEN,
    MCP_TOKEN_ENV,
    SNAPSHOT_CASES,
    Capture,
    claude_code_mcp,
    compare_or_record,
    drive_entry,
    fake_home,
    normalized_snapshot,
    project_config_text,
    stop_sessions,
    user_config_text,
    write_config,
)


@pytest.fixture
def capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Capture:
    home = fake_home(tmp_path)
    user_file = write_config(home, user_config_text())
    claude_code_mcp(home)
    capture = stop_sessions(monkeypatch, home)
    monkeypatch.setenv("DATA_AGENT_CONFIG", str(user_file))
    monkeypatch.setenv(MCP_TOKEN_ENV, MCP_TOKEN)
    return capture


@pytest.mark.parametrize(("entry", "layer"), SNAPSHOT_CASES)
def test_an_evaluation_entry_ignores_the_users_config(
    entry: str,
    layer: str,
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran = drive_entry(
        entry, layer, tmp_path / "entry", capsys, capture, monkeypatch, project_config_text()
    )
    got = normalized_snapshot(ran.session, ran.version, ran.roots)
    compare_or_record(f"{entry}-{layer}", got)
