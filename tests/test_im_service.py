"""Starting the bot from `data-agent web --im` (data_agent.im.service, the `--im` lines of
data_agent.cli.serve_web, the lifespan of data_agent.web.app), past the acceptance tests in
tests/acceptance/test_im_config.py: the missing extra, a platform not implemented yet, a bad file
(exit code 2, no token on stderr), no platform library without `--im`, and the bot's start and
stop around the app's. A platform in memory; no Slack."""

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient

from data_agent import cli
from data_agent.im import service
from data_agent.im.config import PlatformConfig
from data_agent.im.platform import Capabilities, Limits, OnMessage, OnPress, Outgoing
from data_agent.web.app import create_app
from data_agent.web.launcher import Launcher

BOT_ENV = "DATA_AGENT_SLACK_BOT_TOKEN"
APP_ENV = "DATA_AGENT_SLACK_APP_TOKEN"
# Made up, in no real token's shape.
BOT_TOKEN = "sb-" + "Hy4c" + "-made-up-bot"
APP_TOKEN = "sa-" + "Vn6t" + "-made-up-app"
SLACK = f"""
[slack]
bot_token_env = "{BOT_ENV}"
app_token_env = "{APP_ENV}"
allowed_users = ["U0ALICE01"]
"""
CONFIG = PlatformConfig(
    "slack",
    frozenset({"U0ALICE01"}),
    {"bot_token_env": BOT_ENV, "app_token_env": APP_ENV},
    {"bot_token_env": BOT_TOKEN, "app_token_env": APP_TOKEN},
)


@pytest.fixture
def tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BOT_ENV, BOT_TOKEN)
    monkeypatch.setenv(APP_ENV, APP_TOKEN)
    monkeypatch.setenv("DATA_AGENT_TELEGRAM_BOT_TOKEN", "tg-made-up")


def web(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, toml: str | None) -> None:
    """`data-agent web`, with `--im` when `toml` is given, up to `uvicorn.run`."""
    monkeypatch.setattr(uvicorn, "run", lambda *_args, **_kwargs: None)
    runs = tmp_path / "runs"
    runs.mkdir(exist_ok=True)
    extra: list[str] = []
    if toml is not None:
        path = tmp_path / "im.toml"
        path.write_text(toml, encoding="utf-8")
        extra = ["--im", str(path)]
    cli.main(["web", "--runs-dir", str(runs), *extra])


def refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], toml: str
) -> str:
    with pytest.raises(SystemExit) as exited:
        web(tmp_path, monkeypatch, toml)
    assert exited.value.code == 2
    err = capsys.readouterr().err
    assert BOT_TOKEN not in err
    assert APP_TOKEN not in err
    return err


def test_without_the_extra_im_says_how_to_install_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tokens: None,
) -> None:
    # An entry of None makes the import fail, as when the package is not installed.
    monkeypatch.setitem(sys.modules, "slack_bolt", None)
    err = refused(tmp_path, monkeypatch, capsys, SLACK)
    assert "uv sync --extra im" in err
    assert "slack" in err
    assert "Traceback" not in err


def test_a_platform_not_implemented_yet_is_refused_by_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tokens: None,
) -> None:
    monkeypatch.setenv("DATA_AGENT_DISCORD_BOT_TOKEN", "dc-made-up")
    discord = '[discord]\nbot_token_env = "DATA_AGENT_DISCORD_BOT_TOKEN"\nallowed_users = ["1"]\n'
    err = refused(tmp_path, monkeypatch, capsys, SLACK + discord)
    assert "discord" in err
    assert "not implemented" in err


def test_a_bad_file_stops_the_server_naming_the_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tokens: None,
) -> None:
    err = refused(tmp_path, monkeypatch, capsys, SLACK.replace(APP_ENV, "DATA_AGENT_API_KEY"))
    assert "slack" in err


def test_a_token_written_where_its_variable_name_goes_is_not_echoed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tokens: None,
) -> None:
    # `refused` checks that the token is nowhere on stderr.
    err = refused(tmp_path, monkeypatch, capsys, SLACK.replace(f'"{BOT_ENV}"', f'"{BOT_TOKEN}"'))
    assert "slack" in err


def test_without_im_runs_get_the_servers_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[Any] = []
    monkeypatch.setattr("data_agent.web.app.create_app", lambda *args, **kwargs: made.append(args))
    web(tmp_path, monkeypatch, None)
    popens: list[dict[str, Any]] = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *_args, **kwargs: popens.append(kwargs))
    [(_, _, start_run)] = made
    start_run(["--task=x"], tmp_path)
    assert "env" not in popens[0]


def test_without_im_no_platform_library_is_imported(tmp_path: Path) -> None:
    """In a process of its own: this test run has imported slack_bolt elsewhere."""
    runs = tmp_path / "runs"
    runs.mkdir()
    code = (
        "import sys, uvicorn\n"
        "uvicorn.run = lambda *a, **k: None\n"
        "from data_agent import cli\n"
        f"cli.main(['web', '--runs-dir', {str(runs)!r}])\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('slack_bolt', 'slack_sdk')"
        " or m == 'data_agent.im.slack'))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    assert out.stdout.strip() == "[]"


# --- the bot beside the app ----------------------------------------------------------------


class Platform:
    def __init__(self, order: list[str], fail: bool = False) -> None:
        self.name = "slack"
        self.capabilities = Capabilities(edit=True, buttons=True, typing=False)
        self.limits = Limits(max_chars=500, edit_interval=2.0)
        self.order = order
        self.fail = fail

    async def start(self, on_message: OnMessage, on_press: OnPress) -> None:
        self.order.append("platform.start")
        if self.fail:
            raise ConnectionError("Slack is not there")

    async def stop(self) -> None:
        self.order.append("platform.stop")

    async def send(self, chat_id: str, thread_id: str | None, message: Outgoing) -> str:
        return "m1"

    async def edit(self, chat_id: str, message_id: str, message: Outgoing) -> None:
        pass

    async def typing(self, chat_id: str, thread_id: str | None) -> None:
        pass


def app_with_bot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: Platform) -> Any:
    def no_run(*_args: Any) -> Any:
        raise AssertionError("no run starts here")

    monkeypatch.setattr(service, "make_platform", lambda _config: platform)
    app = create_app(tmp_path, [], no_run, ui_dir=tmp_path / "no-ui")
    service.start(app, {"slack": CONFIG})
    return app


def wait_for(order: list[str], entry: str) -> None:
    deadline = time.monotonic() + 5
    while entry not in order:
        if time.monotonic() > deadline:
            raise AssertionError(f"{entry} never happened: {order}")
        time.sleep(0.01)


def test_the_bot_starts_with_the_app_and_stops_before_the_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    monkeypatch.setattr(Launcher, "shutdown", lambda _self: order.append("launcher.shutdown"))
    app = app_with_bot(tmp_path, monkeypatch, Platform(order))
    assert order == []
    with TestClient(app):
        wait_for(order, "platform.start")
    assert order == ["platform.start", "platform.stop", "launcher.shutdown"]


def test_a_bot_that_cannot_connect_leaves_the_web_ui_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    order: list[str] = []
    app = app_with_bot(tmp_path, monkeypatch, Platform(order, fail=True))
    with TestClient(app) as client:
        wait_for(order, "platform.start")
        assert client.get("/api/options").status_code == 200
    assert "could not connect" in caplog.text
    assert "ConnectionError" in caplog.text
