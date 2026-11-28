"""The config loader's behaviour beyond tests/acceptance/test_config_load.py (ticket 143): what an
expanded `${VAR}` value never reaches, relative paths, the text's round trip, `[mcp.servers]`
kept as written, the old run config names on `--config`, the file our MCP server is given, and
the web server's config without any file."""

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from data_agent import agent, cli, config, dbt_execute, run_config, sandbox, tracing
from data_agent.config import ConfigFileError, EffectiveConfig, load_effective_config
from data_agent.web.app import create_app
from data_agent.web.launcher import Launcher
from data_agent.web.runs import RunsDir
from data_agent.web.sources import DataSource

# Built from parts so no scanner takes it for a key.
SECRET = "val-" + "Qx71" + "-made-up"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def load(
    *paths: Path, environ: dict[str, str] | None = None, cli: dict[str, Any] | None = None
) -> EffectiveConfig:
    return load_effective_config(list(paths), environ or {}, cli or {})


def error_of(
    *paths: Path, environ: dict[str, str] | None = None, cli: dict[str, Any] | None = None
) -> ConfigFileError:
    with pytest.raises(ConfigFileError) as caught:
        load(*paths, environ=environ, cli=cli)
    return caught.value


def test_the_code_defaults_match_the_modules_they_stand_for() -> None:
    assert config.TARGETS == dbt_execute.TARGETS
    assert config.BACKENDS == sandbox.BACKENDS
    assert config.TRACE_TIERS == tracing.TRACE_TIERS
    defaults = config.default_config()
    for name, price in agent.PRICES.items():
        assert defaults.value(f"models.{name}.input_price") == price.input
        assert defaults.value(f"models.{name}.cache_read_price") == price.cached_input
        assert defaults.value(f"models.{name}.output_price") == price.output
        assert defaults.value(f"models.{name}.base_url") == agent.DEFAULT_BASE_URL
    assert defaults.value("roles.main") == agent.DEFAULT_MODEL
    old = run_config.load_config("full")
    assert dict(old.max_turns) == {
        kind: defaults.value(f"limits.max_turns.{kind}") for kind in ("dbt", "query", "large")
    }
    assert old.max_budget_usd == defaults.value("limits.max_budget_usd")


def test_an_expanded_value_is_in_no_repr_text_or_raw_value(tmp_path: Path) -> None:
    path = write(
        tmp_path / "c.toml",
        '[models.m]\napi_key = "${DATA_AGENT_TEST_KEY}"\n\n'
        '[prompts]\nanswer_rule = "Say ${DATA_AGENT_TEST_KEY} never"\n',
    )
    loaded = load(path, environ={"DATA_AGENT_TEST_KEY": SECRET})

    assert loaded.value("models.m.api_key") == SECRET
    assert loaded.value("prompts.answer_rule") == f"Say {SECRET} never"
    assert loaded.raw_value("models.m.api_key") == "${DATA_AGENT_TEST_KEY}"
    assert SECRET not in repr(loaded)
    assert SECRET not in loaded.text
    assert SECRET not in json.dumps(dict(loaded.raw))


def test_an_error_never_holds_the_value_at_fault(tmp_path: Path) -> None:
    for text in (
        f'[limits]\nmax_turns = "{SECRET}"\n',
        f'[sandbox]\nbackend = "{SECRET}"\n',
        f'[models.m]\ncontext_window = "{SECRET}"\n',
        f'[im.slack]\nbot_token = "{SECRET}"\n',
    ):
        error = error_of(write(tmp_path / "bad.toml", text))
        assert SECRET not in str(error) and SECRET not in repr(error.args), text


def test_a_secret_key_is_refused_on_the_command_line_too() -> None:
    error = error_of(cli={"models.m.api_key": SECRET})
    assert "models.m.api_key" in str(error) and "${" in str(error)
    assert SECRET not in str(error)


def test_an_unknown_command_line_key_is_refused() -> None:
    error = error_of(cli={"limits.max_budget": 2.0})
    assert error.key == "limits.max_budget" and error.path is None


def test_a_command_line_value_the_policy_already_has_is_taken(tmp_path: Path) -> None:
    home = tmp_path / "home"
    write(home / ".data-agent" / "config.toml", "[policy]\nnetwork = false\n")
    loaded = load_effective_config([], {"HOME": str(home)}, {"sandbox.network": False})
    assert loaded.value("sandbox.network") is False
    assert loaded.source("sandbox.network") == "policy"


