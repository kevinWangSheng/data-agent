"""The config on the command line (.scratch/product-config/spec.md "配置文件与分层", "产品默认与
入口"; decisions.md Q1, Q8): `data-agent config show`, `data-agent run --config <file>`, and the
three old file options folded into the config.

Written by ticket 141. Every test here is xfail(strict) until ticket 143 makes it pass and removes
the marker, changing nothing else. The seam: the CLI in-process, sessions stopped at the SDK's
client (tests/acceptance/product_config_support.py); `data-agent web` with `create_app`,
`uvicorn.run` and `service.start` stood in for (as tests/acceptance/test_im_config.py does).

The contract fixed here (ticket 141 Context contract items 2 and 3; for the human review):

1. `data-agent config show [--config FILE]... [--workspace DIR]` prints the effective config
   (tests/acceptance/test_config_load.py), one key per line, every key including the defaults:
   `<dotted key> = <value as TOML>  # <source>`, the source as `EffectiveConfig.source` gives
   it. A secret shows as its `${VAR}` reference, never its value. `--config` as for `run`;
   `--workspace` is where the project-level file is looked for (default: the working directory).
   A config error exits 2, its message on stderr.
2. `data-agent run --config FILE`: optional and repeatable, later files win; without it, the
   user-level and the project-level file (of `--workspace`) are read, or `DATA_AGENT_CONFIG`.
   A run's data source may come from `[sources.<name>]` (then `--duckdb`/`--postgres-env` are
   left out). During ticket 143 `--config` also takes the old ablation names (`full`, ...);
   ticket 146 removes them (test_config_snapshot.py).
3. The `version` event's `config` is the name of the last config file merged without its
   `.toml` (`product` for `product.toml`; a layer file's name on an evaluation: `full` for
   `layers/full.toml`), and `default` when no file is read.
4. A config file error (unknown key, plaintext secret, unset `${VAR}`): exit 2, stderr names the
   file and the key (and the variable), no session starts.
5. `run --data-sources`, `run --mcp-servers`, `web --data-sources`, `web --mcp-servers` and
   `web --im` are gone: argparse refuses them (exit 2, `unrecognized arguments`). Their contents
   come from the config: `[sources.*]` (the data server's `--duckdb`, or `--source` entries for
   several), `[mcp]` (`mcp_servers` and `allowed_tools`, by ticket 116's rules, which
   test_mcp_config.py pins), `[im.<platform>]` (what `service.start` is given).
"""

import inspect
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from product_config_support import (
    Capture,
    assert_refused_before_session,
    assert_session,
    fake_home,
    flag_value,
    flag_values,
    key_of,
    query_args,
    read_events,
    run_cli,
    server_args,
    servers_of,
    stop_sessions,
    toml_path,
    version_event,
    write_config,
    write_file,
)
from seam_support import build_shop_duckdb

from data_agent import cli
from data_agent.im import service
from data_agent.web import app as web_app

TEST_KEY = "test-" + "Jm42" + "-key"
GITHUB_TOKEN = "tok-" + "Ve18" + "-fake"
SLACK_BOT = "sb-" + "Rt6n" + "-made-up-bot"
SLACK_APP = "sa-" + "Pq2c" + "-made-up-app"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return fake_home(tmp_path)


@pytest.fixture
def capture(home: Path, monkeypatch: pytest.MonkeyPatch) -> Capture:
    return stop_sessions(monkeypatch, home)


def shop_source(path: Path, name: str = "shop") -> str:
    return f'[sources.{name}]\nkind = "duckdb"\npath = {toml_path(path)}\n'


# --- config show --------------------------------------------------------------------------


def line_of(stdout: str, key: str) -> str:
    lines = [line for line in stdout.splitlines() if line.startswith(f"{key} = ")]
    assert len(lines) == 1, f"expected one line for {key}, got {lines}: {stdout[-2000:]!r}"
    return lines[0]


