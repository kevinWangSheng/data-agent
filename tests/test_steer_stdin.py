"""Steering a running turn (ticket 92, .scratch/chat-ui/spec.md decision 9): a `--chat` run reads
stdin one JSON message per line (`{"id", "text"}`, tests/acceptance/test_chat_api.py contract item
6) and sends each into the live `ClaudeSDKClient` as one more `query()`, its id as the uuid, as
SignalPilot does (claude_agent.py:355-408). The CLI echoes the message when the model takes it in
(`replay-user-messages`), and the run then writes a `steer_delivered` event; its `seq` is where the
model read it. The CLI session is a scripted fake client, so no model is called.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from data_agent import agent, cli
from data_agent.agent import ConversationTurn, Outcome, Task
from data_agent.run_config import load_config

ENV = {"DATA_AGENT_API_KEY": "sk-test-secret"}
QUERY_TOOL = "mcp__data__query_database"
STEER = {"id": "6f1c2a9e-0b7d-4f3e-9a51-2d8c7e4b1a60", "text": "Only the top 3, please."}


@dataclass(frozen=True)
class AwaitSteers:
    """In a script: the session goes on once this many steering messages have been queried."""

    count: int


@dataclass(frozen=True)
class Ended:
    """In a script: marks that the turn's result has been sent (`FakeClient.ended`)."""


@dataclass(frozen=True)
class Echo:
    """In a script: the CLI's echo (`replay-user-messages`) of steering message `index` (0 the
    first), sent when the model takes it in; it waits until that message has been queried."""

    index: int


class FakeClient:
    """A CLI session that plays `script` and keeps every prompt it was sent, with its uuid. Like
    the SDK's, `receive_response` ends at the first ResultMessage and `receive_messages` does
    not."""

    script: tuple[Any, ...] = ()
    last: "FakeClient | None" = None

    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.options = options
        self.prompts: list[str] = []
        self.uuids: list[str | None] = []
        self.ended = asyncio.Event()
        self.disconnected = False
        FakeClient.last = self

    async def connect(self) -> None:
        pass

    async def query(self, prompt: str | AsyncIterable[dict[str, Any]]) -> None:
        if isinstance(prompt, str):
            self.prompts.append(prompt)
            self.uuids.append(None)
            return
        async for message in prompt:
            self.prompts.append(message["message"]["content"])
            self.uuids.append(message.get("uuid"))

    async def wait_for_steers(self, count: int) -> None:
        async with asyncio.timeout(5):
            while len(self.prompts) < 1 + count:
                await asyncio.sleep(0.001)

    async def receive_messages(self) -> AsyncIterator[Any]:
        for item in self.script:
            if isinstance(item, AwaitSteers):
                await self.wait_for_steers(item.count)
            elif isinstance(item, Ended):
                self.ended.set()
            elif isinstance(item, Echo):
                await self.wait_for_steers(item.index + 1)
                yield UserMessage(
                    content=self.prompts[1 + item.index], uuid=self.uuids[1 + item.index]
                )
            else:
                yield item

    async def receive_response(self) -> AsyncIterator[Any]:
        async for message in self.receive_messages():
            yield message
            if isinstance(message, ResultMessage):
                return

    async def disconnect(self) -> None:
        self.disconnected = True


def init() -> SystemMessage:
    return SystemMessage(
        subtype="init",
        data={"model": "m", "tools": [], "mcp_servers": [], "session_id": "sess-1"},
    )


def tool_call(call_id: str) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolUseBlock(id=call_id, name=QUERY_TOOL, input={"sql": "SELECT 1"})],
        model="m",
    )


def tool_result(call_id: str) -> UserMessage:
    return UserMessage(content=[ToolResultBlock(tool_use_id=call_id, content="1")])


def result(
    text: str = "the top 3",
    *,
    num_turns: int = 2,
    cost: float = 0.01,
    output: int = 7,
    subtype: str = "success",
    model_usage: dict[str, Any] | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=subtype != "success",
        model_usage=model_usage,
        num_turns=num_turns,
        session_id="sess-1",
        usage={"input_tokens": 3, "output_tokens": output},
        total_cost_usd=cost,
        result=text,
    )


def answer(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model="m")


CONVERSATION = "conv-20261117T000000Z-abcd1234"


def make_turn(tmp_path: Path, resume: str | None = None) -> tuple[Task, ConversationTurn]:
    """Turn 1 of a conversation, or with `resume` turn 2, resuming that session."""
    conversation = tmp_path / CONVERSATION
    workspace = conversation / "workspace"
    workspace.mkdir(parents=True)
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    task = Task(text="rank the customers", type="query", workspace=workspace, duckdb=db)
    return task, ConversationTurn(conversation, 1 if resume is None else 2, resume=resume)


