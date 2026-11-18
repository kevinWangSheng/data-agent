"""DAB runner (ticket 13): DAB's own tools served over MCP, and the runner around our agent.

DAB itself is replaced by `tests/fixtures/fake_dab`, a stand-in with the same module paths and tool
interface (no DAB code), so these run without a DAB clone or its dependencies. The real runner is
proven by a real run on a DAB query (ticket 13 Comments).
"""

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    HookContext,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)
from fake_docker import install
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from data_agent import agent
from data_agent.gateway import TIMEOUT_SECONDS
from data_agent.run_config import load_config, skills_path
from data_agent.sandbox import DockerSandbox, LocalSandbox
from evals import dab
from evals import dab_server as dab_server_module
from evals.__main__ import main as evals_main
from evals.dab_server import RESULT_CHARS, QueryTimeout

FAKE_DAB = Path(__file__).parent / "fixtures" / "fake_dab"
ENV = {"DATA_AGENT_API_KEY": "sk-test-secret"}
# Every layer DAB can serve: its tool server has no context tools, so `full` is refused there.
CONFIG = "no-context"
GROUND_TRUTH = "GOLD-ANSWER-7731"


def make_dab(root: Path, question: Any = "Which index is most volatile?") -> Path:
    """A DAB checkout with one dataset `toyset`: a SQLite db, a mongo db, one query."""
    shutil.copytree(FAKE_DAB, root)
    dataset = root / "query_toyset"
    (dataset / "query_dataset").mkdir(parents=True)
    with sqlite3.connect(dataset / "query_dataset" / "prices.db") as conn:
        conn.execute("CREATE TABLE prices (ticker TEXT, close REAL)")
        conn.executemany(
            "INSERT INTO prices VALUES (?, ?)", [(f"T{i:03d}", float(i)) for i in range(300)]
        )
    (dataset / "dump").mkdir()
    (dataset / "dump" / "articles.bson").write_bytes(b"x" * 10)
    config = {
        "db_clients": {
            "prices_db": {"db_type": "sqlite", "db_path": "query_dataset/prices.db"},
            "articles_db": {"db_type": "mongo", "db_name": "articles", "dump_folder": "dump"},
            "other_db": {"db_type": "mysql", "db_path": "query_dataset/other.db"},
        }
    }
    (dataset / "db_config.yaml").write_text(json.dumps(config))
    (dataset / "db_description.txt").write_text("prices_db holds daily closes.\n")
    (dataset / "db_description_withhint.txt").write_text("Hint: close is in USD.\n")
    query = dataset / "query1"
    query.mkdir()
    (query / "query.json").write_text(json.dumps(question))
    (query / "ground_truth.csv").write_text(GROUND_TRUTH)
    (query / "validate.py").write_text(f"GT = {GROUND_TRUTH!r}\n")
    return root


