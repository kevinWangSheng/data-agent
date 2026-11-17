"""A conversation turn (ticket 90, .scratch/chat-ui/spec.md decisions 14-18): `data-agent run
--chat` resumes the previous turn's session, keeps its record in the conversation's own
`CLAUDE_CONFIG_DIR`, shares the conversation's working directory, and records the conversation and
turn in its event file. The SDK's session is replaced by a scripted stream, so no model is called.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, SystemMessage

from data_agent import agent, cli
from data_agent.agent import ConversationTurn, ModelSettings, Outcome, Task
from data_agent.run_config import load_config

ENV = {"DATA_AGENT_API_KEY": "sk-test-secret"}
SETTINGS = ModelSettings.from_env(ENV)


def make_conversation(tmp_path: Path) -> tuple[Path, Task]:
    """A conversation directory as the chat backend lays it out, and a query task in its
    workspace."""
    conversation = tmp_path / "conv-20261117T000000Z-abcd1234"
    workspace = conversation / "workspace"
    workspace.mkdir(parents=True)
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    return conversation, Task(text="q", type="query", workspace=workspace, duckdb=db)


def options_of(task: Task, turn: ConversationTurn | None) -> ClaudeAgentOptions:
    return agent.build_options(
        task, load_config("full"), SETTINGS, [], lambda *_, **__: None, turn=turn
    )


def test_first_turn_starts_a_session_in_the_conversation_config_dir(tmp_path: Path) -> None:
    conversation, task = make_conversation(tmp_path)
    options = options_of(task, ConversationTurn(conversation, 1))
    assert options.resume is None
    assert options.env["CLAUDE_CONFIG_DIR"] == str((conversation / "claude-config").resolve())
    assert options.cwd == str(task.workspace.resolve())


def test_later_turn_resumes_the_previous_session_in_the_same_place(tmp_path: Path) -> None:
    conversation, task = make_conversation(tmp_path)
    first = options_of(task, ConversationTurn(conversation, 1))
    second = options_of(task, ConversationTurn(conversation, 2, resume="sess-1"))
    assert second.resume == "sess-1"
    assert second.env["CLAUDE_CONFIG_DIR"] == first.env["CLAUDE_CONFIG_DIR"]
    assert second.cwd == first.cwd


def test_single_run_has_no_conversation_options(tmp_path: Path) -> None:
    _, task = make_conversation(tmp_path)
    options = options_of(task, None)
    assert options.resume is None
    assert "CLAUDE_CONFIG_DIR" not in options.env


def result(**overrides: Any) -> ResultMessage:
    fields: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "session_id": "sess-2",
        "usage": {"input_tokens": 3, "output_tokens": 7},
        "total_cost_usd": 0.01,
        "result": "42",
    } | overrides
    return ResultMessage(**fields)


def init(session_id: str) -> SystemMessage:
    return SystemMessage(
        subtype="init",
        data={"model": "m", "tools": [], "mcp_servers": [], "session_id": session_id},
    )


async def run_turn(
    monkeypatch: pytest.MonkeyPatch,
    task: Task,
    turn: ConversationTurn | None,
    *messages: Any,
    prompts: list[str] | None = None,
) -> tuple[ClaudeAgentOptions, list[dict[str, Any]]]:
    """`agent.run` on a scripted session: the options it started the session with, and its
    events. The prompt it sent goes to `prompts`, when given."""
    seen: list[ClaudeAgentOptions] = []

    async def fake_query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        seen.append(options)
        if prompts is not None:
            prompts.append(prompt)
        for message in messages:
            yield message

    monkeypatch.setattr(agent, "query", fake_query)
    events_dir = task.workspace.parent / "events"
    outcome = await agent.run(task, load_config("full"), events_dir, ENV, turn=turn)
    events = [json.loads(line) for line in outcome.events_path.read_text().splitlines()]
    [options] = seen
    return options, events


def of_type(events: list[dict[str, Any]], type_: str) -> dict[str, Any]:
    [event] = [e for e in events if e["type"] == type_]
    return event


async def test_turn_event_file_names_its_conversation_turn_and_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conversation, task = make_conversation(tmp_path)
    turn = ConversationTurn(conversation, 2, resume="sess-1")
    options, events = await run_turn(
        monkeypatch, task, turn, init("sess-1"), result(session_id="sess-1")
    )
    assert options.resume == "sess-1"
    assert options.env["CLAUDE_CONFIG_DIR"] == str((conversation / "claude-config").resolve())
    assert (conversation / "claude-config").is_dir()
    task_event = of_type(events, "task")
    assert (task_event["conversation"], task_event["turn"]) == (conversation.name, 2)
    assert of_type(events, "status")["session_id"] == "sess-1"
    assert of_type(events, "result")["session_id"] == "sess-1"


async def test_single_run_event_file_names_no_conversation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, task = make_conversation(tmp_path)
    options, events = await run_turn(
        monkeypatch, task, None, init("sess-9"), result(session_id="sess-9")
    )
    assert options.resume is None
    assert "CLAUDE_CONFIG_DIR" not in options.env
    assert not {"conversation", "turn"} & of_type(events, "task").keys()


async def test_query_is_one_turn_on_a_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec decision 15: the session runs on a `ClaudeSDKClient`. The prompt is sent once
    connected; the stream ends with the turn's result, and the client is disconnected then."""
    calls: list[Any] = []
    later = result(result="never read")

    class FakeClient:
        def __init__(self, options: ClaudeAgentOptions) -> None:
            calls.append(("options", options.model))

        async def connect(self) -> None:
            calls.append("connect")

        async def query(self, prompt: str) -> None:
            calls.append(("query", prompt))

        async def receive_response(self) -> AsyncIterator[Any]:
            yield init("s")
            yield result()
            yield later

        async def disconnect(self) -> None:
            calls.append("disconnect")

    monkeypatch.setattr(agent, "ClaudeSDKClient", FakeClient)
    messages = [m async for m in agent.query(prompt="q", options=ClaudeAgentOptions(model="m"))]
    assert [type(m) for m in messages] == [SystemMessage, ResultMessage]
    assert later not in messages
    assert calls == [("options", "m"), "connect", ("query", "q"), "disconnect"]