def test_relative_paths_count_from_the_file_that_sets_them(tmp_path: Path) -> None:
    path = write(
        tmp_path / "conf" / "c.toml",
        '[sources.shop]\nkind = "duckdb"\npath = "data/shop.duckdb"\n\n'
        '[skills]\ndirs = ["../skills"]\n\n[mcp]\nfile = "mcp.json"\n',
    )
    loaded = load(path)

    base = path.resolve().parent
    assert loaded.value("sources.shop.path") == base / "data" / "shop.duckdb"
    assert loaded.value("skills.dirs") == [base / ".." / "skills"]
    assert loaded.value("mcp.file") == base / "mcp.json"
    assert str(base / "mcp.json") in loaded.text


def test_the_text_loads_back_to_the_same_values(tmp_path: Path) -> None:
    path = write(
        tmp_path / "c.toml",
        '[limits]\neffort = "high"\n\n[sources.shop]\nkind = "duckdb"\npath = "shop.duckdb"\n\n'
        '[mcp.servers.gh]\ncommand = "gh-mcp"\nallowedTools = ["*"]\n\n'
        '[mcp.servers.gh.env]\nDATA_AGENT_MCP_GH_TOKEN = "${DATA_AGENT_MCP_GH_TOKEN}"\n',
    )
    environ = {"DATA_AGENT_MCP_GH_TOKEN": SECRET}
    first = load(path, environ=environ)
    again = load(write(tmp_path / "again.toml", first.text), environ=environ)

    assert dict(again.values) == dict(first.values)
    assert again.text == first.text


def test_mcp_servers_stay_as_written_for_their_own_rules(tmp_path: Path) -> None:
    """Ticket 116 expands only `DATA_AGENT_MCP_*` variables, in `env` and `headers`: the loader
    leaves the servers' references to it (`external_mcp.load_servers`)."""
    path = write(
        tmp_path / "c.toml",
        '[mcp.servers.gh]\ntype = "http"\nurl = "https://mcp.example"\nallowedTools = ["*"]\n'
        'headers = { Authorization = "Bearer ${DATA_AGENT_API_KEY}" }\n',
    )
    loaded = load(path, environ={"DATA_AGENT_API_KEY": SECRET})

    assert loaded.value("mcp.servers.gh.headers.Authorization") == "Bearer ${DATA_AGENT_API_KEY}"
    assert SECRET not in json.dumps(loaded.value("mcp.servers"))


def test_a_name_with_a_dot_is_refused(tmp_path: Path) -> None:
    error = error_of(write(tmp_path / "c.toml", '[sources."a.b"]\nkind = "duckdb"\n'))
    assert error.key == "sources.a.b"


def test_a_missing_explicit_file_is_refused(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    error = error_of(missing)
    assert error.path == missing and str(missing) in str(error)


def test_the_run_config_takes_the_layers_and_names_the_last_file(tmp_path: Path) -> None:
    a = write(tmp_path / "a.toml", "[layers]\nskill = false\n")
    b = write(tmp_path / "product.toml", "[layers]\ncontext = false\n")
    derived = run_config.load_config(load(a, b))

    assert derived.name == "product"
    assert derived.layers() == {"skill": False, "verifiers": True, "context": False}
    assert run_config.load_config(load()).name == "default"


# --- `--config` on the command line ---------------------------------------------------------


@pytest.fixture
def stopped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, Any]]:
    """`cli.run` replaced: records what each run is given and, while the run's files exist,
    starts our MCP server the way the session would (its arguments from `build_options`), with
    `build_server` recording the RunConfig it got instead of serving."""
    seen: list[dict[str, Any]] = []
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DATA_AGENT_CONFIG", raising=False)

    class Served(Exception):
        pass

    def build_server(gateway: Any, served_config: Any, *args: Any, **kwargs: Any) -> Any:
        seen[-1]["server_config"] = served_config
        raise Served

    async def run(task: Any, given: Any, events_dir: Path, *args: Any, **kwargs: Any) -> Any:
        settings = agent.ModelSettings.from_env({"DATA_AGENT_API_KEY": "sk-test"})
        options = agent.build_options(task, given, settings, [], lambda *_, **__: None)
        server = options.mcp_servers[agent.MCP_SERVER]  # type: ignore[index]
        seen.append({"config": given, "external": kwargs.get("external_mcp")})
        server_args = list(server["args"])  # type: ignore[index]
        with pytest.raises(Served):
            cli.main(server_args[server_args.index("mcp-server") :])
        return agent.Outcome("", True, False, [], events_dir / "events.jsonl")

    monkeypatch.setattr(cli, "run", run)
    monkeypatch.setattr(cli, "build_server", build_server)
    return seen


