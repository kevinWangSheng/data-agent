"""External MCP config parsing and the stdio wrapper (ticket 116): the behaviour the acceptance
tests (tests/acceptance/test_mcp_*.py) leave open. No MCP server, no model, no network."""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from data_agent import agent, external_mcp
from data_agent.external_mcp import McpConfigError, load

TOKEN = "tok-" + "Uh62" + "-fake"
ENV = {"DATA_AGENT_MCP_TOKEN": TOKEN, "PATH": "/usr/bin:/bin", "HOME": "/home/someone"}


def write(tmp_path: Path, servers: dict[str, Any]) -> Path:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


def http(**overrides: Any) -> dict[str, Any]:
    return {
        "type": "http",
        "url": "https://mcp.example/readonly",
        "headers": {"Authorization": "Bearer ${DATA_AGENT_MCP_TOKEN}"},
        "allowedTools": ["*"],
        **overrides,
    }


def stdio(**overrides: Any) -> dict[str, Any]:
    return {
        "command": "docker",
        "args": ["run", "--rm", "-i", "-e", "SECRET", "image.example/server"],
        "env": {"SECRET": "${DATA_AGENT_MCP_TOKEN}", "MODE": "read-only"},
        "allowedTools": ["find"],
        **overrides,
    }


def refused(tmp_path: Path, servers: dict[str, Any], env: dict[str, str] = ENV) -> str:
    with pytest.raises(McpConfigError) as raised:
        load(write(tmp_path, servers), env)
    return str(raised.value)


def test_the_reserved_name_is_our_own_servers() -> None:
    assert external_mcp.RESERVED_NAME == agent.MCP_SERVER


@pytest.mark.parametrize(
    "server",
    [
        pytest.param(http(oauth={"clientId": "abc"}), id="oauth"),
        pytest.param(http(auth={"type": "oauth"}), id="auth"),
        pytest.param(http(headersHelper="/bin/get-token"), id="headers-helper"),
        pytest.param(stdio(cwd="/tmp"), id="unknown-stdio-field"),
    ],
)
def test_oauth_and_unknown_fields_are_refused_naming_the_server_and_field(
    tmp_path: Path, server: dict[str, Any]
) -> None:
    field = next(key for key in server if key not in {*http(), *stdio()})
    message = refused(tmp_path, {"remote": server})
    assert "'remote'" in message
    assert field in message


def test_an_oauth_field_says_oauth_is_not_supported(tmp_path: Path) -> None:
    assert "OAuth" in refused(tmp_path, {"remote": http(oauth={"clientId": "abc"})})


@pytest.mark.parametrize(
    ("server", "field"),
    [
        pytest.param(stdio(args=["--token", "${DATA_AGENT_MCP_TOKEN}"]), "args", id="args"),
        pytest.param(stdio(command="${DATA_AGENT_MCP_TOKEN}"), "command", id="command"),
        pytest.param(http(url="https://mcp.example/?k=${DATA_AGENT_MCP_TOKEN}"), "url", id="url"),
        pytest.param(stdio(env={"${DATA_AGENT_MCP_TOKEN}": "x"}), "env", id="env-key"),
    ],
)
def test_a_variable_outside_env_and_header_values_is_refused(
    tmp_path: Path, server: dict[str, Any], field: str
) -> None:
    """The CLI expands `${VAR}` itself in what it is given, with any variable of its own
    environment, so one left in place would bypass the DATA_AGENT_MCP_ prefix rule."""
    message = refused(tmp_path, {"remote": server})
    assert "'remote'" in message and field in message
    assert TOKEN not in message


@pytest.mark.parametrize(
    "value",
    ["${DATA_AGENT_MCP_TOKEN:-fallback}", "${}", "${DATA_AGENT_MCP_TOKEN"],
    ids=["default", "empty", "unclosed"],
)
def test_a_malformed_reference_is_refused(tmp_path: Path, value: str) -> None:
    message = refused(tmp_path, {"remote": http(headers={"Authorization": value})})
    assert "'remote'" in message


def test_an_expanded_value_holding_a_reference_is_refused(tmp_path: Path) -> None:
    """The CLI would expand it again."""
    env = {**ENV, "DATA_AGENT_MCP_TOKEN": "${ANTHROPIC_API_KEY}"}
    message = refused(tmp_path, {"remote": http()}, env)
    assert "DATA_AGENT_MCP_TOKEN" in message


@pytest.mark.parametrize(
    ("allowed", "why"),
    [
        (["*", "find"], "*"),
        (["find", 3], "string"),
        ("find", "list"),
        (["find,drop"], "find,drop"),
        (["mcp__x__find"], "mcp__x__find"),
    ],
    ids=["star-and-names", "not-a-string", "not-a-list", "comma", "full-name"],
)
def test_a_bad_allow_list_is_refused(tmp_path: Path, allowed: Any, why: str) -> None:
    message = refused(tmp_path, {"remote": http(allowedTools=allowed)})
    assert "'remote'" in message and why in message


