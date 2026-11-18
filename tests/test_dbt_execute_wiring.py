"""Where dbt_execute is served (ticket 99): only on a dbt run with a sandbox, with the run's
`--dbt-target`; without `--sandbox` the tools and the server's arguments are what they were. The
SDK's `query()` is scripted, so no model runs."""

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

import duckdb
import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from data_agent import agent, cli
from data_agent.agent import ConfigError, Outcome, Task
from data_agent.dbt_execute import DbtExecutor, Target
from data_agent.gateway import DuckDBGateway
from data_agent.mcp_server import build_server
from data_agent.run_config import CONFIG_NAMES, load_config
from data_agent.sandbox import Backend, LocalSandbox

DATA_AGENT = Path(sys.executable).with_name("data-agent")
DBT_SHOP = Path(__file__).parent / "fixtures" / "dbt_shop"
ENV = {"DATA_AGENT_API_KEY": "sk-test-secret", "DATA_AGENT_BASE_URL": "https://llm.example/x"}
SKILLS = Path("/opt/skills")


def make_db(path: Path) -> Path:
    duckdb.connect(str(path)).close()
    return path


async def listing(server: Any) -> dict[str, dict[str, Any]]:
    return {tool.name: tool.model_dump() for tool in await server.list_tools()}


@pytest.mark.parametrize("config", CONFIG_NAMES)
async def test_dbt_execute_adds_one_tool_and_changes_no_other(config: str, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = make_db(tmp_path / "x.duckdb")
    sandbox = LocalSandbox(workspace, SKILLS)
    gateway = DuckDBGateway(db)
    before = await listing(build_server(gateway, load_config(config), workspace, sandbox=sandbox))
    executor = DbtExecutor(workspace, "scratch", "r1", tmp_path / "profiles", duckdb=db)
    after = await listing(
        build_server(gateway, load_config(config), workspace, sandbox=sandbox, dbt=executor)
    )
    assert "dbt_execute" not in before
    assert set(after) - set(before) == {"dbt_execute"}
    assert {name: tool for name, tool in after.items() if name != "dbt_execute"} == before
    schema = after["dbt_execute"]["input_schema"]["properties"]
    assert set(schema) == {"command", "select", "exclude", "full_refresh", "threads"}
    assert "da_run_r1" in after["dbt_execute"]["description"]


# --- The run's options -------------------------------------------------------------------------


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
        result="done",
    )


def server_args(options: ClaudeAgentOptions) -> list[str]:
    assert isinstance(options.mcp_servers, dict)
    server: dict[str, Any] = dict(options.mcp_servers[agent.MCP_SERVER])
    return list(server["args"])


async def run_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    task_type: Literal["dbt", "query"],
    sandbox: Backend | None,
    dbt_target: Target = "task",
    events_dir: Path | None = None,
) -> ClaudeAgentOptions:
    """The options a run hands the SDK."""
    seen: list[ClaudeAgentOptions] = []

    async def fake_query(*, options: ClaudeAgentOptions, **_: Any) -> AsyncIterator[Any]:
        seen.append(options)
        yield result()

    monkeypatch.setattr(agent, "query", fake_query)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    task = Task(
        text="t", type=task_type, workspace=workspace, duckdb=make_db(tmp_path / "x.duckdb")
    )
    events = events_dir or tmp_path / "run-7"
    await agent.run(
        task, load_config("no-verifiers"), events, ENV, sandbox=sandbox, dbt_target=dbt_target
    )
    [options] = seen
    return options


def comparable(args: list[str]) -> list[str]:
    """The arguments with the dictionary's temporary path masked, so two runs' arguments
    compare."""
    return ["<dictionary>" if arg.endswith("dictionary.json") else arg for arg in args]


@pytest.mark.parametrize("task_type", ["dbt", "query"])
@pytest.mark.parametrize("target", ["task", "scratch"])
async def test_without_a_sandbox_the_dbt_target_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    task_type: Literal["dbt", "query"],
    target: Target,
) -> None:
    default = await run_options(monkeypatch, tmp_path, task_type, None)
    given = await run_options(monkeypatch, tmp_path, task_type, None, target)
    assert comparable(server_args(given)) == comparable(server_args(default))
    assert not any(arg.startswith("--dbt") for arg in server_args(given))
    assert given.tools == default.tools
    assert given.disallowed_tools == default.disallowed_tools


async def test_a_dbt_run_with_a_sandbox_writes_to_the_task_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    args = server_args(await run_options(monkeypatch, tmp_path, "dbt", "local"))
    assert args[args.index("--dbt-target") :][:2] == ["--dbt-target", "task"]
    assert "--dbt-profiles-dir" not in args


