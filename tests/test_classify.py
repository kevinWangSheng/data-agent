"""classify_text (ticket 70): the product server's tool and its label tables, the DAB server's tool,
and how its model calls count toward the run's spend. The model is `fake_model`'s local stand-in."""

import csv
import dataclasses
import itertools
import json
import os
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fake_model import FakeModel, by_fixture, fixture_rows, serve
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from test_agent_outcomes import TOOL_RESULT, call, result, run_with
from test_dab import dab_server, logged_calls, make_dab

from data_agent import agent, classify
from data_agent.gateway import DuckDBGateway
from data_agent.mcp_server import build_server
from data_agent.run_config import load_config
from data_agent.tool_spend import LEDGER_ENV, Ledger, RunState, ToolTokens
from evals import dab, spend

CATEGORIES = ["weather", "sport", "cooking", "music"]
API_KEY = "sk-classify-secret-4471"
MODEL = "deepseek-flash"
FLASH = agent.PRICES[MODEL]
# One fake call (fake_model.USAGE) at deepseek-flash's prices.
CALL_USD = (1000 * FLASH.input + 200 * FLASH.cached_input + 50 * FLASH.output) / 1e6
ARTICLES = "SELECT id, body, year FROM articles"


def make_ledger(tmp_path: Path, budget: float = 1.0, spent: float = 0.0) -> Ledger:
    directory = tmp_path / "spend"
    directory.mkdir()
    ledger = Ledger(directory)
    ledger.publish(RunState(budget, spent, FLASH.input, FLASH.cached_input, FLASH.output))
    return ledger


def model_env(model: FakeModel, ledger: Ledger | None) -> dict[str, str]:
    """What the run gives the tool server (agent.build_options)."""
    env = {
        "DATA_AGENT_BASE_URL": model.url,
        "DATA_AGENT_API_KEY": API_KEY,
        "DATA_AGENT_MODEL": MODEL,
    }
    return env | ({LEDGER_ENV: str(ledger.directory)} if ledger else {})


@pytest.fixture
def articles_db(tmp_path: Path) -> Path:
    db = tmp_path / "articles.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE TABLE articles (id INTEGER, body VARCHAR, year INTEGER)")
        con.executemany(
            "INSERT INTO articles VALUES (?, ?, ?)",
            [(i, text, 2020 + i % 3) for i, text, _ in fixture_rows()],
        )
    finally:
        con.close()
    return db


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "ws"
    path.mkdir()
    return path


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[FakeModel]:
    """The fake model labelling the samples with their own categories, set up for the server."""
    with serve(FakeModel(by_fixture)) as model:
        for name, value in model_env(model, make_ledger(tmp_path)).items():
            monkeypatch.setenv(name, value)
        yield model


def product_server(db: Path, workspace: Path, config: str = "baseline") -> MCPServer:
    return build_server(DuckDBGateway(db), load_config(config), workspace=workspace)


async def call_tool(server: MCPServer, tool: str, arguments: dict[str, Any]) -> CallToolResult:
    """The tool's result; a tool error comes back as an error result, as over stdio."""
    try:
        outcome = await server.call_tool(tool, arguments)
    except ToolError as exc:
        return CallToolResult(content=[TextContent(type="text", text=str(exc))], is_error=True)
    assert isinstance(outcome, CallToolResult)
    return outcome


def text_of(outcome: CallToolResult) -> str:
    return "\n".join(item.text for item in outcome.content if isinstance(item, TextContent))


def classify_args(sql: str = ARTICLES, **overrides: Any) -> dict[str, Any]:
    return {
        "sql": sql,
        "key_column": "id",
        "text_column": "body",
        "categories": CATEGORIES,
    } | overrides


# --- the product server ------------------------------------------------------------------------


