"""Answer charts, the switch (.scratch/charts/spec.md "开关" and "Testing Decisions"): only a run
with `--charts` has `display_chart` and the charting rule, the web UI's query runs and conversation
turns pass it, and the evaluation entries never do.

Written by ticket 118. The guards (no mark) pass now and must keep passing; the `TICKET_119`
tests pass once ticket 119 builds the switch, which then drops their marks and changes nothing
else.

Contract fixed here (ticket 118, "本工单定下的契约" 1 and 8):

- `data-agent run --charts` and `data-agent mcp-server --charts`, both off by default; the run
  passes the switch to its MCP server on the command line, not through the environment. Only the
  server's option name is used directly here (and the `--help` checks): the run is checked by what
  its session gets, the tools its MCP server lists and its system prompt.
- The charting rule names `display_chart`: a session with the switch has it in its system prompt,
  one without has not.
- The evaluation entries are driven as tests/acceptance/test_chat_eval_unchanged.py drives them
  (its helpers are imported, that file is not changed); spec "开关" puts this check there, and it
  lands here because that file is never edited.
"""

import os
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import pytest
from seam_support import DATA_AGENT
from test_chat_eval_unchanged import Captured, captured, dab_query, harbor_dbt

from data_agent import cli
from data_agent.web.conversations import Conversations
from data_agent.web.launcher import Launcher
from data_agent.web.runs import RunsDir
from data_agent.web.sources import DataSource
from evals.harbor_agent import DataAgent

__all__ = ["captured"]

TICKET_119 = pytest.mark.xfail(
    strict=True, reason="ticket 119: the --charts switch is not built yet"
)

CHART_TOOL = "display_chart"


def help_of(command: str) -> str:
    done = subprocess.run(
        [str(DATA_AGENT), command, "--help"], capture_output=True, text=True, check=True
    )
    return done.stdout


def tool_names(session: Captured) -> list[str]:
    """Every tool the session can call: built-in, allowed, and listed by its MCP servers."""
    options = session.options
    tools = options.tools if isinstance(options.tools, list) else []
    return [
        *tools,
        *options.allowed_tools,
        *(tool["name"] for listed in session.mcp_tools.values() for tool in listed),
    ]


def prompt_of(session: Captured) -> str:
    prompt = session.options.system_prompt
    assert prompt is not None
    return prompt if isinstance(prompt, str) else repr(prompt)


def has_charts(session: Captured) -> bool:
    """Whether the session has the chart tool, and whether its prompt has the rule; they agree."""
    tool = any(name.endswith(CHART_TOOL) for name in tool_names(session))
    rule = CHART_TOOL in prompt_of(session)
    assert tool == rule, f"chart tool {tool}, charting rule {rule}"
    return tool


def query_run(tmp_path: Path, seen: list[Captured], *flags: str) -> Captured:
    """`data-agent run` on a query task, in-process, stopped before the CLI starts."""
    import duckdb  # pyright: ignore[reportMissingImports]

    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    # Outside the database's directory: a docker run refuses a database in its workspace.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    argv = [
        "run",
        *("--type", "query", "--workspace", str(workspace), "--duckdb", str(db)),
        *("--config", "no-context", "--events-dir", str(tmp_path / "events")),
        *("--task", "How many tables are there?", *flags),
    ]
    with pytest.raises(SystemExit):
        cli.main(argv)
    [session] = seen
    return session


# --- guards: no switch, no charts --------------------------------------------------------------


def test_a_run_without_the_switch_has_no_chart_tool_and_no_rule(
    tmp_path: Path, captured: list[Captured]
) -> None:
    session = query_run(tmp_path, captured)
    assert "data" in session.mcp_tools
    assert not has_charts(session)