async def lines_of(lines: Iterable[str]) -> AsyncGenerator[str]:
    """stdin with these lines, then closed."""
    for line in lines:
        yield line


async def run_steered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    steering: AsyncGenerator[str],
    *script: Any,
    client: type[FakeClient] = FakeClient,
    deadline: float | None = None,
    resume: str | None = None,
) -> tuple[Outcome, FakeClient, list[dict[str, Any]]]:
    """`agent.run` as a turn steered by `steering`, on a `client` session that plays `script`."""
    monkeypatch.setattr(client, "script", script)
    monkeypatch.setattr(agent, "ClaudeSDKClient", client)
    task, turn = make_turn(tmp_path, resume)
    async with asyncio.timeout(10):
        outcome = await agent.run(
            task,
            load_config("full"),
            tmp_path / "events",
            ENV,
            deadline,
            turn=turn,
            steering=steering,
        )
    assert FakeClient.last is not None
    events = [json.loads(line) for line in outcome.events_path.read_text().splitlines()]
    return outcome, FakeClient.last, events


def of_type(events: list[dict[str, Any]], type_: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == type_]


def seq_of(events: list[dict[str, Any]], type_: str, call_id: str) -> int:
    [event] = [e for e in events if e["type"] == type_ and e.get("tool_use_id") == call_id]
    return event["seq"]


async def test_a_stdin_message_is_one_query_and_one_event_where_it_went_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcome, client, events = await run_steered(
        monkeypatch,
        tmp_path,
        lines_of([json.dumps(STEER) + "\n"]),
        init(),
        tool_call("q1"),
        AwaitSteers(1),
        tool_result("q1"),
        Echo(0),
        tool_call("q2"),
        tool_result("q2"),
        result(),
    )
    assert outcome.finished
    assert client.prompts[1:] == [STEER["text"]]
    # The message goes in as one stream-json user message, its id the uuid the CLI echoes.
    assert client.uuids[1:] == [STEER["id"]]
    assert client.options.extra_args == {"replay-user-messages": None}
    [delivered] = of_type(events, "steer_delivered")
    assert (delivered["id"], delivered["text"]) == (STEER["id"], STEER["text"])
    # Sent while the first query ran, taken in by the model after it: where the echo came.
    assert seq_of(events, "tool_end", "q1") < delivered["seq"] < seq_of(events, "tool_start", "q2")
    assert len(of_type(events, "result")) == 1


async def test_a_line_that_is_no_message_is_skipped_with_an_error_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad = [
        "not json\n",
        "[1, 2]\n",
        '{"id": 7, "text": "a number for an id"}\n',
        '{"id": "m1"}\n',
        '{"id": "m2", "text": "   "}\n',
    ]
    outcome, client, events = await run_steered(
        monkeypatch,
        tmp_path,
        lines_of([*bad, "\n", json.dumps(STEER) + "\n", json.dumps(STEER) + "\n"]),
        init(),
        tool_call("q1"),
        AwaitSteers(1),
        tool_result("q1"),
        Echo(0),
        result(),
    )
    assert outcome.finished
    assert client.prompts[1:] == [STEER["text"]]
    assert [(e["id"], e["text"]) for e in of_type(events, "steer_delivered")] == [
        (STEER["id"], STEER["text"])
    ]
    errors = of_type(events, "error")
    assert [e["source"] for e in errors] == ["steer"] * (len(bad) + 1)
    # Numbered by stdin line; the blank line 6 is ignored without an event. Line 8 repeats the
    # id of line 7, which the CLI would drop unechoed.
    assert [e["error"].split(" skipped")[0] for e in errors] == [
        f"stdin line {n}" for n in (1, 2, 3, 4, 5, 8)
    ]
    assert errors[-1]["error"] == f"stdin line 8 skipped: id {STEER['id']!r} already sent"