async def test_rows_are_counted_per_category_and_kept_for_later_sql(
    fake: FakeModel, articles_db: Path, workspace: Path
) -> None:
    server = product_server(articles_db, workspace)
    outcome = await call_tool(server, "classify_text", classify_args())
    text = text_of(outcome)
    assert not outcome.is_error, text
    assert "20 rows fetched; 20 labeled with a category." in text
    for category in CATEGORIES:
        assert f"  {category}: 5" in text
    assert "Rows without a category" not in text
    # 20 rows are one batch, sent at temperature 0 with the run's key as a bearer token.
    [request] = fake.requests
    assert request["path"] == "/anthropic/v1/messages"
    assert request["body"]["temperature"] == 0
    assert request["body"]["model"] == MODEL
    assert request["headers"]["Authorization"] == f"Bearer {API_KEY}"

    # Later SQL reads the label table, with the query's other columns kept.
    later = await call_tool(
        server,
        "query_labels",
        {"sql": "SELECT year, label, count(*) AS n FROM labels_1 GROUP BY ALL ORDER BY ALL"},
    )
    assert not later.is_error, text_of(later)
    expected: dict[tuple[int, str], int] = {}
    for i, _, category in fixture_rows():
        expected[(2020 + i % 3, category)] = expected.get((2020 + i % 3, category), 0) + 1
    lines = text_of(later).splitlines()[2:-1]
    assert lines == [f"{y} | {c} | {n}" for (y, c), n in sorted(expected.items())]
    # And the file in the workspace: every row's key and label.
    with (workspace / "data_agent_labels" / "labels_1.csv").open(newline="") as f:
        stored = list(csv.DictReader(f))
    assert list(stored[0]) == ["id", "year", "label", "outcome", "model_label"]
    assert {int(r["id"]): r["label"] for r in stored} == {i: c for i, _, c in fixture_rows()}
    assert {r["outcome"] for r in stored} == {"labeled"}


async def test_rows_past_the_gateway_cap_come_in_pages(
    monkeypatch: pytest.MonkeyPatch, fake: FakeModel, articles_db: Path, workspace: Path
) -> None:
    monkeypatch.setattr(classify, "MAX_ROWS", 6)
    monkeypatch.setattr(classify, "BATCH_ROWS", 7)
    server = product_server(articles_db, workspace)
    outcome = await call_tool(server, "classify_text", classify_args())
    assert "20 rows fetched; 20 labeled with a category." in text_of(outcome)
    assert len(fake.requests) == 3
    sent = [text for request in fake.requests for text in fake.texts(request)]
    assert sorted(sent) == sorted(text for _, text, _ in fixture_rows())


