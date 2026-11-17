"""The clarification tool (ticket 91, .scratch/chat-ui/spec.md decision 8; contract in
tests/acceptance/test_chat_api.py item 2): in conversation mode the agent can ask one question with
2-5 options; the call writes a `clarification` event and ends the turn as a success, with no
answer. Without conversation mode (single runs, every evaluation) there is no such tool.

The MCP tool is driven over stdio as the CLI starts it; the turn's end is driven by a scripted
session that calls the hooks the options carry, as the bundled CLI does (CLI 2.1.301: a
PostToolUse hook's `continue: false` makes no further model call and ends with a `success` result,
`terminal_reason: "hook_stopped"` and an empty `result`; probed against a local fake endpoint).
"""

import json
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from data_agent import agent
from data_agent.agent import CLARIFICATION_TOOL, ConversationTurn, ModelSettings, Task
from data_agent.run_config import load_config

ENV = {"DATA_AGENT_API_KEY": "sk-test-secret"}
SETTINGS = ModelSettings.from_env(ENV)
QUESTION = "Should refunded orders count toward revenue?"
OPTIONS = ["Gross: count refunded orders", "Net: leave refunded orders out"]


def make_conversation(tmp_path: Path) -> tuple[Path, Task]:
    conversation = tmp_path / "conv-20261117T000000Z-abcd1234"
    workspace = conversation / "workspace"
    workspace.mkdir(parents=True)
    db = tmp_path / "x.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE TABLE items AS SELECT range AS id FROM range(3)")
    finally:
        con.close()
    return conversation, Task(
        text="What was revenue?", type="query", workspace=workspace, duckdb=db
    )


def options_of(task: Task, turn: ConversationTurn | None) -> ClaudeAgentOptions:
    return agent.build_options(
        task, load_config("full"), SETTINGS, [], lambda *_, **__: None, turn=turn
    )


@asynccontextmanager
async def served(options: ClaudeAgentOptions) -> AsyncIterator[ClientSession]:
    """The data MCP server, started exactly as the options name it, with the environment the
    SDK's CLI would give it."""
    assert isinstance(options.mcp_servers, dict)
    server: dict[str, Any] = dict(options.mcp_servers[agent.MCP_SERVER])
    params = StdioServerParameters(
        command=server["command"],
        args=list(server["args"]),
        env={**os.environ, **options.env, **server.get("env", {})},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


async def tool_names(options: ClaudeAgentOptions) -> list[str]:
    async with served(options) as session:
        listed = await session.list_tools()
    return [tool.name for tool in listed.tools]


# --- the tool ------------------------------------------------------------------------------


async def test_a_conversation_turn_serves_the_clarification_tool(tmp_path: Path) -> None:
    conversation, task = make_conversation(tmp_path)
    options = options_of(task, ConversationTurn(conversation, 1))
    assert "clarification" in await tool_names(options)
    assert f"mcp__{agent.MCP_SERVER}__clarification" == CLARIFICATION_TOOL


async def test_a_single_run_has_no_clarification_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, task = make_conversation(tmp_path)
    # Not even when the shell it runs from has conversation mode switched on.
    monkeypatch.setenv(agent.CHAT_ENV, "1")
    options = options_of(task, None)
    assert "clarification" not in await tool_names(options)
    assert "clarification" not in agent.system_prompt("query", load_config("full")).lower()


async def test_the_tool_takes_a_question_and_two_to_five_options(tmp_path: Path) -> None:
    conversation, task = make_conversation(tmp_path)
    options = options_of(task, ConversationTurn(conversation, 1))
    async with served(options) as session:
        for count in (2, 5):
            choices = [f"option {n}" for n in range(count)]
            result = await session.call_tool(
                "clarification", {"question": QUESTION, "options": choices}
            )
            assert not result.is_error, result


@pytest.mark.parametrize(
    "arguments",
    [
        {"question": QUESTION, "options": []},
        {"question": QUESTION, "options": ["only one"]},
        {"question": QUESTION, "options": [f"option {n}" for n in range(6)]},
        {"question": QUESTION, "options": ["same", "same"]},
        {"question": QUESTION, "options": ["fine", " "]},
        {"question": " ", "options": OPTIONS},
    ],
    ids=["no-options", "one-option", "six-options", "repeated", "blank-option", "blank-question"],
)
async def test_the_tool_refuses_other_shapes(tmp_path: Path, arguments: dict[str, Any]) -> None:
    conversation, task = make_conversation(tmp_path)
    options = options_of(task, ConversationTurn(conversation, 1))
    async with served(options) as session:
        result = await session.call_tool("clarification", arguments)
    assert result.is_error, result


# --- the turn ends -------------------------------------------------------------------------


def init(session_id: str) -> SystemMessage:
    return SystemMessage(
        subtype="init",
        data={"model": "m", "tools": [], "mcp_servers": [], "session_id": session_id},
    )


def asks(tool_use_id: str, arguments: dict[str, Any]) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolUseBlock(id=tool_use_id, name=CLARIFICATION_TOOL, input=arguments)],
        model="m",
        message_id="msg_1",
    )


def answered(tool_use_id: str, is_error: bool = False) -> UserMessage:
    return UserMessage(
        content=[ToolResultBlock(tool_use_id=tool_use_id, content="sent", is_error=is_error)]
    )