@pytest.fixture(autouse=True)
def clean_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean commit, whatever state this checkout is in while the tests run: a dirty one is
    refused on rerun."""
    monkeypatch.setattr(dab, "git_commit", lambda: "c0ffee")


@pytest.fixture
def dab_root(tmp_path: Path) -> Path:
    return make_dab(tmp_path / "dab")


# --- the MCP server around DAB's tools ---------------------------------------------------------


SKILLS = skills_path("skills")
RUN_ID = "run0-0000aaaa"


@asynccontextmanager
async def dab_server(
    root: Path, log: Path, *extra: str, env: dict[str, str] | None = None
) -> AsyncIterator[ClientSession]:
    config = root / "query_toyset" / "db_config.yaml"
    workspace = log.parent / "workspace"
    workspace.mkdir(exist_ok=True)
    params = StdioServerParameters(
        command=sys.executable,
        args=[*dab.server_args(root, config, log, workspace, RUN_ID, SKILLS), *extra],
        env={**os.environ, **dab.server_env(), **(env or {})},
        cwd=str(workspace),  # as for the agent: not the repo root
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def text_of(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def data_of(result: CallToolResult) -> Any:
    """The JSON result, on the reply's first line (a result id's line may follow it)."""
    data, _, _ = text_of(result).partition("\n")
    return json.loads(data)


def local_dab(root: Path, workspace: Path) -> MCPServer:
    """The tool server in this process, its sandbox_exec on the local backend: the tests reach no
    Docker. `main` gives it the docker backend (test_the_server_runs_code_in_dabs_image...)."""
    tools = dab.scaffold(root)
    config = str(root / "query_toyset" / "db_config.yaml")
    log = workspace.parent / "tools.jsonl"
    query_tool = tools.query_tool(
        log_path=log, name="query_db", db_config_path=config, check_load=False
    )
    list_tool = tools.list_tool(
        log_path=log, name="list_db", db_config_path=config, check_load=False
    )
    workspace.mkdir(exist_ok=True)
    sandbox = LocalSandbox(workspace, SKILLS)
    return dab_server_module.build_server(query_tool, list_tool, sandbox, QueryTimeout(30))


async def call_local(server: MCPServer, tool: str, arguments: dict[str, Any]) -> str:
    """The reply of an in-process call; a tool error fails the test."""
    result = await server.call_tool(tool, arguments)
    assert isinstance(result, CallToolResult)
    assert not result.is_error, text_of(result)
    return text_of(result)


def logged_calls(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


async def test_server_exposes_dab_tools_under_our_tool_names(
    dab_root: Path, tmp_path: Path
) -> None:
    async with dab_server(dab_root, tmp_path / "tools.jsonl") as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
    assert set(tools) == {
        "query_database",
        "list_tables",
        "sandbox_exec",  # ours, as on every run (ticket 100), in place of DAB's execute_python
        "describe_table",  # ours, for Postgres and DuckDB databases (ticket 37)
        "explain_query",
        "classify_text",  # ours, for every database (ticket 70)
    }
    # The descriptions are DAB's own, read from its tool specs at run time.
    assert tools["query_database"].description == "FAKE-DAB query description."
    assert tools["list_tables"].description == "FAKE-DAB list description."
    sql_arg = tools["query_database"].input_schema["properties"]["sql"]
    assert sql_arg["description"] == "FAKE-DAB query argument."
    # The docker backend, offline (ticket 100).
    sandbox_description = tools["sandbox_exec"].description or ""
    assert "an isolated container" in sandbox_description
    assert "It has no network" in sandbox_description
    assert set(tools["sandbox_exec"].input_schema["properties"]) == {
        "command",
        "timeout_seconds",
        "data_files",
    }


async def test_query_runs_through_the_dab_tool(dab_root: Path, tmp_path: Path) -> None:
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        result = await session.call_tool(
            "query_database",
            {"db_name": "prices_db", "sql": "SELECT ticker FROM prices WHERE close = 2"},
        )
        tables = await session.call_tool("list_tables", {"db_name": "prices_db"})
    assert not result.is_error
    assert data_of(result) == [{"ticker": "T002"}]
    assert data_of(tables) == ["prices"]
    assert [call["tool_name"] for call in logged_calls(log)] == ["query_db", "query_db"]


def loads(root: Path) -> list[dict[str, str]]:
    """What the fake DAB tools loaded and dropped, in order."""
    path = root / "query_toyset" / "loads.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_the_tool_server_neither_loads_nor_drops_databases(
    dab_root: Path, tmp_path: Path
) -> None:
    # Loading a large dump (imdb's 1.6 GB) outlasts the SDK's wait for the server to start, and a
    # drop on exit would race the next run's load: the runner owns both (ticket 23).
    async with dab_server(dab_root, tmp_path / "tools.jsonl") as session:
        result = await session.call_tool("list_tables", {"db_name": "articles_db"})
    assert not result.is_error
    assert loads(dab_root) == []


async def test_sql_tables_are_listed_through_the_gateway(dab_root: Path, tmp_path: Path) -> None:
    # DAB's ListDBTool sends its own SQL to the database; ours goes through the gateway and DAB's
    # query tool like any other query.
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        tables = await session.call_tool("list_tables", {"db_name": "prices_db"})
    assert data_of(tables) == ["prices"]
    [call] = logged_calls(log)
    assert call["tool_name"] == "query_db"
    assert "LIMIT 10000" in call["args"]["query"].upper()


async def test_mongo_collections_are_listed_by_dab(dab_root: Path, tmp_path: Path) -> None:
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        collections = await session.call_tool("list_tables", {"db_name": "articles_db"})
    assert data_of(collections) == ["articles"]
    assert [call["tool_name"] for call in logged_calls(log)] == ["list_db"]


async def test_listing_tables_the_gateway_cannot_check_is_refused(
    dab_root: Path, tmp_path: Path
) -> None:
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        result = await session.call_tool("list_tables", {"db_name": "other_db"})
    assert result.is_error
    assert "mysql" in text_of(result)
    assert logged_calls(log) == []


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE prices",
        "DELETE FROM prices",
        "SELECT 1; DELETE FROM prices",
        "ATTACH DATABASE '/tmp/x.db' AS x",
    ],
)
async def test_gateway_blocks_writes_before_they_reach_dab(
    dab_root: Path, tmp_path: Path, sql: str
) -> None:
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        result = await session.call_tool("query_database", {"db_name": "prices_db", "sql": sql})
    assert result.is_error
    assert "read-only gateway" in text_of(result)
    assert logged_calls(log) == []


async def test_gateway_caps_the_rows_it_sends_to_dab(dab_root: Path, tmp_path: Path) -> None:
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        await session.call_tool(
            "query_database", {"db_name": "prices_db", "sql": "SELECT * FROM prices"}
        )
    sent = logged_calls(log)[0]["args"]["query"]
    assert "LIMIT 10000" in sent.upper()


async def test_dab_errors_come_back_as_tool_errors(dab_root: Path, tmp_path: Path) -> None:
    async with dab_server(dab_root, tmp_path / "tools.jsonl") as session:
        unknown = await session.call_tool("query_database", {"db_name": "nope", "sql": "SELECT 1"})
        bad = await session.call_tool(
            "query_database", {"db_name": "prices_db", "sql": "SELECT missing FROM prices"}
        )
    assert unknown.is_error
    assert "nope" in text_of(unknown)
    assert bad.is_error
    assert "missing" in text_of(bad)


async def test_a_database_type_with_no_sql_dialect_is_refused(
    dab_root: Path, tmp_path: Path
) -> None:
    """Only MongoDB skips the SQL check; any other type the gateway cannot parse is refused."""
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        result = await session.call_tool(
            "query_database", {"db_name": "other_db", "sql": "SELECT 1"}
        )
    assert result.is_error
    assert "mysql" in text_of(result)
    assert logged_calls(log) == []


@pytest.mark.parametrize("budget", ["NaN", "-1", "0", "inf", "lots"])
def test_cli_rejects_a_bad_budget(
    dab_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], budget: str
) -> None:
    code = evals_main(
        [
            *("dab", "--dab-root", str(dab_root), "--dataset", "toyset", "--query", "1"),
            *("--config", CONFIG, "--out", str(tmp_path / "out"), "--budget", budget),
        ]
    )
    assert code == 2
    assert "budget" in capsys.readouterr().err


def test_cli_stops_when_spend_cannot_be_totalled(
    dab_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    broken = dab.run_dir(out, "other", "1", 0)
    broken.mkdir(parents=True)
    (broken / "events.jsonl").write_text("not json\n")
    code = evals_main(
        [
            *("dab", "--dab-root", str(dab_root), "--dataset", "toyset", "--query", "1"),
            *("--config", CONFIG, "--out", str(out), "--budget", "1"),
        ]
    )
    assert code == 2
    assert "not JSON" in capsys.readouterr().err


async def test_mongo_queries_skip_the_sql_check(dab_root: Path, tmp_path: Path) -> None:
    query = json.dumps({"collection": "articles", "filter": {}, "limit": 1})
    async with dab_server(dab_root, tmp_path / "tools.jsonl") as session:
        result = await session.call_tool("query_database", {"db_name": "articles_db", "sql": query})
    assert not result.is_error
    assert data_of(result) == [{"echo": query}]


# --- the query timeout (ticket 36) ---


def add_databases(root: Path) -> None:
    """Give toyset a DuckDB file and a (fake) Postgres database too."""
    dataset = root / "query_toyset"
    duckdb.connect(str(dataset / "query_dataset" / "numbers.duckdb")).close()
    config_path = dataset / "db_config.yaml"
    config = json.loads(config_path.read_text())
    config["db_clients"]["numbers_db"] = {
        "db_type": "duckdb",
        "db_path": "query_dataset/numbers.duckdb",
    }
    config["db_clients"]["films_db"] = {"db_type": "postgres", "db_name": "films"}
    config_path.write_text(json.dumps(config))


SHORT_TIMEOUT = ("--query-timeout", "0.5")
# Per database: a query that runs for far longer than the short timeout, then a quick one and its
# rows. The fake Postgres and MongoDB servers sleep for as long as the query asks, unless a
# setting the connection was opened with stops them first.
SLOW_THEN_QUICK = {
    "prices_db": (
        "SELECT count(*) AS n FROM prices a, prices b, prices c, (SELECT * FROM prices LIMIT 20) d",
        "SELECT count(*) AS n FROM prices",
        [{"n": 300}],
    ),
    "numbers_db": (
        "SELECT count(*) AS n FROM range(100000) a, range(100000) b "
        "WHERE (a.range * b.range) % 7 = 3",
        "SELECT 42 AS n",
        [{"n": 42}],
    ),
    "films_db": ("SELECT * FROM sleep_30", "SELECT * FROM sleep_0", [{"slept": 0.0}]),
    "articles_db": (
        '{"collection": "articles", "sleep": 30}',
        '{"collection": "articles"}',
        [{"echo": '{"collection": "articles"}'}],
    ),
}


@pytest.mark.parametrize("db_name", list(SLOW_THEN_QUICK))
async def test_a_slow_query_times_out_and_the_next_one_runs(
    db_name: str, dab_root: Path, tmp_path: Path
) -> None:
    add_databases(dab_root)
    slow, quick, rows = SLOW_THEN_QUICK[db_name]
    async with dab_server(dab_root, tmp_path / "tools.jsonl", *SHORT_TIMEOUT) as session:
        started = time.monotonic()
        stopped = await session.call_tool("query_database", {"db_name": db_name, "sql": slow})
        elapsed = time.monotonic() - started
        after = await session.call_tool("query_database", {"db_name": db_name, "sql": quick})
    assert stopped.is_error
    if db_name in ("numbers_db", "films_db"):
        # Postgres and DuckDB timeouts also carry the plan and the indexes (ticket 37).
        assert ": Query timed out after 0.5s\n\nPlan (EXPLAIN" in text_of(stopped)
    else:
        assert text_of(stopped).endswith(": Query timed out after 0.5s")
    assert elapsed < 10
    assert not after.is_error, text_of(after)
    assert data_of(after) == rows


async def test_a_mongo_client_side_timeout_is_a_timeout(dab_root: Path, tmp_path: Path) -> None:
    """pymongo's socket can give up before the server's maxTimeMS answers."""
    add_databases(dab_root)
    query = '{"collection": "articles", "sleep": 30, "client_side": true}'
    async with dab_server(dab_root, tmp_path / "tools.jsonl", *SHORT_TIMEOUT) as session:
        result = await session.call_tool("query_database", {"db_name": "articles_db", "sql": query})
    assert text_of(result).endswith(": Query timed out after 0.5s")


async def test_a_slow_failure_of_another_kind_keeps_its_own_error(
    dab_root: Path, tmp_path: Path
) -> None:
    """Only a stopped query is a timeout: an unreachable server after the deadline says so."""
    add_databases(dab_root)
    query = '{"collection": "articles", "unreachable": 1}'
    async with dab_server(dab_root, tmp_path / "tools.jsonl", *SHORT_TIMEOUT) as session:
        result = await session.call_tool("query_database", {"db_name": "articles_db", "sql": query})
    assert result.is_error
    assert "(ServerSelectionTimeoutError)" in text_of(result)
    assert "timed out after" not in text_of(result)


# --- database facts (ticket 37): Postgres and DuckDB only ---


def add_shop(root: Path) -> None:
    """Give toyset a DuckDB database with keys, an index and a view, beside add_databases'."""
    add_databases(root)
    dataset = root / "query_toyset"
    con = duckdb.connect(str(dataset / "query_dataset" / "shop.duckdb"))
    try:
        con.execute(
            "CREATE TABLE customers (id INTEGER PRIMARY KEY, name VARCHAR);"
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER "
            "REFERENCES customers (id));"
            "CREATE INDEX orders_customer ON orders (customer_id);"
            "INSERT INTO customers VALUES (1, 'a'), (2, 'b'), (3, 'c');"
            "INSERT INTO orders VALUES (10, 1), (11, 1);"
            "CREATE VIEW named AS SELECT name FROM customers;"
        )
    finally:
        con.close()
    config_path = dataset / "db_config.yaml"
    config = json.loads(config_path.read_text())
    config["db_clients"]["shop_db"] = {"db_type": "duckdb", "db_path": "query_dataset/shop.duckdb"}
    config_path.write_text(json.dumps(config))