def run_args(tmp_path: Path, *extra: str) -> list[str]:
    import duckdb

    db = tmp_path / "shop.duckdb"
    if not db.exists():
        duckdb.connect(str(db)).close()
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return [
        *("run", "--task", "t", "--type", "query", "--workspace", str(workspace)),
        *("--duckdb", str(db), "--events-dir", str(tmp_path / "events"), *extra),
    ]


def exit_code(argv: Sequence[str]) -> Any:
    with pytest.raises(SystemExit) as stopped:
        cli.main(list(argv))
    return stopped.value.code


def test_an_old_name_alone_reads_no_file(
    tmp_path: Path, stopped: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path / "home" / ".data-agent" / "config.toml", "[layers]\nskill = false\n")
    assert exit_code(run_args(tmp_path, "--config", "baseline")) == 0

    [only] = stopped
    assert only["config"].name == "baseline" and only["config"].server_arg() == "baseline"
    assert only["server_config"].layers() == run_config.load_config("baseline").layers()


def test_a_file_config_gives_our_server_a_file_of_the_effective_config(
    tmp_path: Path, stopped: list[dict[str, Any]]
) -> None:
    path = write(tmp_path / "product.toml", "[layers]\nverifiers = false\n")
    assert exit_code(run_args(tmp_path, "--config", str(path))) == 0

    [only] = stopped
    assert only["config"].name == "product"
    assert only["server_config"].layers() == {"skill": True, "verifiers": False, "context": True}
    server_file = Path(only["config"].server_config)
    assert server_file.name == "product.toml"
    # The run's scratch directory is gone once the run ends.
    assert not server_file.exists()