async def test_stdin_closed_from_the_start_leaves_the_turn_as_it_is(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcome, client, events = await run_steered(
        monkeypatch, tmp_path, lines_of([]), init(), tool_call("q1"), tool_result("q1"), result()
    )
    assert outcome.finished
    assert len(client.prompts) == 1
    assert not of_type(events, "steer_delivered") and not of_type(events, "error")


async def test_a_message_after_the_result_is_not_sent_and_stdin_left_open_ends_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The run ends with its result although stdin stays open; a line that comes after the
    result is never sent and has no event."""

    async def late_line() -> AsyncGenerator[str]:
        assert FakeClient.last is not None
        await FakeClient.last.ended.wait()
        yield json.dumps(STEER) + "\n"
        await asyncio.Event().wait()  # stdin stays open

    outcome, client, events = await run_steered(
        monkeypatch,
        tmp_path,
        late_line(),
        init(),
        tool_call("q1"),
        tool_result("q1"),
        Ended(),
        result(),
        tool_call("never-read"),
    )
    assert outcome.finished
    assert len(client.prompts) == 1
    assert not of_type(events, "steer_delivered")
    assert len(of_type(events, "result")) == 1


async def test_stdin_lines_reads_a_pipe_line_by_line_until_it_closes() -> None:
    read_end, write_end = os.pipe()
    os.write(write_end, b'{"id": "a", "text": "x"}\n{"id": "b",')
    lines = agent.stdin_lines(read_end)
    async with asyncio.timeout(5):
        assert await anext(lines) == '{"id": "a", "text": "x"}\n'
        os.write(write_end, b' "text": "y"}\nno newline at the end')
        os.close(write_end)
        assert [line async for line in lines] == [
            '{"id": "b", "text": "y"}\n',
            "no newline at the end",
        ]


async def test_stdin_lines_can_stop_while_the_pipe_stays_open() -> None:
    read_end, write_end = os.pipe()
    os.write(write_end, b"one\n")
    lines = agent.stdin_lines(read_end)
    async with asyncio.timeout(5):
        assert await anext(lines) == "one\n"
        waiting = asyncio.create_task(anext(lines))
        await asyncio.sleep(0.01)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        await lines.aclose()
    os.close(write_end)


@pytest.mark.parametrize("chat", [True, False])
def test_only_a_chat_run_steers_from_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, chat: bool
) -> None:
    """A `--chat` run steers from stdin, fd 0; a single run (evaluations included) gets no
    steering, so it never reads stdin."""
    task, turn = make_turn(tmp_path)
    assert task.duckdb is not None
    seen: list[AsyncGenerator[str] | None] = []

    async def fake_run(*_: Any, steering: AsyncGenerator[str] | None = None, **__: Any) -> Outcome:
        seen.append(steering)
        return Outcome("", True, False, [], tmp_path / "events.jsonl")

    read_fds: list[int] = []
    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setattr(cli, "stdin_lines", lambda fd: read_fds.append(fd) or lines_of([]))
    chat_args = ("--chat", "--conversation-dir", str(turn.directory), "--turn", "1")
    with pytest.raises(SystemExit) as exit_:
        cli.main(
            [
                *("run", "--task", "q", "--type", "query", "--config", "full"),
                *("--workspace", str(task.workspace), "--duckdb", str(task.duckdb)),
                *("--events-dir", str(tmp_path / "events")),
                *(chat_args if chat else ()),
            ]
        )
    assert exit_.value.code == 0
    [steering] = seen
    assert (steering is not None, read_fds) == ((True, [0]) if chat else (False, []))


def test_a_process_ends_while_its_stdin_stays_open() -> None:
    """The reader thread, blocked on a stdin that stays open, neither holds the process up at
    exit nor breaks its shutdown."""
    script = (
        "import asyncio\n"
        "from data_agent.agent import stdin_lines\n"
        "async def main():\n"
        "    print(await anext(stdin_lines(0)), end='', flush=True)\n"
        "asyncio.run(main())\n"
    )
    with subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        assert process.stdin is not None and process.stdout is not None
        assert process.stderr is not None
        process.stdin.write(b"first\n")
        process.stdin.flush()
        try:
            code = process.wait(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
        assert (code, process.stdout.read(), process.stderr.read()) == (0, b"first\n", b"")


async def test_a_message_the_session_cannot_take_is_an_error_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class GoneClient(FakeClient):
        async def query(self, prompt: str | AsyncIterable[dict[str, Any]]) -> None:
            await super().query(prompt)  # counts the attempt
            if len(self.prompts) > 1:
                raise ConnectionError("the CLI has gone")

    async def two_then_open() -> AsyncGenerator[str]:
        yield json.dumps(STEER) + "\n"
        yield json.dumps({**STEER, "id": "m2"}) + "\n"
        await asyncio.Event().wait()

    outcome, client, events = await run_steered(
        monkeypatch,
        tmp_path,
        two_then_open(),
        init(),
        tool_call("q1"),
        AwaitSteers(1),
        tool_result("q1"),
        result(),
        client=GoneClient,
    )
    assert outcome.finished
    assert not of_type(events, "steer_delivered")
    assert len(client.prompts) == 2
    # Steering stops at the first message not sent: line 2 is never tried.
    assert [(e["source"], e["error"]) for e in of_type(events, "error")] == [
        ("steer", "stdin line 1 not sent: the CLI has gone")
    ]


async def test_a_run_stopped_at_its_deadline_while_steering_still_disconnects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def open_stdin() -> AsyncGenerator[str]:
        await asyncio.Event().wait()
        yield ""

    # The session hangs on a steer that never comes; the deadline stops the run first.
    deadline = time.monotonic() + agent.DEADLINE_MARGIN_S + 0.3
    _, client, events = await run_steered(
        monkeypatch,
        tmp_path,
        open_stdin(),
        init(),
        tool_call("q1"),
        AwaitSteers(1),
        deadline=deadline,
    )
    assert events[-1]["terminal_reason"] == "deadline"
    assert client.disconnected


async def test_a_message_sent_after_the_last_step_is_read_before_the_turn_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A message sent once the model has taken its last step comes too late for that step: the
    CLI ends it with a result, then takes the message in (its echo) as the next step, with a
    result of its own (probes of 2026-11-17). The turn ends at that second result: its answer,
    one `steer_delivered` where the model read the message, and one `result` for both steps.
    The CLI's cost and model usage count the whole process, its turns and usage each step."""
    outcome, client, events = await run_steered(
        monkeypatch,
        tmp_path,
        lines_of([json.dumps(STEER) + "\n"]),
        init(),
        tool_call("q1"),
        tool_result("q1"),
        answer("all the customers"),
        AwaitSteers(1),
        result("all the customers", num_turns=2, cost=0.01, output=7),
        init(),
        Echo(0),
        answer("the top 3"),
        result("the top 3", num_turns=1, cost=0.03, output=5),
    )
    assert outcome.finished and outcome.answer == "the top 3"
    assert client.uuids[1:] == [STEER["id"]]
    [delivered] = of_type(events, "steer_delivered")
    assert (delivered["id"], delivered["text"]) == (STEER["id"], STEER["text"])
    texts = [e for e in of_type(events, "text")]
    assert texts[0]["seq"] < delivered["seq"] < texts[1]["seq"]
    [final] = of_type(events, "answer")
    assert final["text"] == "the top 3"
    [turn_result] = of_type(events, "result")
    assert (turn_result["num_turns"], turn_result["sdk_cost_usd"]) == (3, 0.03)
    assert turn_result["output_tokens"] == 12
    assert len(of_type(events, "status")) == 1


async def test_two_messages_sent_together_end_the_turn_once_both_are_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two messages sent after the last step: the CLI echoes the first after the step's result
    and the second when it takes both in as one step (probe of 2026-11-17)."""
    second = {"id": "m2", "text": "And their totals."}
    outcome, _, events = await run_steered(
        monkeypatch,
        tmp_path,
        lines_of([json.dumps(STEER) + "\n", json.dumps(second) + "\n"]),
        init(),
        answer("all the customers"),
        AwaitSteers(2),
        result("all the customers"),
        Echo(0),
        init(),
        Echo(1),
        answer("the top 3 with totals"),
        result("the top 3 with totals"),
    )
    assert outcome.answer == "the top 3 with totals"
    assert [e["id"] for e in of_type(events, "steer_delivered")] == [STEER["id"], "m2"]
    assert len(of_type(events, "result")) == 1


async def test_an_error_result_ends_the_turn_with_a_message_unread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A step that fails (here its turn limit) ends the turn even with a message waiting for
    its echo: no `steer_delivered`, an `error` event saying it was not read."""
    outcome, _, events = await run_steered(
        monkeypatch,
        tmp_path,
        lines_of([json.dumps(STEER) + "\n"]),
        init(),
        tool_call("q1"),
        AwaitSteers(1),
        result("", subtype="error_max_turns"),
        Echo(0),
        result("never read"),
    )
    assert not outcome.finished
    assert not of_type(events, "steer_delivered")
    assert [(e["source"], e["error"]) for e in of_type(events, "error")] == [
        ("steer", f"message {STEER['id']!r} was not read: the turn ended")
    ]
    [turn_result] = of_type(events, "result")
    assert turn_result["subtype"] == "error_max_turns"


def model_usage(uncached: int, cached: int, output: int, cost: float) -> dict[str, Any]:
    return {
        "m": {
            "inputTokens": uncached,
            "cacheCreationInputTokens": 0,
            "cacheReadInputTokens": cached,
            "outputTokens": output,
            "costUSD": cost,
        }
    }


async def test_a_resumed_turn_of_two_steps_counts_its_own_spend_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each step's result holds the CLI's totals so far, the restored earlier turns among them
    (ticket 90, `restored_sdk_cost_usd`): the turn's result is the last one less what was
    restored, not a sum of the two."""
    record = tmp_path / CONVERSATION / "claude-config" / "projects" / "-cwd" / "sess-1.jsonl"
    record.parent.mkdir(parents=True)
    restored = {
        "type": "cost-state",
        "sessionId": "sess-1",
        "totalCostUSD": 0.5,
        "modelUsage": model_usage(100, 40, 20, 0.5),
    }
    record.write_text(json.dumps(restored) + "\n")
    _, _, events = await run_steered(
        monkeypatch,
        tmp_path,
        lines_of([json.dumps(STEER) + "\n"]),
        init(),
        answer("all the customers"),
        AwaitSteers(1),
        result("all", num_turns=1, cost=0.52, model_usage=model_usage(130, 50, 27, 0.52)),
        init(),
        Echo(0),
        result("top 3", num_turns=1, cost=0.55, model_usage=model_usage(160, 50, 31, 0.55)),
        resume="sess-1",
    )
    [turn_result] = of_type(events, "result")
    assert (
        turn_result["uncached_input_tokens"],
        turn_result["cached_input_tokens"],
        turn_result["output_tokens"],
        round(turn_result["sdk_cost_usd"], 6),
        turn_result["num_turns"],
        turn_result["restored_sdk_cost_usd"],
    ) == (60, 10, 11, 0.05, 2, 0.5)


async def test_a_session_ending_inside_the_steered_step_is_no_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The CLI may end its stream (exit 0) inside the step a message was taken in for: the
    earlier step's answer is not the turn's, so the run has none."""
    outcome, _, events = await run_steered(
        monkeypatch,
        tmp_path,
        lines_of([json.dumps(STEER) + "\n"]),
        init(),
        answer("all the customers"),
        AwaitSteers(1),
        result("all the customers"),
        Echo(0),
    )
    assert not outcome.finished
    assert not of_type(events, "answer")
    assert [(e["source"], e["error"]) for e in of_type(events, "error")] == [
        ("sdk", "the session ended before the step reading a steering message did")
    ]
    assert len(of_type(events, "steer_delivered")) == 1


MAX_TURNS = load_config("full").max_turns["query"]


async def test_the_steps_of_a_turn_keep_to_its_turn_limit_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A step after the first gets the CLI's whole turn limit afresh: the run stops it once
    the steps together pass the turn's limit, as the CLI would have (spec decision 13)."""
    outcome, client, events = await run_steered(
        monkeypatch,
        tmp_path,
        lines_of([json.dumps(STEER) + "\n"]),
        init(),
        answer("all the customers"),
        AwaitSteers(1),
        result("all the customers", num_turns=MAX_TURNS - 1),
        init(),
        Echo(0),
        tool_call("q1"),
        tool_result("q1"),
        tool_call("q2"),
        tool_result("q2"),
        answer("never read"),
        result("never read", num_turns=3),
    )
    assert not outcome.finished
    # Stopped once q1's answer took the turn past its limit, as the CLI stops a step.
    assert client.disconnected
    assert "q2" not in [e.get("tool_use_id") for e in of_type(events, "tool_start")]
    assert not of_type(events, "answer")
    [turn_result] = of_type(events, "result")
    assert (turn_result["subtype"], turn_result["terminal_reason"], turn_result["num_turns"]) == (
        "error_max_turns",
        "max_turns",
        MAX_TURNS + 1,
    )


async def test_a_message_after_the_last_turn_is_left_unread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A step that used the turn's last turn ends it: a message still waiting gets no step."""
    outcome, _, events = await run_steered(
        monkeypatch,
        tmp_path,
        lines_of([json.dumps(STEER) + "\n"]),
        init(),
        answer("all the customers"),
        AwaitSteers(1),
        result("all the customers", num_turns=MAX_TURNS),
        Echo(0),
        result("never read"),
    )
    assert outcome.finished and outcome.answer == "all the customers"
    assert not of_type(events, "steer_delivered")
    assert [e["error"] for e in of_type(events, "error")] == [
        f"message {STEER['id']!r} was not read: the turn ended"
    ]
