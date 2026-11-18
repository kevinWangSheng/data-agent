"""`--sandbox` on `data-agent run` and `data-agent mcp-server` (ticket 97)."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from data_agent import cli
from data_agent.agent import Outcome

DATA_AGENT = Path(sys.executable).with_name("data-agent")


def make_db(path: Path) -> Path:
    duckdb.connect(str(path)).close()
    return path


def help_text(command: str) -> str:
    proc = subprocess.run(
        [str(DATA_AGENT), command, "--help"], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return " ".join(proc.stdout.split())


@pytest.mark.parametrize("command", ["run", "mcp-server"])
def test_help_says_local_is_only_for_a_run_inside_an_isolated_container(command: str) -> None:
    text = help_text(command)
    assert "--sandbox {docker,local}" in text
    assert "local only inside an isolated container" in text


@pytest.mark.parametrize("backend", ["docker", "local"])
def test_run_passes_the_backend_to_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    seen: list[Any] = []

    async def fake_run(*_: Any, **options: Any) -> Outcome:
        seen.append(options.get("sandbox"))
        return Outcome("", True, False, [], tmp_path / "events.jsonl")

    monkeypatch.setattr(cli, "run", fake_run)
    args = [
        *("run", "--task", "q", "--type", "query", "--workspace", str(tmp_path)),
        *("--duckdb", str(make_db(tmp_path / "x.duckdb")), "--config", "full"),
        *("--events-dir", str(tmp_path / "events")),
    ]
    with pytest.raises(SystemExit):
        cli.main([*args, "--sandbox", backend])
    with pytest.raises(SystemExit):
        cli.main(args)
    assert seen == [backend, None]


def serve(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    args = ["mcp-server", "--config", "full", "--duckdb", str(make_db(tmp_path / "x.duckdb"))]
    return subprocess.run(
        [str(DATA_AGENT), *args, *extra], capture_output=True, text=True, timeout=30
    )


def test_server_with_docker_needs_the_run_id(tmp_path: Path) -> None:
    proc = serve(tmp_path, "--workspace", str(tmp_path), "--sandbox", "docker")
    assert proc.returncode == 2 and "--run-id" in proc.stderr


def test_server_with_a_sandbox_needs_the_workspace(tmp_path: Path) -> None:
    proc = serve(tmp_path, "--sandbox", "local")
    assert proc.returncode == 2 and "--workspace" in proc.stderr


async def test_server_with_the_local_backend_serves_sandbox_exec(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    params = StdioServerParameters(
        command=str(DATA_AGENT),
        args=[
            *("mcp-server", "--config", "full", "--duckdb", str(make_db(tmp_path / "x.duckdb"))),
            *("--workspace", str(workspace), "--sandbox", "local"),
        ],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        names = {tool.name for tool in (await session.list_tools()).tools}
        result = await session.call_tool("sandbox_exec", {"command": "pwd"})
    assert "sandbox_exec" in names
    [content] = result.content
    assert isinstance(content, TextContent)
    assert content.text == f"exit_code: 0\nstdout:\n{workspace.resolve()}\n"
