"""Postgres as a data source (ticket 08): the gateway's connection, the CLI's `--postgres-env`, and
what a Postgres run hands the agent. Tests that need a database read DATA_AGENT_TEST_POSTGRES_URL
(a scratch database owned by this worktree) and are `integration`; seam B covers the MCP tools."""

import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from data_agent import agent
from data_agent.agent import DBT_SKILLS, QUERY_SKILLS, SKILLS_DIR, ConfigError, Task
from data_agent.gateway import PostgresGateway, QueryBlocked, QueryFailed
from data_agent.run_config import load_config

DATA_AGENT = Path(sys.executable).with_name("data-agent")
URL_ENV = "DATA_AGENT_TEST_POSTGRES_URL"
# Nothing listens on port 1; the password and host must not reach the error text.
UNREACHABLE = "postgresql://someone:hunter2-secret@127.0.0.1:1/nowhere?connect_timeout=3"


def test_unreachable_database_is_a_readable_error_without_credentials() -> None:
    with pytest.raises(QueryFailed) as caught:
        PostgresGateway(UNREACHABLE).query("SELECT 1")
    message = str(caught.value)
    assert "connect" in message.lower()
    assert "hunter2-secret" not in message
    assert "127.0.0.1" not in message


def test_gateway_blocks_before_connecting() -> None:
    with pytest.raises(QueryBlocked):
        PostgresGateway(UNREACHABLE).query("DELETE FROM raw.orders")


def cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("DATA_AGENT_")}
    base["DATA_AGENT_API_KEY"] = "sk-test-not-used"
    return subprocess.run(
        [str(DATA_AGENT), *args], env=base | (env or {}), capture_output=True, text=True, timeout=60
    )


def test_mcp_server_rejects_an_unset_postgres_variable() -> None:
    proc = cli("mcp-server", "--config", "full", "--postgres-env", "DA_TEST_NO_SUCH_VAR")
    assert proc.returncode == 2
    assert "DA_TEST_NO_SUCH_VAR" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_mcp_server_takes_one_database(tmp_path: Path) -> None:
    proc = cli(
        "mcp-server",
        *("--config", "full", "--duckdb", str(tmp_path / "x.duckdb")),
        *("--postgres-env", "DA_TEST_URL"),
        env={"DA_TEST_URL": UNREACHABLE},
    )
    assert proc.returncode == 2
    assert "not allowed with" in proc.stderr


def test_run_rejects_an_unset_postgres_variable(tmp_path: Path) -> None:
    proc = cli(
        "run",
        *("--task", "q", "--type", "query", "--workspace", str(tmp_path)),
        *("--postgres-env", "DA_TEST_NO_SUCH_VAR", "--config", "full"),
        *("--events-dir", str(tmp_path / "events")),
    )
    assert proc.returncode == 2
    assert "DA_TEST_NO_SUCH_VAR" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not (tmp_path / "events").exists()


async def run_postgres(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    task_type: str,
    said: str = "done",
    url: str = UNREACHABLE,
) -> tuple[Any, list[dict[str, Any]]]:
    seen: list[Any] = []

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        options.user_prompt = prompt
        seen.append(options)
        yield AssistantMessage(content=[TextBlock(text=said)], model="m")
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            result="42",
        )

    monkeypatch.setattr(agent, "query", fake_query)
    monkeypatch.setattr(agent.shutil, "which", lambda *_, **__: "/usr/bin/dbt")
    task = Task(text="q", type=task_type, workspace=tmp_path, postgres_env="DA_TEST_URL")  # pyright: ignore[reportArgumentType]
    env = {"DATA_AGENT_API_KEY": "sk-test", "DA_TEST_URL": url}
    outcome = await agent.run(task, load_config("full"), tmp_path / "events", env)
    events = [json.loads(line) for line in outcome.events_path.read_text().splitlines()]
    return seen[0], events


@pytest.mark.parametrize("task_type", ["query", "dbt"])
async def test_postgres_run_serves_the_database_by_variable_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, task_type: str
) -> None:
    options, _ = await run_postgres(monkeypatch, tmp_path, task_type)
    args = options.mcp_servers[agent.MCP_SERVER]["args"]
    assert args[args.index("--postgres-env") + 1] == "DA_TEST_URL"
    assert "--duckdb" not in args
    # The URL reaches the server through the SDK subprocess's environment, never the config.
    assert "hunter2-secret" not in json.dumps(options.mcp_servers)
    assert "Database: PostgreSQL" in options.user_prompt


@pytest.mark.parametrize(("task_type", "names"), [("query", QUERY_SKILLS), ("dbt", DBT_SKILLS)])
async def test_postgres_run_loads_the_postgres_dialect_skill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, task_type: str, names: tuple[str, ...]
) -> None:
    options, _ = await run_postgres(monkeypatch, tmp_path, task_type)
    assert "name: postgres-sql" in options.system_prompt
    assert "name: duckdb-sql" not in options.system_prompt
    for name in names:
        if name != "duckdb-sql":
            assert f"name: {name}" in options.system_prompt, name