def test_config_show_prints_each_value_with_its_source(
    home: Path,
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_AGENT_TEST_KEY", TEST_KEY)
    user = write_config(
        home,
        "[limits]\nmax_budget_usd = 2.0\n\n"
        '[models.m]\nbase_url = "https://llm.example/v1"\napi_key = "${DATA_AGENT_TEST_KEY}"\n',
    )
    workspace = tmp_path / "workspace"
    project = write_config(workspace, '[limits]\neffort = "high"\n')
    run = run_cli(capsys, ["config", "show", "--workspace", str(workspace)])

    assert run.code == 0, run.stderr
    assert line_of(run.stdout, "limits.max_budget_usd") == f"limits.max_budget_usd = 2.0  # {user}"
    assert line_of(run.stdout, "limits.effort") == f'limits.effort = "high"  # {project}'
    assert line_of(run.stdout, "limits.max_turns.dbt") == "limits.max_turns.dbt = 200  # default"
    assert (
        line_of(run.stdout, "models.m.api_key")
        == f'models.m.api_key = "${{DATA_AGENT_TEST_KEY}}"  # {user}'
    )
    assert TEST_KEY not in run.stdout + run.stderr


def test_config_show_takes_config_files_later_ones_winning(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    a = write_file(tmp_path / "a.toml", "[limits]\nmax_budget_usd = 3.0\n")
    b = write_file(tmp_path / "b.toml", "[limits]\nmax_budget_usd = 4.0\n")
    run = run_cli(capsys, ["config", "show", "--config", str(a), "--config", str(b)])

    assert run.code == 0, run.stderr
    assert line_of(run.stdout, "limits.max_budget_usd") == f"limits.max_budget_usd = 4.0  # {b}"


def test_config_show_refuses_a_bad_file(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = write_file(tmp_path / "bad.toml", "[limits]\nmax_budget = 2.0\n")
    run = run_cli(capsys, ["config", "show", "--config", str(bad)])

    assert run.code == 2
    assert str(bad) in run.stderr
    assert "limits.max_budget" in run.stderr


# --- run --config -------------------------------------------------------------------------


def test_run_takes_its_data_source_from_the_config_file(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    db = build_shop_duckdb(tmp_path / "shop.duckdb")
    config = write_file(tmp_path / "product.toml", shop_source(db))
    argv, events_dir = query_args(tmp_path / "run", "--config", str(config), database=False)
    session = assert_session(run_cli(capsys, argv), capture)

    assert flag_value(server_args(session.options), "--duckdb") == str(db.resolve())
    task = [event for event in read_events(events_dir) if event.get("type") == "task"]
    assert task and key_of(task[0], "data_source") == "shop"


def test_run_takes_several_sources_from_the_config_file(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    shop = build_shop_duckdb(tmp_path / "shop.duckdb")
    other = build_shop_duckdb(tmp_path / "other.duckdb")
    config = write_file(
        tmp_path / "product.toml", shop_source(shop) + "\n" + shop_source(other, "other")
    )
    argv, _ = query_args(tmp_path / "run", "--config", str(config), database=False)
    session = assert_session(run_cli(capsys, argv), capture)

    sources = flag_values(server_args(session.options), "--source")
    assert sorted(sources) == ["other", "shop"]


def test_a_later_config_file_wins(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    db = build_shop_duckdb(tmp_path / "shop.duckdb")
    a = write_file(
        tmp_path / "a.toml",
        shop_source(db) + "\n[layers]\nskill = true\nverifiers = true\ncontext = true\n",
    )
    b = write_file(tmp_path / "b.toml", "[layers]\nskill = false\ncontext = false\n")
    argv, events_dir = query_args(
        tmp_path / "run", "--config", str(a), "--config", str(b), database=False
    )
    assert_session(run_cli(capsys, argv), capture)

    version = version_event(events_dir)
    assert key_of(version, "layers") == {"skill": False, "verifiers": True, "context": False}
    assert key_of(version, "config") == "b"


def test_a_product_run_names_its_config_file_in_the_version_event(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_file(tmp_path / "product.toml", '[limits]\neffort = "high"\n')
    argv, events_dir = query_args(tmp_path / "run", "--config", str(config))
    assert_session(run_cli(capsys, argv), capture)

    assert key_of(version_event(events_dir), "config") == "product"


def test_a_run_without_a_config_file_records_default(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    argv, events_dir = query_args(tmp_path / "run")
    assert_session(run_cli(capsys, argv), capture)

    assert key_of(version_event(events_dir), "config") == "default"


def bad_files(tmp_path: Path) -> list[tuple[Path, list[str]]]:
    """(file, the words stderr must hold)."""
    unknown = write_file(tmp_path / "unknown.toml", "[limits]\nmax_budget = 2.0\n")
    plain = write_file(
        tmp_path / "plain.toml",
        f'[models.m]\nbase_url = "https://llm.example/v1"\napi_key = "{TEST_KEY}"\n',
    )
    unset = write_file(
        tmp_path / "unset.toml",
        '[models.m]\nbase_url = "https://llm.example/v1"\napi_key = "${DATA_AGENT_UNSET_KEY}"\n',
    )
    return [
        (unknown, [str(unknown), "limits.max_budget"]),
        (plain, [str(plain), "models.m.api_key"]),
        (unset, [str(unset), "models.m.api_key", "DATA_AGENT_UNSET_KEY"]),
    ]


@pytest.mark.parametrize("which", [0, 1, 2], ids=["unknown-key", "plaintext-secret", "unset-var"])
def test_a_bad_config_file_stops_the_run_before_its_session(
    which: int, tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    path, words = bad_files(tmp_path)[which]
    argv, _ = query_args(tmp_path / "run", "--config", str(path))
    run = run_cli(capsys, argv)

    assert_refused_before_session(run, capture, *words)
    assert TEST_KEY not in run.stderr


def test_a_bad_user_level_file_stops_the_run_too(
    home: Path, tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    user = write_config(home, '[colours]\nsky = "blue"\n')
    argv, _ = query_args(tmp_path / "run")
    run = run_cli(capsys, argv)

    assert_refused_before_session(run, capture, str(user), "colours")


# --- The three old file options -------------------------------------------------------------


@pytest.mark.parametrize("option", ["--data-sources", "--mcp-servers"])
def test_run_refuses_the_old_file_options(
    option: str, tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    db = build_shop_duckdb(tmp_path / "shop.duckdb")
    old = write_file(tmp_path / "old.toml", shop_source(db))
    argv, _ = query_args(tmp_path / "run", option, str(old), database=option != "--data-sources")
    run = run_cli(capsys, argv)

    assert_refused_before_session(run, capture, "unrecognized arguments", option)


def serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: Sequence[str]
) -> tuple[Any, list[dict[str, Any]], list[tuple[Any, ...]]]:
    """`data-agent web` with `create_app`, `uvicorn.run` and `service.start` stood in for: the
    command's exit code (None when it returned), `create_app`'s arguments bound to its signature,
    and every call of `service.start`."""
    import uvicorn

    seen: list[dict[str, Any]] = []
    started: list[tuple[Any, ...]] = []
    signature = inspect.signature(web_app.create_app)

    def fake_create_app(*args: Any, **kwargs: Any) -> object:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        seen.append(dict(bound.arguments))
        return object()

    start_signature = inspect.signature(service.start)

    def fake_start(*args: Any, **kwargs: Any) -> None:
        started.append(tuple(start_signature.bind(*args, **kwargs).arguments.values()))

    monkeypatch.setattr(web_app, "create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "start", fake_start)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(exist_ok=True)
    try:
        cli.main(["web", "--runs-dir", str(runs_dir), *extra])
    except SystemExit as exc:
        return exc.code, seen, started
    return None, seen, started


@pytest.mark.parametrize("option", ["--data-sources", "--mcp-servers", "--im"])
def test_web_refuses_the_old_file_options(
    option: str,
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_AGENT_SLACK_BOT_TOKEN", SLACK_BOT)
    monkeypatch.setenv("DATA_AGENT_SLACK_APP_TOKEN", SLACK_APP)
    db = build_shop_duckdb(tmp_path / "shop.duckdb")
    texts = {
        "--data-sources": shop_source(db),
        "--mcp-servers": '{"mcpServers": {}}',
        "--im": '[slack]\nbot_token_env = "DATA_AGENT_SLACK_BOT_TOKEN"\n'
        'app_token_env = "DATA_AGENT_SLACK_APP_TOKEN"\nallowed_users = ["U012ABCDEF"]\n',
    }
    old = write_file(tmp_path / "old-file", texts[option])
    capsys.readouterr()
    code, seen, started = serve(tmp_path, monkeypatch, [option, str(old)])
    err = capsys.readouterr().err

    assert code == 2, f"exit code {code!r}"
    assert "unrecognized arguments" in err and option in err, err
    assert seen == [] and started == []


def test_the_mcp_section_reaches_the_session(
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_AGENT_MCP_GITHUB_TOKEN", GITHUB_TOKEN)
    config = write_file(
        tmp_path / "product.toml",
        "[mcp.servers.github]\n"
        'type = "http"\nurl = "https://mcp.example/readonly"\n'
        'headers = { Authorization = "Bearer ${DATA_AGENT_MCP_GITHUB_TOKEN}" }\n'
        'allowedTools = ["*"]\n',
    )
    argv, events_dir = query_args(tmp_path / "run", "--config", str(config))
    session = assert_session(run_cli(capsys, argv), capture)

    servers = servers_of(session.options)
    assert sorted(servers) == ["data", "github"]
    assert "mcp__github__*" in session.options.allowed_tools
    external = key_of(version_event(events_dir), "external_mcp_servers")
    assert [server["name"] for server in external] == ["github"]


def test_the_sources_and_im_sections_reach_the_web_server(
    home: Path,
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_AGENT_SLACK_BOT_TOKEN", SLACK_BOT)
    monkeypatch.setenv("DATA_AGENT_SLACK_APP_TOKEN", SLACK_APP)
    db = build_shop_duckdb(tmp_path / "shop.duckdb")
    config = write_file(
        tmp_path / "product.toml",
        shop_source(db) + "\n[im.slack]\n"
        'bot_token_env = "DATA_AGENT_SLACK_BOT_TOKEN"\n'
        'app_token_env = "DATA_AGENT_SLACK_APP_TOKEN"\nallowed_users = ["U012ABCDEF"]\n',
    )
    code, seen, started = serve(tmp_path, monkeypatch, ["--config", str(config)])

    assert code is None, f"data-agent web exited {code!r}: {capsys.readouterr().err[-2000:]}"
    assert len(seen) == 1, "create_app was not called once"
    sources = key_of(seen[0], "data_sources")
    assert [(source.name, source.duckdb) for source in sources] == [("shop", db.resolve())]
    assert len(started) == 1, "service.start was not called once"
    configs = started[0][1]
    assert set(configs) == {"slack"}
    assert configs["slack"].allowed_users == frozenset({"U012ABCDEF"})
