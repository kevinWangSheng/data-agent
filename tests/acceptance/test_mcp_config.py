"""External MCP servers from a config file (.scratch/mcp/spec.md "Testing Decisions": parsing and
checks, `${VAR}` expansion, the allow list, the `run` entry, the `version` event).

Written by ticket 115. Every test here is xfail(strict) until ticket 116 makes it pass and removes
the marker, changing nothing else. The seam: `data-agent run ... --mcp-servers FILE`, in-process,
stopped at the SDK's transport (tests/acceptance/mcp_support.py), so no model is called.

The contract fixed here (ticket 115 Context, items 1, 3, 4, 5; human-reviewed):

1. CLI: `data-agent run ... --mcp-servers FILE` and `data-agent web ... --mcp-servers FILE`.
   FILE is a Claude Code / Cursor `mcpServers` JSON with an `allowedTools` list per server.
   Evaluations never pass it.
3. Config errors: `run` ends before any session starts, exit code 2, stderr naming the server
   (and, for a `${VAR}` error, the variable). A server whose `allowedTools` is missing or empty
   gets a warning: a line of stderr holding `warning` and the server's name.
4. The `version` event's new field `external_mcp_servers`: `[{"name": str, "allowed_tools":
   [str, ...]}, ...]` in the order of the config file; `allowed_tools` as the file has it
   (`["*"]` is recorded as `["*"]`). A run without `--mcp-servers` has no such field (as `effort`,
   recorded only when given). Only `name` and `allowed_tools` are asserted, plus that no env value,
   header value or URL query string is anywhere in the event file; whether other fields (`type`, a
   URL without its query) are recorded is ticket 116's choice.
5. The options' shape: `mcp_servers` is keyed by `data` plus the config's server names.
   - An http server is `{"type": "http", "url": ..., "headers": {...}}`, header values expanded.
   - A stdio server's `command` is not the config's (it is the wrapper,
     tests/acceptance/test_mcp_launch.py), and no expanded value is in its `command` or `args`.
   - `allowed_tools` is what it was plus `mcp__<server>__<tool>` or `mcp__<server>__*`, the
     earlier entries unchanged and in their order.
   - `tools` and `disallowed_tools` unchanged, `permission_mode == "dontAsk"`,
     `strict_mcp_config is True` (the user's `.mcp.json` and `~/.claude.json` still never join a
     run; ticket 115 "spec 里的解读" 3, for the human to confirm).

What is not asserted (ticket 115 "spec 里的解读"):
- The SDK serialises `mcp_servers` whole into the CLI's `--mcp-config` argument (research note
  2026-11-23-external-mcp.md Q4.3), so an http server's expanded header reaches that argv. Only
  the web's child command line, a stdio server's `command`/`args` and the event file are checked
  (item 1, left to the human).
- `${VAR}` inside `args`, `url` or `command` (item 2, left to the human or ticket 116).

The config files are written by the tests into tmp_path and hold only `${DATA_AGENT_MCP_*}`
references and `.example` hosts. The fake secret values are built here, never written to a file.
"""

from pathlib import Path
from typing import Any

import pytest
from mcp_support import (
    Capture,
    CliRun,
    assert_refused_before_session,
    entries_for,
    events_text,
    external_entries,
    external_servers,
    only_session,
    run_args,
    run_cli,
    server_argv,
    server_of,
    servers_of,
    stop_at_transport,
    version_event,
    warning_naming,
    write_config,
)

TICKET_116 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 116: external MCP is not wired yet"
)

# Fake values, built here so no file holds them.
GITHUB_TOKEN = "tok-" + "Zq81" + "-fake"
MONGO_PASSWORD = "pw-" + "9xQv"
MONGO_URL = f"mongodb://reader:{MONGO_PASSWORD}@mongo.example:27017/support"
# The query string a URL carries; it must not reach the event file.
TENANT = "t-" + "123"
GITHUB_URL = f"https://mcp.example/readonly?tenant={TENANT}"
SECRETS = (GITHUB_TOKEN, MONGO_URL, MONGO_PASSWORD)

