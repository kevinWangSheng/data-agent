"""How a run ends when the SDK reports failure: exit class, terminal event, diagnostics.

The SDK's `query()` is replaced by a scripted stream, so these run without a model; the happy path
is covered by seam A with a real model.
"""

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import ResultError, ResultMessage

from data_agent import agent
from data_agent.agent import Outcome, Task
from data_agent.run_config import load_config

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
) -> tuple[Outcome, list[dict[str, Any]]]:
    monkeypatch.setattr(agent, "query", fake)
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    task = Task(text="q", type="query", workspace=tmp_path, duckdb=db)
    outcome = await agent.run(task, load_config("full"), tmp_path / "events", env)
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
