"""A traced DAB run whose export fails (ticket 112): the version header is still the first event
(CODING_STANDARDS.md), the failure one `error` event, and the run goes on to its result. One whose
export works records the run's end before the export waits (ticket 122). The DAB
checkout is built as tests/acceptance/test_observability_evals.py builds it."""

import json
import shutil
import sqlite3
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage

from data_agent import trace_relay, tracing
from data_agent.run_config import load_config
from data_agent.trace_relay import RelayReport
from data_agent.tracing import LANGFUSE_ENV
from evals import dab

FAKE_DAB = Path(__file__).resolve().parent / "fixtures" / "fake_dab"


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


ENV = {"DATA_AGENT_API_KEY": "sk-test"} | dict(
    zip(LANGFUSE_ENV, ("pk-lf-1", "sk-lf-1", "http://127.0.0.1:9"), strict=True)
)


async def answering(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
    del prompt, options
    yield ResultMessage(
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
    monkeypatch.setattr(dab, "query", answering)
    monkeypatch.setattr(dab, "git_commit", lambda: "c0ffee")
    monkeypatch.setattr(trace_relay, "TraceRelay", RefusingRelay)
    root = make_dab(tmp_path / "dab")
    query = dab.load_query(root, "toyset", "1", hints=False)
    await dab.run_query(
        root, query, load_config("no-context"), tmp_path / "out", 1, ENV, trace=True
    )
    [events_file] = (tmp_path / "out").rglob("events.jsonl")
    types = [json.loads(line)["type"] for line in events_file.read_text().splitlines()]
    assert types[0] == "version"
    assert types[-1] == "result"
    assert types.count("error") == 1 and "trace" not in types


class SlowRelay:
    """A relay that takes a while to drain, as the real one waits up to its timeout."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    def start(self) -> str:
        return "http://127.0.0.1:9/v1/traces"

    def stop(self) -> RelayReport:
        time.sleep(0.05)
        return RelayReport(1, 1, ())


async def test_the_result_records_the_runs_end_before_the_export_waits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ticket 122: `result.ended_ts` is taken before the export, so it precedes its events."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    monkeypatch.setattr(dab, "query", answering)
    monkeypatch.setattr(dab, "git_commit", lambda: "c0ffee")
    monkeypatch.setattr(trace_relay, "TraceRelay", SlowRelay)
    monkeypatch.setattr(tracing, "parent_exporter", lambda _: InMemorySpanExporter())
    monkeypatch.setattr(tracing, "project_id", lambda _: None)
    root = make_dab(tmp_path / "dab")
    query = dab.load_query(root, "toyset", "1", hints=False)
    await dab.run_query(
        root, query, load_config("no-context"), tmp_path / "out", 1, ENV, trace=True
    )
    [events_file] = (tmp_path / "out").rglob("events.jsonl")
    events = [json.loads(line) for line in events_file.read_text().splitlines()]
    *_, trace, result = events
    assert (trace["type"], result["type"]) == ("trace", "result")
    ended = datetime.fromisoformat(result["ended_ts"])
    assert ended < datetime.fromisoformat(trace["ts"]) < datetime.fromisoformat(result["ts"])
