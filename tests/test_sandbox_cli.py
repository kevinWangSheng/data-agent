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
from data_agent import sandbox as sandbox_module
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
    # Ticket 100: without the option, the docker backend.
    assert seen == [backend, "docker"]


def test_run_without_docker_fails_with_exit_code_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ticket 100: as a dbt run without dbt on PATH, before any session starts."""
    monkeypatch.setattr(sandbox_module, "docker_on_path", lambda: False)
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-test")
    args = [
        *("run", "--task", "q", "--type", "query", "--workspace", str(tmp_path)),
        *("--duckdb", str(make_db(tmp_path / "x.duckdb")), "--config", "full"),
        *("--events-dir", str(tmp_path / "events")),
    ]
    with pytest.raises(SystemExit) as exited:
        cli.main(args)
    assert exited.value.code == 2
    assert "docker is not on PATH" in capsys.readouterr().err
    assert not (tmp_path / "events").exists()


def fake_docker_cli(directory: Path) -> Path:
    """A `docker` on PATH that records its arguments and fails, as one without a daemon does."""
    directory.mkdir()
    calls = directory / "calls.txt"
    script = directory / "docker"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> {calls}\nexit 1\n')
    script.chmod(0o755)
    return calls


@pytest.mark.parametrize(("flags", "online"), [((), False), (("--network",), True)])
async def test_the_server_starts_its_container_offline_unless_given_network(
    tmp_path: Path, flags: tuple[str, ...], online: bool
) -> None:
    """Ticket 100: `--network` reaches the docker backend; without it `--network none`."""
    calls = fake_docker_cli(tmp_path / "bin")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    params = StdioServerParameters(
        command=str(DATA_AGENT),
        args=[
            *("mcp-server", "--config", "full", "--duckdb", str(make_db(tmp_path / "x.duckdb"))),
            *("--workspace", str(workspace), "--sandbox", "docker", "--run-id", "r-1", *flags),
        ],
        env={**os.environ, "PATH": f"{calls.parent}{os.pathsep}{os.environ['PATH']}"},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("sandbox_exec", {"command": "true"})
    assert result.is_error
    run = calls.read_text().splitlines()
    assert run[0] == "run"
    assert ("--network" in run) is not online
    if not online:
        assert run[run.index("--network") + 1] == "none"


def test_server_network_goes_with_a_sandbox(tmp_path: Path) -> None:
    proc = serve(tmp_path, "--workspace", str(tmp_path), "--network")
    assert proc.returncode == 2 and "--network goes with --sandbox" in proc.stderr


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


@pytest.mark.parametrize("how", ["file", "link"])
def test_a_docker_run_refuses_a_duckdb_file_inside_its_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], how: str
) -> None:
    """Review round 1 (Codex P1): the container mounts the workspace writable, so a database
    file in it would be the agent's to change (ADR-0010: the sandbox sees no database file). A
    link in the workspace to the file is refused too: Write and Edit follow it."""
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-test")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    if how == "file":
        db = make_db(workspace / "shop.duckdb")
    else:
        db = workspace / "shop.duckdb"
        db.symlink_to(make_db(tmp_path / "shop.duckdb"))
    args = [
        *("run", "--task", "q", "--type", "query", "--workspace", str(workspace)),
        *("--duckdb", str(db), "--config", "full", "--events-dir", str(tmp_path / "events")),
    ]
    with pytest.raises(SystemExit) as exited:
        cli.main(args)
    assert exited.value.code == 2
    assert "inside the workspace" in capsys.readouterr().err
    assert not (tmp_path / "events").exists()


@pytest.mark.parametrize("name", ["analytics.duckdb", "analytics.duckdb.wal"])
def test_a_docker_run_refuses_other_duckdb_files_in_its_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-test")
    workspace = tmp_path / "ws"
    nested = workspace / "data"
    nested.mkdir(parents=True)
    (nested / name).touch()
    db = make_db(tmp_path / "source.duckdb")
    args = [
        *("run", "--task", "q", "--type", "query", "--workspace", str(workspace)),
        *("--duckdb", str(db), "--config", "full", "--events-dir", str(tmp_path / "events")),
    ]

    def unexpected_run(*_: Any, **__: Any) -> Any:
        raise AssertionError("database check must run before the SDK session")

    monkeypatch.setattr("data_agent.agent.query", unexpected_run)
    with pytest.raises(SystemExit) as exited:
        cli.main(args)
    assert exited.value.code == 2
    assert f"data/{name}" in capsys.readouterr().err
    assert not (tmp_path / "events").exists()


@pytest.mark.parametrize("name", [".env", "config/.pgpass", "keys/deploy.pem", "linked"])
def test_a_docker_run_refuses_a_credential_file_inside_its_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
) -> None:
    """Ticket 100 return round 1 (Codex P1): the container mounts the whole workspace, and the
    workspace guard stops only the file tools, so a shell in sandbox_exec could read a project's
    `.env` (ADR-0010: the sandbox holds no credentials). A link to one counts too."""
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-test")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    if name == "linked":
        secret = tmp_path / ".env"
        secret.write_text("PASSWORD=x\n")
        shown = "settings"
        (workspace / shown).symlink_to(secret)
    else:
        shown = name
        (workspace / name).parent.mkdir(parents=True, exist_ok=True)
        (workspace / name).write_text("PASSWORD=x\n")
    args = [
        *("run", "--task", "q", "--type", "query", "--workspace", str(workspace)),
        *("--duckdb", str(make_db(tmp_path / "shop.duckdb")), "--config", "full"),
        *("--events-dir", str(tmp_path / "events")),
    ]
    with pytest.raises(SystemExit) as exited:
        cli.main(args)
    assert exited.value.code == 2
    err = capsys.readouterr().err
    assert "credential" in err and shown in err
    assert not (tmp_path / "events").exists()


def test_a_docker_run_refuses_a_literal_secret_in_a_compact_profile_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-test")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    placeholder = "fixture-only"
    (workspace / "profiles.yml").write_text(
        'shop:\n  outputs:\n    dev: {"type":"postgres","password":"' + placeholder + '"}\n'
    )
    args = [
        *("run", "--task", "q", "--type", "query", "--workspace", str(workspace)),
        *("--duckdb", str(make_db(tmp_path / "shop.duckdb")), "--config", "full"),
        *("--events-dir", str(tmp_path / "events")),
    ]

    def unexpected_run(*_: Any, **__: Any) -> Any:
        raise AssertionError("credential check must run before the SDK session")

    monkeypatch.setattr("data_agent.agent.query", unexpected_run)
    with pytest.raises(SystemExit) as exited:
        cli.main(args)
    assert exited.value.code == 2
    err = capsys.readouterr().err
    assert "credential" in err and "profiles.yml" in err
    assert placeholder not in err
    assert not (tmp_path / "events").exists()
