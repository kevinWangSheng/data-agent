"""Seam B: the MCP server, driven by a real MCP client over stdio (spec "Testing Decisions").

External interface fixed here:

- Launch: `data-agent mcp-server --config <name> (--duckdb <path> | --postgres-env <VAR>)`,
  where VAR names an environment variable holding a Postgres connection string.
- Configs: baseline, full, no-skill, no-verifiers, no-context, full-old-skill.
- Base tools (every config): `query_database(sql)`, `list_tables(name_contains=None)`.
- Context tools (hidden by `baseline` and `no-context`): `discover(query)` returning
  `{"results": [{"ref", "summary"}]}`, `hydrate(ref)`, `dictionary_search(keyword)`.
- Verifier tools (hidden by `baseline` and `no-verifiers`), ported from SignalPilot:
  `map_columns(model_name, project_dir)`, `check_model_schema(model_name, yml_columns)`,
  `audit_model_sources(model_name, source_tables)`, `verify_model_values(model_name)`. Like
  upstream they report observations; the tests check that a broken model's defect is visible.
- Errors: a tool error is an MCP error result or text starting with "Error", with a readable
  message and no traceback.

Fixture facts used below (tests/fixtures/shop.sql): raw.orders has 37 rows; orders_enriched fans
out to 74; customer C0001 has 9 orders, shown as 18 in customer_orders_fanout.
"""

import json
import os
import re
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from seam_support import (
    CONTEXT_TOOLS,
    DATA_AGENT,
    DBT_SHOP,
    POSTGRES_URL_ENV,
    VERIFIER_TOOLS,
    build_shop_duckdb,
    duckdb_rows,
)

CONFIGS = ["baseline", "full", "no-skill", "no-verifiers", "no-context", "full-old-skill"]
BASE_TOOLS = {"query_database", "list_tables"}