async def test_too_many_rows_is_an_error_before_any_model_call(
    monkeypatch: pytest.MonkeyPatch, fake: FakeModel, articles_db: Path, workspace: Path
) -> None:
    monkeypatch.setattr(classify, "ROW_LIMIT", 19)
    server = product_server(articles_db, workspace)
    outcome = await call_tool(server, "classify_text", classify_args())
    assert outcome.is_error
    assert "the query returns 20 rows; classify_text classifies at most 19 rows" in text_of(outcome)
    assert fake.requests == []
    assert not (workspace / "data_agent_labels").exists()


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (classify_args(key_column="year"), "key_column 'year' must identify each row"),
        (classify_args(text_column="missing"), "no column 'missing'"),
        (classify_args(categories=[]), "non-empty list"),
        (classify_args(categories=["a", "a "]), "must not repeat"),
        (classify_args("SELECT id, body, 1 AS label FROM articles"), "rename the query's column"),
    ],
)
async def test_bad_requests_fail_before_any_model_call(
    args: dict[str, Any], error: str, fake: FakeModel, articles_db: Path, workspace: Path
) -> None:
    outcome = await call_tool(product_server(articles_db, workspace), "classify_text", args)
    assert outcome.is_error
    assert error in text_of(outcome)
    assert fake.requests == []


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM articles",
        "SELECT id, body FROM articles; DROP TABLE articles",
        "SELECT 1 AS id, content AS body FROM read_text('/etc/hosts')",
    ],
)
async def test_the_gateway_blocks_writes_and_file_reads(
    sql: str, fake: FakeModel, articles_db: Path, workspace: Path
) -> None:
    outcome = await call_tool(
        product_server(articles_db, workspace), "classify_text", classify_args(sql)
    )
    assert outcome.is_error
    assert "blocked by the read-only gateway" in text_of(outcome)
    assert fake.requests == []
    con = duckdb.connect(str(articles_db), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM articles").fetchone() == (20,)
    finally:
        con.close()


async def test_labels_outside_the_categories_and_failed_calls_count_in_no_category(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, articles_db: Path, workspace: Path
) -> None:
    monkeypatch.setattr(classify, "BATCH_ROWS", 1)

    def label(text: str) -> str | None:
        if "cello" in text:
            # Not a category, and quoting the key (the model never sees it; masked all the same).
            return f"chamber music {API_KEY}"
        if "fog" in text:
            return None  # left out of the reply
        return by_fixture(text)

    model = FakeModel(label, fail=lambda texts: any("record" in t for t in texts))
    sql = f"{ARTICLES} UNION ALL SELECT 99, NULL, 2020"
    with serve(model):
        for name, value in model_env(model, make_ledger(tmp_path)).items():
            monkeypatch.setenv(name, value)
        server = product_server(articles_db, workspace)
        outcome = await call_tool(server, "classify_text", classify_args(sql))
        table = await call_tool(
            server,
            "query_labels",
            {
                "sql": "SELECT id, label, outcome, model_label FROM labels_1 WHERE label IS NULL "
                "ORDER BY id"
            },
        )
    text = text_of(outcome)
    assert not outcome.is_error, text
    assert "21 rows fetched; 17 labeled with a category." in text
    assert "  weather: 4" in text and "  sport: 4" in text
    assert "  cooking: 5" in text and "  music: 4" in text
    assert "  outside_categories: 1 " in text
    assert "  no_label: 1 " in text
    assert "  failed: 1 " in text
    assert "  empty_text: 1 " in text
    assert "Labels outside the categories: 'chamber music [REDACTED]' x1" in text
    # The failed call's error is reported, the key it quoted masked.
    assert "fake failure for Bearer [REDACTED]" in text
    assert API_KEY not in text
    # 20 calls: the empty text was not sent.
    assert len(model.requests) == 20
    assert API_KEY not in (workspace / "data_agent_labels" / "labels_1.csv").read_text()
    assert text_of(table).splitlines()[2:-1] == [
        "4 | None | outside_categories | chamber music [REDACTED]",
        "6 | None | failed | None",
        "9 | None | no_label | None",
        "99 | None | empty_text | None",
    ]


async def test_the_budget_stops_the_classification_and_keeps_what_is_done(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, articles_db: Path, workspace: Path
) -> None:
    monkeypatch.setattr(classify, "BATCH_ROWS", 5)
    monkeypatch.setattr(classify, "MAX_CONCURRENT_BATCHES", 1)
    # The run has spent half a call already: after one call it is under the budget, after two
    # over it.
    ledger = make_ledger(tmp_path, budget=2 * CALL_USD, spent=0.5 * CALL_USD)
    with serve(FakeModel(by_fixture)) as model:
        for name, value in model_env(model, ledger).items():
            monkeypatch.setenv(name, value)
        server = product_server(articles_db, workspace)
        outcome = await call_tool(server, "classify_text", classify_args())
        kept = await call_tool(
            server, "query_labels", {"sql": "SELECT count(*) AS n FROM labels_1"}
        )
    text = text_of(outcome)
    assert len(model.requests) == 2
    assert "20 rows fetched; 10 labeled with a category." in text
    assert "  not classified: 10 (the run's budget of $" in text
    assert text_of(kept).splitlines()[2] == "10"
    assert ledger.tool_tokens() == ToolTokens(2000, 400, 100)


async def test_concurrent_batches_keep_to_the_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, articles_db: Path, workspace: Path
) -> None:
    """Ten batches fit eight at a time, but the budget holds two and a half calls: the first call
    runs alone to learn a call's cost, then each running batch reserves that much."""
    monkeypatch.setattr(classify, "BATCH_ROWS", 2)
    ledger = make_ledger(tmp_path, budget=2.5 * CALL_USD)
    with serve(FakeModel(by_fixture)) as model:
        for name, value in model_env(model, ledger).items():
            monkeypatch.setenv(name, value)
        outcome = await call_tool(
            product_server(articles_db, workspace), "classify_text", classify_args()
        )
    assert 2 <= len(model.requests) <= 3, text_of(outcome)
    state = ledger.run_state()
    assert state is not None
    assert ledger.spent_usd(state) <= state.budget_usd + CALL_USD


async def test_an_empty_result_classifies_nothing(
    fake: FakeModel, articles_db: Path, workspace: Path
) -> None:
    server = product_server(articles_db, workspace)
    outcome = await call_tool(server, "classify_text", classify_args(f"{ARTICLES} WHERE id < 0"))
    assert "0 rows fetched; 0 labeled with a category." in text_of(outcome)
    assert fake.requests == []


