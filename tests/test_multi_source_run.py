"""A run with several data sources (ticket 89, .scratch/chat-ui/spec.md decision 20): what the
agent is given (the MCP server's arguments, the system prompt) and what the event file records.
The SDK's `query()` is a scripted stream, as in tests/test_agent_outcomes.py; no model is called."""

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ToolUseBlock
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_agent_outcomes import ENV, result

from data_agent import agent, cli
from data_agent.agent import ConversationTurn, Outcome, Task
from data_agent.run_config import load_config
from data_agent.web.sources import DataSource

PG_ENV = "DA_TEST_CUSTOMERS_URL"
PG_PASSWORD = "s3cret-" + "pw9"
PG_HOST = "customers-db.internal.example"
PG_URL = f"postgresql://agent:{PG_PASSWORD}@{PG_HOST}:5432/crm"


def make_db(path: Path, table: str) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute(f"CREATE TABLE {table} AS SELECT range AS id FROM range(3)")
    finally:
        con.close()
    return path


def asks(*calls: tuple[str, dict[str, Any]]) -> AssistantMessage:
    blocks = [
        ToolUseBlock(id=f"t{i}", name=name, input=args) for i, (name, args) in enumerate(calls)
    ]
    return AssistantMessage(content=list(blocks), model="m")


async def run_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    task: Task,
    *messages: Any,
    env: dict[str, str] = ENV,
    config: str = "no-context",
    turn: ConversationTurn | None = None,
) -> tuple[ClaudeAgentOptions, str, list[dict[str, Any]]]:
    """The options and the prompt the session got, and the run's events."""
    seen: list[tuple[ClaudeAgentOptions, str]] = []

    async def fake_query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        seen.append((options, prompt))
        for message in messages:
            yield message
        yield result()

    monkeypatch.setattr(agent, "query", fake_query)
    outcome = await agent.run(task, load_config(config), tmp_path / "events", env, turn=turn)
    [(options, prompt)] = seen
    events = [json.loads(line) for line in outcome.events_path.read_text().splitlines()]
    return options, prompt, events


@pytest.fixture
def two_duckdb(tmp_path: Path) -> Task:
    orders = make_db(tmp_path / "orders.duckdb", "orders")
    customers = make_db(tmp_path / "customers.duckdb", "customers")
    sources = (DataSource("orders", duckdb=orders), DataSource("customers", duckdb=customers))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return Task(text="q", type="query", workspace=workspace, sources=sources)


def mixed(tmp_path: Path) -> Task:
    orders = make_db(tmp_path / "orders.duckdb", "orders")
    sources = (DataSource("orders", duckdb=orders), DataSource("customers", postgres_env=PG_ENV))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return Task(text="q", type="query", workspace=workspace, sources=sources)


async def listed_tools(options: ClaudeAgentOptions) -> dict[str, dict[str, Any]]:
    """The tools the run's MCP server lists, started as the options name it."""
    assert isinstance(options.mcp_servers, dict)
    server: Any = options.mcp_servers[agent.MCP_SERVER]
    params = StdioServerParameters(
        command=server["command"], args=list(server["args"]), env={**os.environ, **options.env}
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
    return {tool.name: tool.input_schema for tool in tools}


# --- what the agent is given ----------------------------------------------------------------------


async def test_the_server_the_run_starts_serves_every_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, two_duckdb: Task
) -> None:
    options, _, _ = await run_task(monkeypatch, tmp_path, two_duckdb)
    tools = await listed_tools(options)
    assert tools["query_database"]["required"][0] == "database"
    assert tools["list_tables"]["required"] == ["database"]


async def test_the_system_prompt_lists_the_databases_by_name_and_kind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = ENV | {PG_ENV: PG_URL}
    options, prompt, _ = await run_task(monkeypatch, tmp_path, mixed(tmp_path), env=env)
    system = str(options.system_prompt)
    assert "- orders (DuckDB)\n- customers (PostgreSQL)\n" in system
    assert "`database`" in system
    assert "orders" in prompt and "customers" in prompt
    # Both dialects' Skills.
    assert "name: duckdb-sql\n" in system and "name: postgres-sql\n" in system


async def test_a_run_with_one_source_has_no_database_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = make_db(tmp_path / "x.duckdb", "items")
    task = Task(text="q", type="query", workspace=tmp_path, duckdb=db)
    options, _, _ = await run_task(monkeypatch, tmp_path, task)
    assert agent.DATABASES_HEADING not in str(options.system_prompt)


