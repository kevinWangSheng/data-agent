"""Other runs' scratch schemas stay out of the agent's view (ticket 140, item 3).

dbt_execute's `scratch` target writes to `da_run_<run id>` (`dbt_execute.scratch_schema`), and
those schemas are kept after the run. The listing, the catalog the base and context tools look
tables up in, and the value dictionary show none of another run's; the run's own one stays in
view, so the agent can query the models it just built. A schema whose name only looks alike
(`da_runxy`) is no scratch schema. Driven through the MCP server's tools, in-process, on DuckDB
(and on Postgres as `integration`), and once over stdio through `data-agent mcp-server`.
"""

import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
import psycopg
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from data_agent.context_tools import DictionaryBuild
from data_agent.db_facts import HiddenSchemas
from data_agent.dbt_execute import scratch_schema
from data_agent.gateway import DuckDBGateway, Gateway, PostgresGateway
from data_agent.mcp_server import build_multi_server, build_server
from data_agent.run_config import load_config

DATA_AGENT = Path(sys.executable).with_name("data-agent")
OWN_RUN = "mine-1234abcd"
OWN = scratch_schema(OWN_RUN)
OTHER = "da_run_other_5678"
LOOKALIKE = "da_runxy"
# A model with a custom schema materializes in `<scratch schema>_<custom>` (dbt's
# generate_schema_name, `dbt_execute.tool_description`): the run's own stays, another's goes.
OWN_CUSTOM = f"{OWN}_staging"
OTHER_CUSTOM = f"{OTHER}_staging"


def schema_sql(schema: str) -> str:
    return (
        f"CREATE SCHEMA {schema}; "
        f"CREATE TABLE {schema}.stage_{schema} AS "
        "SELECT 'shipped' AS status FROM range(3) UNION ALL SELECT 'returned'"
    )


DUCKDB_SQL = [
    "CREATE TABLE orders AS SELECT 'open' AS status FROM range(3)",
    schema_sql(OTHER),
    schema_sql(LOOKALIKE),
    schema_sql(OWN),
    schema_sql(OWN_CUSTOM),
    schema_sql(OTHER_CUSTOM),
]


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "shop.duckdb"
    con = duckdb.connect(str(path))
    try:
        for statement in DUCKDB_SQL:
            con.execute(statement)
    finally:
        con.close()
    return path


