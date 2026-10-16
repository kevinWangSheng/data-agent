"""Read, Glob, Grep, Write and Edit may only reach files inside the task workspace."""

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


async def decide(
    workspace: Path, tool: str, tool_input: dict[str, Any], protected: tuple[Path, ...] = ()
) -> str:
    guard = workspace_guard(workspace, protected)
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


@pytest.mark.parametrize(
    ("tool", "tool_input", "decision"),
    [
        ("Write", {"file_path": "models/marts/new.sql", "content": "select 1"}, "none"),
        (
            "Edit",
            {"file_path": "{ws}/models/orders.sql", "old_string": "1", "new_string": "2"},
            "none",
        ),
        ("Write", {"file_path": "../secret.env", "content": "x"}, "deny"),
        ("Write", {"file_path": "{home}/.bashrc", "content": "x"}, "deny"),
        ("Write", {"file_path": "escape/secret.env", "content": "x"}, "deny"),
        ("Edit", {"file_path": "secret_link", "old_string": "x", "new_string": "y"}, "deny"),
        ("Write", {"file_path": "shop.duckdb", "content": ""}, "deny"),
        ("Write", {"file_path": "{ws}/shop.duckdb.wal", "content": ""}, "deny"),
        ("Edit", {"file_path": "shop.duckdb", "old_string": "a", "new_string": "b"}, "deny"),
    ],
)
async def test_writes_stay_inside_the_workspace_and_off_the_database(
    workspace: Path, tool: str, tool_input: dict[str, Any], decision: str
) -> None:
    filled = {k: v.format(ws=workspace, home=Path.home()) for k, v in tool_input.items()}
    assert await decide(workspace, tool, filled, (workspace / "shop.duckdb",)) == decision


def test_dbt_runs_guard_file_writes_and_bash(tmp_path: Path) -> None:
    task = Task(text="t", type="dbt", workspace=tmp_path, duckdb=tmp_path / "x.duckdb")
    settings = ModelSettings.from_env({"DATA_AGENT_API_KEY": "k"})
    options = agent.build_options(task, load_config("full"), settings, [])
    matchers = (options.hooks or {}).get("PreToolUse", [])
    guarded = {name for m in matchers if m.matcher for name in m.matcher.split("|")}
    assert {"Read", "Glob", "Grep", "Write", "Edit", "Bash"} <= guarded
    assert {"Write", "Edit", "Bash"} <= set(options.tools or [])
    query = Task(text="q", type="query", workspace=tmp_path, duckdb=tmp_path / "x.duckdb")
    query_tools = agent.build_options(query, load_config("full"), settings, []).tools or []
    assert not {"Write", "Edit", "Bash"} & set(query_tools)