@asynccontextmanager
async def mcp_server(*args: str) -> AsyncIterator[Any]:
    from mcp import ClientSession, StdioServerParameters  # pyright: ignore[reportMissingImports]
    from mcp.client.stdio import stdio_client  # pyright: ignore[reportMissingImports]

    params = StdioServerParameters(
        command=str(DATA_AGENT), args=["mcp-server", *args], env=dict(os.environ)
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


async def tool_names(session: Any) -> set[str]:
    listed = await session.list_tools()
    return {tool.name for tool in listed.tools}


async def call(session: Any, name: str, arguments: dict[str, Any]) -> Any:
    from mcp.types import CallToolResult  # pyright: ignore[reportMissingImports]

    result = await session.call_tool(name, arguments)
    assert isinstance(result, CallToolResult), f"{name} returned {type(result).__name__}"
    return result


def text_of(result: Any) -> str:
    from mcp.types import TextContent  # pyright: ignore[reportMissingImports]

    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def payload(result: Any) -> Any:
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(text_of(result))


def is_tool_error(result: Any) -> bool:
    return bool(result.is_error) or text_of(result).lstrip().lower().startswith("error")


# Wording of the gateway's own rejections (SignalPilot engine/). A database driver refusing a write
# on a read-only connection says "read-only" instead, so these words show the gateway caught it.
GATEWAY_WORDS = ("blocked", "not allowed", "not permitted", "multiple statements", "stacking")


def assert_readable_block(result: Any, sql: str) -> None:
    text = text_of(result)
    assert is_tool_error(result), f"not blocked: {sql!r} -> {text!r}"
    assert "Traceback" not in text, f"traceback leaked for {sql!r}"
    lowered = text.lower()
    assert any(word in lowered for word in GATEWAY_WORDS), (
        f"not a gateway block: {sql!r} -> {text!r}"
    )


# The Postgres variant carries its own xfail: ticket 08 removes it once the Postgres dialect works.
POSTGRES = pytest.param(
    "postgres",
    marks=[pytest.mark.integration, pytest.mark.xfail(strict=True, reason="ticket 08")],
)


@pytest.fixture(params=["duckdb", POSTGRES])
def db_args(request: pytest.FixtureRequest, tmp_path: Path) -> list[str]:
    """Server arguments for the fixture database, once per SQL dialect."""
    if request.param == "duckdb":
        return ["--duckdb", str(build_shop_duckdb(tmp_path / "shop.duckdb"))]
    request.getfixturevalue("postgres_url")
    return ["--postgres-env", POSTGRES_URL_ENV]


@pytest.fixture
def dialect(db_args: list[str]) -> str:
    return "duckdb" if db_args[0] == "--duckdb" else "postgres"


async def orders_count(session: Any) -> str:
    result = await call(session, "query_database", {"sql": "SELECT count(*) AS n FROM raw.orders"})
    assert not is_tool_error(result), text_of(result)
    return text_of(result)


# --- Tool list follows the run config -------------------------------------------------------


@pytest.mark.parametrize("config", CONFIGS)
async def test_base_tools_in_every_config(config: str, shop_duckdb: Path) -> None:
    async with mcp_server("--config", config, "--duckdb", str(shop_duckdb)) as session:
        assert await tool_names(session) >= BASE_TOOLS


# (config, layer on). Configs that switch a layer off pass as soon as ticket 05 adds the configs;
# the others wait for the ticket that adds the layer's tools. Each case carries its own xfail.
CONTEXT_CASES = [
    pytest.param("baseline", False),
    pytest.param("full", True, marks=pytest.mark.xfail(strict=True, reason="ticket 06")),
    pytest.param("no-skill", True, marks=pytest.mark.xfail(strict=True, reason="ticket 06")),
    pytest.param("no-verifiers", True, marks=pytest.mark.xfail(strict=True, reason="ticket 06")),
    pytest.param("no-context", False),
    pytest.param("full-old-skill", True, marks=pytest.mark.xfail(strict=True, reason="ticket 06")),
]
VERIFIER_CASES = [
    pytest.param("baseline", False),
    pytest.param("full", True, marks=pytest.mark.xfail(strict=True, reason="ticket 07")),
    pytest.param("no-skill", True, marks=pytest.mark.xfail(strict=True, reason="ticket 07")),
    pytest.param("no-verifiers", False),
    pytest.param("no-context", True, marks=pytest.mark.xfail(strict=True, reason="ticket 07")),
    pytest.param("full-old-skill", True, marks=pytest.mark.xfail(strict=True, reason="ticket 07")),
]


@pytest.mark.parametrize(("config", "layer_on"), CONTEXT_CASES)
async def test_context_tools_follow_config(config: str, layer_on: bool, shop_duckdb: Path) -> None:
    async with mcp_server("--config", config, "--duckdb", str(shop_duckdb)) as session:
        names = await tool_names(session)
    if layer_on:
        assert names >= CONTEXT_TOOLS
    else:
        assert not names & CONTEXT_TOOLS


@pytest.mark.parametrize(("config", "layer_on"), VERIFIER_CASES)
async def test_verifier_tools_follow_config(config: str, layer_on: bool, shop_duckdb: Path) -> None:
    async with mcp_server("--config", config, "--duckdb", str(shop_duckdb)) as session:
        names = await tool_names(session)
    if layer_on:
        assert names >= VERIFIER_TOOLS
    else:
        assert not names & VERIFIER_TOOLS


# --- Read-only gateway ----------------------------------------------------------------------


async def test_select_executes(db_args: list[str]) -> None:
    async with mcp_server("--config", "full", *db_args) as session:
        result = await call(
            session,
            "query_database",
            {"sql": "SELECT customer_name FROM raw.customers WHERE customer_id = 'C0008'"},
        )
    assert not is_tool_error(result), text_of(result)
    assert "Harper Voss" in text_of(result)


DDL = [
    "DROP TABLE raw.orders",
    "CREATE TABLE raw.copy_of_orders AS SELECT * FROM raw.orders",
    "ALTER TABLE raw.orders ADD COLUMN note VARCHAR",
    "TRUNCATE raw.orders",
]


async def test_ddl_blocked(db_args: list[str]) -> None:
    async with mcp_server("--config", "full", *db_args) as session:
        before = await orders_count(session)
        for sql in DDL:
            assert_readable_block(await call(session, "query_database", {"sql": sql}), sql)
        assert await orders_count(session) == before


DML = {
    "duckdb": [
        "DELETE FROM raw.orders",
        "UPDATE raw.orders SET amount = 0",
        "INSERT INTO raw.orders VALUES (99, 'C0001', 'completed', 1.00, DATE '2026-01-01')",
        "SELECT 1; DELETE FROM raw.orders",
    ],
    "postgres": [
        "DELETE FROM raw.orders",
        "UPDATE raw.orders SET amount = 0",
        "INSERT INTO raw.orders VALUES (99, 'C0001', 'completed', 1.00, DATE '2026-01-01')",
        "SELECT 1; DELETE FROM raw.orders",
        "WITH gone AS (DELETE FROM raw.orders RETURNING *) SELECT count(*) FROM gone",
    ],
}


async def test_dml_blocked(db_args: list[str], dialect: str) -> None:
    async with mcp_server("--config", "full", *db_args) as session:
        before = await orders_count(session)
        for sql in DML[dialect]:
            assert_readable_block(await call(session, "query_database", {"sql": sql}), sql)
        assert await orders_count(session) == before


DANGEROUS = {
    "duckdb": [
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "SELECT * FROM read_text('/etc/hosts')",
        "SELECT * FROM glob('/*')",
        "COPY raw.orders TO '/tmp/data-agent-exfil.csv'",
        "ATTACH ':memory:' AS scratch",
        "INSTALL httpfs",
    ],
    "postgres": [
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_ls_dir('.')",
        "SELECT pg_sleep(5)",
        "COPY raw.orders TO PROGRAM 'id'",
    ],
}


async def test_dangerous_functions_blocked(db_args: list[str], dialect: str) -> None:
    async with mcp_server("--config", "full", *db_args) as session:
        for sql in DANGEROUS[dialect]:
            assert_readable_block(await call(session, "query_database", {"sql": sql}), sql)


# --- Context tools (DuckDB) -----------------------------------------------------------------


def web_events_columns(shop_duckdb: Path) -> list[str]:
    rows = duckdb_rows(
        shop_duckdb,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'raw' AND table_name = 'web_events'",
    )
    return [row[0] for row in rows]


async def discover_web_events(session: Any) -> tuple[Any, str]:
    result = await call(session, "discover", {"query": "web events"})
    assert not is_tool_error(result), text_of(result)
    refs = [item["ref"] for item in payload(result)["results"]]
    matches = [ref for ref in refs if "web_events" in ref]
    assert matches, f"no reference to raw.web_events in {refs}"
    return result, matches[0]


@pytest.mark.xfail(strict=True, reason="ticket 06")
async def test_discover_returns_refs_and_short_summaries(shop_duckdb: Path) -> None:
    async with mcp_server("--config", "full", "--duckdb", str(shop_duckdb)) as session:
        result, _ = await discover_web_events(session)
    results = payload(result)["results"]
    for item in results:
        assert set(item) == {"ref", "summary"}, item
        assert isinstance(item["ref"], str) and item["ref"]
        assert len(item["summary"]) <= 200, item
    columns = web_events_columns(shop_duckdb)
    assert len(columns) == 40
    text = text_of(result)
    assert not all(column in text for column in columns), "discover dumped the full schema"


@pytest.mark.xfail(strict=True, reason="ticket 06")
async def test_hydrate_returns_full_object(shop_duckdb: Path) -> None:
    async with mcp_server("--config", "full", "--duckdb", str(shop_duckdb)) as session:
        _, ref = await discover_web_events(session)
        result = await call(session, "hydrate", {"ref": ref})
    assert not is_tool_error(result), text_of(result)
    text = text_of(result)
    missing = [column for column in web_events_columns(shop_duckdb) if column not in text]
    assert not missing, f"hydrate left out columns: {missing}"


@pytest.mark.xfail(strict=True, reason="ticket 06")
async def test_dictionary_search_returns_real_values(shop_duckdb: Path) -> None:
    async with mcp_server("--config", "full", "--duckdb", str(shop_duckdb)) as session:
        result = await call(session, "dictionary_search", {"keyword": "gold"})
    assert not is_tool_error(result), text_of(result)
    text = text_of(result)
    assert "tier" in text
    for value in ("Gold", "GOLD", "gold_tier"):
        assert value in text, f"real value {value!r} missing"


# --- The six tools the verifiers use (2 base, 4 verifier-only): a passing and a failing case each


async def test_list_tables(db_args: list[str]) -> None:
    async with mcp_server("--config", "full", *db_args) as session:
        everything = await call(session, "list_tables", {})
        nothing = await call(session, "list_tables", {"name_contains": "no_such_table"})
    assert not is_tool_error(everything), text_of(everything)
    for table in ("order_items", "web_events", "stg_customers", "customer_orders_fanout"):
        assert table in text_of(everything)
    assert "Traceback" not in text_of(nothing)
    for table in ("order_items", "web_events", "stg_customers", "customer_orders"):
        assert table not in text_of(nothing)


async def test_query_database(db_args: list[str]) -> None:
    async with mcp_server("--config", "full", *db_args) as session:
        ok = await call(session, "query_database", {"sql": "SELECT count(*) AS n FROM raw.orders"})
        bad = await call(session, "query_database", {"sql": "SELECT * FROM raw.no_such_table"})
    assert not is_tool_error(ok), text_of(ok)
    assert "37" in text_of(ok)
    assert is_tool_error(bad)
    assert "no_such_table" in text_of(bad)
    assert "Traceback" not in text_of(bad)


@pytest.mark.xfail(strict=True, reason="ticket 07")
async def test_map_columns(db_args: list[str], tmp_path: Path) -> None:
    project = tmp_path / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    async with mcp_server("--config", "full", *db_args) as session:
        ok = await call(
            session, "map_columns", {"model_name": "customer_orders", "project_dir": str(project)}
        )
        bad = await call(
            session, "map_columns", {"model_name": "no_such_model", "project_dir": str(project)}
        )
    assert not is_tool_error(ok), text_of(ok)
    assert "stg_orders" in text_of(ok)
    assert "amount" in text_of(ok)
    assert is_tool_error(bad)
    assert "no_such_model" in text_of(bad)


@pytest.mark.xfail(strict=True, reason="ticket 07")
async def test_check_model_schema(db_args: list[str]) -> None:
    async with mcp_server("--config", "full", *db_args) as session:
        ok = await call(
            session,
            "check_model_schema",
            {
                "model_name": "customer_orders",
                "yml_columns": "customer_id,customer_name,n_orders,total_amount",
            },
        )
        bad = await call(
            session,
            "check_model_schema",
            {
                "model_name": "customer_orders",
                "yml_columns": "customer_id,customer_name,n_orders,lifetime_value",
            },
        )
    assert not is_tool_error(ok), text_of(ok)
    for column in ("customer_id", "customer_name", "n_orders", "total_amount"):
        assert column in text_of(ok)
    assert not is_tool_error(bad), text_of(bad)
    assert "lifetime_value" in text_of(bad)


@pytest.mark.xfail(strict=True, reason="ticket 07")
async def test_audit_model_sources(db_args: list[str]) -> None:
    async with mcp_server("--config", "full", *db_args) as session:
        ok = await call(
            session,
            "audit_model_sources",
            {"model_name": "stg_orders", "source_tables": "raw.orders"},
        )
        bad = await call(
            session,
            "audit_model_sources",
            {"model_name": "orders_enriched", "source_tables": "raw.orders"},
        )
    assert not is_tool_error(ok), text_of(ok)
    assert "37" in text_of(ok)
    assert "74" not in text_of(ok)
    assert not is_tool_error(bad), text_of(bad)
    assert "74" in text_of(bad) and "37" in text_of(bad)


@pytest.mark.xfail(strict=True, reason="ticket 07")
async def test_verify_model_values(db_args: list[str]) -> None:
    async with mcp_server("--config", "full", *db_args) as session:
        ok = await call(session, "verify_model_values", {"model_name": "customer_orders"})
        bad = await call(session, "verify_model_values", {"model_name": "customer_orders_fanout"})
    assert not is_tool_error(ok), text_of(ok)
    assert "n_orders" in text_of(ok)
    assert re.search(r"\b9\b", text_of(ok))
    assert not re.search(r"\b18\b", text_of(ok))
    assert not is_tool_error(bad), text_of(bad)
    assert re.search(r"\b18\b", text_of(bad))