async def test_without_a_ledger_the_tool_calls_no_model(
    monkeypatch: pytest.MonkeyPatch, articles_db: Path, workspace: Path
) -> None:
    with serve(FakeModel(by_fixture)) as model:
        for name, value in model_env(model, None).items():
            monkeypatch.setenv(name, value)
        monkeypatch.delenv(LEDGER_ENV, raising=False)
        outcome = await call_tool(
            product_server(articles_db, workspace), "classify_text", classify_args()
        )
    assert outcome.is_error
    assert "no spend ledger" in text_of(outcome)
    assert model.requests == []


SWITCHES = list(itertools.product([False, True], repeat=3))


@pytest.mark.parametrize(("skill", "verifiers", "context"), SWITCHES)
async def test_every_switch_combination_serves_the_tools(
    skill: bool, verifiers: bool, context: bool, articles_db: Path, workspace: Path
) -> None:
    config = dataclasses.replace(
        load_config("full"), skill=skill, verifiers=verifiers, context=context
    )
    server = build_server(DuckDBGateway(articles_db), config, workspace=workspace)
    names = {tool.name for tool in await server.list_tools()}
    assert {"classify_text", "query_labels"} <= names


async def test_query_labels_reads_only_the_label_tables(
    fake: FakeModel, articles_db: Path, workspace: Path
) -> None:
    server = product_server(articles_db, workspace)
    await call_tool(server, "classify_text", classify_args())
    for sql, error in [
        ("SELECT * FROM articles", "Query error"),
        ("DROP TABLE labels_1", "blocked by the read-only gateway"),
        (f"SELECT * FROM read_csv('{workspace}/data_agent_labels/labels_1.csv')", "blocked"),
    ]:
        outcome = await call_tool(server, "query_labels", {"sql": sql})
        assert outcome.is_error, sql
        assert error in text_of(outcome), sql


