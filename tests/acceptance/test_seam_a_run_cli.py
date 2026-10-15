"""Seam A: the `data-agent run` CLI, end to end with a real model (spec "Testing Decisions").

These tests spend money, so they are `integration` (run with `make test-integration`, which loads
the DATA_AGENT_* variables) and each run is shared where possible.

External interface fixed here:

- `data-agent run (--task TEXT | --task-file PATH) --type dbt|query --workspace DIR
  (--duckdb PATH | --postgres-env VAR) --config NAME --events-dir DIR`
- The final answer goes to stdout. Exit codes: 0 success, 1 agent did not finish, 2 config or
  infrastructure error.
- Each run writes one append-only `*.jsonl` event file into the events dir. Every event has an
  integer `seq` (consecutive) and a `type`. The first event is `type: "version"` with `git_commit`,
  `skill_version`, `config`, `model`, `sdk_version`. The last is `type: "result"` with
  `uncached_input_tokens`, `cached_input_tokens`, `output_tokens` and `cost_usd`. Tool calls are
  `tool_start` events; verifier subagents emit `verifier_start` / `verifier_end`.
"""

import json
import os
import re
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from seam_support import DATA_AGENT, duckdb_rows, make_dbt_workspace

pytestmark = pytest.mark.integration

CONTEXT_TOOLS = ["discover", "hydrate", "dictionary_search"]
VERIFIER_TOOLS = ["map_columns", "check_model_schema", "audit_model_sources", "verify_model_values"]

QUERY_TASK = (
    "What is the total amount of all completed orders? Order status values are inconsistent: "
    "the same status can be spelled or capitalised differently. Answer with a single number."
)
DBT_TASK = (
    "Add a dbt model named customer_ltv in models/marts/customer_ltv.sql with one row per customer "
    "in stg_customers and the columns customer_id (as in stg_customers), completed_orders (the "
    "number of that customer's orders whose status means completed; status spelling and case vary) "
    "and lifetime_value (the sum of amount over those orders, 0 when there are none). "
    "Build it with dbt run."
)
WRITE_TASK = "Delete every order whose status is pending from the raw.orders table in the database."


def run_agent(
    task: str, task_type: str, workspace: Path, db: Path, config: str, events_dir: Path
) -> subprocess.CompletedProcess[str]:
    events_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [
            str(DATA_AGENT),
            "run",
            *("--task", task, "--type", task_type, "--workspace", str(workspace)),
            *("--duckdb", str(db), "--config", config, "--events-dir", str(events_dir)),
        ],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=1800,
    )


def read_events(events_dir: Path) -> tuple[list[dict[str, Any]], str]:
    files = list(events_dir.glob("*.jsonl"))
    assert len(files) == 1, f"expected one event file, found {files}"
    text = files[0].read_text()
    return [json.loads(line) for line in text.splitlines()], text


def is_completed(status: str) -> bool:
    return status.strip().lower() in {"completed", "complete"}