async def test_sql_tables_are_listed_with_row_estimates(dab_root: Path, tmp_path: Path) -> None:
    add_shop(dab_root)
    async with dab_server(dab_root, tmp_path / "tools.jsonl") as session:
        shop = await session.call_tool("list_tables", {"db_name": "shop_db"})
        films = await session.call_tool("list_tables", {"db_name": "films_db"})
        prices = await session.call_tool("list_tables", {"db_name": "prices_db"})
    assert data_of(shop) == [
        {"name": "customers", "type": "table", "rows_estimate": 3},
        {"name": "named", "type": "view"},
        {"name": "orders", "type": "table", "rows_estimate": 2},
    ]
    assert data_of(films) == [{"name": "films", "type": "table", "rows_estimate": 1234}]
    assert data_of(prices) == ["prices"]  # SQLite: as before


async def test_describe_table_and_explain_query_run_through_the_dab_tool(
    dab_root: Path, tmp_path: Path
) -> None:
    add_shop(dab_root)
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        tools = {tool.name for tool in (await session.list_tools()).tools}
        orders = await session.call_tool(
            "describe_table", {"db_name": "shop_db", "table": "orders"}
        )
        films = await session.call_tool("describe_table", {"db_name": "films_db", "table": "films"})
        plan = await session.call_tool(
            "explain_query",
            {
                "db_name": "shop_db",
                "sql": "SELECT * FROM orders JOIN customers ON customer_id = customers.id",
            },
        )
        films_plan = await session.call_tool(
            "explain_query", {"db_name": "films_db", "sql": "SELECT * FROM films"}
        )
    assert {"describe_table", "explain_query"} <= tools
    assert not orders.is_error, text_of(orders)
    lines = text_of(orders).splitlines()
    assert lines[0] == "main.orders (table, ~2 rows, 2 columns)"
    assert "  FOREIGN KEY (customer_id) REFERENCES customers(id)" in lines
    assert "  CREATE INDEX orders_customer ON orders(customer_id);" in lines
    assert not films.is_error, text_of(films)
    assert "  id integer, not null; n_distinct=-1, null_frac=0" in text_of(films).splitlines()
    assert "HASH_JOIN" in text_of(plan)
    assert text_of(films_plan) == "Fake Plan  (rows=42) for: SELECT * FROM films LIMIT 10000"
    # Every statement went to DAB's query tool, after the gateway's check.
    assert {call["tool_name"] for call in logged_calls(log)} == {"query_db"}


@pytest.mark.parametrize(
    "sql",
    ["DROP TABLE orders", "EXPLAIN ANALYZE SELECT * FROM orders", "SELECT 1; DELETE FROM orders"],
)
async def test_explain_query_blocks_what_the_gateway_blocks(
    dab_root: Path, tmp_path: Path, sql: str
) -> None:
    add_shop(dab_root)
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        result = await session.call_tool("explain_query", {"db_name": "shop_db", "sql": sql})
    assert result.is_error
    assert "read-only gateway" in text_of(result)
    assert logged_calls(log) == []


@pytest.mark.parametrize("db_name", ["prices_db", "articles_db"])
async def test_fact_tools_are_refused_for_sqlite_and_mongo(
    dab_root: Path, tmp_path: Path, db_name: str
) -> None:
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        described = await session.call_tool("describe_table", {"db_name": db_name, "table": "x"})
        explained = await session.call_tool(
            "explain_query", {"db_name": db_name, "sql": "SELECT 1"}
        )
    for result in (described, explained):
        assert result.is_error
        assert "Postgres and DuckDB" in text_of(result)
    assert logged_calls(log) == []


async def test_a_duckdb_timeout_carries_the_plan_and_indexes(
    dab_root: Path, tmp_path: Path
) -> None:
    add_shop(dab_root)
    sql = (
        "SELECT count(*) FROM orders, range(100000) AS a, range(100000) AS b "
        "WHERE (a.range * b.range + customer_id) % 7 = 3"
    )
    async with dab_server(dab_root, tmp_path / "tools.jsonl", *SHORT_TIMEOUT) as session:
        result = await session.call_tool("query_database", {"db_name": "shop_db", "sql": sql})
    assert result.is_error
    text = text_of(result)
    assert ": Query timed out after 0.5s\n\nPlan (EXPLAIN" in text
    assert "CROSS_PRODUCT" in text
    index = "  main.orders: CREATE INDEX orders_customer ON orders(customer_id);"
    assert index in text.splitlines()


async def test_a_postgres_timeout_carries_the_indexes_of_the_tables_it_resolves(
    dab_root: Path, tmp_path: Path
) -> None:
    """The fake server's search path resolves `films` only; the report says so for sleep_30."""
    add_databases(dab_root)
    sql = "SELECT * FROM sleep_30 CROSS JOIN films"
    async with dab_server(dab_root, tmp_path / "tools.jsonl", *SHORT_TIMEOUT) as session:
        result = await session.call_tool("query_database", {"db_name": "films_db", "sql": sql})
    assert result.is_error
    text = text_of(result)
    indexes = text[text.index("Indexes on the tables") :].splitlines()[1:]
    assert indexes == [
        "  sleep_30: no table or view by this name on the search path",
        "  public.films: CREATE UNIQUE INDEX films_pkey ON public.films USING btree (id)",
    ]


async def test_postgres_connections_carry_the_statement_timeout(
    dab_root: Path, tmp_path: Path
) -> None:
    """The timeout is a server setting of the session, as in the gateway, and DAB's own read-only
    option is kept."""
    add_databases(dab_root)
    setting = "SELECT setting FROM pg_settings WHERE name = '{}'"
    async with dab_server(dab_root, tmp_path / "tools.jsonl", *SHORT_TIMEOUT) as session:
        timeout, read_only = [
            await session.call_tool(
                "query_database", {"db_name": "films_db", "sql": setting.format(name)}
            )
            for name in ("statement_timeout", "default_transaction_read_only")
        ]
    assert data_of(timeout) == [{"setting": "500"}]
    assert data_of(read_only) == [{"setting": "on"}]


async def test_the_timeout_defaults_to_the_gateways(dab_root: Path, tmp_path: Path) -> None:
    add_databases(dab_root)
    sql = "SELECT setting FROM pg_settings WHERE name = 'statement_timeout'"
    async with dab_server(dab_root, tmp_path / "tools.jsonl") as session:
        result = await session.call_tool("query_database", {"db_name": "films_db", "sql": sql})
    assert data_of(result) == [{"setting": str(TIMEOUT_SECONDS * 1000)}]