async def test_no_connection_string_reaches_the_arguments_the_prompts_or_the_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = ENV | {PG_ENV: PG_URL}
    query = {"database": "customers", "sql": f"SELECT '{PG_URL}', '{PG_HOST}'"}
    options, prompt, events = await run_task(
        monkeypatch, tmp_path, mixed(tmp_path), asks((agent.QUERY_TOOL, query)), env=env
    )
    given = json.dumps([options.mcp_servers, str(options.system_prompt), prompt])
    recorded = json.dumps(events)
    for secret in (PG_URL, PG_PASSWORD, PG_HOST):
        assert secret not in given
        assert secret not in recorded
    # The URL reaches the server only through the environment, under its variable.
    assert options.env[PG_ENV] == PG_URL
    assert PG_ENV in given


# --- what the event file records ------------------------------------------------------------------


async def test_each_query_event_names_its_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, two_duckdb: Task
) -> None:
    calls = asks(
        (agent.QUERY_TOOL, {"database": "orders", "sql": "SELECT 1"}),
        (agent.QUERY_TOOL, {"database": "customers", "sql": "SELECT 2"}),
    )
    _, _, events = await run_task(monkeypatch, tmp_path, two_duckdb, calls)
    sql = [(e["sql"], e["database"]) for e in events if e["type"] == "sql"]
    assert sql == [("SELECT 1", "orders"), ("SELECT 2", "customers")]
    [task] = [e for e in events if e["type"] == "task"]
    assert task["data_source"] is None


async def test_a_query_on_no_known_database_names_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, two_duckdb: Task
) -> None:
    """The server refuses such a call, so it ran on no database."""
    calls = asks(
        (agent.QUERY_TOOL, {"database": "warehouse", "sql": "SELECT 1"}),
        (agent.QUERY_TOOL, {"sql": "SELECT 2"}),
    )
    _, _, events = await run_task(monkeypatch, tmp_path, two_duckdb, calls)
    sql = [e for e in events if e["type"] == "sql"]
    assert [e["sql"] for e in sql] == ["SELECT 1", "SELECT 2"]
    assert not [e for e in sql if "database" in e]


