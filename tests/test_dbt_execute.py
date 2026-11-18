"""dbt_execute (ticket 99): the allowlist, the argv it runs dbt with, the profile each target
uses, and a real `dbt run` that lands in the run's scratch schema."""

import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path

import duckdb
import pytest

from data_agent import dbt_execute
from data_agent.dbt_execute import (
    DbtExecuteError,
    DbtExecutor,
    DbtRun,
    dbt_argv,
    postgres_output,
    scratch_profile,
    scratch_schema,
)

FIXTURES = Path(__file__).parent / "fixtures"
DBT_SHOP = FIXTURES / "dbt_shop"
SHOP_SQL = FIXTURES / "shop.sql"
PG_ENV = "DA_TEST_DBT_URL"
PG_URL = "postgresql://agent:pa%40ss@db.example:6543/warehouse?sslmode=require"

# A stand-in dbt: it records how it was started (argv, cwd, environment, process group) and
# exits; `seed` sleeps first, past any limit a test sets.
FAKE_DBT = """#!{python}
import json, os, sys, time
with open({record!r}, "w") as f:
    record = {{"argv": sys.argv, "cwd": os.getcwd(), "env": dict(os.environ)}}
    json.dump(record | {{"pgrp": os.getpgrp()}}, f)
print("fake dbt ran", flush=True)
if sys.argv[1] == "seed":
    time.sleep(60)
"""


@pytest.mark.parametrize(
    "command", ["show", "run-operation", "run; rm -rf /", "docs serve", "run --vars x", "", "ls"]
)
def test_a_command_outside_the_allowlist_is_refused(command: str) -> None:
    with pytest.raises(DbtExecuteError, match="command must be one of"):
        dbt_argv(command)


@pytest.mark.parametrize("command", ["run", "test", "build", "seed", "snapshot", "compile"])
def test_each_allowed_command_is_one_dbt_subcommand(command: str) -> None:
    assert dbt_argv(command) == ["dbt", command, "--no-use-colors"]


def test_docs_generate_is_allowed() -> None:
    assert dbt_argv(" Docs  generate ")[:3] == ["dbt", "docs", "generate"]


@pytest.mark.parametrize("field", ["select", "exclude"])
@pytest.mark.parametrize("value", ["-x", "--vars", "--full-refresh", "a;b", "$(id)", "a|b"])
def test_a_selector_that_is_a_flag_or_not_selector_syntax_is_refused(
    field: str, value: str
) -> None:
    with pytest.raises(DbtExecuteError, match=field):
        if field == "select":
            dbt_argv("run", select=value)
        else:
            dbt_argv("run", exclude=value)


@pytest.mark.parametrize("threads", [0, 9, -1])
def test_threads_outside_one_to_eight_are_refused(threads: int) -> None:
    with pytest.raises(DbtExecuteError, match="threads must be 1 to 8"):
        dbt_argv("run", threads=threads)


def test_the_structured_arguments_become_flags() -> None:
    argv = dbt_argv(
        "build",
        select="tag:nightly stg_orders+",
        exclude="customer_orders",
        full_refresh=True,
        threads=8,
    )
    assert argv == [
        *("dbt", "build", "--no-use-colors"),
        *("--select", "tag:nightly stg_orders+", "--exclude", "customer_orders"),
        *("--full-refresh", "--threads", "8"),
    ]


def test_the_scratch_schema_is_the_run_id_as_a_plain_identifier() -> None:
    assert (
        scratch_schema("20261118T101010Z-a1b2c3-0f9e8d7c")
        == "da_run_20261118t101010z_a1b2c3_0f9e8d7c"
    )
    long = scratch_schema("x" * 80 + "-0f9e8d7c")
    assert len(long) == 63 and long.startswith("da_run_x") and long.endswith("_0f9e8d7c")
    assert "__" not in long


