"""The run-level `network` switch (ticket 98; .scratch/agent-tools/spec.md "`network` 开关"):
WebSearch and WebFetch are loaded with `--network` and denied without it. The product's entries
(the run form, each conversation turn) pass it; the evaluation entries never do (tests in
tests/test_harbor_agent.py and tests/test_dab.py)."""

import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pytest
from claude_agent_sdk import ClaudeAgentOptions, CLIConnectionError
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from data_agent import agent, cli
from data_agent.agent import Task
from data_agent.run_config import CONFIG_NAMES, load_config
from data_agent.web.conversations import Conversations
from data_agent.web.launcher import Launcher
from data_agent.web.runs import RunsDir
from data_agent.web.sources import DataSource

WEB = {"WebSearch", "WebFetch"}
SETTINGS = agent.ModelSettings.from_env({"DATA_AGENT_API_KEY": "sk-test"})
# Ticket 100 (ADR-0010): every task has the same file tools, and Bash is denied on every run.
QUERY_TOOLS = ["Read", "Glob", "Grep", "Write", "Edit"]
DBT_TOOLS = ["Read", "Glob", "Grep", "Write", "Edit"]
OFFLINE_DENIED = ["Bash", "WebSearch", "WebFetch"]
# The tools each run gets without the switch, by task type and config (main at the start of
# ticket 98, with ticket 100's tool surface).
BEFORE: dict[tuple[str, str], list[str]] = {
    **{("query", config): QUERY_TOOLS for config in CONFIG_NAMES},
    **{("dbt", config): [*DBT_TOOLS, "Agent"] for config in CONFIG_NAMES},
    ("dbt", "baseline"): DBT_TOOLS,
    ("dbt", "no-verifiers"): DBT_TOOLS,
}


def options(
    tmp_path: Path, task_type: str, config: str = "full", network: bool | None = None
) -> ClaudeAgentOptions:
    """`build_options` for a task; `network` None leaves it to the default."""
    task = Task(text="t", type=task_type, workspace=tmp_path, duckdb=tmp_path / "x.duckdb")  # type: ignore[arg-type]
    args = (task, load_config(config), SETTINGS, [], lambda *_, **__: None)
    if network is None:
        return agent.build_options(*args)
    return agent.build_options(*args, network=network)


@pytest.mark.parametrize("task_type", ["dbt", "query"])
def test_with_network_the_web_tools_are_loaded_and_allowed(tmp_path: Path, task_type: str) -> None:
    built = options(tmp_path, task_type, network=True)
    assert set(built.tools or []) >= WEB
    assert set(built.allowed_tools) >= WEB
    assert not WEB & set(built.disallowed_tools)


@pytest.mark.parametrize("task_type", ["dbt", "query"])
def test_without_network_the_web_tools_are_left_out_and_denied(
    tmp_path: Path, task_type: str
) -> None:
    """Benchmark integrity (data-eng-bench README): no looking up published solutions."""
    built = options(tmp_path, task_type)
    assert not WEB & set(built.tools or [])
    assert not WEB & set(built.allowed_tools)
    assert built.disallowed_tools == OFFLINE_DENIED


@pytest.mark.parametrize(("task_type", "config"), sorted(BEFORE))
def test_without_network_the_tools_are_what_they_were(
    tmp_path: Path, task_type: str, config: str
) -> None:
    built = options(tmp_path, task_type, config, network=False)
    assert built.tools == BEFORE[task_type, config]
    assert built.allowed_tools == [*BEFORE[task_type, config], "mcp__data"]
    assert built.disallowed_tools == OFFLINE_DENIED


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[ClaudeAgentOptions]:
    """The options of every session a run starts, stopped at the transport's `connect`, so no
    model is called (as tests/acceptance/test_chat_eval_unchanged.py does)."""
    seen: list[ClaudeAgentOptions] = []

    async def connect(self: SubprocessCLITransport) -> None:
        seen.append(self._options)  # pyright: ignore[reportPrivateUsage]
        raise CLIConnectionError("stopped by test_network_option before the CLI starts")

    monkeypatch.setattr(SubprocessCLITransport, "connect", connect)
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-test")
    return seen


@pytest.mark.parametrize(("flags", "online"), [((), False), (("--network",), True)])
def test_the_cli_switch_reaches_the_sessions_tools(
    tmp_path: Path, sessions: list[ClaudeAgentOptions], flags: tuple[str, ...], online: bool
) -> None:
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
    [options] = sessions
    assert (set(options.tools or []) >= WEB) is online
    assert (set(options.disallowed_tools) >= WEB) is not online
    # Ticket 100: the same switch turns the sandbox's network on (`mcp-server --network`).
    assert isinstance(options.mcp_servers, dict)
    args = list(options.mcp_servers["data"]["args"])  # type: ignore[index]
    assert args[args.index("--sandbox") + 1] == "docker"
    assert ("--network" in args) is online


@dataclass
class Recorder:
    """A `start_run` that records the arguments of each run and starts one that exits."""

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


def finished(launcher: Launcher) -> None:
    """Waits for the run going to exit, so the next one may start."""
    current = launcher.current
    assert current is not None
    current.process.wait(timeout=30)


def test_a_run_from_the_run_form_has_the_network_unless_turned_off(
    web: tuple[Launcher, Conversations, Recorder],
) -> None:
    launcher, _, recorder = web
    launcher.submit("t", "query", "shop", "full")
    finished(launcher)
    launcher.submit("t", "query", "shop", "full", network=False)
    with_network, without = recorder.calls
    assert "--network" in with_network
    assert "--network" not in without
    # Ticket 100: the docker sandbox either way; `local` is never offered.
    for args in recorder.calls:
        assert args[args.index("--sandbox") + 1] == "docker"


def test_a_conversation_turn_has_the_network_unless_turned_off(
    web: tuple[Launcher, Conversations, Recorder],
) -> None:
    launcher, conversations, recorder = web
    conversation = conversations.create()
    conversations.send(conversation, "Count the orders")
    finished(launcher)
    conversations.send(conversation, "Again", network=False)
    with_network, without = recorder.calls
    assert "--chat" in with_network
    assert "--network" in with_network
    assert "--network" not in without
    for args in recorder.calls:
        assert args[args.index("--sandbox") + 1] == "docker"
        assert "--dbt-target" not in args
