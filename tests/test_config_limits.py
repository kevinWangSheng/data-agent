"""Ticket 144: the keys of `[limits]`, `[sandbox]`, `[sources.<name>]` and `[server]` the
acceptance tests (tests/acceptance/test_config_wiring.py) do not pin, each from a config file to
its effect: the DuckDB limits in the connection, the row cap and schema choice in the MCP tools,
the longest sandbox_exec, the value dictionary's cache, the loopback-only web address and the
ranges a number must be in."""

import argparse
from pathlib import Path
from typing import Any

import duckdb
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from data_agent import agent, cli, sandbox
from data_agent.config import ConfigFileError, EffectiveConfig, load_effective_config
from data_agent.run_config import load_config


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def load(*paths: Path) -> EffectiveConfig:
    return load_effective_config(list(paths), {}, {})


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    """Five items in `main`, one table in a schema called `secret`."""
    db = tmp_path / "shop.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE TABLE items AS SELECT i FROM range(5) r(i)")
        con.execute("CREATE SCHEMA secret")
        con.execute("CREATE TABLE secret.salaries AS SELECT 1 AS amount")
    finally:
        con.close()
    return db


SHOP_LIMITS = """\
[sources.shop]
kind = "duckdb"
path = "shop.duckdb"
max_rows = 2
query_timeout_seconds = 7
schemas.exclude = ["secret"]
duckdb.memory_limit = "1GiB"
duckdb.max_temp_directory_size = "3GiB"
"""


class Served(Exception):
    pass


def served(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: Path, db: Path, *extra: str
) -> Any:
    """The server `data-agent mcp-server --config <config> --duckdb <db>` builds, before it
    serves."""
    built: list[Any] = []
    real = cli.build_server

    def build_server(*args: Any, **kwargs: Any) -> Any:
        built.append(real(*args, **kwargs))
        raise Served

    monkeypatch.setattr(cli, "build_server", build_server)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DATA_AGENT_CONFIG", raising=False)
    with pytest.raises(Served):
        cli.main(["mcp-server", "--config", str(config), "--duckdb", str(db), *extra])
    [server] = built
    return server


async def call(server: Any, tool: str, arguments: dict[str, Any]) -> str:
    try:
        result = await server.call_tool(tool, arguments)
    except ToolError as exc:
        return f"ERROR {exc}"
    assert isinstance(result, CallToolResult)
    return "\n".join(part.text for part in result.content if isinstance(part, TextContent))


# --- [sources.<name>] -------------------------------------------------------------------------


