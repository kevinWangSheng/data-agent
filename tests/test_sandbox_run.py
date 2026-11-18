"""The sandbox's place in a run (ticket 97): the `--sandbox` option reaches the MCP server with the
run's id, and the run process, not the server, removes the run's container. The SDK's `query()`
is scripted and docker is `FakeDocker`, so no model and no Docker."""

import os
import subprocess
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage
from fake_docker import FakeDocker, install

from data_agent import agent
from data_agent.agent import Task
from data_agent.run_config import load_config
from data_agent.sandbox import OWNER_LABEL, RUN_LABEL, Backend, DockerSandbox

ENV = {"DATA_AGENT_API_KEY": "sk-test-secret", "DATA_AGENT_BASE_URL": "https://llm.example/x"}


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    return install(monkeypatch)


def result() -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
        usage={"input_tokens": 3, "output_tokens": 7},
        total_cost_usd=0.01,
        result="42",
    )


def server_args(options: ClaudeAgentOptions) -> list[str]:
    assert isinstance(options.mcp_servers, dict)
    server: dict[str, Any] = dict(options.mcp_servers[agent.MCP_SERVER])
    return list(server["args"])


def flag(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


async def run_with(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session: Callable[[ClaudeAgentOptions], None],
    sandbox: Backend | None,
) -> None:
    """A run whose SDK session calls `session` with the run's options, then succeeds."""

    async def fake_query(*, options: ClaudeAgentOptions, **_: Any) -> AsyncIterator[Any]:
        session(options)
        yield result()

    monkeypatch.setattr(agent, "query", fake_query)
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = Task(text="q", type="query", workspace=workspace, duckdb=db)
    events_dir = tmp_path / "run-7"
    await agent.run(task, load_config("full"), events_dir, ENV, sandbox=sandbox)


def start_container_as_the_server(options: ClaudeAgentOptions) -> None:
    """What the MCP server does on the first sandbox_exec; then it is killed and removes nothing."""
    args = server_args(options)
    workspace = Path(flag(args, "--workspace"))
    DockerSandbox(workspace, Path("/skills-dir"), flag(args, "--run-id")).execute("true", 30)


async def test_the_run_removes_its_container_after_its_server_is_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docker: FakeDocker
) -> None:
    await run_with(monkeypatch, tmp_path, start_container_as_the_server, "docker")
    assert docker.verb("run"), "the session started a container"
    assert docker.containers == {}


async def test_the_server_gets_the_backend_and_the_events_directorys_name_as_run_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docker: FakeDocker
) -> None:
    seen: list[list[str]] = []
    await run_with(monkeypatch, tmp_path, lambda o: seen.append(server_args(o)), "docker")
    [args] = seen
    assert (flag(args, "--sandbox"), flag(args, "--run-id")) == ("docker", "run-7")


def dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


async def test_the_run_first_removes_containers_whose_server_is_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docker: FakeDocker
) -> None:
    docker.containers = {
        "left-by-a-killed-run": {RUN_LABEL: "old", OWNER_LABEL: str(dead_pid())},
        "of-a-live-run": {RUN_LABEL: "other", OWNER_LABEL: str(os.getpid())},
    }
    at_start: list[set[str]] = []
    await run_with(
        monkeypatch, tmp_path, lambda _: at_start.append(set(docker.containers)), "docker"
    )
    assert at_start == [{"of-a-live-run"}]
    assert set(docker.containers) == {"of-a-live-run"}


@pytest.mark.parametrize("sandbox", [None, "local"])
async def test_no_docker_call_without_the_docker_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docker: FakeDocker, sandbox: Backend | None
) -> None:
    seen: list[list[str]] = []
    await run_with(monkeypatch, tmp_path, lambda o: seen.append(server_args(o)), sandbox)
    assert docker.calls == []
    [args] = seen
    if sandbox is None:
        assert "--sandbox" not in args and "--run-id" not in args
    else:
        assert flag(args, "--sandbox") == "local"
