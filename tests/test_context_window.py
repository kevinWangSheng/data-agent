"""The CLI's context window and auto-compaction (ticket 110;
docs/research/2026-11-22-prompt-too-long.md). Claude Code 2.1.301 does not know `deepseek-flash`,
assumes a 200,000-token window and stops the session at 177,000 (`blocking_limit`); the server's
user settings turn auto-compaction off. Every run's options set the window to the model's 1M,
compact at 400,000 - 33,000 = 367,000, and turn compaction on through `--settings`, whatever the
shell exported or the machine's user settings say."""

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from claude_agent_sdk import ClaudeAgentOptions, CLIConnectionError
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from data_agent import agent
from data_agent.agent import ConversationTurn, Task
from data_agent.run_config import load_config

SETTINGS = agent.ModelSettings.from_env({"DATA_AGENT_API_KEY": "sk-test"})

WINDOW = {
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "1000000",
    # Digits only: 2.1.301 refuses a suffixed value such as `400k`.
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "400000",
}
# The CLI's `ih()`: `Fe(process.env.DISABLE_COMPACT) || a.DISABLE_AUTO_COMPACT`, both through
# `Fe`, true only for "1", "true", "yes" or "on". An empty value is off.
COMPACTION_OFF = ("DISABLE_AUTO_COMPACT", "DISABLE_COMPACT")


def options(tmp_path: Path, turn: ConversationTurn | None = None) -> ClaudeAgentOptions:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    task = Task(text="t", type="query", workspace=workspace, duckdb=tmp_path / "x.duckdb")
    return agent.build_options(
        task, load_config("full"), SETTINGS, [], lambda *_, **__: None, turn=turn
    )


def cli_args(built: ClaudeAgentOptions) -> list[str]:
    """The Claude Code CLI's argv for these options, as the SDK builds it."""
    transport = SubprocessCLITransport(prompt="p", options=built)
    transport._cli_path = "claude"  # pyright: ignore[reportPrivateUsage]  (set by connect)
    return transport._build_command()  # pyright: ignore[reportPrivateUsage]


class Spawned(Exception):
    """Raised in place of starting the CLI, once its environment is recorded."""


def cli_env(built: ClaudeAgentOptions, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The environment the SDK starts the CLI with: what it inherits, under the options' env."""
    seen: dict[str, str] = {}

    async def open_process(*_: Any, env: dict[str, str], **__: Any) -> None:
        seen.update(env)
        raise Spawned

    monkeypatch.setattr(anyio, "open_process", open_process)
    monkeypatch.setenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")
    transport = SubprocessCLITransport(prompt="p", options=built)
    transport._cli_path = "claude"  # pyright: ignore[reportPrivateUsage]

    async def connect() -> None:
        # The SDK reports `Spawned` as a failure to start the CLI.
        with pytest.raises(CLIConnectionError):
            await transport.connect()

    anyio.run(connect)
    assert seen, "the SDK never reached the CLI's spawn"
    return seen


def turns(tmp_path: Path) -> list[ConversationTurn | None]:
    """A single run (every evaluation) and a conversation turn (the chat page)."""
    return [None, ConversationTurn(tmp_path / "conversation", 1)]


@pytest.mark.parametrize("chat", [False, True])
def test_every_run_sets_the_models_window_and_the_compaction_window(
    tmp_path: Path, chat: bool
) -> None:
    built = options(tmp_path, turns(tmp_path)[chat])
    assert {name: built.env.get(name) for name in WINDOW} == WINDOW


@pytest.mark.parametrize("chat", [False, True])
def test_every_run_turns_auto_compaction_on_through_its_settings_flag(
    tmp_path: Path, chat: bool
) -> None:
    built = options(tmp_path, turns(tmp_path)[chat])
    assert built.settings is not None
    assert json.loads(built.settings) == {"autoCompactEnabled": True}
    argv = cli_args(built)
    assert json.loads(argv[argv.index("--settings") + 1]) == {"autoCompactEnabled": True}


@pytest.mark.parametrize("chat", [False, True])
def test_what_the_shell_exported_does_not_change_the_window_or_turn_compaction_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chat: bool
) -> None:
    for name, value in {
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "200000",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "100000",
        "DISABLE_AUTO_COMPACT": "1",
        "DISABLE_COMPACT": "true",
    }.items():
        monkeypatch.setenv(name, value)
    env = cli_env(options(tmp_path, turns(tmp_path)[chat]), monkeypatch)
    assert {name: env.get(name) for name in WINDOW} == WINDOW
    assert {name: env.get(name) for name in COMPACTION_OFF} == dict.fromkeys(COMPACTION_OFF, "")