async def test_a_scratch_dbt_run_keeps_its_profile_in_the_events_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    args = server_args(await run_options(monkeypatch, tmp_path, "dbt", "local", "scratch"))
    assert args[args.index("--dbt-target") + 1] == "scratch"
    profiles = Path(args[args.index("--dbt-profiles-dir") + 1])
    assert profiles == (tmp_path / "run-7").resolve() / "profiles"
    assert not profiles.is_relative_to((tmp_path / "workspace").resolve())
    assert "--run-id" in args


async def test_a_query_run_with_a_sandbox_gets_no_dbt_execute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    args = server_args(await run_options(monkeypatch, tmp_path, "query", "local", "scratch"))
    assert not any(arg.startswith("--dbt") for arg in args)


async def test_a_scratch_profile_inside_the_workspace_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError, match="outside the workspace"):
        await run_options(
            monkeypatch, tmp_path, "dbt", "local", "scratch", tmp_path / "workspace" / "events"
        )


# --- The command line ----------------------------------------------------------------------------


def test_run_passes_the_dbt_target_and_defaults_to_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []

    async def fake_run(*_: Any, **options: Any) -> Outcome:
        seen.append(options.get("dbt_target"))
        return Outcome("", True, False, [], tmp_path / "events.jsonl")

    monkeypatch.setattr(cli, "run", fake_run)
    args = [
        *("run", "--task", "q", "--type", "dbt", "--workspace", str(tmp_path)),
        *("--duckdb", str(make_db(tmp_path / "x.duckdb")), "--config", "full"),
        *("--events-dir", str(tmp_path / "events")),
    ]
    with pytest.raises(SystemExit):
        cli.main([*args, "--sandbox", "local", "--dbt-target", "scratch"])
    with pytest.raises(SystemExit):
        cli.main(args)
    assert seen == ["scratch", "task"]


def test_run_refuses_a_scratch_target_without_a_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review round 1: scratch asks for writes confined to the run's schema, which only
    dbt_execute gives, so it is not silently dropped."""

    async def fake_run(*_: Any, **__: Any) -> Outcome:
        raise AssertionError("no run should start")

    monkeypatch.setattr(cli, "run", fake_run)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(
            [
                *("run", "--task", "q", "--type", "dbt", "--workspace", str(tmp_path)),
                *("--duckdb", str(make_db(tmp_path / "x.duckdb")), "--config", "full"),
                *("--events-dir", str(tmp_path / "events"), "--dbt-target", "scratch"),
            ]
        )
    assert exit_info.value.code == 2
    assert "--sandbox" in capsys.readouterr().err


def serve(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    args = ["mcp-server", "--config", "full", "--duckdb", str(make_db(tmp_path / "x.duckdb"))]
    return subprocess.run(
        [str(DATA_AGENT), *args, "--workspace", str(tmp_path), *extra],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (("--dbt-target", "task"), "--sandbox"),
        (("--sandbox", "local", "--dbt-target", "scratch"), "--dbt-profiles-dir"),
        (("--sandbox", "local", "--dbt-target", "task", "--dbt-profiles-dir", "/x"), "scratch"),
    ],
)
def test_the_server_refuses_dbt_options_that_do_not_go_together(
    tmp_path: Path, extra: tuple[str, ...], message: str
) -> None:
    proc = serve(tmp_path, *extra)
    assert proc.returncode == 2 and message in proc.stderr, proc.stderr


async def test_the_server_serves_dbt_execute_and_refuses_a_command_off_the_list(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "dbt_shop").mkdir(parents=True)
    (workspace / "dbt_shop" / "dbt_project.yml").write_text(
        (DBT_SHOP / "dbt_project.yml").read_text()
    )
    params = StdioServerParameters(
        command=str(DATA_AGENT),
        args=[
            *("mcp-server", "--config", "full", "--duckdb", str(make_db(tmp_path / "x.duckdb"))),
            *("--workspace", str(workspace), "--sandbox", "local", "--run-id", "r1"),
            *("--dbt-target", "scratch", "--dbt-profiles-dir", str(tmp_path / "profiles")),
        ],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        names = {tool.name for tool in (await session.list_tools()).tools}
        refused = await session.call_tool("dbt_execute", {"command": "run-operation"})
    assert {"dbt_execute", "sandbox_exec"} <= names
    assert refused.is_error
    [content] = refused.content
    assert isinstance(content, TextContent) and "command must be one of" in content.text
    assert not (tmp_path / "profiles").exists()