@pytest.mark.parametrize(
    "text",
    ["not json", "[]", '{"servers": {}}', '{"mcpServers": []}', '{"mcpServers": {"a": 1}}'],
    ids=["not-json", "not-object", "no-mcpServers", "servers-not-object", "server-not-object"],
)
def test_a_malformed_file_is_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(text)
    with pytest.raises(McpConfigError, match=str(path)):
        load(path, ENV)


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(McpConfigError, match="not found"):
        load(tmp_path / "absent.json", ENV)


def test_a_server_without_allowed_tools_is_not_mounted_and_warned_about(tmp_path: Path) -> None:
    loaded = load(write(tmp_path, {"quiet": stdio(allowedTools=[]), "remote": http()}), ENV)
    assert [server.name for server in loaded.servers] == ["remote"]
    assert list(loaded.sdk_servers()) == ["remote"]
    [warning] = loaded.warnings
    assert warning.startswith("warning:") and "'quiet'" in warning


def test_the_sdk_config_has_no_allowed_tools_and_no_stdio_values(tmp_path: Path) -> None:
    path = write(tmp_path, {"mongo": stdio(), "remote": http()})
    loaded = load(path, ENV)
    servers = loaded.sdk_servers()
    assert servers["remote"] == {
        "type": "http",
        "url": "https://mcp.example/readonly",
        "headers": {"Authorization": f"Bearer {TOKEN}"},
    }
    mongo = servers["mongo"]
    assert mongo == {
        "type": "stdio",
        "command": sys.executable,
        "args": [
            *("-P", "-m", "data_agent", "mcp-launch"),
            *("--mcp-servers", str(path), "--server", "mongo"),
        ],
    }
    # The value reaches the wrapper through the CLI's environment, never its argv.
    assert loaded.env() == {"DATA_AGENT_MCP_TOKEN": TOKEN}
    assert "${" not in json.dumps(servers)


def test_the_wrapper_names_the_file_by_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, {"mongo": stdio()})
    monkeypatch.chdir(tmp_path)
    args = load(Path("mcp.json"), ENV).sdk_servers()["mongo"]["args"]  # pyright: ignore[reportGeneralTypeIssues,reportTypedDictNotRequiredAccess]
    assert args[args.index("--mcp-servers") + 1] == str(tmp_path / "mcp.json")


def test_secrets_are_the_referenced_values_only(tmp_path: Path) -> None:
    """Literal values (`read-only`) are not masked: masking them would redact them across the
    whole event file."""
    loaded = load(write(tmp_path, {"mongo": stdio(), "remote": http()}), ENV)
    assert set(loaded.secrets()) == {TOKEN, f"Bearer {TOKEN}"}


