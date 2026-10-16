"""How a run ends when the SDK reports failure: exit class, terminal event, diagnostics.

The SDK's `query()` is replaced by a scripted stream, so these run without a model; the happy path
is covered by seam A with a real model.
"""

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultError,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from data_agent import agent
from data_agent.agent import Outcome, Task
from data_agent.run_config import RunConfig, load_config

ENV = {"DATA_AGENT_API_KEY": "sk-test-secret", "DATA_AGENT_BASE_URL": "https://llm.example/x"}


def result(**overrides: Any) -> ResultMessage:
    fields: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "session_id": "s",
        "usage": {"input_tokens": 3, "cache_read_input_tokens": 5, "output_tokens": 7},
        "total_cost_usd": 0.01,
        "result": "42",
    } | overrides
    return ResultMessage(**fields)


def scripted(
    *messages: Any, then: BaseException | None = None
) -> Callable[..., AsyncIterator[Any]]:
    async def fake_query(**_: Any) -> AsyncIterator[Any]:
        for message in messages:
            yield message
        if then is not None:
            raise then

    return fake_query


async def run_with(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake: Callable[..., AsyncIterator[Any]],
    env: dict[str, str] = ENV,
    config: RunConfig | None = None,
) -> tuple[Outcome, list[dict[str, Any]]]:
    monkeypatch.setattr(agent, "query", fake)
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    task = Task(text="q", type="query", workspace=tmp_path, duckdb=db)
    outcome = await agent.run(task, config or load_config("full"), tmp_path / "events", env)
    lines = outcome.events_path.read_text().splitlines()
    return outcome, [json.loads(line) for line in lines]


async def test_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    outcome, events = await run_with(monkeypatch, tmp_path, scripted(result()))
    assert (outcome.finished, outcome.infra_error, outcome.answer) == (True, False, "42")
    assert events[-1]["type"] == "result"
    assert events[-1]["uncached_input_tokens"] == 3
    assert events[-1]["cached_input_tokens"] == 5


MILLION_TOKENS = {
    "input_tokens": 600_000,
    "cache_creation_input_tokens": 400_000,
    "cache_read_input_tokens": 2_000_000,
    "output_tokens": 1_000_000,
}