# Spec decision 18, spend: per turn (decision 13). On resume the CLI restores the session's cost
# state, the last `cost-state` entry of the session's record, so the result of every later turn
# reports the session's totals since its first turn (CLI 2.1.301, read from its bundle). The
# turn's result event counts only its own share.

MODEL = "deepseek-flash"


def usage(uncached: int, cached: int, output: int, cost: float) -> dict[str, Any]:
    return {
        "inputTokens": uncached,
        "cacheCreationInputTokens": 0,
        "cacheReadInputTokens": cached,
        "outputTokens": output,
        "webSearchRequests": 0,
        "costUSD": cost,
        "contextWindow": 1,
        "maxOutputTokens": 1,
    }


def write_record(conversation: Path, session_id: str, *entries: dict[str, Any]) -> None:
    """The session's record as the CLI keeps it: `projects/<cwd name>/<session id>.jsonl`."""
    record = conversation / "claude-config" / "projects" / "-some-cwd" / f"{session_id}.jsonl"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("".join(json.dumps(entry) + "\n" for entry in entries))


def cost_state(
    session_id: str, uncached: int, cached: int, output: int, cost: float
) -> dict[str, Any]:
    return {
        "type": "cost-state",
        "sessionId": session_id,
        "totalCostUSD": cost,
        "modelUsage": {MODEL: usage(uncached, cached, output, cost)},
    }


def tokens_and_sdk_cost(event: dict[str, Any]) -> tuple[int, int, int, float]:
    return (
        event["uncached_input_tokens"],
        event["cached_input_tokens"],
        event["output_tokens"],
        round(event["sdk_cost_usd"], 6),
    )


def cumulative_result() -> ResultMessage:
    """Turn 2's result: the session's totals over both turns."""
    return result(
        session_id="sess-1", total_cost_usd=0.52, model_usage={MODEL: usage(130, 50, 27, 0.52)}
    )


async def test_resumed_turn_counts_only_its_own_spend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conversation, task = make_conversation(tmp_path)
    write_record(
        conversation,
        "sess-1",
        {"type": "user", "message": {"role": "user", "content": "q"}},
        cost_state("sess-1", 50, 10, 5, 0.25),
        cost_state("other", 999, 999, 999, 9.0),
        cost_state("sess-1", 100, 40, 20, 0.5),
    )
    turn = ConversationTurn(conversation, 2, resume="sess-1")
    _, events = await run_turn(monkeypatch, task, turn, cumulative_result())
    turn_result = of_type(events, "result")
    assert tokens_and_sdk_cost(turn_result) == (30, 10, 7, 0.02)
    assert turn_result["restored_sdk_cost_usd"] == 0.5


async def test_first_turn_and_single_run_spend_is_the_result_as_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conversation, task = make_conversation(tmp_path)
    write_record(conversation, "sess-1", cost_state("sess-1", 100, 40, 20, 0.5))
    for turn in (ConversationTurn(conversation, 1), None):
        _, events = await run_turn(monkeypatch, task, turn, cumulative_result())
        given = of_type(events, "result")
        assert tokens_and_sdk_cost(given) == (130, 50, 27, 0.52)
        assert "restored_sdk_cost_usd" not in given


