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
from data_agent.agent import DBT_SKILLS, QUERY_SKILLS, SKILLS_DIR, Task
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, task_type: str, said: str = "done"
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
    env = {"DATA_AGENT_API_KEY": "sk-test", "DA_TEST_URL": UNREACHABLE}
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
    # The URL reaches the server through the inherited environment, never through the config.
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