async def test_results_reach_the_sandbox_as_data_files(dab_root: Path, tmp_path: Path) -> None:
    """Ticket 100: DAB's result variables became result ids, read through `data_files`, so rows of
    two databases are combined outside both."""
    server = local_dab(dab_root, tmp_path / "workspace")
    prices = await call_local(
        server, "query_database", {"db_name": "prices_db", "sql": "SELECT * FROM prices LIMIT 3"}
    )
    articles = await call_local(
        server, "query_database", {"db_name": "articles_db", "sql": '{"collection": "articles"}'}
    )
    tables = await call_local(server, "list_tables", {"db_name": "prices_db"})
    command = "cat data/query_1.csv data/query_2.csv"
    joined = await call_local(
        server, "sandbox_exec", {"command": command, "data_files": ["query_1", "query_2"]}
    )
    assert "[result id: query_1;" in prices
    assert "[result id: query_2;" in articles
    assert "result id" not in tables  # a listing is no query result
    assert joined == (
        "exit_code: 0\nstdout:\nticker,close\nT000,0.0\nT001,1.0\nT002,2.0\n"
        'echo\n"{""collection"": ""articles""}"\n'
    )


async def test_an_unknown_result_id_is_a_tool_error(dab_root: Path, tmp_path: Path) -> None:
    server = local_dab(dab_root, tmp_path / "workspace")
    with pytest.raises(ToolError, match="unknown result id query_1"):
        await server.call_tool("sandbox_exec", {"command": "true", "data_files": ["query_1"]})


def test_non_finite_numbers_and_nested_documents_become_plain_fields() -> None:
    results = dab_server_module.Results(dab_server_module.QueryResults())
    outcome = {"success": True, "result": [{"x": float("nan"), "doc": {"y": float("inf")}}]}
    text = results.reply(outcome, keep=True)
    data, _, note = text.partition("\n")
    assert json.loads(data) == [{"x": None, "doc": {"y": None}}]
    assert note.startswith("[result id: query_1;")
    kept = results.data.results["query_1"]
    assert kept.columns == ["x", "doc"]
    assert kept.rows == [(None, '{"y": null}')]


async def test_long_results_are_cut_and_data_files_give_them_whole(
    dab_root: Path, tmp_path: Path
) -> None:
    sql = "SELECT p.ticker FROM prices p, prices q LIMIT 5000"
    server = local_dab(dab_root, tmp_path / "workspace")
    text = await call_local(server, "query_database", {"db_name": "prices_db", "sql": sql})
    counted = await call_local(
        server,
        "sandbox_exec",
        {"command": "wc -l < data/query_1.csv", "data_files": ["query_1"]},
    )
    assert len(text) < RESULT_CHARS + 500
    assert "cut at 10,000" in text
    assert "[result id: query_1;" in text
    assert counted.split() == ["exit_code:", "0", "stdout:", "5001"]  # the header and 5000 rows


async def test_failed_commands_come_back_with_their_exit_code(
    dab_root: Path, tmp_path: Path
) -> None:
    server = local_dab(dab_root, tmp_path / "workspace")
    failed = await call_local(server, "sandbox_exec", {"command": "python3 -c '1 / 0'"})
    assert failed.startswith("exit_code: 1\n")
    assert "ZeroDivisionError" in failed


def fake_docker_cli(directory: Path) -> Path:
    """A `docker` that records its arguments and fails, as one without a daemon does."""
    directory.mkdir()
    calls = directory / "calls.txt"
    script = directory / "docker"
    script.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" >> {calls}\necho "no daemon here" >&2\nexit 1\n'
    )
    script.chmod(0o755)
    return calls


async def test_the_server_runs_code_in_dabs_image_without_the_network(
    dab_root: Path, tmp_path: Path
) -> None:
    """Ticket 100: the server's sandbox is the docker backend, offline, on DAB's image, named
    after the run's id (`main`)."""
    calls = fake_docker_cli(tmp_path / "bin")
    path = f"{calls.parent}{os.pathsep}{os.environ['PATH']}"
    async with dab_server(dab_root, tmp_path / "tools.jsonl", env={"PATH": path}) as session:
        failed = await session.call_tool("sandbox_exec", {"command": "echo hi"})
    assert failed.is_error
    assert "no daemon here" in text_of(failed)
    run = calls.read_text().splitlines()
    assert run[0] == "run"
    assert run[run.index("--network") + 1] == "none"
    assert run[run.index("--name") + 1] == f"data-agent-sandbox-{RUN_ID}"
    assert dab.EXEC_IMAGE in run
    mounts = [run[i + 1] for i, arg in enumerate(run) if arg == "--mount"]
    assert any(f"source={SKILLS.resolve()},target=/skills,readonly" in m for m in mounts)


# --- loading a query and sizing its data --------------------------------------------------------


def test_load_query_reads_question_description_and_databases(dab_root: Path) -> None:
    query = dab.load_query(dab_root, "toyset", "1", hints=False)
    assert query.question == "Which index is most volatile?"
    assert query.description == "prices_db holds daily closes."
    assert query.databases == {
        "prices_db": "sqlite",
        "articles_db": "mongo",
        "other_db": "mysql",
    }


def test_hints_append_the_official_hint_file(dab_root: Path) -> None:
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    assert query.description == "prices_db holds daily closes.\n\nHint: close is in USD."


def test_question_may_be_an_object_with_a_query_field(tmp_path: Path) -> None:
    root = make_dab(tmp_path / "dab", question={"query": "How many tickers?"})
    assert dab.load_query(root, "toyset", "1", hints=False).question == "How many tickers?"


def test_unknown_query_is_rejected(dab_root: Path) -> None:
    with pytest.raises(ValueError, match="query9"):
        dab.load_query(dab_root, "toyset", "9", hints=False)


def test_data_size_counts_database_files_and_dump_folders(dab_root: Path) -> None:
    query = dab.load_query(dab_root, "toyset", "1", hints=False)
    db_file = dab_root / "query_toyset" / "query_dataset" / "prices.db"
    assert query.data_bytes == db_file.stat().st_size + 10


def test_large_databases_get_the_large_turn_limit(dab_root: Path, tmp_path: Path) -> None:
    config = load_config(CONFIG)
    query = dab.load_query(dab_root, "toyset", "1", hints=False)
    assert dab.max_turns(query, config) == config.max_turns["query"]
    big = dab_root / "query_toyset" / "dump" / "big.bson"
    with big.open("wb") as f:
        f.truncate(dab.LARGE_DB_BYTES)  # sparse: no real disk use
    query = dab.load_query(dab_root, "toyset", "1", hints=False)
    assert dab.max_turns(query, config) == config.max_turns["large"] == 75


# --- the runner -------------------------------------------------------------------------------


def result(answer: str, cost: float = 0.01) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=3,
        session_id="s",
        usage={"input_tokens": 3, "cache_read_input_tokens": 5, "output_tokens": 7},
        total_cost_usd=cost,
        result=answer,
    )


def scripted(answers: list[str], seen: list[dict[str, Any]]) -> Callable[..., AsyncIterator[Any]]:
    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        seen.append({"prompt": prompt, "options": options})
        yield result(answers[len(seen) - 1])

    return fake_query