def test_a_run_logs_no_expanded_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The whole `data-agent run`, stopped at the SDK transport, with every logger at DEBUG."""
    import duckdb
    from claude_agent_sdk import CLIConnectionError
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    from data_agent import cli

    started: list[object] = []

    async def connect(self: SubprocessCLITransport) -> None:
        started.append(self)
        raise CLIConnectionError("stopped before the CLI")

    monkeypatch.setattr(SubprocessCLITransport, "connect", connect)
    monkeypatch.setenv("DATA_AGENT_API_KEY", "unused-" + "Kp30")
    monkeypatch.setenv("DATA_AGENT_MCP_TOKEN", TOKEN)
    caplog.set_level(logging.DEBUG)
    db = tmp_path / "shop.duckdb"
    duckdb.connect(str(db)).execute("CREATE TABLE orders (id INTEGER)").close()
    (tmp_path / "work").mkdir()
    config = write(tmp_path, {"mongo": stdio(), "remote": http()})
    with pytest.raises(SystemExit):
        cli.main(
            [
                *("run", "--task", "How many orders?", "--type", "query"),
                *("--workspace", str(tmp_path / "work"), "--duckdb", str(db)),
                *("--config", "no-context", "--events-dir", str(tmp_path / "events")),
                *("--sandbox", "local", "--mcp-servers", str(config)),
            ]
        )
    out = capsys.readouterr()
    assert started, "the run never reached the session"
    assert caplog.records, "nothing was logged, so nothing was checked"
    for text in (caplog.text, out.out, out.err):
        assert TOKEN not in text


def test_a_url_value_has_its_password_and_host_masked(tmp_path: Path) -> None:
    password = "pw-" + "Ld83"
    env = {**ENV, "DATA_AGENT_MCP_TOKEN": f"mongodb://reader:{password}@db.example:27017/x"}
    loaded = load(write(tmp_path, {"mongo": stdio()}), env)
    assert {password, "db.example"} <= set(loaded.secrets())
    single = {**ENV, "DATA_AGENT_MCP_TOKEN": "mongodb://mongo-1:27017/x"}
    assert load(write(tmp_path, {"mongo": stdio()}), single).names() == ["mongo-1"]


@pytest.mark.parametrize("name", ["data_", "data__x", "a__b", "_lead"])
def test_a_name_the_cli_would_split_wrongly_is_refused(tmp_path: Path, name: str) -> None:
    """`mcp__data___find` reads as server `data`, whose tools are allowed whole."""
    assert f"'{name}'" in refused(tmp_path, {name: stdio()})


@pytest.mark.parametrize("kind", [["http"], {}, 3], ids=["list", "object", "number"])
def test_a_type_that_is_not_a_string_is_refused(tmp_path: Path, kind: Any) -> None:
    assert "'remote'" in refused(tmp_path, {"remote": http(type=kind)})


def test_a_url_without_a_type_asks_for_http(tmp_path: Path) -> None:
    server = http()
    del server["type"]
    assert '"type": "http"' in refused(tmp_path, {"remote": server})


@pytest.mark.parametrize(
    "server",
    [
        pytest.param(stdio(env={"": "x"}), id="empty-env-name"),
        pytest.param(stdio(env={"A=B": "x"}), id="equals-in-env-name"),
        pytest.param(stdio(env={"A": "x\0y"}), id="nul-in-env-value"),
        pytest.param(stdio(args=["a\0b"]), id="nul-in-arg"),
    ],
)
def test_an_environment_or_argument_exec_would_reject_is_refused(
    tmp_path: Path, server: dict[str, Any]
) -> None:
    """Caught before the session, not as a traceback in the wrapper the CLI started."""
    assert "'remote'" in refused(tmp_path, {"remote": server})


def test_a_path_holding_a_reference_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "${HOME}"
    directory.mkdir()
    with pytest.raises(McpConfigError, match="path"):
        load(write(directory, {"mongo": stdio()}), ENV)


# --- The wrapper ---------------------------------------------------------------------------

ECHO = (
    "import json, os, sys; "
    "open(sys.argv[1], 'w').write(json.dumps({'argv': sys.argv[1:], 'env': dict(os.environ)})); "
    "sys.stdout.write(sys.stdin.read())"
)


def test_the_wrapper_passes_dash_arguments_through_untouched(tmp_path: Path) -> None:
    """`docker run --rm -i -e X image` must reach the server as written; argparse must not take
    `--rm` or `-e` for its own."""
    out = tmp_path / "seen.json"
    args = ["-c", ECHO, str(out), "--rm", "-i", "-e", "SECRET", "--", "--server", "x"]
    path = write(tmp_path, {"echo": stdio(command=sys.executable, args=args)})
    launch = load(path, ENV).sdk_servers()["echo"]
    proc = subprocess.run(
        [launch["command"], *launch["args"]],  # pyright: ignore[reportGeneralTypeIssues,reportTypedDictNotRequiredAccess]
        input="line\n",
        env={**ENV, "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin", "OTHER": "x"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "line\n"
    seen = json.loads(out.read_text())
    assert seen["argv"] == [str(out), "--rm", "-i", "-e", "SECRET", "--", "--server", "x"]
    assert seen["env"]["SECRET"] == TOKEN
    assert seen["env"]["MODE"] == "read-only"
    assert "OTHER" not in seen["env"]
    assert "DATA_AGENT_MCP_TOKEN" not in seen["env"]


def test_the_wrapper_refuses_a_server_the_file_does_not_mount(tmp_path: Path) -> None:
    path = write(tmp_path, {"quiet": stdio(allowedTools=[])})
    proc = subprocess.run(
        [sys.executable, *external_mcp.launch_args(path, "quiet")],
        env=ENV | {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "'quiet'" in proc.stderr


def test_the_launch_environment_keeps_only_basic_variables() -> None:
    server = external_mcp.ExternalServer(
        name="s", type="stdio", allowed_tools=("find",), command="x", env={"A": "1"}
    )
    parent = {"PATH": "/bin", "HOME": "/h", "DATA_AGENT_API_KEY": "k", "DOCKER_HOST": "unix://x"}
    assert external_mcp.launch_env(server, parent) == {"PATH": "/bin", "HOME": "/h", "A": "1"}


def test_data_agent_web_refuses_a_bad_config_before_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """待人定 3, the default: `data-agent web` checks the file when it starts (`serve_web`)."""
    import uvicorn

    from data_agent import cli

    served: list[object] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **_kwargs: served.append(args))
    monkeypatch.delenv("DATA_AGENT_MCP_TOKEN", raising=False)
    runs = tmp_path / "runs"
    runs.mkdir()
    config = write(tmp_path, {"remote": http()})
    with pytest.raises(SystemExit) as exited:
        cli.main(["web", "--runs-dir", str(runs), "--mcp-servers", str(config)])
    assert exited.value.code == 2
    err = capsys.readouterr().err
    assert "'remote'" in err and "DATA_AGENT_MCP_TOKEN" in err
    assert served == []


def test_the_wrapper_exits_127_for_a_command_it_cannot_start(tmp_path: Path) -> None:
    path = write(tmp_path, {"gone": stdio(command="no-such-command-116")})
    proc = subprocess.run(
        [sys.executable, *external_mcp.launch_args(path, "gone")],
        env=ENV | {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == external_mcp.EXIT_NOT_FOUND
    assert proc.stdout == ""
    assert "'gone'" in proc.stderr
