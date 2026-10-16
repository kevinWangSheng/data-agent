"""How a run ends when the SDK reports failure: exit class, terminal event, diagnostics.

The SDK's `query()` is replaced by a scripted stream, so these run without a model; the happy path
is covered by seam A with a real model.
"""

import dataclasses
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import AssistantMessage, ResultError, ResultMessage, TextBlock, UserMessage

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


def turn(
    message_id: str, output_tokens: int, parent: str | None = None, text: str = "..."
) -> AssistantMessage:
    """One model call; deepseek-flash output costs $1.20 per 1M tokens."""
    return AssistantMessage(
        content=[TextBlock(text=text)],
        model="deepseek-flash",
        parent_tool_use_id=parent,
        usage={"input_tokens": 0, "output_tokens": output_tokens},
        message_id=message_id,
    )


TOOL_RESULT = UserMessage(content=[])


async def test_priced_model_stops_once_price_table_spend_passes_the_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """$0.60 on the main thread plus $0.60 in a verifier passes the $1 cap; the run stops before
    the next model call, so the SDK's own result never comes."""
    closed: list[bool] = []

    async def fake_query(**_: Any) -> AsyncIterator[Any]:
        try:
            for message in (
                turn("m1", 500_000),
                TOOL_RESULT,
                turn("s1", 500_000, parent="toolu_verifier"),
                TOOL_RESULT,
                turn("m2", 500_000, text="never reached"),
                result(),
            ):
                yield message
        finally:
            closed.append(True)

    outcome, events = await run_with(monkeypatch, tmp_path, fake_query)
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
    assert last["num_turns"] == 1
    assert outcome.errors == last["errors"]
    assert len(outcome.errors) == 1


async def test_a_model_call_split_into_several_messages_is_counted_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The CLI sends each content block of one response as its own message, all with the same
    id and usage: three blocks of $0.60 are one $0.60 call, under the $1 cap."""
    fake = scripted(turn("m1", 500_000), turn("m1", 500_000), turn("m1", 500_000), result())
    outcome, events = await run_with(monkeypatch, tmp_path, fake)
    assert outcome.finished
    assert events[-1]["terminal_reason"] is None


async def test_final_answer_that_passes_the_cap_still_ends_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only a further model call is stopped; the answer that crossed the cap is kept."""
    fake = scripted(turn("m1", 1_000_000, text="the answer"), result())
    outcome, events = await run_with(monkeypatch, tmp_path, fake)
    assert (outcome.finished, outcome.answer) == (True, "42")
    assert events[-1]["subtype"] == "success"


async def test_unpriced_model_is_not_stopped_by_the_price_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without a price, the SDK's own cap (set to the configured value) is the only one."""
    fake = scripted(turn("m1", 5_000_000), TOOL_RESULT, turn("m2", 5_000_000), result())
    env = ENV | {"DATA_AGENT_MODEL": "some-unpriced-model"}
    outcome, events = await run_with(monkeypatch, tmp_path, fake, env)
    assert outcome.finished
    assert events[-1]["cost_basis"] == "sdk"


async def test_cap_follows_the_run_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = dataclasses.replace(load_config("full"), max_budget_usd=0.005)
    fake = scripted(turn("m1", 5_000), TOOL_RESULT, turn("m2", 5_000), result())
    outcome, events = await run_with(monkeypatch, tmp_path, fake, config=config)
    assert not outcome.finished
    assert events[-1]["terminal_reason"] == "budget_exhausted"
    assert events[-1]["cost_usd"] == pytest.approx(0.006)


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