async def test_the_server_gets_the_model_and_ledger_over_stdio(
    tmp_path: Path, articles_db: Path, workspace: Path
) -> None:
    """The variables the run sets reach the server's process (no argv, no MCP config)."""
    with serve(FakeModel(by_fixture)) as model:
        env = {**os.environ, **model_env(model, make_ledger(tmp_path))}
        params = StdioServerParameters(
            command=str(Path(sys.executable).with_name("data-agent")),
            args=[
                *("mcp-server", "--config", "baseline", "--duckdb", str(articles_db)),
                *("--workspace", str(workspace)),
            ],
            env=env,
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            outcome = await session.call_tool("classify_text", classify_args())
    assert "20 rows fetched; 20 labeled with a category." in text_of(outcome)


# --- the DAB server ----------------------------------------------------------------------------


def add_notes(root: Path) -> None:
    """A table of the sample texts in toyset's SQLite database."""
    import sqlite3

    with sqlite3.connect(root / "query_toyset" / "query_dataset" / "prices.db") as conn:
        conn.execute("CREATE TABLE notes (id INTEGER, body TEXT)")
        conn.executemany("INSERT INTO notes VALUES (?, ?)", [(i, t) for i, t, _ in fixture_rows()])


@asynccontextmanager
async def dab_with_model(
    root: Path, log: Path, model: FakeModel, ledger: Ledger, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[ClientSession]:
    for name, value in model_env(model, ledger).items():
        monkeypatch.setenv(name, value)
    async with dab_server(root, log) as session:
        yield session


PRINT = "import json\nprint('FAKE-RESULT:' + json.dumps({}))"


async def test_dab_classifies_sql_rows_into_a_python_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = make_dab(tmp_path / "dab")
    add_notes(root)
    log = tmp_path / "tools.jsonl"
    with serve(FakeModel(by_fixture)) as model:
        async with dab_with_model(root, log, model, make_ledger(tmp_path), monkeypatch) as session:
            names = {tool.name for tool in (await session.list_tools()).tools}
            args = classify_args("SELECT id, body FROM notes") | {"db_name": "prices_db"}
            outcome = await session.call_tool("classify_text", args)
            code = PRINT.format("sorted((r['id'], r['label']) for r in var_1)")
            labels = await session.call_tool("execute_python", {"code": code})
    assert "classify_text" in names
    text = text_of(outcome)
    assert not outcome.is_error, text
    assert "20 rows fetched; 20 labeled with a category." in text
    assert "Label table: variable `var_1`" in text
    _, _, data = text_of(labels).partition("\n")
    assert json.loads(data) == [[i, c] for i, _, c in fixture_rows()]
    # The rows came through DAB's query tool, the count first.
    queries = [c["args"]["query"] for c in logged_calls(log) if c["tool_name"] == "query_db"]
    assert queries[0].startswith("SELECT COUNT(*) AS n FROM (")


async def test_dab_classifies_mongo_documents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = make_dab(tmp_path / "dab")
    documents = [{"_id": i, "body": t} for i, t, _ in fixture_rows()[:4]]
    query = json.dumps({"collection": "articles", "documents": documents})
    with serve(FakeModel(by_fixture)) as model:
        async with dab_with_model(
            root, tmp_path / "t.jsonl", model, make_ledger(tmp_path), monkeypatch
        ) as session:
            args = classify_args(query, key_column="_id") | {"db_name": "articles_db"}
            outcome = await session.call_tool("classify_text", args)
    text = text_of(outcome)
    assert "4 rows fetched; 4 labeled with a category." in text
    assert "  weather: 1" in text and "  music: 1" in text


@pytest.mark.parametrize(
    ("db_name", "query"),
    [
        ("prices_db", "SELECT id, body FROM notes WHERE id < 0"),
        ("articles_db", json.dumps({"collection": "articles", "documents": []})),
    ],
)
async def test_dab_classifies_an_empty_result_as_zero_rows(
    db_name: str, query: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DAB's rows are dicts, so an empty result has no column names to check."""
    root = make_dab(tmp_path / "dab")
    add_notes(root)
    with serve(FakeModel(by_fixture)) as model:
        ledger = make_ledger(tmp_path)
        async with dab_with_model(
            root, tmp_path / "t.jsonl", model, ledger, monkeypatch
        ) as session:
            args = classify_args(query) | {"db_name": db_name}
            outcome = await session.call_tool("classify_text", args)
    assert not outcome.is_error, text_of(outcome)
    assert "0 rows fetched; 0 labeled with a category." in text_of(outcome)
    assert model.requests == []


async def test_dab_blocks_writes_before_they_reach_dab(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = make_dab(tmp_path / "dab")
    log = tmp_path / "tools.jsonl"
    with serve(FakeModel(by_fixture)) as model:
        async with dab_with_model(root, log, model, make_ledger(tmp_path), monkeypatch) as session:
            args = classify_args("DROP TABLE prices") | {"db_name": "prices_db"}
            outcome = await session.call_tool("classify_text", args)
    assert outcome.is_error
    assert "blocked by the read-only gateway" in text_of(outcome)
    assert logged_calls(log) == []
    assert model.requests == []


async def test_dab_masks_the_key_in_tool_output_and_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = make_dab(tmp_path / "dab")
    add_notes(root)
    log = tmp_path / "tools.jsonl"
    model = FakeModel(by_fixture, fail=lambda texts: True)
    with serve(model):
        async with dab_with_model(root, log, model, make_ledger(tmp_path), monkeypatch) as session:
            args = classify_args("SELECT id, body FROM notes") | {"db_name": "prices_db"}
            outcome = await session.call_tool("classify_text", args)
    text = text_of(outcome)
    assert "  failed: 20 " in text
    assert "Bearer [REDACTED]" in text
    assert API_KEY not in text
    assert API_KEY not in log.read_text()


# --- the run's spend ---------------------------------------------------------------------------


def tool_call_then(*after: Any, tokens: ToolTokens, seen: list[Any]) -> Any:
    """A scripted session: a main call that uses a tool, during which the tool server records
    `tokens` in the run's ledger, then the tool's result and `after`."""

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        seen.append(options)
        ledger = Ledger(Path(options.env[LEDGER_ENV]))
        for message in call("m1", 10, input_tokens=100):
            yield message
        seen.append(ledger.run_state())
        ledger.record("classify_text", tokens)
        yield TOOL_RESULT
        for message in after:
            yield message

    return fake_query


async def test_tool_tokens_count_in_cost_and_stop_the_run_at_the_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[Any] = []
    # $1.20 of tool input passes the $1 budget; the next main call must never start.
    fake = tool_call_then(*call("m2", 10), result(), tokens=ToolTokens(4_000_000, 0, 0), seen=seen)
    env = {"DATA_AGENT_API_KEY": API_KEY, "DATA_AGENT_BASE_URL": "https://llm.example/x"}
    outcome, events = await run_with(
        monkeypatch, tmp_path, fake, env=env, config=load_config("baseline")
    )
    options, state = seen
    # The tool server gets the model and the ledger through the environment only.
    assert options.env["DATA_AGENT_API_KEY"] == API_KEY
    assert options.env["DATA_AGENT_MODEL"] == MODEL
    assert API_KEY not in json.dumps(options.mcp_servers)
    # The ledger had the run's budget and its first call's spend when the tool ran.
    main_usd = (100 * FLASH.input + 10 * FLASH.output) / 1e6
    assert state.spent_usd == pytest.approx(main_usd)
    assert state == RunState(1.0, state.spent_usd, FLASH.input, FLASH.cached_input, FLASH.output)
    assert not outcome.finished
    final = events[-1]
    assert final["type"] == "result"
    assert final["terminal_reason"] == "budget_exhausted"
    assert final["tool_uncached_input_tokens"] == 4_000_000
    assert final["tool_cost_usd"] == pytest.approx(1.2)
    assert final["cost_usd"] == pytest.approx(main_usd + 1.2)
    assert not [e for e in events if e["type"] == "text" and "m2" in json.dumps(e)]
    # The batch guard reads the same cost.
    assert float(spend.file_spend(outcome.events_path).listed) == pytest.approx(main_usd + 1.2)
    assert API_KEY not in outcome.events_path.read_text()
    # The ledger lived outside the workspace and is gone with the run.
    ledger_dir = Path(options.env[LEDGER_ENV])
    assert tmp_path not in ledger_dir.parents
    assert not ledger_dir.exists()


async def test_tool_spend_adds_to_a_run_the_sdk_ended(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[Any] = []
    fake = tool_call_then(result(), tokens=ToolTokens(1000, 0, 100), seen=seen)
    _, events = await run_with(monkeypatch, tmp_path, fake, config=load_config("baseline"))
    final = events[-1]
    tool_usd = (1000 * FLASH.input + 100 * FLASH.output) / 1e6
    assert final["tool_cost_usd"] == pytest.approx(tool_usd)
    # result()'s own tokens (3 input, 5 cached, 7 output) plus the tool's.
    sdk_usd = (3 * FLASH.input + 5 * FLASH.cached_input + 7 * FLASH.output) / 1e6
    assert final["cost_usd"] == pytest.approx(sdk_usd + tool_usd)


async def test_a_dab_run_gives_its_server_the_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = make_dab(tmp_path / "dab")
    seen: list[Any] = []
    monkeypatch.setattr(
        dab, "query", tool_call_then(result(), tokens=ToolTokens(1000, 0, 0), seen=seen)
    )
    monkeypatch.setattr(dab, "git_commit", lambda: "c0ffee")
    query = dab.load_query(root, "toyset", "1", hints=False)
    env = {"DATA_AGENT_API_KEY": API_KEY}
    await dab.run_query(root, query, load_config("no-context"), tmp_path / "out", 1, env)
    options = seen[0]
    assert options.env[LEDGER_ENV]
    assert API_KEY not in json.dumps(options.mcp_servers)
    trace = dab.run_dir(tmp_path / "out", "toyset", "1", 0) / "events.jsonl"
    final = json.loads(trace.read_text().splitlines()[-1])
    assert final["tool_cost_usd"] == pytest.approx(1000 * FLASH.input / 1e6)
    assert API_KEY not in trace.read_text()


# --- the real model (make test-integration; the main session runs it) ---------------------------


@pytest.mark.integration
async def test_the_run_model_classifies_the_samples(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, articles_db: Path, workspace: Path
) -> None:
    settings = agent.ModelSettings.from_env(os.environ)
    price = agent.PRICES.get(settings.model)
    if price is None:
        pytest.skip(f"{settings.model} has no price, so classify_text refuses to run")
    directory = tmp_path / "spend"
    directory.mkdir()
    ledger = Ledger(directory)
    ledger.publish(RunState(0.05, 0.0, price.input, price.cached_input, price.output))
    for name, value in (settings.tool_env() | {LEDGER_ENV: str(directory)}).items():
        monkeypatch.setenv(name, value)
    server = product_server(articles_db, workspace)
    outcome = await call_tool(server, "classify_text", classify_args())
    text = text_of(outcome)
    print(text)  # the counts and the call's tokens and cost, for the cost per 1,000 rows
    assert not outcome.is_error, text
    later = await call_tool(server, "query_labels", {"sql": "SELECT id, label FROM labels_1"})
    labels = dict(line.split(" | ") for line in text_of(later).splitlines()[2:-1])
    right = sum(labels.get(str(i)) == c for i, _, c in fixture_rows())
    assert right >= 18, text
    assert any(ledger.tool_tokens())