async def test_cost_is_priced_from_tokens_at_the_models_upper_list_price(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """deepseek-flash at its upper prices: $0.30 input, $0.006 cache read, $1.20 output per 1M."""
    final = result(usage=MILLION_TOKENS, total_cost_usd=9.9)
    _, events = await run_with(monkeypatch, tmp_path, scripted(final))
    assert events[-1]["cost_usd"] == pytest.approx(0.30 + 2 * 0.006 + 1.20)
    assert events[-1]["sdk_cost_usd"] == 9.9
    assert events[-1]["cost_basis"] == "price_table"


async def test_cost_of_a_model_without_a_price_is_the_sdk_figure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    final = result(usage=MILLION_TOKENS, total_cost_usd=9.9)
    env = ENV | {"DATA_AGENT_MODEL": "some-unpriced-model"}
    _, events = await run_with(monkeypatch, tmp_path, scripted(final), env)
    assert (events[-1]["cost_usd"], events[-1]["sdk_cost_usd"]) == (9.9, 9.9)
    assert events[-1]["cost_basis"] == "sdk"


async def test_turn_limit_is_an_unfinished_agent_with_its_errors_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    final = result(
        subtype="error_max_turns",
        is_error=True,
        terminal_reason="max_turns",
        result=None,
        errors=["Reached maximum number of turns (50)"],
    )
    then = ResultError("max turns", data={"subtype": "error_max_turns"})
    outcome, events = await run_with(monkeypatch, tmp_path, scripted(final, then=then))
    assert (outcome.finished, outcome.infra_error) == (False, False)
    assert outcome.errors == ["Reached maximum number of turns (50)"]
    assert events[-1]["type"] == "result"
    assert events[-1]["errors"] == ["Reached maximum number of turns (50)"]


async def test_api_error_without_http_status_is_infrastructure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Connection failures and timeouts end with terminal_reason api_error and no HTTP status."""
    final = result(
        is_error=True, terminal_reason="api_error", result="API Error: Connection error."
    )
    outcome, _ = await run_with(monkeypatch, tmp_path, scripted(final))
    assert outcome.infra_error


async def test_sdk_failure_outside_its_error_types_still_ends_the_event_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The SDK raises a plain Exception when the CLI never answers `initialize`."""
    then = Exception("Control request timeout: initialize")
    outcome, events = await run_with(monkeypatch, tmp_path, scripted(then=then))
    assert outcome.infra_error
    assert [e["type"] for e in events[-2:]] == ["error", "result"]
    assert "initialize" in events[-2]["error"]


async def test_error_text_is_masked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    then = Exception("401 from https://llm.example/x with key sk-test-secret")
    outcome, _ = await run_with(monkeypatch, tmp_path, scripted(then=then))
    text = outcome.events_path.read_text()
    assert "sk-test-secret" not in text
    assert "llm.example" not in text


async def test_tokens_and_cost_cover_the_subagents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`usage` counts the main thread only; `model_usage` covers the session, verifiers included."""
    model_usage = {
        "deepseek-flash": {
            "inputTokens": 600_000,
            "cacheCreationInputTokens": 400_000,
            "cacheReadInputTokens": 2_000_000,
            "outputTokens": 1_000_000,
            "webSearchRequests": 0,
            "costUSD": 9.9,
            "contextWindow": 0,
            "maxOutputTokens": 0,
        }
    }
    final = result(model_usage=model_usage, total_cost_usd=9.9)
    _, events = await run_with(monkeypatch, tmp_path, scripted(final))
    assert events[-1]["uncached_input_tokens"] == 1_000_000
    assert events[-1]["cached_input_tokens"] == 2_000_000
    assert events[-1]["output_tokens"] == 1_000_000
    assert events[-1]["cost_usd"] == pytest.approx(0.30 + 2 * 0.006 + 1.20)


def usage(input_tokens: int = 0, output_tokens: int = 0) -> dict[str, int]:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


def stream_event(event: dict[str, Any], parent: str | None = None) -> StreamEvent:
    return StreamEvent(uuid="u", session_id="s", event=event, parent_tool_use_id=parent)


def call(
    message_id: str,
    output_tokens: int,
    *,
    input_tokens: int = 0,
    tool: bool = True,
    text: str = "...",
) -> list[Any]:
    """One main-thread model call as the CLI streams it with partial messages: message_start
    (usage before any output), one message per content block (the same usage), message_delta
    (the final usage). deepseek-flash costs $0.30 input and $1.20 output per 1M tokens."""
    blocks: list[Any] = [TextBlock(text=text)]
    if tool:
        blocks.append(ToolUseBlock(id=f"toolu_{message_id}", name="Agent", input={}))
    start = usage(input_tokens)
    return [
        stream_event({"type": "message_start", "message": {"id": message_id, "usage": start}}),
        *(
            AssistantMessage(
                content=[block],
                model="deepseek-flash",
                usage=start,
                message_id=message_id,
            )
            for block in blocks
        ),
        stream_event(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use" if tool else "end_turn"},
                "usage": usage(input_tokens, output_tokens),
            }
        ),
    ]


TOOL_RESULT = UserMessage(content=[])
TRANSCRIPT = Path(__file__).parent / "fixtures" / "subagent_transcript.jsonl"
# The fixture is a real verifier transcript (CLI 2.1.301, scrubbed): 4 calls, 9678 uncached
# input, 26112 cache-read and 7201 output tokens.
TRANSCRIPT_USD = (9678 * 0.30 + 26112 * 0.006 + 7201 * 1.2) / 1e6


@dataclasses.dataclass
class SubagentStart:
    """In a scripted stream: the CLI calls the SubagentStart hooks for a new subagent."""

    agent_id: str = "a1"


@dataclasses.dataclass
class SubagentStop:
    """In a scripted stream: the CLI calls the SubagentStop hooks for a finished subagent."""

    transcript: Path
    agent_id: str = "a1"


async def call_hooks(options: Any, event: str, **fields: Any) -> None:
    hook_input = {"hook_event_name": event, "agent_type": "verifier", **fields}
    for matcher in options.hooks.get(event, []):
        for hook in matcher.hooks:
            await hook(hook_input, None, {"signal": None})


async def stop_subagent(options: Any, transcript: Path, agent_id: str = "a1") -> None:
    """What the CLI does when a subagent ends: call the SubagentStop hooks."""
    await call_hooks(
        options, "SubagentStop", agent_id=agent_id, agent_transcript_path=str(transcript)
    )


def capped_script(
    *steps: Any, closed: list[bool] | None = None
) -> Callable[..., AsyncIterator[Any]]:
    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        try:
            for step in steps:
                if isinstance(step, SubagentStart):
                    await call_hooks(options, "SubagentStart", agent_id=step.agent_id)
                elif isinstance(step, SubagentStop):
                    await stop_subagent(options, step.transcript, step.agent_id)
                else:
                    yield step
        finally:
            if closed is not None:
                closed.append(True)

    return fake_query


def budget(usd: float) -> RunConfig:
    return dataclasses.replace(load_config("full"), max_budget_usd=usd)


async def test_run_stops_after_the_main_call_whose_output_passes_the_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Output is billed from message_delta: the per-block messages carry the usage from the start
    of the response, with no output. Two calls of $0.60 pass the $1 cap; the SDK stream is closed
    before the tools of the second run or a third call is made."""
    closed: list[bool] = []
    fake = capped_script(
        *call("m1", 500_000),
        TOOL_RESULT,
        *call("m2", 500_000),
        UserMessage(content=[ToolResultBlock(tool_use_id="toolu_m2", content="never reached")]),
        *call("m3", 1, text="never reached"),
        result(),
        closed=closed,
    )
    outcome, events = await run_with(monkeypatch, tmp_path, fake)
    assert (outcome.finished, outcome.infra_error) == (False, False)
    assert closed == [True]
    assert "never reached" not in outcome.events_path.read_text()
    last = events[-1]
    assert last["type"] == "result"
    assert (last["subtype"], last["is_error"]) == ("error_max_budget_usd", True)
    assert last["terminal_reason"] == "budget_exhausted"
    assert last["cost_usd"] == pytest.approx(1.20)
    assert last["cost_basis"] == "price_table"
    assert last["output_tokens"] == 1_000_000
    assert last["num_turns"] == 2
    assert outcome.errors == last["errors"]
    assert len(outcome.errors) == 1


async def test_cap_is_checked_only_between_main_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A call whose input already passes the cap still runs to its end: every content block of
    it is recorded before the run stops."""
    fake = capped_script(*call("m1", 1_000, input_tokens=100_000, text="first block"), result())
    outcome, events = await run_with(monkeypatch, tmp_path, fake, config=budget(0.005))
    assert not outcome.finished
    assert [e["type"] for e in events if e["type"] in ("text", "tool_start")] == [
        "text",
        "tool_start",
    ]
    assert events[-1]["terminal_reason"] == "budget_exhausted"
    assert events[-1]["cost_usd"] == pytest.approx((100_000 * 0.30 + 1_000 * 1.2) / 1e6)


async def test_final_answer_that_passes_the_cap_still_ends_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only a further model call is stopped; the answer that crossed the cap is kept."""
    fake = capped_script(*call("m1", 1_000_000, tool=False, text="the answer"), result())
    outcome, events = await run_with(monkeypatch, tmp_path, fake)
    assert (outcome.finished, outcome.answer) == (True, "42")
    assert events[-1]["subtype"] == "success"


async def test_verifier_spend_is_read_from_its_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The verifier's calls are counted when it stops, from the transcript the CLI wrote; the run
    stops on the next message instead of making another main call."""
    fake = capped_script(
        *call("m1", 1_000),
        SubagentStop(TRANSCRIPT),
        TOOL_RESULT,
        *call("m2", 1, text="never reached"),
        result(),
    )
    outcome, events = await run_with(monkeypatch, tmp_path, fake, config=budget(0.01))
    assert not outcome.finished
    assert "never reached" not in outcome.events_path.read_text()
    last = events[-1]
    assert last["terminal_reason"] == "budget_exhausted"
    assert last["cost_usd"] == pytest.approx(1_000 * 1.2 / 1e6 + TRANSCRIPT_USD)
    assert (last["uncached_input_tokens"], last["cached_input_tokens"]) == (9678, 26112)
    assert last["output_tokens"] == 1_000 + 7201
    assert last["num_turns"] == 1


async def test_subagents_still_running_are_let_finish(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two verifiers run side by side; the first to stop passes the cap. The run stops only once
    the second has stopped too, so both are counted from their transcripts."""
    second = tmp_path / "agent-a2.jsonl"  # the same calls under other message ids
    second.write_text(TRANSCRIPT.read_text().replace('"message":{"id":"', '"message":{"id":"a2-'))
    fake = capped_script(
        *call("m1", 1_000),
        SubagentStart("a1"),
        SubagentStart("a2"),
        SubagentStop(TRANSCRIPT, "a1"),
        TOOL_RESULT,
        AssistantMessage(
            content=[TextBlock(text="second verifier still at work")],
            model="deepseek-flash",
            parent_tool_use_id="toolu_m1",
        ),
        SubagentStop(second, "a2"),
        TOOL_RESULT,
        *call("m2", 1, text="never reached"),
        result(),
    )
    outcome, events = await run_with(monkeypatch, tmp_path, fake, config=budget(0.01))
    text = outcome.events_path.read_text()
    assert "second verifier still at work" in text
    assert "never reached" not in text
    assert events[-1]["terminal_reason"] == "budget_exhausted"
    assert events[-1]["output_tokens"] == 1_000 + 2 * 7201


def test_transcript_usage_reads_the_clis_line_format() -> None:
    """Each content block of a call is its own line; the call's last line has its final usage.
    If the CLI changes the format, this fails rather than the cap counting nothing."""
    calls = agent.transcript_usage(TRANSCRIPT)
    assert len(calls) == 4
    assert tuple(map(sum, zip(*calls.values(), strict=True))) == (9678, 26112, 7201)


@pytest.mark.parametrize(
    "content",
    [
        "not json\n",
        '{"type": "assistant", "message": {"id": "x"}}\n',
        '{"type": "user", "message": {"role": "user"}}\n',
    ],
    ids=["not-json", "no-usage", "no-calls"],
)
def test_transcript_in_another_format_is_an_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "agent.jsonl"
    path.write_text(content)
    with pytest.raises(agent.TranscriptError):
        agent.transcript_usage(path)


def without_last_usage() -> tuple[str, str]:
    """The fixture split before the line with the last call's final usage, as the CLI may still
    be writing it when SubagentStop comes."""
    lines = TRANSCRIPT.read_text().splitlines(keepends=True)
    final = max(i for i, line in enumerate(lines) if '"stop_reason":"end_turn"' in line)
    return "".join(lines[:final]), "".join(lines[final:])


async def test_transcript_still_being_written_is_waited_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The subagent's last call gets its final usage line only after SubagentStop; the hook waits
    for it instead of counting that call without its output."""
    path = tmp_path / "agent.jsonl"
    head, tail = without_last_usage()
    path.write_text(head)

    async def finish_writing() -> None:
        await asyncio.sleep(0.05)
        with path.open("a") as f:
            f.write(tail)

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        for message in call("m1", 1_000):
            yield message
        writer = asyncio.create_task(finish_writing())
        await stop_subagent(options, path)
        await writer
        yield TOOL_RESULT
        yield result()

    _, events = await run_with(monkeypatch, tmp_path, fake_query, config=budget(0.01))
    assert events[-1]["terminal_reason"] == "budget_exhausted"
    assert events[-1]["output_tokens"] == 1_000 + 7201


async def test_transcript_never_finished_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(agent, "TRANSCRIPT_WAIT_S", 0.05)
    path = tmp_path / "agent.jsonl"
    path.write_text(without_last_usage()[0])
    fake = capped_script(*call("m1", 1_000), SubagentStop(path), TOOL_RESULT, result())
    _, events = await run_with(monkeypatch, tmp_path, fake)
    errors = [e for e in events if e["type"] == "error"]
    assert [e["source"] for e in errors] == ["spend"]
    assert "unfinished" in errors[0]["error"]


async def test_unreadable_transcript_is_recorded_and_streamed_usage_counts_instead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The verifier's calls the SDK streamed (input known, output not) are what is left to count;
    the failure is an event, never a silent zero."""
    verifier_call = AssistantMessage(
        content=[TextBlock(text="checking")],
        model="deepseek-flash",
        parent_tool_use_id="toolu_m1",
        usage=usage(20_000),
        message_id="s1",
    )
    missing = tmp_path / "gone.jsonl"
    fake = capped_script(
        *call("m1", 0),
        verifier_call,
        SubagentStop(missing),
        TOOL_RESULT,
        *call("m2", 1, text="never reached"),
        result(),
    )
    outcome, events = await run_with(monkeypatch, tmp_path, fake, config=budget(0.005))
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["source"] == "spend"
    assert str(missing) in errors[0]["error"]
    assert not outcome.finished
    assert events[-1]["terminal_reason"] == "budget_exhausted"
    assert events[-1]["cost_usd"] == pytest.approx(20_000 * 0.30 / 1e6)


async def test_unpriced_model_is_not_stopped_by_the_price_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without a price, the SDK's own cap (set to the configured value) is the only one."""
    fake = capped_script(*call("m1", 5_000_000), TOOL_RESULT, *call("m2", 5_000_000), result())
    env = ENV | {"DATA_AGENT_MODEL": "some-unpriced-model"}
    outcome, events = await run_with(monkeypatch, tmp_path, fake, env)
    assert outcome.finished
    assert events[-1]["cost_basis"] == "sdk"


async def sdk_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, model: str, budget: float
) -> float | None:
    """The `max_budget_usd` a run hands the SDK."""
    seen: list[Any] = []

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        seen.append(options)
        yield result()

    config = dataclasses.replace(load_config("full"), max_budget_usd=budget)
    env = ENV | {"DATA_AGENT_MODEL": model}
    await run_with(monkeypatch, tmp_path, fake_query, env, config)
    return seen[0].max_budget_usd


async def test_sdk_cap_of_a_priced_model_is_a_backstop_scaled_to_claude_rates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The CLI prices a model it does not know at $6.25 cache write, $0.50 cache read, $25 output
    per 1M. The backstop is the cap times the largest ratio of that to our price, so the SDK's
    figure cannot reach it while ours is under the cap: deepseek-flash's cache read 0.50/0.006."""
    flash = await sdk_cap(monkeypatch, tmp_path, "deepseek-flash", 1.0)
    assert flash == pytest.approx(0.50 / 0.006)
    pro = await sdk_cap(monkeypatch, tmp_path, "deepseek-v4-pro", 2.0)
    assert pro == pytest.approx(2.0 * 25 / 3.96)


async def test_sdk_cap_of_an_unpriced_model_is_the_configured_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert await sdk_cap(monkeypatch, tmp_path, "some-unpriced-model", 1.0) == 1.0