async def test_a_sources_duckdb_limits_are_the_connections_settings(
    tmp_path: Path, shop: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write(tmp_path / "config.toml", SHOP_LIMITS)
    server = served(monkeypatch, tmp_path, config, shop)
    text = await call(
        server,
        "query_database",
        {
            "sql": "SELECT current_setting('memory_limit') AS m, "
            "current_setting('max_temp_directory_size') AS t"
        },
    )
    assert "1.0 GiB" in text and "3.0 GiB" in text


async def test_without_the_keys_the_connection_has_the_codes_limits(
    tmp_path: Path, shop: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write(
        tmp_path / "config.toml", '[sources.shop]\nkind = "duckdb"\npath = "shop.duckdb"\n'
    )
    server = served(monkeypatch, tmp_path, config, shop)
    text = await call(
        server, "query_database", {"sql": "SELECT current_setting('memory_limit') AS m"}
    )
    # gateway.DUCKDB_MEMORY_LIMIT, as DuckDB writes it.
    assert "2.0 GiB" in text


async def test_a_sources_max_rows_caps_query_database_and_says_so(
    tmp_path: Path, shop: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write(tmp_path / "config.toml", SHOP_LIMITS)
    server = served(monkeypatch, tmp_path, config, shop)
    text = await call(server, "query_database", {"sql": "SELECT i FROM items ORDER BY i"})
    assert "[ROW CAP]" in text and "at most 2 rows" in text
    assert "\n4" not in text


async def test_a_query_under_the_cap_has_no_note(
    tmp_path: Path, shop: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write(tmp_path / "config.toml", SHOP_LIMITS)
    server = served(monkeypatch, tmp_path, config, shop)
    text = await call(server, "query_database", {"sql": "SELECT i FROM items WHERE i = 0"})
    assert "[ROW CAP]" not in text


async def test_an_excluded_schema_is_out_of_the_listing(
    tmp_path: Path, shop: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write(tmp_path / "config.toml", SHOP_LIMITS)
    server = served(monkeypatch, tmp_path, config, shop)
    text = await call(server, "list_tables", {})
    assert "items" in text and "salaries" not in text


async def test_with_include_only_those_schemas_are_listed(
    tmp_path: Path, shop: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write(
        tmp_path / "config.toml",
        '[sources.shop]\nkind = "duckdb"\npath = "shop.duckdb"\nschemas.include = ["SECRET"]\n',
    )
    server = served(monkeypatch, tmp_path, config, shop)
    text = await call(server, "list_tables", {})
    assert "salaries" in text and "items" not in text


def test_a_sources_query_timeout_and_dbt_timeout_are_read_by_name(tmp_path: Path) -> None:
    config = load(write(tmp_path / "config.toml", SHOP_LIMITS + "dbt_timeout_seconds = 30\n"))
    limits = cli.source_limits(config, "shop")
    assert limits.query_timeout_seconds == 7
    assert cli.dbt_timeout(config, "shop") == 30
    # Another source, or none: the code's.
    assert cli.source_limits(config, "other") == cli.SourceLimits()
    assert cli.dbt_timeout(config, None) == cli.DBT_TIMEOUT_S


# --- [sandbox].exec_max_seconds ---------------------------------------------------------------


def sandbox_of(tmp_path: Path, text: str) -> sandbox.Sandbox:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    args = argparse.Namespace(sandbox="local", run_id=None, network=False, workspace=workspace)
    config = load(write(tmp_path / "config.toml", text))
    built = cli.server_sandbox(argparse.ArgumentParser(), args, load_config(config), config)
    assert built is not None
    return built


def test_exec_max_seconds_is_the_longest_sandbox_exec(tmp_path: Path) -> None:
    box = sandbox_of(tmp_path, "[sandbox]\nexec_max_seconds = 20\n")
    with pytest.raises(sandbox.SandboxError, match="1 to 20"):
        sandbox.execute_tool(box, sandbox.QueryResults(), "true", 21, [])
    assert "at most 20)" in sandbox.tool_description(box)
    # Under the longest, the default comes down to it.
    assert "default 20," in sandbox.tool_description(box)


def test_without_exec_max_seconds_a_call_may_ask_for_900(tmp_path: Path) -> None:
    box = sandbox_of(tmp_path, "[layers]\nskill = true\n")
    assert box.max_seconds == sandbox.MAX_TIMEOUT_S == 900
    assert f"default {sandbox.DEFAULT_TIMEOUT_S}, at most 900)" in sandbox.tool_description(box)


# --- [limits].max_turns.large -----------------------------------------------------------------


def test_every_turn_cap_comes_from_the_limits(tmp_path: Path) -> None:
    config = load(
        write(tmp_path / "config.toml", "[limits.max_turns]\ndbt = 9\nquery = 8\nlarge = 7\n")
    )
    limited = cli.with_limits(load_config(config), config)
    assert limited.max_turns == {"dbt": 9, "query": 8, "large": 7}


def test_without_limits_the_turn_caps_are_todays() -> None:
    config = load()
    limited = cli.with_limits(load_config(config), config)
    assert limited.max_turns == {"dbt": 200, "query": 50, "large": 75}
    assert limited.max_budget_usd == 1.0


# --- The value dictionary's cache -------------------------------------------------------------


def build_dictionary(gateway: Any, workspace: Path, cache: Path | None) -> dict[str, Any]:
    events: list[dict[str, Any]] = []

    def emit(kind: str, **fields: Any) -> None:
        events.append({"type": kind, **fields})

    with agent.prebuilt_dictionary(gateway, workspace, emit, cache=cache) as path:
        assert path.is_file()
    [event] = events
    return event


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """How many dictionaries were built (not read from the cache)."""
    builds: list[int] = []
    real = agent.build_interruptible

    def build(job: Any) -> Any:
        builds.append(1)
        return real(job)

    monkeypatch.setattr(agent, "build_interruptible", build)
    return builds


def test_a_second_product_run_reuses_the_dictionary(
    tmp_path: Path, shop: Path, counted: list[int]
) -> None:
    gateway = cli.DuckDBGateway(shop)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cache = tmp_path / "cache"
    first = build_dictionary(gateway, workspace, cache)
    second = build_dictionary(gateway, workspace, cache)
    assert first["cached"] is False and second["cached"] is True
    assert len(counted) == 1
    assert {k: v for k, v in first.items() if k != "cached"} == {
        k: v for k, v in second.items() if k != "cached"
    }


def test_a_changed_catalog_is_built_again(tmp_path: Path, shop: Path, counted: list[int]) -> None:
    gateway = cli.DuckDBGateway(shop)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cache = tmp_path / "cache"
    build_dictionary(gateway, workspace, cache)
    con = duckdb.connect(str(shop))
    try:
        con.execute("CREATE TABLE customers AS SELECT 1 AS id")
    finally:
        con.close()
    again = build_dictionary(gateway, workspace, cache)
    assert again["cached"] is False and len(counted) == 2


def test_an_evaluation_builds_the_dictionary_every_time(
    tmp_path: Path, shop: Path, counted: list[int]
) -> None:
    gateway = cli.DuckDBGateway(shop)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for _ in range(2):
        event = build_dictionary(gateway, workspace, None)
        assert "cached" not in event
    assert len(counted) == 2


@pytest.fixture
def run_kwargs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, Any]]:
    """`cli.run` replaced: what each run is given."""
    seen: list[dict[str, Any]] = []
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("DATA_AGENT_CONFIG", raising=False)

    async def run(task: Any, given: Any, events_dir: Path, *args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return agent.Outcome("", True, False, [], events_dir / "events.jsonl")

    monkeypatch.setattr(cli, "run", run)
    return seen


def run_argv(tmp_path: Path, shop: Path, *extra: str) -> list[str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return [
        *("run", "--task", "t", "--type", "query", "--workspace", str(workspace)),
        *("--duckdb", str(shop), "--events-dir", str(tmp_path / "events"), *extra),
    ]


def exit_of(argv: list[str]) -> Any:
    with pytest.raises(SystemExit) as stopped:
        cli.main(argv)
    return stopped.value.code


def test_a_product_run_gets_the_cache_and_an_evaluation_none(
    tmp_path: Path, shop: Path, run_kwargs: list[dict[str, Any]]
) -> None:
    config = write(tmp_path / "config.toml", "[layers]\nskill = true\n")
    assert exit_of(run_argv(tmp_path, shop)) == 0
    assert exit_of(run_argv(tmp_path, shop, "--config", str(config))) == 0
    # An evaluation names an old run config (`evals/`), or turns the cache off.
    assert exit_of(run_argv(tmp_path, shop, "--config", "full")) == 0
    assert exit_of(run_argv(tmp_path, shop, "--config", str(config), "--no-dictionary-cache")) == 0
    caches = [kwargs["dictionary_cache"] for kwargs in run_kwargs]
    assert caches == [tmp_path / "xdg" / "data-agent" / "dictionaries"] * 2 + [None, None]


def test_the_deadline_margin_is_the_longest_query_timeout(
    tmp_path: Path, shop: Path, run_kwargs: list[dict[str, Any]]
) -> None:
    """`run` stops itself a statement's timeout before its deadline (`agent.DEADLINE_MARGIN_S`
    unless a data source sets a longer or shorter one), so a query started just before ends."""
    config = write(
        tmp_path / "config.toml",
        '[sources.shop]\nkind = "duckdb"\npath = "shop.duckdb"\nquery_timeout_seconds = 30\n',
    )
    assert exit_of(run_argv(tmp_path, shop)) == 0
    assert exit_of(run_argv(tmp_path, shop, "--config", str(config))) == 0
    margins = [kwargs["deadline_margin"] for kwargs in run_kwargs]
    assert margins == [agent.DEADLINE_MARGIN_S, 30]


def test_a_run_on_a_sources_file_takes_that_sources_limits(
    tmp_path: Path, shop: Path, run_kwargs: list[dict[str, Any]]
) -> None:
    """`--duckdb` on the file a `[sources.<name>]` names: the run's dictionary leaves out the
    schemas that source excludes, as the MCP server's tools do (`cli.served_source`)."""
    config = write(tmp_path / "config.toml", SHOP_LIMITS)
    assert exit_of(run_argv(tmp_path, shop, "--config", str(config))) == 0
    [limits] = run_kwargs[0]["source_limits"].values()
    assert limits.exclude_schemas == ("secret",) and limits.max_rows == 2


# --- [server].host ----------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "127.0.0.2"])
def test_a_loopback_host_is_served(host: str) -> None:
    assert cli.web_host(argparse.ArgumentParser(), host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.10", "example.com", "::"])
def test_any_other_host_is_refused_until_the_web_ui_takes_a_token(
    host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.web_host(argparse.ArgumentParser(), host)
    assert stopped.value.code == 2
    assert "[server].host" in capsys.readouterr().err


# --- Ranges -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "key", "says"),
    [
        ("[limits]\nmax_budget_usd = 0\n", "limits.max_budget_usd", "greater than 0"),
        ("[limits]\nagent_timeout_sec = -1\n", "limits.agent_timeout_sec", "greater than 0"),
        ("[limits.max_turns]\nquery = 0\n", "limits.max_turns.query", "at least 1"),
        ("[sandbox]\ncpus = 0\n", "sandbox.cpus", "greater than 0"),
        ("[sandbox]\nexec_max_seconds = 0.5\n", "sandbox.exec_max_seconds", "at least 1"),
        ("[server]\nport = 70000\n", "server.port", "from 1 to 65535"),
        (
            '[sources.s]\nkind = "duckdb"\npath = "s.duckdb"\nmax_rows = 10001\n',
            "sources.s.max_rows",
            "from 1 to 10000",
        ),
        (
            '[sources.s]\nkind = "duckdb"\npath = "s.duckdb"\nquery_timeout_seconds = 0\n',
            "sources.s.query_timeout_seconds",
            "greater than 0",
        ),
        (
            '[sources.s]\nkind = "duckdb"\npath = "s.duckdb"\ndbt_timeout_seconds = 0\n',
            "sources.s.dbt_timeout_seconds",
            "greater than 0",
        ),
    ],
)
def test_a_number_out_of_its_range_is_refused(
    tmp_path: Path, text: str, key: str, says: str
) -> None:
    path = write(tmp_path / "config.toml", text)
    with pytest.raises(ConfigFileError) as refused:
        load(path)
    assert refused.value.key == key
    assert says in str(refused.value) and str(path) in str(refused.value)


def test_the_bounds_themselves_are_taken(tmp_path: Path) -> None:
    path = write(
        tmp_path / "config.toml",
        "[limits.max_turns]\nquery = 1\n\n[sandbox]\nexec_max_seconds = 1\n\n"
        '[server]\nport = 65535\n\n[sources.s]\nkind = "duckdb"\npath = "s.duckdb"\n'
        "max_rows = 10000\n",
    )
    config = load(path)
    assert config.value("server.port") == 65535
    assert config.value("sources.s.max_rows") == 10_000


def test_the_row_bound_is_the_gateways() -> None:
    from data_agent import config, gateway

    assert config.MAX_ROWS == gateway.MAX_ROWS


# --- Codex review, round 1 --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flags", "policy", "key"),
    [
        (["--network"], "[policy]\nnetwork = false\n", "policy.network"),
        (["--dbt-target", "task"], '[policy]\ndbt_target = "scratch"\n', "policy.dbt_target"),
    ],
)
def test_mcp_server_flags_against_the_policy_are_refused(
    tmp_path: Path,
    shop: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    policy: str,
    key: str,
) -> None:
    """Started by hand, `mcp-server` may not open what `[policy]` locks, as `run` may not."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DATA_AGENT_CONFIG", raising=False)
    config = write(tmp_path / "config.toml", policy)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    argv = [
        *("mcp-server", "--config", str(config), "--duckdb", str(shop)),
        *("--sandbox", "local", "--workspace", str(workspace), *flags),
    ]
    assert exit_of(argv) == 2
    assert key in capsys.readouterr().err


def test_a_runs_server_file_holds_its_source_alone(tmp_path: Path, shop: Path) -> None:
    """Two sources on one file: the server of a run on the second finds the second's limits,
    not the first's (`served_source` matches by location)."""
    config = load(
        write(
            tmp_path / "config.toml",
            '[sources.a]\nkind = "duckdb"\npath = "shop.duckdb"\nmax_rows = 3\n\n'
            '[sources.b]\nkind = "duckdb"\npath = "shop.duckdb"\nmax_rows = 7\n',
        )
    )
    task = agent.Task(text="q", type="query", workspace=tmp_path, duckdb=shop, data_source="b")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    served_config = cli.server_config(load_config(config), config, scratch, task)
    assert served_config.server_config is not None
    given = load(Path(served_config.server_config))
    assert given.names("sources") == ["b"]
    assert given.value("sources.b.max_rows") == 7


def test_a_dictionary_that_left_a_table_out_is_not_cached(
    tmp_path: Path, shop: Path, counted: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A table whose sampling failed is `not_profiled`: the next run builds again, so the table
    is read once the database answers."""
    from data_agent import context_tools
    from data_agent.gateway import QueryFailed

    real = context_tools.sample_table
    failing = [True]

    def sample_table(session: Any, table: Any) -> Any:
        if table.name == "items" and failing[0]:
            raise QueryFailed("statement timeout")
        return real(session, table)

    monkeypatch.setattr(context_tools, "sample_table", sample_table)
    gateway = cli.DuckDBGateway(shop)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cache = tmp_path / "cache"
    first = build_dictionary(gateway, workspace, cache)
    assert first["tables_not_profiled"] == 1
    failing[0] = False
    second = build_dictionary(gateway, workspace, cache)
    assert second["cached"] is False and second["tables_not_profiled"] == 0
    third = build_dictionary(gateway, workspace, cache)
    assert third["cached"] is True and len(counted) == 2


@pytest.mark.parametrize(("timeout", "expected"), [(0.5, 0.5), (150, 10.0)])
def test_the_dictionarys_statements_take_a_shorter_source_timeout(
    tmp_path: Path, shop: Path, timeout: float, expected: float
) -> None:
    """`[sources.<name>].query_timeout_seconds` under the dictionary's own 10 s bounds its
    statements too; a longer one leaves the 10 s."""
    seen: list[float] = []
    gateway = cli.DuckDBGateway(shop, timeout)
    real = gateway.session

    def session(timeout_seconds: float) -> Any:
        seen.append(timeout_seconds)
        return real(timeout_seconds)

    gateway.session = session  # type: ignore[method-assign]
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    build_dictionary(gateway, workspace, None)
    assert seen and set(seen) == {expected}


def test_a_copy_of_a_sources_file_reuses_its_dictionary(
    tmp_path: Path, shop: Path, counted: list[int]
) -> None:
    """Spec: by data source. A web dbt run works on its own copy of the source's DuckDB file;
    keyed by the file it would never reuse a dictionary and would add one per run."""
    import shutil

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cache = tmp_path / "cache"
    copies = []
    for run in ("r1", "r2"):
        copy = tmp_path / run / "shop.duckdb"
        copy.parent.mkdir()
        shutil.copy(shop, copy)
        copies.append(copy)
    events: list[dict[str, Any]] = []
    origin = f"duckdb:{shop.resolve()}"
    for copy in copies:
        with agent.prebuilt_dictionary(
            cli.DuckDBGateway(copy),
            workspace,
            lambda kind, **fields: events.append(fields),
            cache=cache,
            source="shop",
            origin=origin,
        ):
            pass
    assert [event["cached"] for event in events] == [False, True]
    assert len(counted) == 1 and len(list(cache.iterdir())) == 1
    # Another source of the same catalog keeps its own.
    with agent.prebuilt_dictionary(
        cli.DuckDBGateway(copies[0]),
        workspace,
        lambda *_, **__: None,
        cache=cache,
        source="crm",
        origin=origin,
    ):
        pass
    assert len(counted) == 2


def duckdb_of(path: Path, value: int) -> Path:
    """A `shop.duckdb` of one table, `items(i)`, holding `value`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute(f"CREATE TABLE items AS SELECT {value} AS i")
    finally:
        con.close()
    return path


def test_two_projects_sources_of_one_name_keep_their_own_dictionaries(
    tmp_path: Path, counted: list[int]
) -> None:
    """Two configs' `[sources.shop]` of one catalog and other values (two projects): keyed by the
    name alone, the second project's run would serve the first one's values. Each copy of a
    project's file (a web dbt run's) still reuses that project's dictionary."""
    import shutil

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cache = tmp_path / "cache"
    events: list[dict[str, Any]] = []
    for project, value in (("a", 1), ("b", 2), ("a", 1), ("b", 2)):
        original = tmp_path / project / "shop.duckdb"
        if not original.exists():
            duckdb_of(original, value)
        copy = tmp_path / "runs" / f"{len(events)}" / "shop.duckdb"
        copy.parent.mkdir(parents=True)
        shutil.copy(original, copy)
        with agent.prebuilt_dictionary(
            cli.DuckDBGateway(copy),
            workspace,
            lambda kind, **fields: events.append(fields),
            cache=cache,
            source="shop",
            origin=f"duckdb:{original.resolve()}",
        ) as path:
            assert str(value) in path.read_text()
    assert [event["cached"] for event in events] == [False, False, True, True]
    assert len(counted) == 2 and len(list(cache.iterdir())) == 2


def test_a_runs_dictionary_origin_is_its_sources_file_not_the_copy(
    tmp_path: Path, shop: Path, run_kwargs: list[dict[str, Any]]
) -> None:
    """A web dbt run is `--duckdb <copy> --data-source-name shop`: its dictionary is keyed by
    the file `[sources.shop]` names, so every copy of it shares one, and another config's
    `shop` at another file keeps its own."""
    import shutil

    config = write(tmp_path / "config.toml", SHOP_LIMITS)
    copy = tmp_path / "run" / "db" / "shop.duckdb"
    copy.parent.mkdir(parents=True)
    shutil.copy(shop, copy)
    argv = run_argv(tmp_path, copy, "--config", str(config), "--data-source-name", "shop")
    assert exit_of(argv) == 0
    # A bare `--duckdb` file: no origin, keyed by the file itself.
    assert exit_of(run_argv(tmp_path, copy, "--config", str(config))) == 0
    origins = [kwargs["dictionary_origins"] for kwargs in run_kwargs]
    assert origins == [{"shop": f"duckdb:{shop.resolve()}"}, {}]


async def test_two_unnamed_files_of_one_name_keep_their_own_dictionaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, counted: list[int]
) -> None:
    """A bare `--duckdb` run names no data source: its dictionary is keyed by the file, so two
    `shop.duckdb` files of one schema and other values never share one."""
    seen: list[str | None] = []
    real = agent.cached_dictionary

    def cached(gateway: Any, hidden: Any, cache: Path, source: Any = None, *rest: Any) -> Any:
        seen.append(source)
        return real(gateway, hidden, cache, source, *rest)

    monkeypatch.setattr(agent, "cached_dictionary", cached)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cache = tmp_path / "cache"
    events: list[dict[str, Any]] = []
    for place, value in (("a", 1), ("b", 2)):
        db = tmp_path / place / "shop.duckdb"
        db.parent.mkdir()
        con = duckdb.connect(str(db))
        con.execute(f"CREATE TABLE items AS SELECT {value} AS i")
        con.close()
        task = agent.Task(text="q", type="query", workspace=workspace, duckdb=db)
        with agent.prebuilt_dictionary(
            cli.DuckDBGateway(db),
            workspace,
            lambda kind, **fields: events.append(fields),
            cache=cache,
            source=task.data_source,
        ):
            pass
    assert seen == [None, None]
    assert [event["cached"] for event in events] == [False, False] and len(counted) == 2
