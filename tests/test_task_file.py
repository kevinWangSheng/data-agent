"""The task text saved outside the workspace for runs with the verifier layer (ticket 32): the main
agent and both verifiers are told where it is, and the guard lets them only Read it."""

import json
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import ResultMessage

from data_agent import agent
from data_agent.agent import Task
from data_agent.run_config import load_config

# Quotes, a trailing space and no final newline: the file must keep the text byte for byte.
TASK_TEXT = 'Build `fct_orders`:\n  - "net" = gross - refunds \nKeep cancelled orders. ünïcode'

# DBT_PROMPT before ticket 32, kept here so runs without the verifier layer can be compared to it.
DBT_PROMPT_BEFORE = """\
Task type: dbt
Database: {database}. dbt writes to it through the project's profile; you read it only through the \
`{server}` MCP tools.
Workspace (your working directory; the dbt project is this directory or one inside it): {workspace}
Bash runs only dbt commands{scripts}, from the working directory; for a project in a \
subdirectory, pass --project-dir and --profiles-dir. Relative paths in a profile resolve from the \
working directory.

<task>
{task}
</task>

End with a final message that lists the models you created or changed, how the last dbt run went, \
then the assumptions you made.
"""

QUERY_PROMPT_BEFORE = """\
Task type: query
Database: {database}, reachable only through the `{server}` MCP tools.
Workspace (your working directory): {workspace}

<task>
{task}
</task>

End with a final message that gives the answer first, then the assumptions you made.
"""

RESULT = ResultMessage(
    subtype="success",
    duration_ms=1,
    duration_api_ms=1,
    is_error=False,
    num_turns=1,
    session_id="s",
    usage={},
    total_cost_usd=0.0,
    result="done",
)


async def run_capturing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, task_type: str, config: str
) -> dict[str, Any]:
    """Run against a scripted SDK and keep what it was given, and the task file as seen mid-run."""
    seen: dict[str, Any] = {}

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        seen["prompt"], seen["options"] = prompt, options
        paths = [p for p in prompt.split() if p.rstrip(".").endswith("task.md")]
        seen["task_file"] = Path(paths[0].rstrip(".")) if paths else None
        if seen["task_file"] is not None:
            seen["content"] = seen["task_file"].read_bytes()
        seen["temp_files"] = sorted(p.name for p in temp.rglob("*"))
        yield RESULT

    # Temporary files land here, so a run that writes a task file shows it.
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setattr(agent, "query", fake_query)
    monkeypatch.setattr(shutil, "which", lambda *_, **__: "/usr/bin/dbt")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    db = workspace / "x.duckdb"
    duckdb.connect(str(db)).close()
    task = Task(text=TASK_TEXT, type=task_type, workspace=workspace, duckdb=db)  # pyright: ignore[reportArgumentType]
    outcome = await agent.run(
        task, load_config(config), tmp_path / "events", {"DATA_AGENT_API_KEY": "k"}
    )
    seen["events"] = [json.loads(line) for line in outcome.events_path.read_text().splitlines()]
    seen["workspace"] = workspace
    return seen


@pytest.mark.parametrize("config", ["full", "no-skill", "no-context"])
async def test_verifier_runs_save_the_task_text_outside_the_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: str
) -> None:
    seen = await run_capturing(monkeypatch, tmp_path, "dbt", config)
    task_file: Path = seen["task_file"]
    assert task_file is not None, seen["prompt"]
    assert task_file.is_absolute() and task_file.name == "task.md"
    assert seen["content"] == TASK_TEXT.encode()
    assert seen["workspace"].resolve() not in task_file.resolve().parents
    agents = seen["options"].agents
    assert set(agents) == {"verifier", "value-verifier"}
    for definition in agents.values():
        assert str(task_file) in definition.prompt
    # Nothing is left behind once the run ends.
    assert not task_file.parent.exists()


async def test_the_runs_guard_lets_the_task_file_be_read_but_not_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = await run_capturing(monkeypatch, tmp_path, "dbt", "full")
    task_file: Path = seen["task_file"]
    matchers = seen["options"].hooks["PreToolUse"]
    guard = next(m.hooks[0] for m in matchers if m.matcher and "Read" in m.matcher.split("|"))

    async def decision(tool: str, tool_input: dict[str, Any]) -> str:
        hook_input: Any = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
        }
        output: Any = await guard(hook_input, "u", {"signal": None})
        return output.get("hookSpecificOutput", {}).get("permissionDecision", "none")

    assert await decision("Read", {"file_path": str(task_file)}) == "none"
    assert await decision("Write", {"file_path": str(task_file), "content": "x"}) == "deny"


@pytest.mark.parametrize(
    ("task_type", "config"),
    [("dbt", "baseline"), ("dbt", "no-verifiers"), ("query", "full"), ("query", "baseline")],
)
async def test_runs_without_the_verifier_layer_keep_the_prompt_and_write_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, task_type: str, config: str
) -> None:
    seen = await run_capturing(monkeypatch, tmp_path, task_type, config)
    assert seen["temp_files"] == []
    before = DBT_PROMPT_BEFORE if task_type == "dbt" else QUERY_PROMPT_BEFORE
    run_config = load_config(config)
    expected = before.format(
        server=agent.MCP_SERVER,
        database="DuckDB",
        workspace=seen["workspace"].resolve(),
        task=TASK_TEXT,
        scripts=" and the dbt-workflow scripts" if agent.dbt_scripts(run_config) else "",
    )
    assert seen["prompt"] == expected
    assert "task.md" not in str(seen["options"].system_prompt)
