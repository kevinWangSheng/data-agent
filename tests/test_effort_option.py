"""The run-level `effort` option (ticket 104; .scratch/dab-gap/spec.md (effort, 104)):
`data-agent run --effort low|high|max` sets `ClaudeAgentOptions.effort` and the run's `version`
event; without it the options are what they were. DAB's side is in tests/test_dab.py."""

import json
from dataclasses import fields
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import ClaudeAgentOptions, CLIConnectionError
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from data_agent import agent, cli
from data_agent.agent import Task
from data_agent.run_config import load_config

SETTINGS = agent.ModelSettings.from_env({"DATA_AGENT_API_KEY": "sk-test"})
# Built anew on every call (closures, the stderr list's bound method): compared by presence.
CALLABLES = {"hooks", "stderr"}


def options(tmp_path: Path, **kwargs: Any) -> ClaudeAgentOptions:
    task = Task(text="t", type="query", workspace=tmp_path, duckdb=tmp_path / "x.duckdb")
    return agent.build_options(
        task, load_config("full"), SETTINGS, [], lambda *_, **__: None, **kwargs
    )


def cli_args(built: ClaudeAgentOptions) -> list[str]:
    """The Claude Code CLI's argv for these options, as the SDK builds it."""
    transport = SubprocessCLITransport(prompt="p", options=built)
    transport._cli_path = "claude"  # pyright: ignore[reportPrivateUsage]  (set by connect)
    return transport._build_command()  # pyright: ignore[reportPrivateUsage]


def test_without_effort_the_options_leave_the_models_reasoning_to_the_endpoint(
    tmp_path: Path,
) -> None:
    built = options(tmp_path)
    assert (built.effort, built.thinking, built.max_thinking_tokens) == (None, None, None)
    argv = cli_args(built)
    assert not {"--effort", "--thinking", "--max-thinking-tokens"} & set(argv)


def test_effort_max_sets_the_sessions_effort_and_nothing_else(tmp_path: Path) -> None:
    plain, maxed = options(tmp_path), options(tmp_path, effort="max")
    assert maxed.effort == "max"
    argv = cli_args(maxed)
    assert argv[argv.index("--effort") + 1] == "max"
    for f in fields(ClaudeAgentOptions):
        if f.name == "effort":
            continue
        if f.name in CALLABLES:
            assert (getattr(plain, f.name) is None) == (getattr(maxed, f.name) is None)
        else:
            assert getattr(plain, f.name) == getattr(maxed, f.name), f.name


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[ClaudeAgentOptions]:
    """The options of every session a run starts, stopped at the transport's `connect`, so no
    model is called (as tests/test_network_option.py does)."""
    seen: list[ClaudeAgentOptions] = []

    async def connect(self: SubprocessCLITransport) -> None:
        seen.append(self._options)  # pyright: ignore[reportPrivateUsage]
        raise CLIConnectionError("stopped by test_effort_option before the CLI starts")

    monkeypatch.setattr(SubprocessCLITransport, "connect", connect)
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-test")
    return seen


def run_argv(tmp_path: Path, *flags: str) -> list[str]:
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return [
        "run",
        *("--type", "query", "--workspace", str(workspace), "--duckdb", str(db)),
        *("--config", "no-context", "--events-dir", str(tmp_path / "events")),
        *("--task", "How many tables are there?", *flags),
    ]


def version_event(events_dir: Path) -> dict[str, Any]:
    [path] = events_dir.glob("*.jsonl")
    header = json.loads(path.read_text().splitlines()[0])
    assert header["type"] == "version"
    return header


def test_the_cli_effort_reaches_the_session_and_the_version_event(
    tmp_path: Path, sessions: list[ClaudeAgentOptions]
) -> None:
    with pytest.raises(SystemExit):
        cli.main(run_argv(tmp_path, "--effort", "max"))
    [built] = sessions
    assert built.effort == "max"
    assert version_event(tmp_path / "events")["effort"] == "max"


def test_without_the_cli_effort_the_run_records_none(
    tmp_path: Path, sessions: list[ClaudeAgentOptions]
) -> None:
    with pytest.raises(SystemExit):
        cli.main(run_argv(tmp_path))
    [built] = sessions
    assert built.effort is None
    # The record is what it was before ticket 104: no `effort` key at all.
    assert "effort" not in version_event(tmp_path / "events")


@pytest.mark.parametrize("value", ["medium", "xhigh", "MAX", ""])
def test_an_effort_deepseek_does_not_take_is_refused_at_parsing(
    tmp_path: Path,
    sessions: list[ClaudeAgentOptions],
    capsys: pytest.CaptureFixture[str],
    value: str,
) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(run_argv(tmp_path, "--effort", value))
    assert exited.value.code == 2
    assert "--effort" in capsys.readouterr().err
    assert sessions == []
    assert not (tmp_path / "events").exists()