def test_inline_mcp_servers_are_written_for_the_wrapper(
    tmp_path: Path, stopped: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_AGENT_MCP_GH_TOKEN", SECRET)
    path = write(
        tmp_path / "product.toml",
        '[mcp.servers.gh]\ncommand = "gh-mcp"\nallowedTools = ["*"]\n\n'
        '[mcp.servers.gh.env]\nDATA_AGENT_MCP_GH_TOKEN = "${DATA_AGENT_MCP_GH_TOKEN}"\n',
    )
    assert exit_code(run_args(tmp_path, "--config", str(path))) == 0

    external = stopped[0]["external"]
    assert [server.name for server in external.servers] == ["gh"]
    assert external.path.name == "mcp-servers.json"
    assert external.env() == {"DATA_AGENT_MCP_GH_TOKEN": SECRET}


@pytest.mark.parametrize("second", ["full", "./x.toml"])
def test_an_old_name_goes_alone(
    tmp_path: Path, stopped: list[dict[str, Any]], capsys: pytest.CaptureFixture[str], second: str
) -> None:
    assert exit_code(run_args(tmp_path, "--config", "baseline", "--config", second)) == 2
    assert "goes alone" in capsys.readouterr().err
    assert stopped == []


def test_a_path_spelled_like_a_name_is_a_file(
    tmp_path: Path,
    stopped: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write(tmp_path / "full", "[layers]\nskill = false\n")
    monkeypatch.chdir(tmp_path)
    assert exit_code(run_args(tmp_path, "--config", "./full")) == 0
    assert stopped[0]["config"].layers()["skill"] is False


def test_mcp_servers_inline_and_as_a_file_are_refused(
    tmp_path: Path, stopped: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(
        tmp_path / "product.toml",
        '[mcp]\nfile = "mcp.json"\n\n[mcp.servers.gh]\ncommand = "gh"\nallowedTools = ["*"]\n',
    )
    assert exit_code(run_args(tmp_path, "--config", str(path))) == 2
    assert "not both" in capsys.readouterr().err


def test_config_show_prints_nothing_expanded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_AGENT_TEST_KEY", SECRET)
    path = write(tmp_path / "c.toml", '[models.m]\napi_key = "${DATA_AGENT_TEST_KEY}"\n')
    cli.main(["config", "show", "--config", str(path)])
    out = capsys.readouterr().out

    assert 'models.m.api_key = "${DATA_AGENT_TEST_KEY}"' in out
    assert "layers.skill = true  # default" in out
    assert SECRET not in out


# --- The web server's config ----------------------------------------------------------------


def launcher(tmp_path: Path, given: EffectiveConfig | None, sources: list[DataSource]) -> Launcher:
    runs = tmp_path / "runs"
    runs.mkdir(exist_ok=True)

    def start_run(args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
        raise AssertionError("not started")

    return Launcher(RunsDir(runs), sources, start_run, given)


def test_create_app_without_a_config_reads_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """141 round 2 N1: `create_app(config=None)` is the code defaults, even with a user-level
    file in HOME."""
    home = tmp_path / "home"
    write(home / ".data-agent" / "config.toml", "[layers]\nskill = false\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DATA_AGENT_CONFIG", str(home / ".data-agent" / "config.toml"))
    runs = tmp_path / "runs"
    runs.mkdir()
    app = create_app(runs, [], lambda *_: None)  # type: ignore[arg-type, return-value]
    launched = app.state.conversations.launcher
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    [flag, path] = launched.config_args(run_dir)

    assert flag == "--config" and Path(path).name == "default.toml"
    given = load(Path(path))
    assert given.value("layers.skill") is True
    assert dict(given.values) == dict(config.default_config().values)


def test_a_runs_config_file_has_the_servers_sources_and_no_im(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHOP_URL", "postgresql://u:" + SECRET + "@h/db")
    db = tmp_path / "shop.duckdb"
    db.write_bytes(b"")
    server = write(
        tmp_path / "server.toml",
        '[limits]\neffort = "high"\n\n'
        '[sources.shop]\nkind = "duckdb"\npath = "shop.duckdb"\ndescription = "Shop orders"\n\n'
        '[im.telegram]\nbot_token_env = "DATA_AGENT_TELEGRAM_BOT_TOKEN"\nallowed_users = [1]\n',
    )
    sources = [DataSource("shop", duckdb=db), DataSource("wh", postgres_env="SHOP_URL")]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    [_, path] = launcher(tmp_path, load(server), sources).config_args(run_dir)
    text = Path(path).read_text()
    given = load(Path(path), environ={"SHOP_URL": "x"})

    assert Path(path).name == "server.toml"
    assert given.value("limits.effort") == "high"
    assert given.names("sources") == ["shop", "wh"]
    assert given.value("sources.shop.description") == "Shop orders"
    assert given.value("sources.wh.env") == "SHOP_URL"
    assert given.value("im") is None
    assert SECRET not in text


def test_a_form_run_keeps_the_picked_layers_over_the_servers_config(tmp_path: Path) -> None:
    """Until ticket 144 drops the form's `config`, its pick still decides the layers."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    [_, path] = launcher(tmp_path, None, []).layers_args(run_dir, "baseline")
    given = load(Path(path))
    assert Path(path).stem == "baseline"
    assert run_config.load_config(given).layers() == run_config.load_config("baseline").layers()


def test_inline_mcp_servers_are_checked_when_the_web_server_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DATA_AGENT_CONFIG", raising=False)
    path = write(
        tmp_path / "c.toml",
        '[mcp.servers.gh]\ncommand = "gh"\nallowedTools = ["*"]\n\n'
        '[mcp.servers.gh.env]\nDATA_AGENT_MCP_GH_TOKEN = "${DATA_AGENT_API_KEY}"\n',
    )
    monkeypatch.setenv("DATA_AGENT_API_KEY", SECRET)
    runs = tmp_path / "runs"
    runs.mkdir()
    assert exit_code(["web", "--runs-dir", str(runs), "--config", str(path)]) == 2
    err = capsys.readouterr().err
    assert "DATA_AGENT_MCP_" in err and SECRET not in err


# --- Review round 1 (Codex) -----------------------------------------------------------------

GITHUB_PAT = "ghp" + "_" + "Zq1x" * 9
SLACK_BOT = "xoxb" + "-" + "1234567890-abcdefghij"


@pytest.mark.parametrize(
    ("text", "key"),
    [
        (
            '[mcp.servers.gh]\ntype = "http"\nurl = "https://mcp.example"\n'
            f'headers = {{ Authorization = "Bearer {GITHUB_PAT}" }}\n',
            "mcp.servers.gh.headers.Authorization",
        ),
        (
            '[mcp.servers.gh]\ntype = "http"\nurl = "https://mcp.example"\n'
            'headers = { Authorization = "Bearer made-up-literal" }\n',
            "mcp.servers.gh.headers.Authorization",
        ),
        (
            '[mcp.servers.gh]\ntype = "http"\nurl = "https://mcp.example"\n'
            'headers = { X-Api-Key = "made-up-literal" }\n',
            "mcp.servers.gh.headers.X-Api-Key",
        ),
        (f'[prompts]\nanswer_rule = "use {SLACK_BOT}"\n', "prompts.answer_rule"),
    ],
)
def test_a_recognisable_secret_is_refused_under_any_key(
    tmp_path: Path, text: str, key: str
) -> None:
    error = error_of(write(tmp_path / "c.toml", text))
    assert error.key == key and "${" in str(error)
    assert GITHUB_PAT not in str(error) and SLACK_BOT not in str(error)


def test_an_authorization_header_with_a_reference_is_taken(tmp_path: Path) -> None:
    path = write(
        tmp_path / "c.toml",
        '[mcp.servers.gh]\ntype = "http"\nurl = "https://mcp.example"\n'
        'headers = { Authorization = "Bearer ${DATA_AGENT_MCP_GH_TOKEN}" }\n',
    )
    loaded = load(path, environ={"DATA_AGENT_MCP_GH_TOKEN": SECRET})
    assert loaded.value("mcp.servers.gh.headers.Authorization") == (
        "Bearer ${DATA_AGENT_MCP_GH_TOKEN}"
    )


def test_a_user_id_reference_is_checked_and_expanded(tmp_path: Path) -> None:
    path = write(
        tmp_path / "c.toml",
        '[im.slack]\nbot_token_env = "DATA_AGENT_SLACK_BOT_TOKEN"\n'
        'app_token_env = "DATA_AGENT_SLACK_APP_TOKEN"\nallowed_users = ["${SLACK_USER}", 7]\n',
    )
    error = error_of(path)
    assert error.key == "im.slack.allowed_users" and "SLACK_USER" in str(error)
    loaded = load(path, environ={"SLACK_USER": "U012ABCDEF"})
    assert loaded.value("im.slack.allowed_users") == ["U012ABCDEF", 7]


def test_the_web_server_gives_the_bot_the_expanded_user_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    from data_agent.im import service

    started: list[Any] = []
    monkeypatch.setattr(uvicorn, "run", lambda *_, **__: None)
    monkeypatch.setattr(service, "start", lambda app, configs: started.append(configs))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name, value in (
        ("DATA_AGENT_SLACK_BOT_TOKEN", "bot-made-up"),
        ("DATA_AGENT_SLACK_APP_TOKEN", "app-made-up"),
        ("SLACK_USER", "U012ABCDEF"),
    ):
        monkeypatch.setenv(name, value)
    path = write(
        tmp_path / "c.toml",
        '[im.slack]\nbot_token_env = "DATA_AGENT_SLACK_BOT_TOKEN"\n'
        'app_token_env = "DATA_AGENT_SLACK_APP_TOKEN"\nallowed_users = ["${SLACK_USER}"]\n',
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    cli.main(["web", "--runs-dir", str(runs), "--config", str(path)])
    assert started[0]["slack"].allowed_users == frozenset({"U012ABCDEF"})


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("[im.slack]\n", "im.slack"),
        ("[mcp.servers.github]\n", "mcp.servers.github"),
        ("[sources.shop]\n", "sources.shop"),
    ],
)
def test_an_empty_entry_is_refused(tmp_path: Path, text: str, key: str) -> None:
    error = error_of(write(tmp_path / "c.toml", text))
    assert error.key == key and "empty" in str(error)
