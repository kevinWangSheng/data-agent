"""Startup checks of the deployment mode (.scratch/cloud-deploy/spec.md "验收测试接缝" 启动检查;
.scratch/product-config/decisions.md Q8; ADR-0012 and decisions.md "建 settings 工单时主会话定的
细节" 1: the one token is `DATA_AGENT_ADMIN_TOKEN`).

Written by ticket 151. The tests marked TICKET_153 are xfail(strict) until ticket 153 makes them
pass and removes the marker, changing nothing else. The tests named `test_guard_...` pass now and
must keep passing: a missing `${VAR}` and an empty IM allowlist are refused in every mode
(product-config ticket 143), and a local server without `[server]` listens on the loopback address
and asks for no token.

Seams (tests/acceptance/cloud_support.py): the whole `data-agent web --config F` with
`uvicorn.run` stood in for; the whole `data-agent run --config F` with its session stopped at the
SDK transport.

The contract fixed here (ticket 151 contract item 1; for the human review):

1. A server that must not start is refused before it serves: exit code 2, stderr naming the
   reason (the variable or the config key), `uvicorn.run` never called. The same refusal on
   `data-agent run`: exit code 2, stderr naming the reason, no session started. Refused:
   - in any mode, a `[server].host` other than the loopback address without
     `DATA_AGENT_ADMIN_TOKEN` set (stderr names `DATA_AGENT_ADMIN_TOKEN`); with it set the server
     listens on the configured host;
   - in cloud mode, `[sandbox].backend = "local"`, by `data-agent web` and `data-agent run` alike
     (stderr names `backend` or `local`);
   - in any mode, a `${VAR}` the config references that is not set (stderr names the variable),
     and an IM platform whose `allowed_users` is empty (stderr names the platform).
   The mode is `[server].mode`, or the environment's `DATA_AGENT_MODE` when the files set none.
"""

from pathlib import Path

import pytest
from cloud_support import (
    ADMIN_TOKEN_ENV,
    ANY_ADDRESS,
    LOOPBACK,
    MODE_ENV,
    assert_refused,
    assert_run_refused,
    isolated,
    query_args,
    run_cli,
    served,
    server_table,
    set_admin_token,
    stop_sessions,
    write_file,
)

TICKET_153 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 153: [server].mode is not wired yet"
)

pytestmark = pytest.mark.usefixtures(isolated.__name__)

MODES = ("local", "cloud")
MISSING_ENV = "DATA_AGENT_CLOUD_TEST_MISSING"
SLACK_BOT_ENV = "DATA_AGENT_SLACK_BOT_TOKEN"
SLACK_APP_ENV = "DATA_AGENT_SLACK_APP_TOKEN"


def config(tmp_path: Path, text: str, name: str = "config.toml") -> Path:
    return write_file(tmp_path / "config" / name, text)


# --- The listening address and the token ----------------------------------------------------


@TICKET_153
@pytest.mark.parametrize("mode", MODES)
def test_a_non_loopback_host_needs_the_admin_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    path = config(tmp_path, server_table(mode, ANY_ADDRESS))
    with served(tmp_path / "without", monkeypatch, capsys, ["--config", str(path)]) as server:
        assert_refused(server, ADMIN_TOKEN_ENV)
    set_admin_token(monkeypatch)
    with served(tmp_path / "with", monkeypatch, capsys, ["--config", str(path)]) as server:
        assert server.host == ANY_ADDRESS


def test_guard_a_local_server_without_server_listens_on_loopback_without_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = config(tmp_path, "# no [server]\n")
    with served(tmp_path, monkeypatch, capsys, ["--config", str(path)]) as server:
        assert server.host == LOOPBACK


# --- The sandbox backend in cloud mode ------------------------------------------------------

CLOUD_LOCAL_BACKEND = server_table("cloud") + '\n[sandbox]\nbackend = "local"\n'


@TICKET_153
def test_cloud_web_refuses_the_local_sandbox_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    set_admin_token(monkeypatch)
    path = config(tmp_path, CLOUD_LOCAL_BACKEND)
    with served(tmp_path, monkeypatch, capsys, ["--config", str(path)]) as server:
        assert_refused(server, "backend", "local")


@TICKET_153
def test_cloud_run_refuses_the_local_sandbox_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    set_admin_token(monkeypatch)
    sessions = stop_sessions(monkeypatch)
    path = config(tmp_path, CLOUD_LOCAL_BACKEND)
    run = run_cli(capsys, query_args(tmp_path / "run", path))
    assert_run_refused(run, sessions, "backend", "local")


@TICKET_153
def test_data_agent_mode_cloud_acts_as_cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    set_admin_token(monkeypatch)
    monkeypatch.setenv(MODE_ENV, "cloud")
    path = config(tmp_path, '[sandbox]\nbackend = "local"\n')
    with served(tmp_path, monkeypatch, capsys, ["--config", str(path)]) as server:
        assert_refused(server, "backend", "local")


# --- Refused in every mode (product-config ticket 143) --------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_guard_a_missing_variable_is_refused_naming_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    set_admin_token(monkeypatch)
    text = server_table(mode) + f'\n[trace]\nbase_url = "${{{MISSING_ENV}}}"\n'
    path = config(tmp_path, text)
    with served(tmp_path, monkeypatch, capsys, ["--config", str(path)]) as server:
        assert_refused(server, MISSING_ENV)


@pytest.mark.parametrize("mode", MODES)
def test_guard_an_empty_im_allowlist_is_refused_naming_the_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    set_admin_token(monkeypatch)
    monkeypatch.setenv(SLACK_BOT_ENV, "sb-" + "Nf4c" + "-made-up")
    monkeypatch.setenv(SLACK_APP_ENV, "sa-" + "Ku7e" + "-made-up")
    text = server_table(mode) + (
        f'\n[im.slack]\nbot_token_env = "{SLACK_BOT_ENV}"\napp_token_env = "{SLACK_APP_ENV}"\n'
        "allowed_users = []\n"
    )
    path = config(tmp_path, text)
    with served(tmp_path, monkeypatch, capsys, ["--config", str(path)]) as server:
        assert_refused(server, "slack")