def hook_stopped() -> ResultMessage:
    """The result the CLI sends when a hook stopped the turn (probed, CLI 2.1.301)."""
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=2,
        session_id="sess-1",
        stop_reason="tool_use",
        usage={"input_tokens": 3, "output_tokens": 7},
        total_cost_usd=0.01,
        result="",
        terminal_reason="hook_stopped",
    )


async def post_tool_use(
    options: ClaudeAgentOptions, event: str, tool_use_id: str, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every hook the CLI would call after the clarification call, as it calls them; their
    outputs."""
    hook_input = {
        "hook_event_name": event,
        "session_id": "sess-1",
        "tool_name": CLARIFICATION_TOOL,
        "tool_input": arguments,
        "tool_use_id": tool_use_id,
        "tool_response": [{"type": "text", "text": "sent"}],
        "error": "refused",
    }
    outputs: list[dict[str, Any]] = []
    hooks: dict[Any, Any] = dict(options.hooks or {})
    for matcher in hooks.get(event, []):
        if matcher.matcher and not re.fullmatch(matcher.matcher, CLARIFICATION_TOOL):
            continue
        for hook in matcher.hooks:
            output = await hook(cast(Any, hook_input), tool_use_id, {"signal": None})
            outputs.append(dict(output))
    return outputs


async def run_turn(
    monkeypatch: pytest.MonkeyPatch,
    task: Task,
    turn: ConversationTurn | None,
    arguments: dict[str, Any],
    event: str = "PostToolUse",
) -> tuple[agent.Outcome, list[dict[str, Any]], list[dict[str, Any]]]:
    """`agent.run` on a session in which the agent calls the clarification tool; the CLI calls
    the hooks after the call and, if one stopped the turn, ends it there. The outcome, the
    events, and the hooks' outputs."""
    outputs: list[dict[str, Any]] = []

    async def fake_query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        yield init("sess-1")
        yield asks("toolu_1", arguments)
        outputs.extend(await post_tool_use(options, event, "toolu_1", arguments))
        yield answered("toolu_1", is_error=event == "PostToolUseFailure")
        if any(output.get("continue_") is False for output in outputs):
            yield hook_stopped()
            return
        # Not stopped: the agent goes on and answers.
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=2,
            session_id="sess-1",
            usage={"input_tokens": 3, "output_tokens": 7},
            total_cost_usd=0.01,
            result="Revenue was 42.",
        )

    monkeypatch.setattr(agent, "query", fake_query)
    outcome = await agent.run(
        task, load_config("full"), task.workspace.parent / "events", ENV, turn=turn
    )
    events = [json.loads(line) for line in outcome.events_path.read_text().splitlines()]
    return outcome, events, outputs


def of_type(events: list[dict[str, Any]], type_: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == type_]


async def test_asking_writes_a_clarification_event_and_ends_the_turn_as_a_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conversation, task = make_conversation(tmp_path)
    arguments = {"question": QUESTION, "options": OPTIONS}
    outcome, events, outputs = await run_turn(
        monkeypatch, task, ConversationTurn(conversation, 1), arguments
    )
    # The hook stops the turn: the CLI makes no further model call.
    assert any(output.get("continue_") is False for output in outputs), outputs
    [clarification] = of_type(events, "clarification")
    assert clarification["tool_use_id"] == "toolu_1"
    assert (clarification["question"], clarification["options"]) == (QUESTION, OPTIONS)
    # Not an error, not a cancellation: a success with no answer.
    assert not of_type(events, "answer")
    assert not of_type(events, "error")
    [result] = of_type(events, "result")
    assert (result["subtype"], result["is_error"]) == ("success", False)
    assert outcome.finished and not outcome.infra_error
    assert outcome.answer == ""
    after = [e for e in events if e["seq"] > clarification["seq"]]
    assert not of_type(after, "tool_start")


async def test_a_refused_call_neither_asks_nor_ends_the_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With the wrong number of options the tool errs (PostToolUseFailure): the agent reads the
    error and goes on."""
    conversation, task = make_conversation(tmp_path)
    arguments = {"question": QUESTION, "options": ["only one"]}
    outcome, events, outputs = await run_turn(
        monkeypatch, task, ConversationTurn(conversation, 1), arguments, "PostToolUseFailure"
    )
    assert not any(output.get("continue_") is False for output in outputs), outputs
    assert not of_type(events, "clarification")
    assert [e["text"] for e in of_type(events, "answer")] == ["Revenue was 42."]
    assert outcome.finished


def test_only_a_conversation_turn_carries_the_stop_hook(tmp_path: Path) -> None:
    conversation, task = make_conversation(tmp_path)

    def matchers(options: ClaudeAgentOptions) -> list[str | None]:
        return [m.matcher for m in (options.hooks or {}).get("PostToolUse", [])]

    assert CLARIFICATION_TOOL in matchers(options_of(task, ConversationTurn(conversation, 1)))
    assert CLARIFICATION_TOOL not in matchers(options_of(task, None))


def test_a_conversation_turn_is_told_when_to_ask(tmp_path: Path) -> None:
    conversation, task = make_conversation(tmp_path)
    chat = options_of(task, ConversationTurn(conversation, 1)).system_prompt
    single = options_of(task, None).system_prompt
    assert isinstance(chat, str) and isinstance(single, str)
    assert "`clarification`" in chat
    assert chat.startswith(single)
