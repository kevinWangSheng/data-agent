"""A traced DAB run whose export fails (ticket 112): the version header is still the first event
(CODING_STANDARDS.md), the failure one `error` event, and the run goes on to its result. The DAB
checkout is built as tests/acceptance/test_observability_evals.py builds it."""

import json
import shutil
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage

from data_agent import trace_relay
from data_agent.run_config import load_config
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

    monkeypatch.setattr(dab, "query", fake_query)
    monkeypatch.setattr(dab, "git_commit", lambda: "c0ffee")
    monkeypatch.setattr(trace_relay, "TraceRelay", RefusingRelay)
    root = make_dab(tmp_path / "dab")
    query = dab.load_query(root, "toyset", "1", hints=False)
    env = {"DATA_AGENT_API_KEY": "sk-test"} | dict(
        zip(LANGFUSE_ENV, ("pk-lf-1", "sk-lf-1", "http://127.0.0.1:9"), strict=True)
    )
    await dab.run_query(
        root, query, load_config("no-context"), tmp_path / "out", 1, env, trace=True
    )
    [events_file] = (tmp_path / "out").rglob("events.jsonl")
    types = [json.loads(line)["type"] for line in events_file.read_text().splitlines()]
    assert types[0] == "version"
    assert types[-1] == "result"
    assert types.count("error") == 1 and "trace" not in types