def test_harbor_never_passes_the_switch(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    agent = DataAgent(
        logs_dir=logs,
        model_name="deepseek/deepseek-flash",
        environment_logs_dir=PurePosixPath(logs),
    )
    command = agent._run_command("Build the model")  # pyright: ignore[reportPrivateUsage]
    assert " run --type dbt " in command
    assert "--charts" not in command


def test_the_harbor_run_has_no_chart_tool_and_no_rule(
    tmp_path: Path, captured: list[Captured]
) -> None:
    assert not has_charts(harbor_dbt(tmp_path, captured))


async def test_the_dab_session_has_no_chart_tool_and_no_rule(
    tmp_path: Path, captured: list[Captured], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not has_charts(await dab_query(tmp_path, captured, monkeypatch))


async def test_a_server_without_the_switch_or_a_sandbox_adds_no_id_line(tmp_path: Path) -> None:
    import duckdb  # pyright: ignore[reportMissingImports]
    from mcp import ClientSession, StdioServerParameters  # pyright: ignore[reportMissingImports]
    from mcp.client.stdio import stdio_client  # pyright: ignore[reportMissingImports]
    from mcp.types import TextContent  # pyright: ignore[reportMissingImports]

    db = tmp_path / "empty.duckdb"
    duckdb.connect(str(db)).close()
    params = StdioServerParameters(
        command=str(DATA_AGENT),
        args=["mcp-server", "--config", "no-context", "--duckdb", str(db)],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("query_database", {"sql": "SELECT 1 AS one, 2 AS two"})
        listed = await session.list_tools()
    text = "\n".join(item.text for item in result.content if isinstance(item, TextContent))
    assert not result.is_error, text
    assert text.splitlines() == ["one | two", "---------", "1 | 2", "(1 rows)"]
    assert "[result id:" not in text
    assert CHART_TOOL not in {tool.name for tool in listed.tools}


# --- the switch on (ticket 119) ----------------------------------------------------------------


@TICKET_119
def test_a_run_with_the_switch_has_the_chart_tool_and_the_rule(
    tmp_path: Path, captured: list[Captured]
) -> None:
    assert "--charts" in help_of("run"), "ticket 119: run has no --charts yet"
    session = query_run(tmp_path, captured, "--charts")
    assert CHART_TOOL in {tool["name"] for tool in session.mcp_tools["data"]}
    assert has_charts(session)


@dataclass
class Recorder:
    """A `start_run` that records the arguments of each run and starts one that exits (as
    tests/test_network_option.py does)."""

    calls: list[list[str]] = field(default_factory=list)
    processes: list["subprocess.Popen[bytes]"] = field(default_factory=list)

    def __call__(self, args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
        self.calls.append(list(args))
        process = subprocess.Popen([sys.executable, "-c", "pass"], cwd=cwd, stdin=subprocess.PIPE)
        self.processes.append(process)
        return process


@pytest.fixture
def web(tmp_path: Path) -> Iterator[tuple[Launcher, Conversations, Recorder]]:
    shop = tmp_path / "shop.duckdb"
    shop.write_bytes(b"")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    sources = [DataSource("shop", duckdb=shop)]
    recorder = Recorder()
    runs = RunsDir(runs_dir)
    launcher = Launcher(runs, sources, recorder)
    try:
        yield launcher, Conversations(runs, launcher, sources), recorder
    finally:
        # Only the runs this test started, by their own handles.
        for process in recorder.processes:
            process.wait(timeout=30)


@TICKET_119
def test_a_query_run_from_the_run_form_has_the_switch(
    web: tuple[Launcher, Conversations, Recorder],
) -> None:
    launcher, _, recorder = web
    launcher.submit("Revenue by month", "query", "shop", "full")
    [args] = recorder.calls
    assert "--charts" in args


@TICKET_119
def test_a_conversation_turn_has_the_switch(
    web: tuple[Launcher, Conversations, Recorder],
) -> None:
    _, conversations, recorder = web
    conversations.send(conversations.create(), "Revenue by month")
    [args] = recorder.calls
    assert "--chat" in args
    assert "--charts" in args
