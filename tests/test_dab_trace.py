"""A traced DAB run whose export fails (ticket 112): the version header is still the first event
(CODING_STANDARDS.md), the failure one `error` event, and the run goes on to its result. A traced
run's export takes the session's events, an untraced run records into its event log (ticket 134).
The DAB checkout is built as tests/acceptance/test_observability_evals.py builds it."""

import json
import shutil
import sqlite3
from collections.abc import AsyncIterator, Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any, ClassVar

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from data_agent import trace_relay
from data_agent.events import EventLog
from data_agent.run_config import load_config
from data_agent.tracing import LANGFUSE_ENV, CliTrace
from evals import dab

FAKE_DAB = Path(__file__).resolve().parent / "fixtures" / "fake_dab"
TRACED_ENV = {"DATA_AGENT_API_KEY": "sk-test"} | dict(
    zip(LANGFUSE_ENV, ("pk-lf-1", "sk-lf-1", "http://127.0.0.1:9"), strict=True)
)


def make_dab(root: Path) -> Path:
    shutil.copytree(FAKE_DAB, root)
    dataset = root / "query_toyset"
    (dataset / "query_dataset").mkdir(parents=True)
    with sqlite3.connect(dataset / "query_dataset" / "prices.db") as conn:
        conn.execute("CREATE TABLE prices (ticker TEXT, close REAL)")
    (dataset / "dump").mkdir()
    (dataset / "dump" / "articles.bson").write_bytes(b"x" * 10)
    clients = {
        "prices_db": {"db_type": "sqlite", "db_path": "query_dataset/prices.db"},
        "articles_db": {"db_type": "mongo", "db_name": "articles", "dump_folder": "dump"},
    }
    (dataset / "db_config.yaml").write_text(json.dumps({"db_clients": clients}))
    (dataset / "db_description.txt").write_text("prices_db holds daily closes.\n")
    query = dataset / "query1"
    query.mkdir()
    (query / "query.json").write_text(json.dumps("Which ticker closed highest?"))
    (query / "ground_truth.csv").write_text("T002")
    (query / "validate.py").write_text("GT = 'T002'\n")
    return root


def result_message() -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
        usage={"input_tokens": 1, "output_tokens": 1},
        total_cost_usd=0.0,
        result="T002",
    )


class RefusingRelay:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    def start(self) -> str:
        raise OSError("no port")

    def stop(self) -> Any:
        raise AssertionError("never started")


async def test_a_failed_export_leaves_the_version_header_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        del prompt, options
        yield result_message()

    monkeypatch.setattr(dab, "query", fake_query)
    monkeypatch.setattr(dab, "git_commit", lambda: "c0ffee")
    monkeypatch.setattr(trace_relay, "TraceRelay", RefusingRelay)
    root = make_dab(tmp_path / "dab")
    query = dab.load_query(root, "toyset", "1", hints=False)
    await dab.run_query(
        root, query, load_config("no-context"), tmp_path / "out", 1, TRACED_ENV, trace=True
    )
    [events_file] = (tmp_path / "out").rglob("events.jsonl")
    types = [json.loads(line)["type"] for line in events_file.read_text().splitlines()]
    assert types[0] == "version"
    assert types[-1] == "result"
    assert types.count("error") == 1 and "trace" not in types


def one_step_session() -> Callable[..., AsyncIterator[Any]]:
    """A session whose one assistant message says something and calls a tool."""

    async def fake_query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        del prompt, options
        yield AssistantMessage(
            content=[
                TextBlock(text="Looking at prices first."),
                ToolUseBlock(id="call-1", name="mcp__dab__query_db", input={"sql": "SELECT 1"}),
            ],
            model="m",
        )
        yield result_message()

    return fake_query


class FakeExport:
    """Stands in for `RunExport`: keeps the log `observing` wraps and the events its emitter
    gets, which the real one hands to `observe`."""

    made: ClassVar[list["FakeExport"]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.wrapped: Any = None
        self.observed: list[tuple[str, dict[str, Any]]] = []
        FakeExport.made.append(self)

    def __enter__(self) -> "FakeExport":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def start(self, facts: Any) -> CliTrace:
        del facts
        return CliTrace(endpoint="http://127.0.0.1:9", tier="full")

    def context_env(self) -> dict[str, str]:
        return {}

    def active(self) -> Any:
        return nullcontext()

    def observing(self, log: Any) -> Any:
        self.wrapped = log
        observed = self.observed

        class Observed:
            def emit(self, kind: str, **fields: Any) -> None:
                log.emit(kind, **fields)
                observed.append((kind, fields))

        return Observed()

    def finish(self, fields: Any, answer: Any) -> list[tuple[str, dict[str, Any]]]:
        del fields, answer
        return []


async def test_a_traced_run_gives_the_export_tool_inputs_and_texts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    FakeExport.made = []
    monkeypatch.setattr(dab, "query", one_step_session())
    monkeypatch.setattr(dab, "git_commit", lambda: "c0ffee")
    monkeypatch.setattr(dab, "RunExport", FakeExport)
    root = make_dab(tmp_path / "dab")
    query = dab.load_query(root, "toyset", "1", hints=False)
    await dab.run_query(
        root, query, load_config("no-context"), tmp_path / "out", 1, TRACED_ENV, trace=True
    )
    [export] = FakeExport.made
    assert isinstance(export.wrapped, EventLog)
    observed = dict(export.observed)
    assert observed["text"]["text"] == "Looking at prices first."
    assert observed["tool_start"]["tool_use_id"] == "call-1"
    assert observed["tool_start"]["input"] == {"sql": "SELECT 1"}


async def test_an_untraced_run_records_messages_into_its_event_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorders: list[Any] = []
    record = dab.record_message

    def spy(log: Any, message: Any, tools: dict[str, str]) -> None:
        recorders.append(log)
        record(log, message, tools)

    monkeypatch.setattr(dab, "query", one_step_session())
    monkeypatch.setattr(dab, "git_commit", lambda: "c0ffee")
    monkeypatch.setattr(dab, "record_message", spy)
    root = make_dab(tmp_path / "dab")
    query = dab.load_query(root, "toyset", "1", hints=False)
    await dab.run_query(
        root, query, load_config("no-context"), tmp_path / "out", 1, {"DATA_AGENT_API_KEY": "k"}
    )
    assert recorders and all(type(log) is EventLog for log in recorders)
