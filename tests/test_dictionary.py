"""The prebuilt value dictionary (ticket 42): built once over one read-only connection before the
agent starts, searched from memory, never from the database."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import duckdb
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from data_agent import agent
from data_agent.agent import Task
from data_agent.context_tools import (
    CARDINALITY_THRESHOLD,
    SAMPLE_ROWS,
    VALUES_STORED,
    Dictionary,
    DictionaryBuild,
    DictionaryCancelled,
    build_interruptible,
)
from data_agent.gateway import DuckDBGateway, DuckDBSession, QueryResult
from data_agent.run_config import load_config

SHOP_SQL = Path(__file__).resolve().parent / "fixtures" / "shop.sql"
DATA_AGENT = Path(sys.executable).with_name("data-agent")


def make_db(path: Path, *statements: str) -> Path:
    con = duckdb.connect(str(path))
    try:
        for statement in statements:
            con.execute(statement)
    finally:
        con.close()
    return path


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    return make_db(tmp_path / "shop.duckdb", SHOP_SQL.read_text())


def build(db: Path, **kwargs: Any) -> Dictionary:
    return DictionaryBuild(DuckDBGateway(db), **kwargs).run()


def entry(dictionary: Dictionary, ref: str, column: str) -> list[tuple[str, int]]:
    for item in dictionary.entries:
        if (item.ref, item.column) == (ref, column):
            return item.values
    raise AssertionError(f"{ref}.{column} not in the dictionary: {dictionary.entries}")


def columns_of(dictionary: Dictionary, ref: str) -> set[str]:
    return {item.column for item in dictionary.entries if item.ref == ref}


# --- Building ------------------------------------------------------------------------------


def test_build_keeps_categorical_columns_with_counts_by_frequency(shop: Path) -> None:
    dictionary = build(shop)
    values = entry(dictionary, "raw.customers", "tier")
    assert values[0] == ("Bronze", 4), values  # most frequent first
    assert {value for value, _ in values} >= {"Gold", "GOLD", "gold_tier", "Silver", "silver"}
    assert dictionary.coverage.error is None
    assert dictionary.coverage.timed_out is False
    assert dictionary.coverage.tables_profiled == dictionary.coverage.tables_total


def test_build_skips_id_like_names_non_text_types_and_views(tmp_path: Path) -> None:
    db = make_db(
        tmp_path / "x.duckdb",
        "CREATE TABLE t (customer_id VARCHAR, created_at VARCHAR, email VARCHAR, phone_number "
        "VARCHAR, note VARCHAR, amount DECIMAL(10, 2), n INTEGER, when_date DATE, "
        "status VARCHAR, flag BOOLEAN, kind TEXT)",
        "INSERT INTO t VALUES ('c1', 'x', 'a@b', '1', 'n', 1.0, 1, DATE '2025-01-01', 'ok', "
        "true, 'k')",
        "CREATE VIEW v AS SELECT status AS view_status FROM t",
    )
    dictionary = build(db)
    assert columns_of(dictionary, "main.t") == {"status", "flag", "kind"}
    assert columns_of(dictionary, "main.v") == set()
    assert entry(dictionary, "main.t", "flag") == [("True", 1)]
    assert dictionary.coverage.tables_total == 1  # views are not base tables


def test_build_skips_high_cardinality_columns_and_stores_at_most_100_values(
    tmp_path: Path,
) -> None:
    db = make_db(
        tmp_path / "x.duckdb",
        f"CREATE TABLE t AS SELECT 'id-' || range AS code, 'v' || (range % {VALUES_STORED + 50}) "
        f"AS wide, 'v' || (range % {CARDINALITY_THRESHOLD}) AS edge FROM range({SAMPLE_ROWS})",
    )
    dictionary = build(db)
    assert columns_of(dictionary, "main.t") == {"wide", "edge"}
    assert len(entry(dictionary, "main.t", "wide")) == VALUES_STORED
    assert len(entry(dictionary, "main.t", "edge")) == VALUES_STORED
    assert dictionary.coverage.columns_profiled == 2


def test_build_samples_only_the_first_10000_rows(tmp_path: Path) -> None:
    db = make_db(
        tmp_path / "x.duckdb",
        f"CREATE TABLE t AS SELECT CASE WHEN range < {SAMPLE_ROWS} THEN 'early' ELSE 'late' END "
        f"AS phase FROM range({SAMPLE_ROWS + 5})",
    )
    dictionary = build(db)
    assert entry(dictionary, "main.t", "phase") == [("early", SAMPLE_ROWS)]
    assert dictionary.coverage.sample_rows == SAMPLE_ROWS


def ticking(*readings: float) -> Callable[[], float]:
    """A clock that returns the given readings in turn, then keeps returning the last one."""
    values = iter(readings)
    last = readings[-1]

    def clock() -> float:
        nonlocal last
        last = next(values, last)
        return last

    return clock


def test_build_stops_at_the_time_limit_and_keeps_what_it_has(tmp_path: Path) -> None:
    db = make_db(
        tmp_path / "x.duckdb",
        "CREATE TABLE a (kind VARCHAR); INSERT INTO a VALUES ('first')",
        "CREATE TABLE b (kind VARCHAR); INSERT INTO b VALUES ('second')",
        "CREATE TABLE c (kind VARCHAR); INSERT INTO c VALUES ('third')",
    )
    # Read at start, before a (ok), before b (ok), before c (past the limit), and at the end.
    dictionary = build(db, time_limit=10.0, clock=ticking(0.0, 1.0, 2.0, 11.0, 12.0))
    assert dictionary.coverage.timed_out is True
    assert dictionary.coverage.tables_profiled == 2
    assert dictionary.coverage.tables_total == 3
    assert dictionary.coverage.not_profiled == ["main.c"]
    assert dictionary.coverage.elapsed_seconds == 12.0
    assert "second" in dictionary.search("second")
    assert "not_profiled" in dictionary.search("third")
    assert "main.c" in dictionary.search("third")


def test_a_failed_connection_gives_an_empty_dictionary_with_the_reason(tmp_path: Path) -> None:
    dictionary = DictionaryBuild(DuckDBGateway(tmp_path / "missing.duckdb")).run()
    assert dictionary.entries == []
    assert dictionary.coverage.error is not None
    text = dictionary.search("anything")
    assert "not_profiled" in text
    assert "query_database" in text


# --- The database is left alone ---------------------------------------------------------------


def open_read_write_elsewhere(db: Path) -> subprocess.CompletedProcess[str]:
    code = (
        "import duckdb, sys; con = duckdb.connect(sys.argv[1]); "
        "con.execute('CREATE TABLE built_by_dbt AS SELECT 1 AS x'); con.close()"
    )
    return subprocess.run(
        [sys.executable, "-I", "-c", code, str(db)], capture_output=True, text=True, timeout=30
    )


def test_build_releases_the_database_file(shop: Path) -> None:
    gateway = DuckDBGateway(shop)
    dictionary = DictionaryBuild(gateway).run()
    assert dictionary.entries
    proc = open_read_write_elsewhere(shop)
    assert proc.returncode == 0, proc.stderr


class SlowSession(DuckDBSession):
    """Every statement first runs a query that takes minutes unless the connection is
    interrupted: the "sleep injection" of the cancel tests, on the real connection."""

    def query(self, sql: str, row_limit: int = 10_000) -> QueryResult:
        super().query("SELECT count(*) FROM range(1000000000000)", 1)
        return super().query(sql, row_limit)


class SlowGateway(DuckDBGateway):
    def session(self, timeout_seconds: float) -> SlowSession:
        return SlowSession(self.path, timeout_seconds)


def test_cancel_interrupts_the_statement_and_closes_the_connection(shop: Path) -> None:
    build = DictionaryBuild(SlowGateway(shop))
    threading.Timer(0.5, build.cancel).start()
    started = time.monotonic()
    with pytest.raises(DictionaryCancelled):
        build.run()
    assert time.monotonic() - started < 2.0
    proc = open_read_write_elsewhere(shop)
    assert proc.returncode == 0, proc.stderr


def test_cancel_before_the_build_starts_opens_no_connection(shop: Path) -> None:
    build = DictionaryBuild(SlowGateway(shop))
    build.cancel()
    with pytest.raises(DictionaryCancelled):
        build.run()


@pytest.mark.parametrize(
    ("signum", "raised"), [(signal.SIGINT, KeyboardInterrupt), (signal.SIGTERM, SystemExit)]
)
def test_signals_cancel_the_build(shop: Path, signum: int, raised: type[BaseException]) -> None:
    build = DictionaryBuild(SlowGateway(shop))
    threading.Timer(0.5, signal.raise_signal, [signum]).start()
    started = time.monotonic()
    with pytest.raises(raised):
        build_interruptible(build)
    assert time.monotonic() - started < 2.0
    proc = open_read_write_elsewhere(shop)
    assert proc.returncode == 0, proc.stderr


# --- Searching ---------------------------------------------------------------------------------


def test_search_hits_name_the_column_and_report_coverage(shop: Path) -> None:
    text = build(shop).search("gold")
    assert "raw.customers.tier: 'Gold' (2)" in text
    assert "main.stg_customers.tier:" in text
    lines = text.splitlines()
    assert lines[-1].startswith("Coverage: ")
    assert "base tables sampled" in lines[-1]
    assert f"up to {SAMPLE_ROWS} rows" in lines[-1]
    assert "time limit" not in lines[-1]


def test_search_ignores_case_and_matches_substrings(shop: Path) -> None:
    text = build(shop).search("OMPLET")
    assert "raw.orders.status: " in text
    assert "'Completed'" in text and "'completed'" in text


def test_search_miss_says_the_value_is_not_in_the_sample(shop: Path) -> None:
    text = build(shop).search("zebra")
    assert "value_not_in_sample" in text
    assert "not_profiled" not in text
    assert "query_database" in text
    assert "'zebra'" in text


def test_search_does_not_touch_the_database(shop: Path) -> None:
    dictionary = build(shop)
    shop.rename(shop.with_name("gone.duckdb"))
    assert "raw.customers.tier: 'Gold' (2)" in dictionary.search("gold")


def test_search_output_is_bounded(tmp_path: Path) -> None:
    long_value = "gold" + "x" * 500
    columns = ", ".join(f"'{long_value}' AS c{j:02d}" for j in range(60))
    db = make_db(tmp_path / "x.duckdb", f"CREATE TABLE many AS SELECT {columns}")
    lines = build(db).search("gold").splitlines()
    assert len(lines) == 52  # 50 columns, the "more columns" line, the coverage line
    assert "more specific keyword" in lines[-2]
    assert all(len(line) < 200 for line in lines[:-1])


def test_search_shows_at_most_20_values_per_column(tmp_path: Path) -> None:
    db = make_db(
        tmp_path / "x.duckdb", "CREATE TABLE t AS SELECT 'v' || range AS kind FROM range(30)"
    )
    [hits, _coverage] = build(db).search("v").splitlines()
    assert hits.count("'v") == 20
    assert "… 10 more" in hits


# --- The file ----------------------------------------------------------------------------------


def test_dictionary_round_trips_through_json(shop: Path, tmp_path: Path) -> None:
    dictionary = build(shop)
    path = tmp_path / "dictionary.json"
    dictionary.save(path)
    loaded = Dictionary.load(path)
    assert loaded == dictionary
    assert json.loads(path.read_text())["coverage"]["sample_rows"] == SAMPLE_ROWS


# --- The MCP server and the run ----------------------------------------------------------------


@asynccontextmanager
async def mcp_server(*args: str) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(
        command=str(DATA_AGENT), args=["mcp-server", *args], env=dict(os.environ)
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def text_of(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


async def test_server_searches_the_dictionary_file_it_is_given(tmp_path: Path) -> None:
    """The file, not the database: the database has no such value, the file does."""
    db = make_db(tmp_path / "x.duckdb", "CREATE TABLE t (kind VARCHAR)")
    path = tmp_path / "dictionary.json"
    build(db).save(path)
    data = json.loads(path.read_text())
    planted = {"ref": "main.t", "column": "kind", "distinct": 1, "values": [["planted", 3]]}
    data["entries"] = [planted]
    path.write_text(json.dumps(data))
    async with mcp_server("--config", "full", "--duckdb", str(db), "--dictionary", str(path)) as s:
        result = await s.call_tool("dictionary_search", {"keyword": "plant"})
    assert "main.t.kind: 'planted' (3)" in text_of(result)


async def test_standalone_server_builds_at_startup_and_searches_without_the_file(
    shop: Path,
) -> None:
    async with mcp_server("--config", "full", "--duckdb", str(shop)) as session:
        shop.rename(shop.with_name("gone.duckdb"))
        result = await session.call_tool("dictionary_search", {"keyword": "gold"})
    assert "raw.customers.tier: 'Gold' (2)" in text_of(result)


async def run_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: str
) -> tuple[list[dict[str, Any]], Any]:
    """`data-agent run` with a fake SDK; the events it wrote and the options it built."""
    seen: list[Any] = []

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        seen.append(options)
        yield agent.ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            result="42",
        )

    monkeypatch.setattr(agent, "query", fake_query)
    db = make_db(tmp_path / "shop.duckdb", SHOP_SQL.read_text())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = Task(text="q", type="query", workspace=workspace, duckdb=db)
    env = {"DATA_AGENT_API_KEY": "sk-test"}
    outcome = await agent.run(task, load_config(config), tmp_path / "events", env)
    events = [json.loads(line) for line in outcome.events_path.read_text().splitlines()]
    return events, seen[0]


async def test_run_builds_the_dictionary_before_the_agent_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events, options = await run_config(monkeypatch, tmp_path, "full")
    [built] = [event for event in events if event["type"] == "dictionary"]
    assert built["tables_profiled"] == built["tables_total"] > 0
    assert built["columns_profiled"] > 0
    assert built["timed_out"] is False
    assert built["elapsed_seconds"] >= 0
    args = options.mcp_servers["data"]["args"]
    path = Path(args[args.index("--dictionary") + 1])
    assert path.name == "dictionary.json"
    assert not path.is_relative_to(tmp_path / "workspace")
    # The server read the file while the run was on; it is gone with the run.
    assert not path.exists()


@pytest.mark.parametrize("config", ["baseline", "no-context"])
async def test_run_without_the_context_layer_builds_no_dictionary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: str
) -> None:
    events, options = await run_config(monkeypatch, tmp_path, config)
    assert [event["type"] for event in events] == ["version", "result"]
    assert "--dictionary" not in options.mcp_servers["data"]["args"]