async def test_result_below_the_restored_state_is_kept_and_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A result smaller than the cost state it would have restored means the CLI did not restore
    it: the result is kept as given and the mismatch is an error event, not a negative spend."""
    conversation, task = make_conversation(tmp_path)
    write_record(conversation, "sess-1", cost_state("sess-1", 500, 40, 20, 0.5))
    turn = ConversationTurn(conversation, 2, resume="sess-1")
    _, events = await run_turn(monkeypatch, task, turn, cumulative_result())
    assert tokens_and_sdk_cost(of_type(events, "result")) == (130, 50, 27, 0.52)
    assert "restored_sdk_cost_usd" not in of_type(events, "result")
    assert of_type(events, "error")["source"] == "spend"


async def test_a_session_id_is_not_a_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`--resume` names a session; it reaches no record outside the session's own file."""
    conversation, task = make_conversation(tmp_path)
    write_record(conversation, "sess-1", cost_state("sess-1", 100, 40, 20, 0.5))
    for resume in ("*", "../-some-cwd/sess-1"):
        turn = ConversationTurn(conversation, 2, resume=resume)
        _, events = await run_turn(monkeypatch, task, turn, cumulative_result())
        assert tokens_and_sdk_cost(of_type(events, "result")) == (130, 50, 27, 0.52)


# Spec decision 18, turn budget: per turn (decision 13). The CLI counts the turns of each prompt
# from 1, a resumed one too, so every turn of a conversation gets the run config's whole limit.


async def test_resumed_turn_gets_the_whole_turn_limit_and_its_own_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conversation, task = make_conversation(tmp_path)
    limit = load_config("full").max_turns["query"]
    prompts: list[str] = []
    follow_up = Task(
        text="And in 2024?", type="query", workspace=task.workspace, duckdb=task.duckdb
    )
    turn = ConversationTurn(conversation, 3, resume="sess-1")
    options, _ = await run_turn(monkeypatch, follow_up, turn, result(), prompts=prompts)
    assert options.max_turns == limit
    [prompt] = prompts
    assert f"Turn limit: {limit}." in prompt
    assert "<task>\nAnd in 2024?\n</task>" in prompt


# `data-agent run` arguments of a turn (tests/acceptance/test_chat_api.py docstring, item 5).

CONVERSATION = "conv-20261117T000000Z-abcd1234"


def cli_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *extra: str, task_type: str = "query"
) -> tuple[int | str | None, list[ConversationTurn | None]]:
    """`data-agent run` in-process with `agent.run` replaced: its exit code, and the turn each
    run was given. `{conv}` in `extra` is the conversation's directory."""
    conversation, task = make_conversation(tmp_path)
    assert task.duckdb is not None
    seen: list[ConversationTurn | None] = []

    async def fake_run(*_: Any, turn: ConversationTurn | None = None, **__: Any) -> Outcome:
        seen.append(turn)
        return Outcome("", True, False, [], tmp_path / "events.jsonl")

    monkeypatch.setattr(cli, "run", fake_run)
    args = [
        *("run", "--task", "q", "--type", task_type, "--config", "full"),
        *("--workspace", str(task.workspace), "--duckdb", str(task.duckdb)),
        *("--events-dir", str(tmp_path / "events")),
        *(part.replace("{conv}", str(conversation)) for part in extra),
    ]
    with pytest.raises(SystemExit) as exit_:
        cli.main(args)
    return exit_.value.code, seen


def test_cli_first_turn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    code, [turn] = cli_run(
        monkeypatch, tmp_path, "--chat", "--conversation-dir", "{conv}", "--turn", "1"
    )
    assert code == 0
    assert turn is not None
    assert (turn.conversation, turn.number, turn.resume) == (CONVERSATION, 1, None)


def test_cli_later_turn_resumes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    code, [turn] = cli_run(
        monkeypatch,
        tmp_path,
        *("--chat", "--conversation-dir", "{conv}", "--turn", "2", "--resume", "sess-1"),
    )
    assert code == 0
    assert turn is not None
    assert (turn.conversation, turn.number, turn.resume) == (CONVERSATION, 2, "sess-1")


def test_cli_single_run_is_no_turn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert cli_run(monkeypatch, tmp_path) == (0, [None])


@pytest.mark.parametrize(
    ("extra", "task_type", "message"),
    [
        (("--conversation-dir", "{conv}", "--turn", "1"), "query", "--chat"),
        (("--resume", "sess-1"), "query", "--chat"),
        (("--chat", "--turn", "1"), "query", "--conversation-dir"),
        (("--chat", "--conversation-dir", "{conv}"), "query", "--turn"),
        (("--chat", "--conversation-dir", "{conv}", "--turn", "0"), "query", "--turn"),
        (("--chat", "--conversation-dir", "{conv}/missing", "--turn", "1"), "query", "missing"),
        # Spec decision 18, the verifiers' dispatch count and Stop state: the verifiers check
        # dbt work only, and a conversation only queries (decision 3), so no conversation ever
        # holds that state.
        (("--chat", "--conversation-dir", "{conv}", "--turn", "1"), "dbt", "--type query"),
    ],
)
def test_cli_rejects_a_turn_out_of_place(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extra: tuple[str, ...],
    task_type: str,
    message: str,
) -> None:
    assert cli_run(monkeypatch, tmp_path, *extra, task_type=task_type) == (2, [])
    assert message in capsys.readouterr().err
