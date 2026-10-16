"""Read, Glob and Grep may only reach files inside the task workspace."""

from pathlib import Path
from typing import Any

import pytest

from data_agent import agent
from data_agent.agent import ModelSettings, Task
from data_agent.run_config import load_config
from data_agent.workspace_guard import workspace_guard


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "models").mkdir(parents=True)
    (ws / "models" / "orders.sql").write_text("select 1")
    (tmp_path / "secret.env").write_text("KEY=x")
    (ws / "escape").symlink_to(tmp_path)
    (ws / "secret_link").symlink_to(tmp_path / "secret.env")
    return ws


async def decide(workspace: Path, tool: str, tool_input: dict[str, Any]) -> str:
    guard = workspace_guard(workspace)
    hook_input: Any = {
        "hook_event_name": "PreToolUse",
        "session_id": "s",
        "transcript_path": "t",
        "cwd": str(workspace),
        "tool_name": tool,
        "tool_input": tool_input,
        "tool_use_id": "u",
    }
    output: Any = await guard(hook_input, "u", {"signal": None})
    return output.get("hookSpecificOutput", {}).get("permissionDecision", "none")


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "models/orders.sql"}),
        ("Read", {"file_path": "{ws}/models/orders.sql"}),
        ("Glob", {"pattern": "**/*.sql"}),
        ("Glob", {"pattern": "*.sql", "path": "{ws}/models"}),
        ("Glob", {"pattern": "{ws}/models/*.sql"}),
        ("Grep", {"pattern": "select"}),
        ("Grep", {"pattern": "select", "path": "models", "glob": "*.sql"}),
    ],
)
async def test_paths_inside_the_workspace_are_left_to_the_normal_rules(
    workspace: Path, tool: str, tool_input: dict[str, Any]
) -> None:
    filled = {k: v.format(ws=workspace) for k, v in tool_input.items()}
    assert await decide(workspace, tool, filled) == "none"


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "{home}/.ssh/id_ed25519"}),
        ("Read", {"file_path": "{ws}/../secret.env"}),
        ("Read", {"file_path": "../secret.env"}),
        ("Read", {"file_path": "secret_link"}),
        ("Read", {"file_path": "escape/secret.env"}),
        ("Glob", {"pattern": "*", "path": "{home}/.ssh"}),
        ("Glob", {"pattern": "*", "path": ".."}),
        ("Glob", {"pattern": "../*.env"}),
        ("Glob", {"pattern": "{home}/.ssh/*"}),
        ("Glob", {"pattern": "models/../../*.env"}),
        ("Glob", {"pattern": "*", "path": "escape"}),
        ("Grep", {"pattern": "KEY", "path": "{home}/.config"}),
        ("Grep", {"pattern": "KEY", "path": "../"}),
        ("Grep", {"pattern": "KEY", "path": "secret_link"}),
        ("Grep", {"pattern": "KEY", "glob": "../*.env"}),
        ("Grep", {"pattern": "KEY", "glob": "/etc/*"}),
    ],
)
async def test_paths_outside_the_workspace_are_denied(
    workspace: Path, tool: str, tool_input: dict[str, Any]
) -> None:
    filled = {k: v.format(ws=workspace, home=Path.home()) for k, v in tool_input.items()}
    assert await decide(workspace, tool, filled) == "deny"


def test_query_runs_install_the_guard_for_every_file_tool(tmp_path: Path) -> None:
    task = Task(text="q", type="query", workspace=tmp_path, duckdb=tmp_path / "x.duckdb")
    settings = ModelSettings.from_env({"DATA_AGENT_API_KEY": "k"})
    options = agent.build_options(task, load_config("full"), settings, [])
    matchers = (options.hooks or {}).get("PreToolUse", [])
    guarded = {name for m in matchers if m.matcher for name in m.matcher.split("|")}
    assert set(agent.QUERY_BUILTIN_TOOLS) <= guarded