def test_a_scratch_run_writes_its_profile_under_the_events_dir(tmp_path: Path) -> None:
    run = DbtRun.of_run("scratch", tmp_path / "run")
    assert run.server_args() == (
        *(
            "--dbt-target",
            "scratch",
            "--dbt-profiles-dir",
            str((tmp_path / "run").resolve() / "profiles"),
        ),
    )
    assert DbtRun.of_run("task", tmp_path / "run").server_args() == ("--dbt-target", "task")


def test_the_postgres_profile_decodes_the_url_as_libpq_does() -> None:
    """The gateway hands the same URL to libpq, which decodes every part; the profile must
    reach the database the gateway reads (a percent-encoded name, a socket directory as host)."""
    url = "postgresql://agent@%2Fvar%2Frun%2FPg:6543/warehouse%20dev"
    output = postgres_output(url, "da_run_r1")
    assert (output["dbname"], output["host"]) == ("warehouse dev", "/var/run/Pg")


def test_the_postgres_profile_keeps_the_urls_tls_settings() -> None:
    url = (
        "postgresql://h/db?sslmode=verify-full"
        "&sslrootcert=/ca.pem&sslcert=/c.pem&sslkey=/k.pem&connect_timeout=7"
    )
    output = postgres_output(url, "da_run_r1")
    keys = ("sslrootcert", "sslcert", "sslkey", "connect_timeout")
    assert {key: output[key] for key in keys} == {
        "sslrootcert": "/ca.pem",
        "sslcert": "/c.pem",
        "sslkey": "/k.pem",
        "connect_timeout": 7,
    }


@pytest.mark.parametrize(
    "url", ["postgresql://h1,h2:5433/db", "postgresql://h:notaport/db", "postgresql://h/db?bogus=1"]
)
def test_a_postgres_url_the_profile_cannot_hold_is_a_tool_error(url: str) -> None:
    with pytest.raises(DbtExecuteError):
        postgres_output(url, "da_run_r1")


def test_a_postgres_parse_error_does_not_carry_the_password() -> None:
    with pytest.raises(DbtExecuteError) as caught:
        postgres_output("postgresql://agent:fake%ZZsecret@h/db", "da_run_r1")
    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_the_postgres_profile_holds_the_connection_and_the_run_schema() -> None:
    profile = scratch_profile("da_run_r1", None, PG_URL)
    assert profile == {
        "da_run": {
            "target": "da_run",
            "outputs": {
                "da_run": {
                    "type": "postgres",
                    "host": "db.example",
                    "port": 6543,
                    "user": "agent",
                    "password": "pa@ss",
                    "dbname": "warehouse",
                    "schema": "da_run_r1",
                    "threads": 4,
                    "sslmode": "require",
                }
            },
        }
    }


# --- Running dbt -----------------------------------------------------------------------------