async def run_dab(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, out: Path, runs: int = 2, **kwargs: Any
) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted([f"answer {i}" for i in range(runs)], seen))
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    await dab.run_query(dab_root, query, load_config(CONFIG), out, runs, ENV, **kwargs)
    return seen


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_a_dab_session_has_no_web_tools(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    """Ticket 98: DAB builds its options with `build_options` and never turns the network on.
    Ticket 100: no Bash, the file tools of every run, and a tool server whose sandbox is offline
    (its only switch is the image; it has no network option)."""
    [seen] = await run_dab(monkeypatch, dab_root, tmp_path / "out", runs=1)
    options = seen["options"]
    assert not {"WebSearch", "WebFetch"} & set(options.tools or [])
    assert not {"WebSearch", "WebFetch"} & set(options.allowed_tools)
    assert {"Bash", "WebSearch", "WebFetch"} <= set(options.disallowed_tools)
    assert options.tools == ["Read", "Glob", "Grep", "Write", "Edit"]
    args = options.mcp_servers["data"]["args"]
    assert "--network" not in args
    assert args[args.index("--image") + 1] == dab.EXEC_IMAGE


async def test_each_run_writes_a_trace_and_a_submission_entry(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    await run_dab(monkeypatch, dab_root, out, runs=2)
    submission = json.loads((out / "submission.json").read_text())
    assert submission == [
        {"dataset": "toyset", "query": "1", "run": "0", "answer": "answer 0"},
        {"dataset": "toyset", "query": "1", "run": "1", "answer": "answer 1"},
    ]
    for run in (0, 1):
        events = read_events(dab.run_dir(out, "toyset", "1", run) / "events.jsonl")
        assert events[0]["type"] == "version"
        assert events[0]["config"] == CONFIG
        assert events[0]["dab"] == {
            "commit": "unknown",  # the fake DAB root is not a repository
            "dataset": "toyset",
            "query": "1",
            "run": run,
            "hints": True,
            "large": False,
            "max_turns": 50,
            "skills": ["sql-workflow"],  # no sqlite-sql Skill yet
        }
        assert events[-1]["type"] == "result"
        assert events[-1]["cost_usd"] > 0


async def test_the_agent_gets_the_task_and_dab_tools_but_no_answer(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    seen = await run_dab(monkeypatch, dab_root, out, runs=1)
    prompt, options = seen[0]["prompt"], seen[0]["options"]
    assert "Which index is most volatile?" in prompt
    assert "Hint: close is in USD." in prompt
    assert "prices_db (sqlite)" in prompt
    assert "articles_db (mongo)" in prompt
    assert GROUND_TRUTH not in prompt
    assert GROUND_TRUTH not in options.system_prompt
    # The workspace (the file tools' only reach) is an empty directory inside the run.
    workspace = Path(options.cwd)
    assert workspace == (dab.run_dir(out, "toyset", "1", 0) / "workspace").resolve()
    assert dab_root.resolve() not in workspace.parents
    server = options.mcp_servers["data"]
    assert server["args"][:2] == ["-m", "evals.dab_server"]
    assert server["env"] == dab.server_env()
    args = server["args"]
    assert args[args.index("--workspace") + 1] == str(workspace)
    assert options.max_turns == 50
    # The price-table cap holds the configured $1; the SDK gets its backstop.
    assert options.max_budget_usd == agent.sdk_budget_usd(1.0, agent.DEFAULT_MODEL)


async def test_the_prompt_states_the_turn_limit_in_force(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    # Ticket 69: the agent is told the limit the session runs under, `large` on a large database.
    seen = await run_dab(monkeypatch, dab_root, tmp_path / "small", runs=1)
    assert "Turn limit: 50." in seen[0]["prompt"]
    big = dab_root / "query_toyset" / "dump" / "big.bson"
    with big.open("wb") as f:
        f.truncate(dab.LARGE_DB_BYTES)  # sparse: no real disk use
    seen = await run_dab(monkeypatch, dab_root, tmp_path / "large", runs=1)
    assert "Turn limit: 75." in seen[0]["prompt"]
    assert "Turn limit: 50" not in seen[0]["prompt"]
    assert seen[0]["options"].max_turns == 75


async def test_the_turn_budget_follows_the_session(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    # The run feeds the budget the session's messages, so its hooks remind at 10 turns left.
    reminders: dict[int, str] = {}

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        for turn in range(1, 42):
            call = ToolUseBlock(id=f"c{turn}", name="Read", input={})
            yield AssistantMessage(content=[call], model="m", message_id=f"m{turn}")
            for matcher in options.hooks["PostToolUse"]:
                for callback in matcher.hooks:
                    hook_input: Any = {
                        "hook_event_name": "PostToolUse",
                        "session_id": "s",
                        "transcript_path": "t",
                        "cwd": "/w",
                        "tool_name": "Read",
                        "tool_input": {},
                        "tool_response": "ok",
                        "tool_use_id": f"c{turn}",
                    }
                    output: Any = await callback(hook_input, None, HookContext(signal=None))
                    text = (output or {}).get("hookSpecificOutput", {}).get("additionalContext")
                    if text:
                        reminders[turn] = text
        yield result("done")

    monkeypatch.setattr(dab, "query", fake_query)
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    await dab.run_query(dab_root, query, load_config(CONFIG), tmp_path / "out", 1, ENV)
    assert list(reminders) == [40]
    assert "10 turns left" in reminders[40]


async def test_skills_follow_the_run_config(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["a", "b"], seen))
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    await dab.run_query(dab_root, query, load_config(CONFIG), tmp_path / "full", 1, ENV)
    await dab.run_query(dab_root, query, load_config("baseline"), tmp_path / "base", 1, ENV)
    full, baseline = (s["options"].system_prompt for s in seen)
    assert "name: sql-workflow" in full
    assert "name: sql-workflow" not in baseline


async def test_existing_runs_are_never_overwritten(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    await run_dab(monkeypatch, dab_root, out, runs=1)
    # A run with a trace but no submission entry (it broke) is not redone over its trace.
    (out / "submission.json").write_text("[]")
    with pytest.raises(FileExistsError):
        await run_dab(monkeypatch, dab_root, out, runs=1)


async def test_rerun_skips_finished_runs_and_redoes_removed_ones(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    await run_dab(monkeypatch, dab_root, out, runs=2)
    shutil.rmtree(dab.run_dir(out, "toyset", "1", 1))
    submission = json.loads((out / "submission.json").read_text())
    (out / "submission.json").write_text(json.dumps(submission[:1]))
    seen = await run_dab(monkeypatch, dab_root, out, runs=2)
    assert len(seen) == 1  # only run 1 ran again
    assert [e["run"] for e in json.loads((out / "submission.json").read_text())] == ["0", "1"]


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"config": "baseline"}, "config"),
        ({"env": {**ENV, "DATA_AGENT_MODEL": "other-model"}}, "model"),
        ({"hints": False}, "hints"),
        ({"commit": "f00d"}, "DAB commit"),
        ({"git_commit": "beef"}, "git commit"),
        ({"skill_version": "0ld5k1ll"}, "Skill version"),
    ],
)
async def test_rerun_under_other_conditions_refuses_to_mix_them(
    monkeypatch: pytest.MonkeyPatch,
    dab_root: Path,
    tmp_path: Path,
    change: dict[str, Any],
    field: str,
) -> None:
    out = tmp_path / "out"
    await run_dab(monkeypatch, dab_root, out, runs=1)
    monkeypatch.setattr(dab, "dab_commit", lambda _root: change.get("commit", "unknown"))
    commit, version = dab.git_commit(), dab.skill_version
    monkeypatch.setattr(dab, "git_commit", lambda: change.get("git_commit", commit))
    monkeypatch.setattr(
        dab, "skill_version", lambda path: change.get("skill_version", version(path))
    )
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["x", "y"], seen))
    query = dab.load_query(dab_root, "toyset", "1", hints=change.get("hints", True))
    config = load_config(change.get("config", CONFIG))
    with pytest.raises(dab.ConditionsChanged, match=field):
        await dab.run_query(dab_root, query, config, out, 2, change.get("env", ENV))
    assert seen == []  # nothing ran, not even the missing run 1
    assert len(json.loads((out / "submission.json").read_text())) == 1