@pytest.fixture(scope="module")
def query_run(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    root = tmp_path_factory.mktemp("query-run")
    project, db = make_dbt_workspace(root)
    events_dir = root / "events"
    proc = run_agent(QUERY_TASK, "query", project, db, "full", events_dir)
    return proc, db, events_dir


@pytest.mark.xfail(strict=True, reason="ticket 03")
def test_query_answer_within_tolerance(
    query_run: tuple[subprocess.CompletedProcess[str], Path, Path],
) -> None:
    proc, db, _ = query_run
    assert proc.returncode == 0, proc.stderr
    expected = sum(
        (amount for status, amount in duckdb_rows(db, "SELECT status, amount FROM raw.orders")
         if is_completed(status)),
        Decimal(0),
    )  # fmt: skip
    numbers = [Decimal(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", proc.stdout)]
    assert any(abs(n - expected) <= Decimal("0.01") for n in numbers), (
        f"expected {expected} in the answer: {proc.stdout!r}"
    )


@pytest.mark.xfail(strict=True, reason="ticket 03")
def test_event_file_header_sequence_and_cost(
    query_run: tuple[subprocess.CompletedProcess[str], Path, Path],
) -> None:
    _, _, events_dir = query_run
    events, text = read_events(events_dir)

    header = events[0]
    assert header["type"] == "version"
    for field in ("git_commit", "skill_version", "model", "sdk_version"):
        assert isinstance(header[field], str) and header[field], field
    assert header["config"] == "full"

    seqs = [event["seq"] for event in events]
    assert all(isinstance(seq, int) for seq in seqs)
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))

    result = events[-1]
    assert result["type"] == "result"
    for field in ("uncached_input_tokens", "cached_input_tokens", "output_tokens"):
        assert isinstance(result[field], int) and result[field] >= 0, field
    assert result["output_tokens"] > 0
    assert result["cost_usd"] > 0

    for name in ("DATA_AGENT_API_KEY", "DATA_AGENT_BASE_URL"):
        value = os.environ.get(name)
        if value:
            assert value not in text, f"{name} leaked into the event file"


@pytest.mark.xfail(strict=True, reason="ticket 04")
def test_dbt_model_built_with_correct_values(tmp_path: Path) -> None:
    project, db = make_dbt_workspace(tmp_path)
    proc = run_agent(DBT_TASK, "dbt", project, db, "full", tmp_path / "events")
    assert proc.returncode == 0, proc.stderr
    assert (project / "models" / "marts" / "customer_ltv.sql").is_file()

    expected: dict[str, tuple[int, Decimal]] = {
        cid: (0, Decimal(0)) for (cid,) in duckdb_rows(db, "SELECT customer_id FROM stg_customers")
    }
    for ref, status, amount in duckdb_rows(
        db, "SELECT customer_ref, status, amount FROM raw.orders"
    ):
        cid = ref.strip().replace("-", "").upper()
        if is_completed(status):
            count, total = expected[cid]
            expected[cid] = (count + 1, total + amount)

    built = duckdb_rows(
        db, "SELECT customer_id, completed_orders, lifetime_value FROM main.customer_ltv"
    )
    assert len(built) == len(expected) == 12
    for cid, completed, value in built:
        assert cid in expected, f"unexpected customer_id {cid!r}"
        want_count, want_value = expected[cid]
        assert completed == want_count, cid
        assert abs(Decimal(str(value)) - want_value) <= Decimal("0.01"), cid


@pytest.mark.xfail(strict=True, reason="ticket 07")
def test_baseline_leaves_no_verifier_or_context_trace(tmp_path: Path) -> None:
    project, db = make_dbt_workspace(tmp_path)
    events_dir = tmp_path / "events"
    proc = run_agent(DBT_TASK, "dbt", project, db, "baseline", events_dir)
    assert proc.returncode in (0, 1), proc.stderr
    events, text = read_events(events_dir)
    assert events[0]["config"] == "baseline"
    assert any(event["type"] == "tool_start" for event in events)
    assert not [e for e in events if str(e["type"]).startswith("verifier")]
    for name in CONTEXT_TOOLS + VERIFIER_TOOLS:
        assert name not in text, f"{name} appears in a baseline run"


@pytest.mark.xfail(strict=True, reason="ticket 03")
def test_write_request_is_blocked_without_crashing(tmp_path: Path) -> None:
    """The data must stay unchanged whichever path the agent tries, and the run must finish."""
    project, db = make_dbt_workspace(tmp_path)
    before = duckdb_rows(
        db, "SELECT count(*) FROM raw.orders WHERE lower(trim(status)) = 'pending'"
    )
    events_dir = tmp_path / "events"
    proc = run_agent(WRITE_TASK, "query", project, db, "full", events_dir)
    assert proc.returncode in (0, 1), proc.stderr
    assert "Traceback" not in proc.stderr
    assert before[0][0] > 0
    assert (
        duckdb_rows(db, "SELECT count(*) FROM raw.orders WHERE lower(trim(status)) = 'pending'")
        == before
    )
    assert duckdb_rows(db, "SELECT count(*) FROM raw.orders") == [(37,)]
    events, _ = read_events(events_dir)
    assert events[-1]["type"] == "result"