MONGO_TOOLS = ["find", "aggregate"]


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> Capture:
    monkeypatch.setenv("DATA_AGENT_MCP_GITHUB_TOKEN", GITHUB_TOKEN)
    monkeypatch.setenv("DATA_AGENT_MCP_MONGO_URL", MONGO_URL)
    return stop_at_transport(monkeypatch)


def mongo(**overrides: Any) -> dict[str, Any]:
    """A stdio server, as the spec's MongoDB example has it."""
    return {
        "type": "stdio",
        "command": "docker",
        "args": [
            "run",
            "--rm",
            "-i",
            "-e",
            "MDB_MCP_CONNECTION_STRING",
            "mongo-mcp.example/server",
        ],
        "env": {"MDB_MCP_CONNECTION_STRING": "${DATA_AGENT_MCP_MONGO_URL}"},
        "allowedTools": MONGO_TOOLS,
        **overrides,
    }


def github(**overrides: Any) -> dict[str, Any]:
    """An http server with a token in a header."""
    return {
        "type": "http",
        "url": GITHUB_URL,
        "headers": {"Authorization": "Bearer ${DATA_AGENT_MCP_GITHUB_TOKEN}"},
        "allowedTools": ["*"],
        **overrides,
    }


def run_with(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    servers: dict[str, Any],
    name: str = "run",
) -> CliRun:
    config = write_config(tmp_path / "config" / f"{name}.mcp.json", servers)
    argv, events_dir = run_args(tmp_path / name, mcp_servers=config)
    return run_cli(capsys, argv, events_dir)


# --- Parsing and checks ----------------------------------------------------------------------