async def test_rerun_refuses_a_dirty_dab_checkout(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    """Two dirty checkouts at one commit may hold different edits: the commit proves nothing."""
    monkeypatch.setattr(dab, "dab_commit", lambda _root: "f00d-dirty")
    out = tmp_path / "out"
    await run_dab(monkeypatch, dab_root, out, runs=1)
    with pytest.raises(dab.ConditionsChanged, match="dirty"):
        await run_dab(monkeypatch, dab_root, out, runs=2)
    assert len(json.loads((out / "submission.json").read_text())) == 1


async def test_rerun_refuses_a_dirty_data_agent_checkout(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    """`<sha>-dirty` matches any other uncommitted edits at that commit, so it proves nothing."""
    monkeypatch.setattr(dab, "git_commit", lambda: "c0ffee-dirty")
    out = tmp_path / "out"
    await run_dab(monkeypatch, dab_root, out, runs=1)
    with pytest.raises(dab.ConditionsChanged, match="uncommitted"):
        await run_dab(monkeypatch, dab_root, out, runs=2)
    assert len(json.loads((out / "submission.json").read_text())) == 1


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def test_dab_commit_marks_a_dirty_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "dab"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README.md").write_text("DAB\n")
    git(repo, "add", "README.md")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")
    head = git(repo, "rev-parse", "HEAD")
    (repo / "results.csv").write_text("untracked output\n")  # untracked files do not count
    assert dab.dab_commit(repo) == head
    (repo / "README.md").write_text("DAB, edited\n")
    assert dab.dab_commit(repo) == f"{head}-dirty"


def test_dab_commit_counts_a_git_that_times_out_as_dirty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With git-lfs installed and a stale index, `git diff` re-cleans every LFS file (13 GB on the
    server, ticket 21) and outlasts the timeout; that is an unknown state, not a crashed run."""
    repo = tmp_path / "dab"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README.md").write_text("DAB\n")
    git(repo, "add", "README.md")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")
    head = git(repo, "rev-parse", "HEAD")
    run = subprocess.run

    def slow_diff(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "diff" in cmd:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout") or 0)
        return run(cmd, **kwargs)

    monkeypatch.setattr(dab.subprocess, "run", slow_diff)
    assert dab.dab_commit(repo) == f"{head}-dirty"


def test_dab_commit_accepts_lfs_data_that_matches_its_manifest(tmp_path: Path) -> None:
    """DAB keeps its databases in Git LFS; without git-lfs, data put in place by DAB's download.sh
    differs from the committed pointer stubs, and is clean only when it matches the sha256 in DAB's
    dataset_manifest.tsv."""
    repo = tmp_path / "dab"
    (repo / "query_x").mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / ".gitattributes").write_text("*.db filter=lfs diff=lfs merge=lfs -text\n")
    pointer = "version https://git-lfs.github.com/spec/v1\n"
    (repo / "query_x" / "data.db").write_text(pointer)
    (repo / "query_x" / "other.db").write_text(pointer)
    (repo / "query_x" / "query.json").write_text('"How many?"\n')
    data = b"SQLite format 3\x00 real data"
    digest = hashlib.sha256(data).hexdigest()
    (repo / "dataset_manifest.tsv").write_text(f"query_x/data.db\t{digest}\t{len(data)}\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")
    head = git(repo, "rev-parse", "HEAD")
    (repo / "query_x" / "data.db").write_bytes(data)
    assert dab.dab_commit(repo) == head
    (repo / "query_x" / "query.json").write_text('"How many, edited?"\n')
    assert dab.dab_commit(repo) == f"{head}-dirty"


@pytest.mark.parametrize(
    "change",
    ["edited after download", "same size, other bytes", "not in the manifest", "deleted"],
)
def test_dab_commit_marks_lfs_data_that_differs_from_its_manifest(
    tmp_path: Path, change: str
) -> None:
    repo = tmp_path / "dab"
    (repo / "query_x").mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / ".gitattributes").write_text("*.db filter=lfs diff=lfs merge=lfs -text\n")
    pointer = "version https://git-lfs.github.com/spec/v1\n"
    (repo / "query_x" / "data.db").write_text(pointer)
    (repo / "query_x" / "other.db").write_text(pointer)
    data = b"SQLite format 3\x00 real data"
    digest = hashlib.sha256(data).hexdigest()
    (repo / "dataset_manifest.tsv").write_text(f"query_x/data.db\t{digest}\t{len(data)}\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")
    head = git(repo, "rev-parse", "HEAD")
    if change == "edited after download":
        (repo / "query_x" / "data.db").write_bytes(data + b" and a new row")
    elif change == "same size, other bytes":
        (repo / "query_x" / "data.db").write_bytes(data.upper())
    elif change == "not in the manifest":
        (repo / "query_x" / "other.db").write_bytes(data)
    else:
        (repo / "query_x" / "data.db").unlink()
    assert dab.dab_commit(repo) == f"{head}-dirty"


async def test_configs_with_context_tools_are_refused(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    """DAB's tool server has no context tools: a context-on config would claim a layer it lacks."""
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["x"], seen))
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    with pytest.raises(ValueError, match="context"):
        await dab.run_query(dab_root, query, load_config("full"), tmp_path / "out", 1, ENV)
    assert seen == []
    assert not (tmp_path / "out").exists()


def test_cli_refuses_configs_with_context_tools(
    monkeypatch: pytest.MonkeyPatch,
    dab_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["x"], seen))
    args = [
        *("dab", "--dab-root", str(dab_root), "--dataset", "toyset", "--query", "1"),
        *("--runs", "1", "--config", "full-old-skill", "--out", str(tmp_path / "out")),
    ]
    assert evals_main(args) == 2
    assert "context" in capsys.readouterr().err
    assert seen == []


async def test_an_interrupted_submission_write_keeps_the_old_file(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    await run_dab(monkeypatch, dab_root, out, runs=1)
    before = (out / "submission.json").read_text()
    write_text = Path.write_text

    def cut_short(path: Path, data: str, *args: Any, **kwargs: Any) -> int:
        write_text(path, data[:5], *args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(Path, "write_text", cut_short)
    with pytest.raises(KeyboardInterrupt):
        dab.write_submission(out / "submission.json", [])
    monkeypatch.undo()
    assert (out / "submission.json").read_text() == before
    assert [p.name for p in out.iterdir() if p.is_file()] == ["submission.json"]


async def test_a_finished_run_without_its_trace_cannot_be_checked(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    await run_dab(monkeypatch, dab_root, out, runs=1)
    (dab.run_dir(out, "toyset", "1", 0) / "events.jsonl").unlink()
    with pytest.raises(dab.ConditionsChanged, match="trace"):
        await run_dab(monkeypatch, dab_root, out, runs=1)


async def test_another_query_under_other_conditions_refuses_to_join_the_submission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = make_dab(tmp_path / "dab")
    shutil.copytree(root / "query_toyset" / "query1", root / "query_toyset" / "query2")
    out = tmp_path / "out"
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["one", "two"], seen))
    query1 = dab.load_query(root, "toyset", "1", hints=True)
    await dab.run_query(root, query1, load_config(CONFIG), out, 1, ENV)
    query2 = dab.load_query(root, "toyset", "2", hints=True)
    with pytest.raises(dab.ConditionsChanged, match="config"):
        await dab.run_query(root, query2, load_config("baseline"), out, 1, ENV)
    assert len(seen) == 1


def test_cli_fails_when_the_out_directory_holds_other_conditions(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["x", "y"], seen))
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-test-secret")
    out = tmp_path / "out"
    args = [
        *("dab", "--dab-root", str(dab_root), "--dataset", "toyset", "--query", "1"),
        *("--runs", "1", "--out", str(out)),
    ]
    assert evals_main([*args, "--config", CONFIG]) == 0
    assert evals_main([*args, "--config", "baseline"]) == 2
    assert len(seen) == 1