async def test_a_one_source_query_event_keeps_its_fields_and_names_the_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The run page reads `tool_use_id` and `sql` as before; `database` is added."""
    db = make_db(tmp_path / "x.duckdb", "items")
    task = Task(text="q", type="query", workspace=tmp_path, duckdb=db)
    calls = asks((agent.QUERY_TOOL, {"sql": "SELECT 1"}))
    _, _, events = await run_task(monkeypatch, tmp_path, task, calls)
    [sql] = [e for e in events if e["type"] == "sql"]
    assert {k: v for k, v in sql.items() if k not in ("seq", "ts")} == {
        "type": "sql",
        "tool_use_id": "t0",
        "sql": "SELECT 1",
        "database": "x.duckdb",
    }


async def test_the_dictionary_of_each_source_is_built_and_served(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, two_duckdb: Task
) -> None:
    options, _, events = await run_task(monkeypatch, tmp_path, two_duckdb, config="full")
    built = [e["database"] for e in events if e["type"] == "dictionary"]
    assert built == ["orders", "customers"]
    assert isinstance(options.mcp_servers, dict)
    server: Any = options.mcp_servers[agent.MCP_SERVER]
    args: list[str] = list(server["args"])
    named = [args[i + 1] for i, arg in enumerate(args) if arg == "--source-dictionary"]
    assert named == ["orders", "customers"]


async def test_a_conversation_turn_runs_on_every_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, two_duckdb: Task
) -> None:
    """`--chat` with several sources (ticket 88 contract item 5): the turn resumes its session
    and names itself in the `task` event, and the agent still picks the database."""
    conversation = tmp_path / "c1"
    conversation.mkdir()
    turn = ConversationTurn(conversation, 2, resume="sid-1")
    calls = asks((agent.QUERY_TOOL, {"database": "customers", "sql": "SELECT 1"}))
    options, _, events = await run_task(monkeypatch, tmp_path, two_duckdb, calls, turn=turn)
    assert options.resume == "sid-1"
    assert options.env["CLAUDE_CONFIG_DIR"] == str(turn.config_dir)
    assert (await listed_tools(options))["query_database"]["required"][0] == "database"
    [task] = [e for e in events if e["type"] == "task"]
    assert (task["data_source"], task["conversation"], task["turn"]) == (None, "c1", 2)
    assert [e["database"] for e in events if e["type"] == "sql"] == ["customers"]


# --- `data-agent run --config` with `[sources.*]` -------------------------------------------------


def sources_file(tmp_path: Path, *names: str) -> Path:
    for name in names:
        make_db(tmp_path / f"{name}.duckdb", name)
    path = tmp_path / "sources.toml"
    path.write_text(
        "".join(f'[sources.{name}]\nkind = "duckdb"\npath = "{name}.duckdb"\n' for name in names)
    )
    return path


def cli_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *args: str,
    turns: list[ConversationTurn | None] | None = None,
) -> tuple[int | str | None, list[Task]]:
    """`data-agent run` with the run itself replaced: its exit code and the task it got;
    `turns` collects the conversation turn it got."""
    seen: list[Task] = []

    async def fake_run(
        task: Task, *_: Any, turn: ConversationTurn | None = None, **__: Any
    ) -> Outcome:
        seen.append(task)
        if turns is not None:
            turns.append(turn)
        return Outcome("", True, False, [], tmp_path / "events.jsonl")

    monkeypatch.setattr(cli, "run", fake_run)
    with pytest.raises(SystemExit) as exited:
        cli.main(
            [
                *("run", "--task", "q", "--workspace", str(tmp_path)),
                *("--events-dir", str(tmp_path / "events"), *args),
            ]
        )
    return exited.value.code, seen


def cli_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> Task:
    code, [task] = cli_run(tmp_path, monkeypatch, *args)
    assert code == 0
    return task


def test_a_file_with_several_sources_gives_the_run_all_of_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = sources_file(tmp_path, "orders", "customers")
    task = cli_task(tmp_path, monkeypatch, "--type", "query", "--config", str(path))
    assert [s.name for s in task.databases] == ["orders", "customers"]
    assert task.several


def test_a_file_with_one_source_runs_as_that_database_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = sources_file(tmp_path, "orders")
    task = cli_task(tmp_path, monkeypatch, "--type", "query", "--config", str(path))
    assert not task.several
    assert (task.duckdb, task.data_source_name) == ((tmp_path / "orders.duckdb"), "orders")
    assert task.database_args() == ("--duckdb", str((tmp_path / "orders.duckdb").resolve()))


def test_a_conversation_turn_takes_a_file_of_several_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = sources_file(tmp_path, "orders", "customers")
    conversation = tmp_path / "c1"
    conversation.mkdir()
    turns: list[ConversationTurn | None] = []
    code, [task] = cli_run(
        tmp_path,
        monkeypatch,
        *("--type", "query", "--config", str(path), "--chat"),
        *("--conversation-dir", str(conversation), "--turn", "2", "--resume", "sid-1"),
        turns=turns,
    )
    assert code == 0
    assert [s.name for s in task.databases] == ["orders", "customers"]
    assert turns == [ConversationTurn(conversation, 2, "sid-1")]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--type", "dbt"), "a dbt run takes one data source"),
        (("--type", "query", "--data-source-name", "x"), "--data-source-name goes with"),
    ],
)
def test_a_file_with_several_sources_is_refused_where_one_is_needed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: tuple[str, ...],
    message: str,
) -> None:
    path = sources_file(tmp_path, "orders", "customers")
    code, seen = cli_run(tmp_path, monkeypatch, *args, "--config", str(path))
    assert (code, seen) == (2, [])
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ('kind = "duckdb"\npath = "missing.duckdb"\n', "DuckDB file not found"),
        (f'kind = "postgres"\nenv = "{PG_ENV}"\n', f"{PG_ENV} is not set"),
    ],
)
def test_each_source_of_the_file_must_be_there(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entry: str,
    message: str,
) -> None:
    path = sources_file(tmp_path, "orders")
    path.write_text(path.read_text() + "[sources.other]\n" + entry)
    monkeypatch.delenv(PG_ENV, raising=False)
    code, seen = cli_run(tmp_path, monkeypatch, "--type", "query", "--config", str(path))
    assert (code, seen) == (2, [])
    assert message in capsys.readouterr().err
