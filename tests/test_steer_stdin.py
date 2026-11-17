"""Steering a running turn (ticket 92, .scratch/chat-ui/spec.md decision 9): a `--chat` run reads
stdin one JSON message per line (`{"id", "text"}`, tests/acceptance/test_chat_api.py contract item
6), sends each into the live `ClaudeSDKClient` as one more `query()`, as SignalPilot does
(claude_agent.py:355-408), and then writes a `steer_delivered` event; its `seq` is where in the
run it went in. The CLI session is a scripted fake client, so no model is called.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncGenerator, AsyncIterator, Iterable
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


class FakeClient:
    """A CLI session that plays `script` and keeps every prompt it was sent."""

    script: tuple[Any, ...] = ()
    last: "FakeClient | None" = None

    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.prompts: list[str] = []
        self.ended = asyncio.Event()
        self.disconnected = False
        FakeClient.last = self

    async def connect(self) -> None:
        pass

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)

    async def receive_response(self) -> AsyncIterator[Any]:
        for item in self.script:
            if isinstance(item, AwaitSteers):
                async with asyncio.timeout(5):
                    while len(self.prompts) < 1 + item.count:
                        await asyncio.sleep(0.001)
            elif isinstance(item, Ended):
                self.ended.set()
            else:
                yield item

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


def result() -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=2,
        session_id="sess-1",
        usage={"input_tokens": 3, "output_tokens": 7},
        total_cost_usd=0.01,
        result="the top 3",
    )


def make_turn(tmp_path: Path) -> tuple[Task, ConversationTurn]:
    conversation = tmp_path / "conv-20261117T000000Z-abcd1234"
    workspace = conversation / "workspace"
    workspace.mkdir(parents=True)
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    task = Task(text="rank the customers", type="query", workspace=workspace, duckdb=db)
    return task, ConversationTurn(conversation, 1)


async def lines_of(lines: Iterable[str]) -> AsyncGenerator[str]:
    """stdin with these lines, then closed."""
    for line in lines:
        yield line


async def run_steered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    steering: AsyncGenerator[str],
    *script: Any,
) -> tuple[Outcome, FakeClient, list[dict[str, Any]]]:
    monkeypatch.setattr(FakeClient, "script", script)
    monkeypatch.setattr(agent, "ClaudeSDKClient", FakeClient)
    task, turn = make_turn(tmp_path)
    async with asyncio.timeout(10):
        outcome = await agent.run(
            task, load_config("full"), tmp_path / "events", ENV, turn=turn, steering=steering
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
        tool_call("q2"),
        tool_result("q2"),
        result(),
    )
    assert outcome.finished
    assert client.prompts[1:] == [STEER["text"]]
    [delivered] = of_type(events, "steer_delivered")
    assert (delivered["id"], delivered["text"]) == (STEER["id"], STEER["text"])
    # Sent while the first query was running: after its start, before its end.
    assert seq_of(events, "tool_start", "q1") < delivered["seq"] < seq_of(events, "tool_end", "q1")
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
        lines_of([*bad, "\n", json.dumps(STEER) + "\n"]),
        init(),
        tool_call("q1"),
        AwaitSteers(1),
        tool_result("q1"),
        result(),
    )
    assert outcome.finished
    assert client.prompts[1:] == [STEER["text"]]
    assert [(e["id"], e["text"]) for e in of_type(events, "steer_delivered")] == [
        (STEER["id"], STEER["text"])
    ]
    errors = of_type(events, "error")
    assert [e["source"] for e in errors] == ["steer"] * len(bad)
    # Numbered by stdin line; the blank line 6 is ignored without an event.
    assert [e["error"].split(" skipped")[0] for e in errors] == [
        f"stdin line {n}" for n in range(1, 6)
    ]


async def test_stdin_closed_from_the_start_leaves_the_turn_as_it_is(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcome, client, events = await run_steered(
        monkeypatch, tmp_path, lines_of([]), init(), tool_call("q1"), tool_result("q1"), result()
    )
    assert outcome.finished
    assert client.prompts == client.prompts[:1]
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
        async def query(self, prompt: str) -> None:
            await super().query(prompt)  # counts the attempt
            if len(self.prompts) > 1:
                raise ConnectionError("the CLI has gone")

    async def two_then_open() -> AsyncGenerator[str]:
        yield json.dumps(STEER) + "\n"
        yield json.dumps({**STEER, "id": "m2"}) + "\n"
        await asyncio.Event().wait()

    script = (init(), tool_call("q1"), AwaitSteers(1), tool_result("q1"), result())
    monkeypatch.setattr(GoneClient, "script", script)
    monkeypatch.setattr(agent, "ClaudeSDKClient", GoneClient)
    task, turn = make_turn(tmp_path)
    async with asyncio.timeout(10):
        outcome = await agent.run(
            task, load_config("full"), tmp_path / "e", ENV, turn=turn, steering=two_then_open()
        )
    events = [json.loads(line) for line in outcome.events_path.read_text().splitlines()]
    assert outcome.finished
    assert not of_type(events, "steer_delivered")
    assert FakeClient.last is not None and len(FakeClient.last.prompts) == 2
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
    monkeypatch.setattr(FakeClient, "script", (init(), tool_call("q1"), AwaitSteers(1)))
    monkeypatch.setattr(agent, "ClaudeSDKClient", FakeClient)
    task, turn = make_turn(tmp_path)
    deadline = time.monotonic() + agent.DEADLINE_MARGIN_S + 0.3
    async with asyncio.timeout(10):
        outcome = await agent.run(
            task,
            load_config("full"),
            tmp_path / "e",
            ENV,
            deadline,
            turn=turn,
            steering=open_stdin(),
        )
    events = [json.loads(line) for line in outcome.events_path.read_text().splitlines()]
    assert events[-1]["terminal_reason"] == "deadline"
    assert FakeClient.last is not None and FakeClient.last.disconnected