async def test_postgres_url_and_password_are_masked_in_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, events = await run_postgres(
        monkeypatch, tmp_path, "query", said=f"url {UNREACHABLE} and hunter2-secret at 127.0.0.1"
    )
    text = json.dumps(events)
    assert "hunter2-secret" not in text
    assert "127.0.0.1" not in text


async def test_bare_host_name_is_masked_in_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = "postgresql://someone:hunter2-secret@prod-db:5432/nowhere"
    _, events = await run_postgres(
        monkeypatch, tmp_path, "query", said='host name "prod-db" not known', url=url
    )
    assert "prod-db" not in json.dumps(events)


async def test_run_hands_the_database_variable_from_its_env_to_the_sdk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The MCP server reads the URL from the SDK subprocess's environment. Through the Python
    API, `run()`'s `env` is the source: the variable goes over from it, and nothing else does."""
    url = "postgresql://someone:hunter2-secret@from-env:5432/right"
    monkeypatch.setenv("DA_TEST_URL", "postgresql://someone@parent:5432/wrong")
    options, _ = await run_postgres(monkeypatch, tmp_path, "query", url=url)
    assert options.env["DA_TEST_URL"] == url
    settings = agent.ModelSettings.from_env({"DATA_AGENT_API_KEY": "sk-test"})
    assert set(options.env) == {*settings.sdk_env(), "DA_TEST_URL"}


async def test_run_without_the_database_variable_in_its_env_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_query(**_: Any) -> AsyncIterator[Any]:
        raise AssertionError("the run must not start")

    monkeypatch.setattr(agent, "query", no_query)
    task = Task(text="q", type="query", workspace=tmp_path, postgres_env="DA_TEST_URL")
    with pytest.raises(ConfigError, match="DA_TEST_URL"):
        await agent.run(
            task, load_config("full"), tmp_path / "events", {"DATA_AGENT_API_KEY": "sk-test"}
        )
    assert not (tmp_path / "events").exists()


def test_postgres_skill_names_only_real_tools_and_no_scoring() -> None:
    text = (SKILLS_DIR / "postgres-sql" / "SKILL.md").read_text()
    assert text.startswith("---\nname: postgres-sql\n")
    lowered = text.lower()
    for term in ("benchmark", "evaluator", "tolerance", "result.csv", "jaffle"):
        assert term not in lowered, term


# --- Against a real database ----------------------------------------------------------------


@pytest.fixture
def postgres_url() -> str:
    url = os.environ.get(URL_ENV)
    if not url:
        pytest.fail(f"{URL_ENV} is not set")
    return url


@pytest.mark.integration
def test_select_returns_rows(postgres_url: str) -> None:
    result = PostgresGateway(postgres_url).query("SELECT 2 AS a, 'x' AS b")
    assert (result.columns, result.rows) == (["a", "b"], [(2, "x")])


@pytest.mark.integration
def test_connection_is_read_only_behind_the_gateway(postgres_url: str) -> None:
    """nextval() passes the gateway's checks; the read-only transaction still refuses it."""
    import psycopg

    with psycopg.connect(postgres_url, autocommit=True) as con:
        con.execute("DROP SEQUENCE IF EXISTS da_test_seq")
        con.execute("CREATE SEQUENCE da_test_seq")
    gateway = PostgresGateway(postgres_url)
    with pytest.raises(QueryFailed, match="read-only"):
        gateway.query("SELECT nextval('da_test_seq')")
    # The failed statement leaves the connection usable.
    assert gateway.query("SELECT 1").rows == [(1,)]


@pytest.mark.integration
def test_long_query_times_out(postgres_url: str) -> None:
    gateway = PostgresGateway(postgres_url, timeout_seconds=0.5)
    started = time.monotonic()
    with pytest.raises(QueryFailed, match=r"timed out after 0\.5s"):
        gateway.query(
            "SELECT count(*) FROM generate_series(1, 100000) AS a, generate_series(1, 100000) AS b"
        )
    assert time.monotonic() - started < 10


@pytest.mark.integration
def test_lost_connection_is_a_readable_error_and_reconnects(postgres_url: str) -> None:
    import psycopg

    gateway = PostgresGateway(postgres_url)
    (pid,) = gateway.query("SELECT pg_backend_pid()").rows[0]
    with psycopg.connect(postgres_url, autocommit=True) as con:
        con.execute("SELECT pg_terminate_backend(%s)", (pid,))
    with pytest.raises(QueryFailed):
        gateway.query("SELECT 1")
    assert gateway.query("SELECT 1").rows == [(1,)]


@pytest.mark.integration
def test_connection_string_options_are_kept(postgres_url: str) -> None:
    """The gateway adds its statement timeout to the URL's own options, such as a search_path."""
    import psycopg

    with psycopg.connect(postgres_url, autocommit=True) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS da_opt")
        con.execute("CREATE TABLE IF NOT EXISTS da_opt.only_here (x int)")
    sep = "&" if "?" in postgres_url else "?"
    url = f"{postgres_url}{sep}options=-csearch_path%3Dda_opt"
    assert PostgresGateway(url).query("SELECT count(*) FROM only_here").rows == [(0,)]