@pytest.fixture
def fake_dbt(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """An environment whose PATH finds the stand-in dbt, and the file it records to."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    dbt = bin_dir / "dbt"
    record = tmp_path / "record.json"
    dbt.write_text(FAKE_DBT.format(python=sys.executable, record=str(record)))
    dbt.chmod(dbt.stat().st_mode | stat.S_IXUSR)
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "DATA_AGENT_MODEL_KEY": "sk-secret",
        "ANTHROPIC_API_KEY": "sk-other",
        PG_ENV: PG_URL,
        "DBT_PROFILES_DIR": "/elsewhere",
    }
    return env, record


def workspace_with_project(tmp_path: Path, subdir: str = "dbt_shop") -> Path:
    workspace = tmp_path / "run" / "workspace"
    shutil.copytree(DBT_SHOP, workspace / subdir)
    return workspace


def test_scratch_runs_dbt_on_the_workspace_project_with_a_profile_outside_it(
    tmp_path: Path, fake_dbt: tuple[dict[str, str], Path]
) -> None:
    env, record = fake_dbt
    workspace = workspace_with_project(tmp_path)
    db = tmp_path / "run" / "db" / "shop.duckdb"
    profiles = tmp_path / "run" / "profiles"
    executor = DbtExecutor(workspace, "scratch", "r-1", profiles, duckdb=db)
    reply = executor.execute("run", select="stg_orders+", env=env)
    assert reply.startswith("target_schema: da_run_r_1\nexit_code: 0\noutput:\nfake dbt ran")
    seen = json.loads(record.read_text())
    argv = seen["argv"]
    assert isinstance(argv, list)
    # dbt itself, not a shell, with the selector as one argument.
    assert Path(argv[0]).name == "dbt"
    assert argv[1:5] == ["run", "--no-use-colors", "--select", "stg_orders+"]
    project = Path(argv[argv.index("--project-dir") + 1])
    assert project == (workspace / "dbt_shop").resolve()
    assert project.is_relative_to(workspace.resolve())
    profiles_dir = Path(argv[argv.index("--profiles-dir") + 1])
    assert profiles_dir == profiles.resolve()
    assert not profiles_dir.resolve().is_relative_to(workspace.resolve())
    assert argv[argv.index("--profile") + 1] == "da_run"
    assert argv[argv.index("--target") + 1] == "da_run"
    assert Path(str(seen["cwd"])).resolve() == project
    # The profile named on the command line, on the run's DuckDB file and schema.
    profile = json.loads((profiles / "profiles.yml").read_text())
    output = profile["da_run"]["outputs"]["da_run"]
    assert output == {
        "type": "duckdb",
        "path": str(db.resolve()),
        "schema": "da_run_r_1",
        "threads": 4,
    }
    assert stat.S_IMODE((profiles / "profiles.yml").stat().st_mode) == 0o600
    # Nothing of the profile, the model key or the Postgres URL is in the workspace or dbt's env.
    assert not list(workspace.rglob("profiles.yml"))
    dbt_env = seen["env"]
    assert isinstance(dbt_env, dict)
    for name in ("DATA_AGENT_MODEL_KEY", "ANTHROPIC_API_KEY", PG_ENV, "DBT_PROFILES_DIR"):
        assert name not in dbt_env


def test_a_scratch_postgres_run_reads_the_url_from_its_variable(
    tmp_path: Path, fake_dbt: tuple[dict[str, str], Path]
) -> None:
    env, record = fake_dbt
    workspace = workspace_with_project(tmp_path, ".")
    profiles = tmp_path / "run" / "profiles"
    DbtExecutor(workspace, "scratch", "r1", profiles, postgres_env=PG_ENV).execute("test", env=env)
    output = json.loads((profiles / "profiles.yml").read_text())["da_run"]["outputs"]["da_run"]
    assert (output["host"], output["password"], output["schema"]) == (
        "db.example",
        "pa@ss",
        "da_run_r1",
    )
    assert PG_ENV not in json.loads(record.read_text())["env"]


def test_scratch_names_its_profile_whatever_the_project_file_says(
    tmp_path: Path, fake_dbt: tuple[dict[str, str], Path]
) -> None:
    """Codex review on d887788: a dbt_project.yml with YAML anchors, which the project reader
    cannot parse, gave the generated profile the name `default`, which dbt then did not find
    under the project's `profile: shop`."""
    env, record = fake_dbt
    workspace = workspace_with_project(tmp_path)
    project_file = workspace / "dbt_shop" / "dbt_project.yml"
    project_file.write_text(
        project_file.read_text() + "vars:\n  base: &base\n    region: eu\n  copy: *base\n"
    )
    profiles = tmp_path / "run" / "profiles"
    DbtExecutor(workspace, "scratch", "r1", profiles, duckdb=tmp_path / "a.duckdb").execute(
        "run", env=env
    )
    argv = json.loads(record.read_text())["argv"]
    name = argv[argv.index("--profile") + 1]
    assert list(json.loads((profiles / "profiles.yml").read_text())) == [name]


def test_a_relative_scratch_profiles_dir_reaches_dbt_as_the_one_written(
    tmp_path: Path, fake_dbt: tuple[dict[str, str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review on d887788: dbt runs in the project, so a relative `--profiles-dir` named
    another directory than the one the server wrote the profile to."""
    env, record = fake_dbt
    workspace = workspace_with_project(tmp_path)
    monkeypatch.chdir(tmp_path / "run")
    executor = DbtExecutor(
        workspace, "scratch", "r1", Path("profiles"), duckdb=tmp_path / "a.duckdb"
    )
    executor.execute("run", env=env)
    argv = json.loads(record.read_text())["argv"]
    profiles_dir = Path(argv[argv.index("--profiles-dir") + 1])
    assert profiles_dir.is_absolute()
    assert (profiles_dir / "profiles.yml").is_file()
    assert profiles_dir == (tmp_path / "run" / "profiles").resolve()


def test_a_scratch_profiles_dir_inside_the_workspace_is_refused(tmp_path: Path) -> None:
    workspace = workspace_with_project(tmp_path)
    with pytest.raises(DbtExecuteError, match="outside the workspace"):
        DbtExecutor(
            workspace, "scratch", "r1", workspace / "profiles", duckdb=tmp_path / "a.duckdb"
        )


def test_task_runs_dbt_with_the_projects_own_profile(
    tmp_path: Path, fake_dbt: tuple[dict[str, str], Path]
) -> None:
    env, record = fake_dbt
    workspace = workspace_with_project(tmp_path)
    project = workspace / "dbt_shop"
    (project / "profiles.yml").write_text("shop:\n  target: dev\n")
    executor = DbtExecutor(workspace, "task", postgres_env=PG_ENV)
    reply = executor.execute("build", env=env)
    assert reply.startswith("exit_code: 0\n")
    seen = json.loads(record.read_text())
    argv = seen["argv"]
    assert Path(argv[argv.index("--profiles-dir") + 1]) == project.resolve()
    assert "--target" not in argv
    assert Path(seen["cwd"]).resolve() == project.resolve()
    # The task's profile may read the URL and dbt's own variables, as a run's dbt did before.
    assert seen["env"][PG_ENV] == PG_URL
    assert seen["env"]["DBT_PROFILES_DIR"] == "/elsewhere"
    assert "DATA_AGENT_MODEL_KEY" not in seen["env"]
    assert (project / "profiles.yml").read_text() == "shop:\n  target: dev\n"


JAFFLE_PROFILE = """\
jaffle_shop:
  target: dev
  outputs:
    dev:
      type: postgres
      host: "{{ env_var('DBT_ENV_SECRET_DA_JAFFLE_HOST') }}"
      port: "{{ env_var('DA_JAFFLE_PORT') | as_number }}"
      user: "{{ env_var("DA_JAFFLE_USER") }}"
      dbname: "{{ env_var( 'DA_JAFFLE_DBNAME' , 'jaffle') }}"
      schema: jaffle_case
"""
JAFFLE_ENV = {"DA_JAFFLE_PORT": "5433", "DA_JAFFLE_USER": "u", "DA_JAFFLE_DBNAME": "jaffle"}


@pytest.mark.parametrize("where", ["project", "profiles_dir_env", "home"])
def test_task_passes_the_variables_the_tasks_profile_reads(
    tmp_path: Path, fake_dbt: tuple[dict[str, str], Path], where: str
) -> None:
    """Codex review on db500d2: the jaffle evaluation's profile reads DA_JAFFLE_* variables,
    which a task run's dbt did not get. It gets the ones the profile dbt will use names, from
    wherever dbt finds it, and still not the others."""
    env, record = fake_dbt
    workspace = workspace_with_project(tmp_path)
    project = workspace / "dbt_shop"
    env = env | JAFFLE_ENV | {"DA_UNRELATED": "x"}
    if where == "project":
        profiles_dir = project
    elif where == "profiles_dir_env":
        profiles_dir = tmp_path / "case-profiles"
        env |= {"DBT_PROFILES_DIR": str(profiles_dir)}
    else:
        home = tmp_path / "home"
        profiles_dir = home / ".dbt"
        env = {k: v for k, v in env.items() if k != "DBT_PROFILES_DIR"} | {"HOME": str(home)}
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / "profiles.yml").write_text(JAFFLE_PROFILE)
    DbtExecutor(workspace, "task", postgres_env=PG_ENV).execute("run", env=env)
    seen = json.loads(record.read_text())["env"]
    assert {name: seen.get(name) for name in JAFFLE_ENV} == JAFFLE_ENV
    assert "DA_UNRELATED" not in seen
    assert "DATA_AGENT_MODEL_KEY" not in seen


def test_a_workspace_without_a_dbt_project_is_an_error(
    tmp_path: Path, fake_dbt: tuple[dict[str, str], Path]
) -> None:
    env, _ = fake_dbt
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = DbtExecutor(workspace, "task", duckdb=tmp_path / "a.duckdb")
    with pytest.raises(DbtExecuteError, match=r"no dbt_project\.yml"):
        executor.execute("run", env=env)


def test_dbt_stays_in_the_servers_process_group(
    tmp_path: Path, fake_dbt: tuple[dict[str, str], Path]
) -> None:
    """Codex review, round 1: dbt in a session of its own outlived a cancelled run, which the web
    UI stops by signalling the run's process group."""
    env, record = fake_dbt
    workspace = workspace_with_project(tmp_path)
    DbtExecutor(workspace, "task", duckdb=tmp_path / "a.duckdb").execute("run", env=env)
    assert json.loads(record.read_text())["pgrp"] == os.getpgrp()


def test_dbt_past_its_limit_is_killed_and_its_output_returned(
    tmp_path: Path, fake_dbt: tuple[dict[str, str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    env, _ = fake_dbt
    # Long enough for the stand-in's interpreter to start and print on a loaded machine (1 s was
    # not), far short of its 60 s sleep.
    monkeypatch.setattr(dbt_execute, "TIMEOUT_S", 5)
    workspace = workspace_with_project(tmp_path)
    executor = DbtExecutor(workspace, "task", duckdb=tmp_path / "a.duckdb")
    started = time.monotonic()
    with pytest.raises(DbtExecuteError, match=r"timed out after 5 seconds(.|\n)*fake dbt ran"):
        executor.execute("seed", env=env)
    assert time.monotonic() - started < 30


# --- A real dbt run ---------------------------------------------------------------------------


def tables(db: Path) -> dict[str, list[str]]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables ORDER BY 1, 2"
        ).fetchall()
    finally:
        con.close()
    found: dict[str, list[str]] = {}
    for schema, name in rows:
        found.setdefault(schema, []).append(name)
    return found


def test_a_real_dbt_run_materializes_into_the_scratch_schema_only(tmp_path: Path) -> None:
    assert shutil.which("dbt"), "the dev dependency group provides dbt"
    run_dir = tmp_path / "run"
    workspace = workspace_with_project(tmp_path)
    db = run_dir / "db" / "shop.duckdb"
    db.parent.mkdir()
    con = duckdb.connect(str(db))
    try:
        con.execute(SHOP_SQL.read_text())
    finally:
        con.close()
    before = tables(db)
    raw_orders = duckdb.connect(str(db), read_only=True)
    try:
        orders_before = raw_orders.execute("SELECT count(*) FROM raw.orders").fetchone()
    finally:
        raw_orders.close()
    run = DbtRun.of_run("scratch", run_dir)
    assert run.profiles_dir is not None
    executor = DbtExecutor(
        workspace, "scratch", "20261118T000000Z-abc123-0f9e8d7c", run.profiles_dir, duckdb=db
    )

    reply = executor.execute("run", env=os.environ)

    assert "exit_code: 0" in reply, reply
    assert "run_results: success=3" in reply, reply
    after = tables(db)
    schema = "da_run_20261118t000000z_abc123_0f9e8d7c"
    assert sorted(after[schema]) == ["customer_orders", "stg_customers", "stg_orders"]
    assert {name: rows for name, rows in after.items() if name != schema} == before
    con = duckdb.connect(str(db), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM raw.orders").fetchone() == orders_before
    finally:
        con.close()
    assert not list(workspace.rglob("profiles.yml"))
    assert not list(workspace.rglob("*.duckdb"))