@TICKET_116
def test_run_mounts_stdio_and_http_servers_from_the_file(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    """A valid stdio and a valid http server are both given to the session; a server without
    `type` is stdio."""
    untyped = mongo()
    del untyped["type"]
    run_with(tmp_path, capsys, {"mongo": mongo(), "github": github(), "local": untyped})
    options = only_session(capture).options

    assert set(servers_of(options)) == {"data", "mongo", "github", "local"}
    for name in ("mongo", "local"):
        server = server_of(options, name)
        assert server.get("type", "stdio") == "stdio", name
        # The wrapper starts it (test_mcp_launch.py), never the config's command itself.
        assert server_argv(server)[0] != "docker", name
    http = server_of(options, "github")
    assert http["type"] == "http"
    assert http["url"] == GITHUB_URL
    assert options.permission_mode == "dontAsk"
    assert options.strict_mcp_config is True


@TICKET_116
@pytest.mark.parametrize(
    ("name", "server"),
    [
        pytest.param("legacy", github(type="sse"), id="sse"),
        pytest.param("socketeer", github(type="websocket"), id="unknown-type"),
        pytest.param("data", mongo(), id="reserved-name"),
        pytest.param("my.server", mongo(), id="dot-in-name"),
        pytest.param("my server", mongo(), id="space-in-name"),
        pytest.param("my/server", mongo(), id="slash-in-name"),
    ],
)
def test_a_bad_server_is_refused_before_the_session_naming_it(
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    name: str,
    server: dict[str, Any],
) -> None:
    """Only stdio and http; `data` is ours; a name is letters, digits, `-` and `_`."""
    run = run_with(tmp_path, capsys, {"github": github(), name: server})
    assert_refused_before_session(run, capture, name)


@TICKET_116
@pytest.mark.parametrize("allowed", [None, []], ids=["missing", "empty"])
def test_a_server_without_an_allow_list_gets_none_of_its_tools_and_a_warning(
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    allowed: list[str] | None,
) -> None:
    """Whether the server is mounted at all is not asserted; none of its tools is allowed."""
    quiet = mongo()
    if allowed is None:
        del quiet["allowedTools"]
    else:
        quiet["allowedTools"] = allowed
    run = run_with(tmp_path, capsys, {"quietserver": quiet, "github": github()})
    options = only_session(capture).options

    assert warning_naming(run.stderr, "quietserver"), f"no warning naming it: {run.stderr!r}"
    assert entries_for(options.allowed_tools, "quietserver") == []
    # The other server is still mounted and allowed.
    assert entries_for(options.allowed_tools, "github") == ["mcp__github__*"]


# --- `${VAR}` expansion ----------------------------------------------------------------------


@TICKET_116
def test_variables_are_expanded_into_http_headers(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stdio server's `env` is expanded too; test_mcp_launch.py checks it from what the
    server's process sees."""
    run_with(tmp_path, capsys, {"github": github()})
    http = server_of(only_session(capture).options, "github")
    assert http["headers"] == {"Authorization": f"Bearer {GITHUB_TOKEN}"}


@TICKET_116
@pytest.mark.parametrize(
    ("variable", "value", "where"),
    [
        pytest.param("DATA_AGENT_MCP_NOT_SET", None, "headers", id="unset-in-headers"),
        pytest.param("DATA_AGENT_MCP_NOT_SET", None, "env", id="unset-in-env"),
        pytest.param("GITHUB_PAT_ELSEWHERE", "set-" + "w1", "headers", id="other-prefix"),
        # The model's key is never handed to an external server.
        pytest.param("DATA_AGENT_API_KEY", None, "env", id="model-key"),
    ],
)
def test_a_bad_variable_is_refused_before_the_session_naming_it_and_the_server(
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str | None,
    where: str,
) -> None:
    """Unset, or not `DATA_AGENT_MCP_*`; a set variable of another prefix is refused too, so the
    error is about the prefix, not about the value. (DATA_AGENT_API_KEY is set by the fixture.)"""
    if variable != "DATA_AGENT_API_KEY":
        monkeypatch.delenv(variable, raising=False)
    if value is not None:
        monkeypatch.setenv(variable, value)
    if where == "headers":
        name, server = "badheader", github(headers={"Authorization": f"Bearer ${{{variable}}}"})
    else:
        name, server = "badenv", mongo(env={"MDB_MCP_CONNECTION_STRING": f"${{{variable}}}"})
    run = run_with(tmp_path, capsys, {name: server})
    assert_refused_before_session(run, capture, name, variable)


@TICKET_116
def test_expanded_values_stay_out_of_events_output_and_the_stdio_command_line(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    run = run_with(tmp_path, capsys, {"mongo": mongo(), "github": github()})
    options = only_session(capture).options
    # Both are mounted, the header expanded: the values below were there to leak.
    assert server_of(options, "github")["headers"]["Authorization"] == f"Bearer {GITHUB_TOKEN}"
    stdio = server_argv(server_of(options, "mongo"))

    text = events_text(run.events_dir)
    for secret in SECRETS:
        assert secret not in text, "an expanded value is in the event file"
        assert secret not in run.stdout and secret not in run.stderr, "an expanded value printed"
        assert not [arg for arg in stdio if secret in arg], "an expanded value in the stdio argv"


# --- The allow list --------------------------------------------------------------------------


@TICKET_116
def test_allowed_tools_become_mcp_names_and_nothing_else_changes(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Compared with the same run without `--mcp-servers`."""
    argv, events_dir = run_args(tmp_path / "plain")
    run_cli(capsys, argv, events_dir)
    run_with(tmp_path, capsys, {"mongo": mongo(), "github": github()})
    assert len(capture.sessions) == 2, "both runs reach the session"
    plain, mounted = (session.options for session in capture.sessions)

    added = external_entries(mounted.allowed_tools)
    assert sorted(added) == sorted(["mcp__mongo__find", "mcp__mongo__aggregate", "mcp__github__*"])
    assert [name for name in mounted.allowed_tools if name not in added] == plain.allowed_tools
    assert "mcp__*" not in mounted.allowed_tools
    assert mounted.tools == plain.tools
    assert mounted.disallowed_tools == plain.disallowed_tools
    assert mounted.permission_mode == plain.permission_mode == "dontAsk"
    assert mounted.strict_mcp_config is plain.strict_mcp_config is True


# --- The `version` event ---------------------------------------------------------------------


@TICKET_116
def test_the_version_event_names_the_servers_and_their_allow_lists_only(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    run = run_with(tmp_path, capsys, {"mongo": mongo(), "github": github()})
    only_session(capture)
    recorded = external_servers(version_event(run.events_dir))

    assert [(server.get("name"), server.get("allowed_tools")) for server in recorded] == [
        ("mongo", MONGO_TOOLS),
        ("github", ["*"]),
    ]
    text = events_text(run.events_dir)
    for value in (*SECRETS, TENANT):
        assert value not in text, "an env value, header value or query string is in the events"