@pytest.mark.integration
def test_concurrent_queries_do_not_share_a_failed_transaction(postgres_url: str) -> None:
    """MCP runs tool calls on worker threads; a failing statement must not poison another."""
    from concurrent.futures import ThreadPoolExecutor

    gateway = PostgresGateway(postgres_url)

    def one(i: int) -> str:
        try:
            gateway.query("SELECT * FROM no_such_table_x" if i % 2 else "SELECT 1")
        except QueryFailed as exc:
            return str(exc)
        return "ok"

    with ThreadPoolExecutor(8) as pool:
        outcomes = list(pool.map(one, range(200)))
    assert outcomes[0::2] == ["ok"] * 100
    assert all("no_such_table_x" in text for text in outcomes[1::2])


@pytest.mark.integration
def test_postgres_skill_states_division_as_postgres_does_it(postgres_url: str) -> None:
    text = (SKILLS_DIR / "postgres-sql" / "SKILL.md").read_text()
    assert PostgresGateway(postgres_url).query("SELECT 5 / 2, 5 / 2.0").rows[0][0] == 2
    assert "`5 / 2 = 2`" in text


@pytest.mark.integration
def test_postgres_skill_date_guard_rejects_impossible_dates(postgres_url: str) -> None:
    text = (SKILLS_DIR / "postgres-sql" / "SKILL.md").read_text()
    guard = (
        r"CASE WHEN col ~ '^\d{4}-\d{2}-\d{2}$' AND pg_input_is_valid(col, 'date') "
        "THEN col::date END"
    )
    assert f"`{guard}`" in text
    values = "('2024-02-29'), ('2026-99-31'), ('2026-02-30'), ('03/04/2026'), ('junk')"
    sql = f"SELECT {guard} FROM (VALUES {values}) AS t(col)"
    rows = PostgresGateway(postgres_url).query(sql).rows
    dates = [str(row[0]) if row[0] else None for row in rows]
    assert dates == ["2024-02-29", None, None, None, None]


# Postgres-only forms: `~`, to_char, and `::`, DATE_TRUNC and ILIKE, which DuckDB shares.
POSTGRES_MODEL = """\
SELECT o.customer_id,
  count(*) FILTER (WHERE o.status ~ '^ret') AS returns,
  count(*) FILTER (WHERE to_char(o.ordered_at, 'YYYY') = '2025') AS orders_2025,
  count(CASE WHEN o.status::text ILIKE 'PLACED'
    AND DATE_TRUNC('month', o.ordered_at) >= '2024-02-01'::date THEN 1 END) AS late_placed
FROM da19_dialect.orders AS o
GROUP BY o.customer_id"""


@pytest.mark.integration
async def test_verifier_reads_and_counts_a_postgres_model_in_postgres(
    postgres_url: str, tmp_path: Path
) -> None:
    import psycopg
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import TextContent

    with psycopg.connect(postgres_url, autocommit=True) as con:
        con.execute("DROP SCHEMA IF EXISTS da19_dialect CASCADE")
        con.execute("CREATE SCHEMA da19_dialect")
        con.execute(
            "CREATE TABLE da19_dialect.orders (order_id int, customer_id text, status text,"
            " ordered_at date)"
        )
        con.execute(
            "INSERT INTO da19_dialect.orders VALUES"
            " (1, 'c1', 'placed', '2024-01-01'), (2, 'c1', 'returned', '2024-02-01'),"
            " (3, 'c1', 'placed', '2025-03-01'), (4, 'c2', 'returned', '2025-04-01'),"
            " (5, 'c3', 'placed', '2025-05-01')"
        )
        con.execute(f"CREATE TABLE da19_dialect.m AS {POSTGRES_MODEL}")
    project = tmp_path / "pg_project"
    (project / "models").mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: pg_project\nversion: '1.0'\n")
    (project / "models" / "m.sql").write_text(POSTGRES_MODEL)
    params = StdioServerParameters(
        command=str(DATA_AGENT),
        args=["mcp-server", "--config", "full", "--postgres-env", "DA_TEST_URL"],
        env=dict(os.environ) | {"DA_TEST_URL": postgres_url},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(
            "verify_model_values",
            {
                "model_name": "da19_dialect.m",
                "source_tables": "da19_dialect.orders",
                "project_dir": str(project),
            },
        )
    text = "\n".join(item.text for item in result.content if isinstance(item, TextContent))
    assert not result.is_error, text
    assert "Model SQL read" in text, text
    assert "Model matches COUNT(*) FILTER(WHERE status ~ '^ret')" in text, text
    assert "Model matches COUNT(*) FILTER(WHERE TO_CHAR(ordered_at, 'YYYY') = '2025')" in text
    assert text.count("→ Model matches") == 3, text
    assert "MISMATCH" not in text