async def test_each_run_removes_its_sandbox_container_after_the_session(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    # The SDK kills the tool server (SIGINT, SIGTERM, then for good) within about half a second
    # of the session's end, before it could remove its container; the runner removes it instead,
    # also when the session broke.
    docker = install(monkeypatch)
    run_ids: list[str] = []

    async def starting_a_container(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        args = options.mcp_servers["data"]["args"]
        run_ids.append(args[args.index("--run-id") + 1])
        workspace = Path(args[args.index("--workspace") + 1])
        DockerSandbox(workspace, SKILLS, run_ids[-1], image=dab.EXEC_IMAGE).execute("true", 30)
        raise RuntimeError("endpoint unreachable")
        yield

    monkeypatch.setattr(dab, "query", starting_a_container)
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    await dab.run_query(dab_root, query, load_config(CONFIG), tmp_path / "out", 2, ENV)
    assert len(docker.verb("run")) == 2
    assert len(set(run_ids)) == 2
    assert all(run_id.startswith("run") for run_id in run_ids)
    assert docker.containers == {}


async def test_a_dab_server_that_failed_to_start_is_an_infra_error(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    async def server_down(**_: Any) -> AsyncIterator[Any]:
        servers = [{"name": "data", "status": "failed"}]
        yield SystemMessage(subtype="init", data={"mcp_servers": servers, "tools": []})
        yield result("The database is unavailable.")

    monkeypatch.setattr(dab, "query", server_down)
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    out = tmp_path / "out"
    errors = await dab.run_query(dab_root, query, load_config(CONFIG), out, 1, ENV)
    assert errors == 1
    assert json.loads((out / "submission.json").read_text()) == []
    events = read_events(dab.run_dir(out, "toyset", "1", 0) / "events.jsonl")
    assert any(e["type"] == "error" and e["source"] == "mcp" for e in events)


async def test_databases_are_loaded_before_the_session_and_dropped_after(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    at_start: list[list[dict[str, str]]] = []

    async def session(**_: Any) -> AsyncIterator[Any]:
        at_start.append(loads(dab_root))
        yield result("answer")

    monkeypatch.setattr(dab, "query", session)
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    out = tmp_path / "out"
    errors = await dab.run_query(dab_root, query, load_config(CONFIG), out, 2, ENV)
    assert errors == 0
    load, drop = (
        {"action": "load", "db_name": "articles"},
        {"action": "drop", "db_name": "articles"},
    )
    assert at_start == [[load], [load, drop, load]]
    assert loads(dab_root) == [load, drop, load, drop]
    events = read_events(dab.run_dir(out, "toyset", "1", 0) / "events.jsonl")
    assert [e["type"] for e in events[:2]] == ["version", "dab_load"]
    assert events[1]["seconds"] >= 0


def test_databases_are_loaded_from_an_absolute_config_path(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    # DAB joins a dump's path onto the config's directory and hands it to `psql -f`, which runs
    # inside the database container with that container's working directory: with
    # `--dab-root ../da-benchmarks/DataAgentBench` the dump was not found (ticket 23, server run).
    real = dab.scaffold(dab_root)
    paths: list[str] = []

    def query_tool(**kwargs: Any) -> Any:
        paths.append(kwargs["db_config_path"])
        return real.query_tool(**kwargs)

    monkeypatch.setattr(dab, "scaffold", lambda _root: replace(real, query_tool=query_tool))
    monkeypatch.chdir(tmp_path)
    query = dab.load_query(Path("dab"), "toyset", "1", hints=True)
    log = tmp_path / "tools.jsonl"
    dab.load_databases(Path("dab"), query, log, [])
    dab.drop_databases(Path("dab"), query, log, [])
    assert len(paths) == 2
    assert all(Path(path).is_absolute() for path in paths)


async def test_a_failed_load_is_an_infra_error_and_no_session_starts(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    config_path = dab_root / "query_toyset" / "db_config.yaml"
    config = json.loads(config_path.read_text())
    config["db_clients"] = {
        "films_db": {"db_type": "postgres", "db_name": "films", "sql_file": "films.sql"},
        "articles_db": {
            **config["db_clients"]["articles_db"],
            "load_error": "mongorestore: no space",
        },
    }
    config_path.write_text(json.dumps(config))
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["answer"], seen))
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    out = tmp_path / "out"
    errors = await dab.run_query(dab_root, query, load_config(CONFIG), out, 1, ENV)
    assert errors == 1
    assert seen == []
    assert json.loads((out / "submission.json").read_text()) == []
    events = read_events(dab.run_dir(out, "toyset", "1", 0) / "events.jsonl")
    error = next(e for e in events if e["type"] == "error")
    assert error["source"] == "dab_load"
    assert "mongorestore: no space" in error["error"]
    assert events[-1]["type"] == "result"
    # What did load before the failure is dropped again.
    assert loads(dab_root)[-2:] == [
        {"action": "drop", "db_name": "films"},
        {"action": "drop", "db_name": "articles"},
    ]


async def test_a_failed_drop_is_reported_masked_and_the_run_still_counts(
    monkeypatch: pytest.MonkeyPatch,
    dab_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = dab_root / "query_toyset" / "db_config.yaml"
    config = json.loads(config_path.read_text())
    error = "cleanup error: connection to db.internal.example refused"
    config["db_clients"]["articles_db"]["drop_error"] = error
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(dab, "query", scripted(["answer"], []))
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    out = tmp_path / "out"
    env = {**ENV, "PG_HOST": "db.internal.example"}
    errors = await dab.run_query(dab_root, query, load_config(CONFIG), out, 1, env)
    assert errors == 0
    assert json.loads((out / "submission.json").read_text())[0]["answer"] == "answer"
    stderr = capsys.readouterr().err
    assert "could not drop the toyset databases" in stderr
    assert "db.internal.example" not in stderr


async def test_submission_keeps_entries_from_earlier_queries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = make_dab(tmp_path / "dab")
    shutil.copytree(root / "query_toyset" / "query1", root / "query_toyset" / "query2")
    out = tmp_path / "out"
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["one", "two"], seen))
    config = load_config(CONFIG)
    for query_id in ("2", "1"):
        query = dab.load_query(root, "toyset", query_id, hints=True)
        await dab.run_query(root, query, config, out, 1, ENV)
    submission = json.loads((out / "submission.json").read_text())
    assert [(e["query"], e["answer"]) for e in submission] == [("1", "two"), ("2", "one")]


async def test_budget_guard_stops_before_the_next_run(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    # A scripted run costs about $0.00001 at the default model's price, half that off peak; the
    # budget below stops after one run whenever it runs.
    seen = await run_dab(monkeypatch, dab_root, tmp_path / "out", runs=3, budget=Decimal(1))
    assert len(seen) == 3
    with pytest.raises(dab.BudgetReached):
        await run_dab(monkeypatch, dab_root, tmp_path / "out2", runs=3, budget=Decimal("0.000001"))
    assert not dab.run_dir(tmp_path / "out2", "toyset", "1", 1).exists()


def test_budget_counts_billed_spend(tmp_path: Path) -> None:
    """Ticket 34: a DeepSeek run that started off peak (a Saturday) is billed half its
    `cost_usd`; one at peak is billed all of it."""
    for run, ts in enumerate(("2026-10-31T12:00:00+00:00", "2026-11-02T02:00:00+00:00")):
        path = dab.run_dir(tmp_path, "toyset", "1", run) / "events.jsonl"
        path.parent.mkdir(parents=True)
        lines = [
            {"type": "version", "ts": ts, "model": "deepseek-flash"},
            {"type": "result", "cost_usd": 0.4, "cost_basis": "price_table"},
        ]
        path.write_text("".join(json.dumps(e) + "\n" for e in lines))
    assert dab.spent(tmp_path) == Decimal("0.6")


async def test_answers_are_masked_like_the_trace(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["key sk-test-secret here"], seen))
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    await dab.run_query(dab_root, query, load_config(CONFIG), tmp_path / "out", 1, ENV)
    submission = (tmp_path / "out" / "submission.json").read_text()
    assert "sk-test-secret" not in submission
    assert "key [REDACTED] here" in submission


async def test_dab_logs_are_masked_like_the_trace(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    # DAB's tools write their own logs, unmasked; the runner masks them once the session is over.
    async def leaky_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        log = Path(
            options.mcp_servers["data"]["args"][
                options.mcp_servers["data"]["args"].index("--log") + 1
            ]
        )
        log.write_text(json.dumps({"result": "error at sk-test-secret"}) + "\n")
        log.with_name("execute_python_artifacts.jsonl").write_text("sk-test-secret\n")
        yield result("done")

    monkeypatch.setattr(dab, "query", leaky_query)
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    await dab.run_query(dab_root, query, load_config(CONFIG), tmp_path / "out", 1, ENV)
    run = dab.run_dir(tmp_path / "out", "toyset", "1", 0)
    for name in ("dab_tool_calls.jsonl", "execute_python_artifacts.jsonl"):
        text = (run / name).read_text()
        assert "sk-test-secret" not in text
        assert "[REDACTED]" in text


async def test_dab_logs_mask_secrets_in_their_json_escaped_form(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    # In a JSON log a password with a quote, a backslash or a non-ASCII letter is escaped.
    password = 'pa"ss\\wörd'

    async def leaky_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        args = options.mcp_servers["data"]["args"]
        log = Path(args[args.index("--log") + 1])
        entry = {"result": f"bad {password}"}
        lines = [json.dumps(entry, ensure_ascii=ascii) for ascii in (True, False)]
        log.write_text("\n".join(lines) + "\n")
        yield result("done")

    monkeypatch.setattr(dab, "query", leaky_query)
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    env = {**ENV, "PG_PASSWORD": password}
    await dab.run_query(dab_root, query, load_config(CONFIG), tmp_path / "out", 1, env)
    log = dab.run_dir(tmp_path / "out", "toyset", "1", 0) / "dab_tool_calls.jsonl"
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert entries == [{"result": "bad [REDACTED]"}] * 2


async def test_dab_logs_are_masked_when_the_run_is_interrupted(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    # Ctrl-C cancels the session; DAB's log still gets masked.
    async def interrupted_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        args = options.mcp_servers["data"]["args"]
        Path(args[args.index("--log") + 1]).write_text('{"result": "sk-test-secret"}\n')
        raise asyncio.CancelledError
        yield

    monkeypatch.setattr(dab, "query", interrupted_query)
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    with pytest.raises(asyncio.CancelledError):
        await dab.run_query(dab_root, query, load_config(CONFIG), tmp_path / "out", 1, ENV)
    log = dab.run_dir(tmp_path / "out", "toyset", "1", 0) / "dab_tool_calls.jsonl"
    assert log.read_text() == '{"result": "[REDACTED]"}\n'


async def test_dab_logs_are_masked_when_the_load_is_interrupted(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    # Ctrl-C during a long load (minutes for imdb); DAB's log still gets masked.
    def interrupted_load(_root: Path, _query: Any, log: Path, _secrets: list[str]) -> float:
        log.write_text('{"result": "sk-test-secret"}\n')
        raise KeyboardInterrupt

    monkeypatch.setattr(dab, "load_databases", interrupted_load)
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    with pytest.raises(KeyboardInterrupt):
        await dab.run_query(dab_root, query, load_config(CONFIG), tmp_path / "out", 1, ENV)
    log = dab.run_dir(tmp_path / "out", "toyset", "1", 0) / "dab_tool_calls.jsonl"
    assert log.read_text() == '{"result": "[REDACTED]"}\n'


def test_database_connection_settings_are_masked_too() -> None:
    env = {
        **ENV,
        "PG_HOST": "pg.example.internal",
        "PG_PASSWORD": "pg-pass-123",
        "MONGO_URI": "mongodb://mongo.example.internal:27017/",
        "MYSQL_HOST": "localhost",  # a loopback name is no server address and stays readable
    }
    secrets = dab.secrets_of(env)
    text = dab.mask(
        "could not connect to pg.example.internal with pg-pass-123; "
        "mongodb://mongo.example.internal:27017/ and mongo.example.internal; localhost",
        secrets,
    )
    assert text == (
        "could not connect to [REDACTED] with [REDACTED]; [REDACTED] and [REDACTED]; localhost"
    )


async def test_the_trace_masks_database_connection_settings(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["pg.example.internal is down"], seen))
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    env = {**ENV, "PG_HOST": "pg.example.internal"}
    await dab.run_query(dab_root, query, load_config(CONFIG), tmp_path / "out", 1, env)
    trace = (dab.run_dir(tmp_path / "out", "toyset", "1", 0) / "events.jsonl").read_text()
    assert "pg.example.internal" not in trace
    assert "pg.example.internal" not in (tmp_path / "out" / "submission.json").read_text()


def test_infra_errors_are_left_out_of_the_submission_and_fail_the_cli(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    async def broken_query(**_: Any) -> AsyncIterator[Any]:
        raise RuntimeError("endpoint unreachable")
        yield  # an async generator that ends before any ResultMessage

    monkeypatch.setattr(dab, "query", broken_query)
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-test-secret")
    out = tmp_path / "out"
    code = evals_main(
        [
            *("dab", "--dab-root", str(dab_root), "--dataset", "toyset", "--query", "1"),
            *("--runs", "2", "--config", CONFIG, "--out", str(out)),
        ]
    )
    assert code == 2
    assert json.loads((out / "submission.json").read_text()) == []
    # The traces stay, so the failure can be looked at before the runs are redone.
    assert (dab.run_dir(out, "toyset", "1", 1) / "events.jsonl").is_file()


def test_cli_runs_the_requested_queries(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["x", "y"], seen))
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-test-secret")
    out = tmp_path / "out"
    code = evals_main(
        [
            *("dab", "--dab-root", str(dab_root), "--dataset", "toyset", "--query", "1"),
            *("--runs", "2", "--config", "baseline", "--out", str(out)),
        ]
    )
    assert code == 0
    assert len(json.loads((out / "submission.json").read_text())) == 2
    assert "Hint: close is in USD." in seen[0]["prompt"]  # hints are on by default


def test_cli_rejects_a_missing_dab_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = evals_main(
        [
            *("dab", "--dab-root", str(tmp_path / "none"), "--dataset", "toyset", "--query", "1"),
            *("--config", CONFIG, "--out", str(tmp_path / "out")),
        ]
    )
    assert code == 2
    assert "none" in capsys.readouterr().err


def main_call(message_id: str, output_tokens: int) -> list[Any]:
    """One main-thread model call that used a tool, as streamed with partial messages."""

    def event(event: dict[str, Any]) -> StreamEvent:
        return StreamEvent(uuid="u", session_id="s", event=event)

    return [
        event({"type": "message_start", "message": {"id": message_id, "usage": {}}}),
        AssistantMessage(
            content=[ToolUseBlock(id=f"toolu_{message_id}", name="query_database", input={})],
            model="deepseek-flash",
            message_id=message_id,
        ),
        event(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"input_tokens": 0, "output_tokens": output_tokens},
            }
        ),
    ]


async def test_dab_runs_have_the_same_price_table_cap(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    """Two deepseek-flash calls of $0.60 pass the full config's $1: the session stops before a
    third, the run is unfinished with an empty answer, and the SDK got the backstop cap."""
    seen: list[Any] = []

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        seen.append(options)
        for message in (*main_call("m1", 500_000), *main_call("m2", 500_000)):
            yield message
        yield AssistantMessage(content=[TextBlock(text="never reached")], model="deepseek-flash")
        yield result("never reached")

    monkeypatch.setattr(dab, "query", fake_query)
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    out = tmp_path / "out"
    assert await dab.run_query(dab_root, query, load_config(CONFIG), out, 1, ENV) == 0
    events = read_events(dab.run_dir(out, "toyset", "1", 0) / "events.jsonl")
    assert "never reached" not in json.dumps(events)
    assert events[-1]["terminal_reason"] == "budget_exhausted"
    assert events[-1]["cost_usd"] == pytest.approx(1.20)
    submission = json.loads((out / "submission.json").read_text())
    assert submission == [{"dataset": "toyset", "query": "1", "run": "0", "answer": ""}]
    assert seen[0].max_budget_usd == agent.sdk_budget_usd(1.0, agent.DEFAULT_MODEL)
    assert seen[0].include_partial_messages
    assert "SubagentStop" in seen[0].hooks