def text_of(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


async def call(
    gateway: Gateway, tool: str, arguments: dict[str, object], own_schema: str | None = None
) -> str:
    server = build_server(gateway, load_config("full"), own_schema=own_schema)
    try:
        result = await server.call_tool(tool, arguments)
    except ToolError as exc:
        return str(exc)
    assert isinstance(result, CallToolResult)
    return text_of(result)


def discovered(text: str) -> set[str]:
    return {item["ref"] for item in json.loads(text)["results"]}


def dictionary_refs(gateway: Gateway, own_schema: str | None = None) -> set[str]:
    return {entry.ref for entry in DictionaryBuild(gateway, own_schema=own_schema).run().entries}


# --- Without a run of its own (a server started on its own): no scratch schema is shown -------


async def test_list_tables_shows_no_other_runs_schema(db: Path) -> None:
    text = await call(DuckDBGateway(db), "list_tables", {"columns": False})
    assert "main.orders" in text
    assert f"{LOOKALIKE}.stage_{LOOKALIKE}" in text
    assert OTHER not in text
    assert OWN not in text


async def test_the_catalog_shows_no_other_runs_schema(db: Path) -> None:
    refs = discovered(await call(DuckDBGateway(db), "discover", {"query": "stage status"}))
    assert f"{LOOKALIKE}.stage_{LOOKALIKE}" in refs
    assert not any(ref.startswith("da_run_") for ref in refs)
    described = await call(DuckDBGateway(db), "describe_table", {"table": f"{OTHER}.stage_{OTHER}"})
    assert "no table or view" in described


def test_the_dictionary_samples_no_other_runs_schema(db: Path) -> None:
    refs = dictionary_refs(DuckDBGateway(db))
    assert {"main.orders", f"{LOOKALIKE}.stage_{LOOKALIKE}"} <= refs
    assert not any(ref.startswith("da_run_") for ref in refs)


# --- A run's server: its own scratch schema stays in view -------------------------------------


async def test_list_tables_keeps_the_runs_own_scratch_schema(db: Path) -> None:
    text = await call(DuckDBGateway(db), "list_tables", {"columns": False}, own_schema=OWN)
    assert f"{OWN}.stage_{OWN}" in text
    assert OTHER not in text


async def test_the_catalog_keeps_the_runs_own_scratch_schema(db: Path) -> None:
    gateway = DuckDBGateway(db)
    refs = discovered(await call(gateway, "discover", {"query": "stage status"}, own_schema=OWN))
    assert f"{OWN}.stage_{OWN}" in refs
    assert not any(ref.startswith(OTHER) for ref in refs)
    described = await call(gateway, "describe_table", {"table": f"{OWN}.stage_{OWN}"}, OWN)
    assert "status" in described


def test_the_dictionary_keeps_the_runs_own_scratch_schema(db: Path) -> None:
    refs = dictionary_refs(DuckDBGateway(db), own_schema=OWN)
    assert f"{OWN}.stage_{OWN}" in refs
    assert not any(ref.startswith(OTHER) for ref in refs)


async def test_a_server_over_several_databases_hides_other_runs_too(db: Path) -> None:
    gateways = {"a": DuckDBGateway(db), "b": DuckDBGateway(db)}
    server = build_multi_server(gateways, load_config("full"), own_schema=OWN)
    result = await server.call_tool("list_tables", {"database": "b", "columns": False})
    assert isinstance(result, CallToolResult)
    text = text_of(result)
    assert f"{OWN}.stage_{OWN}" in text
    assert OTHER not in text


# --- The command line: `--run-id` names the run's own scratch schema ---------------------------


@asynccontextmanager
async def stdio_server(*args: str) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(
        command=str(DATA_AGENT), args=["mcp-server", *args], env=dict(os.environ)
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


async def test_the_server_of_a_run_keeps_the_schema_its_run_id_names(
    db: Path, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    async with stdio_server(
        *("--config", "baseline", "--duckdb", str(db)),
        *("--workspace", str(workspace), "--sandbox", "local", "--run-id", OWN_RUN),
    ) as session:
        text = text_of(await session.call_tool("list_tables", {"columns": False}))
    assert f"{OWN}.stage_{OWN}" in text
    assert OTHER not in text


# --- Postgres (DATA_AGENT_TEST_POSTGRES_URL) ---------------------------------------------------

PG_URL_ENV = "DATA_AGENT_TEST_POSTGRES_URL"
PG_SCHEMAS = (OTHER, LOOKALIKE, OWN, OWN_CUSTOM, OTHER_CUSTOM, "shop140")


@pytest.fixture
def pg_url() -> str:
    url = os.environ.get(PG_URL_ENV)
    if not url:
        pytest.fail(f"{PG_URL_ENV} is not set")
    with psycopg.connect(url, autocommit=True) as con:
        for schema in PG_SCHEMAS:
            con.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")  # pyright: ignore[reportArgumentType, reportCallIssue]
            sql = schema_sql(schema).replace("range(3)", "generate_series(1, 3)")
            for statement in sql.split("; "):
                con.execute(statement)  # pyright: ignore[reportArgumentType, reportCallIssue]
    return url


@pytest.mark.integration
async def test_postgres_hides_other_runs_and_keeps_the_runs_own(pg_url: str) -> None:
    gateway = PostgresGateway(pg_url)
    listed = await call(gateway, "list_tables", {"columns": False}, own_schema=OWN)
    assert "shop140.stage_shop140" in listed
    assert f"{LOOKALIKE}.stage_{LOOKALIKE}" in listed
    assert f"{OWN}.stage_{OWN}" in listed
    assert OTHER not in listed
    refs = discovered(await call(gateway, "discover", {"query": "stage"}, own_schema=OWN))
    assert f"{OWN}.stage_{OWN}" in refs
    assert not any(ref.startswith(OTHER) for ref in refs)
    sampled = dictionary_refs(gateway, own_schema=OWN)
    assert f"{OWN}.stage_{OWN}" in sampled
    assert not any(ref.startswith(OTHER) for ref in sampled)


@pytest.mark.integration
async def test_postgres_without_a_run_hides_every_scratch_schema(pg_url: str) -> None:
    gateway = PostgresGateway(pg_url)
    listed = await call(gateway, "list_tables", {"columns": False})
    assert "shop140.stage_shop140" in listed
    assert "da_run_" not in listed
    assert not any(ref.startswith("da_run_") for ref in dictionary_refs(gateway))


async def test_the_runs_own_custom_schemas_stay_and_other_runs_go(db: Path) -> None:
    """Codex review of ticket 140: a model with a custom schema is the run's own too."""
    gateway = DuckDBGateway(db)
    listed = await call(gateway, "list_tables", {"columns": False}, own_schema=OWN)
    assert f"{OWN_CUSTOM}.stage_{OWN_CUSTOM}" in listed
    assert OTHER not in listed
    described = await call(
        gateway, "describe_table", {"table": f"{OWN_CUSTOM}.stage_{OWN_CUSTOM}"}, OWN
    )
    assert "status" in described
    assert f"{OWN_CUSTOM}.stage_{OWN_CUSTOM}" in dictionary_refs(gateway, own_schema=OWN)


@pytest.mark.parametrize(("prefix", "kept"), [("", None), ("da_run_", ""), ("da_run_", "x'y")])
def test_hidden_schemas_take_only_plain_names(prefix: str, kept: str | None) -> None:
    """They go into SQL as they are; an empty prefix would hide every schema."""
    with pytest.raises(ValueError, match="name"):
        HiddenSchemas(prefix, kept)
