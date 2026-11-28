"""IM entry, the `--im` file and the tokens (.scratch/im/spec.md "配置"; "Testing Decisions" 配置).

Written by ticket 130. The tests marked TICKET_131 are xfail(strict) until ticket 131 makes them
pass and removes the marker, changing nothing else. The test without it holds today and must keep
holding: without `--im`, `data-agent web` starts no adapter.

The contract (data_agent.im.config, data_agent.im.service; human-reviewed):

1. `load_config(path, environ)` returns a `PlatformConfig` for each table of the file, by
   platform name, and only those. It raises `ImConfigError` naming the platform when a variable
   is not set, `allowed_users` is empty, or a variable's name lacks the platform's own prefix
   `DATA_AGENT_<PLATFORM>_` (so neither `DATA_AGENT_API_KEY` nor another platform's token
   passes); and for a table that is no platform, or a file with no table.
2. `data-agent web --config C`, C holding the file's tables as `[im.<platform>]` (ticket 141;
   was `--im FILE`), reads them with `os.environ`, builds the app, and calls
   `data_agent.im.service.start(app, configs)` with what `create_app` returned; the runs it
   starts get `env=config.run_env(os.environ, configs)`: everything but the token variables.
   The tests ticket 141 moved are `TICKET_143` until ticket 143 reads config files.
3. No token value is in an error's text, a `repr`, the log, stdout or stderr, the thread map, a
   call to the chat backend, or anything the platform is given.

The tokens are made up here at run time (never written in a file) and set with monkeypatch.
"""

import inspect
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from im_support import (
    ALICE,
    answer,
    append_events,
    header,
    message,
    result,
    start_bot,
)
from product_config_support import fake_home, im_config

from data_agent import cli
from data_agent.im import service
from data_agent.im.adapter import IM_DIR
from data_agent.im.config import ImConfigError, PlatformConfig, load_config
from data_agent.web import app as web_app
from data_agent.web.app import create_app

SLACK_BOT_ENV = "DATA_AGENT_SLACK_BOT_TOKEN"
SLACK_APP_ENV = "DATA_AGENT_SLACK_APP_TOKEN"
TELEGRAM_ENV = "DATA_AGENT_TELEGRAM_BOT_TOKEN"
# Made up, and in no token's real shape (the secret scan of pre-commit stops those).
SLACK_BOT = "sb-" + "Qx7v" + "-made-up-bot"
SLACK_APP = "sa-" + "Lm3k" + "-made-up-app"
TELEGRAM = "tg-" + "Wr9p" + "-made-up"
MODEL_KEY = "mk-" + "Zt5d" + "-made-up"
TOKENS = (SLACK_BOT, SLACK_APP, TELEGRAM)

SLACK = f"""
[slack]
bot_token_env = "{SLACK_BOT_ENV}"
app_token_env = "{SLACK_APP_ENV}"
allowed_users = ["{ALICE}"]
"""
TELEGRAM_TABLE = f"""
[telegram]
bot_token_env = "{TELEGRAM_ENV}"
allowed_users = [123456789]
"""


@pytest.fixture
def tokens(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    environ = {SLACK_BOT_ENV: SLACK_BOT, SLACK_APP_ENV: SLACK_APP, TELEGRAM_ENV: TELEGRAM}
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATA_AGENT_API_KEY", MODEL_KEY)
    return {**environ, "DATA_AGENT_API_KEY": MODEL_KEY}


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config" / "im.toml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def assert_no_token(text: str) -> None:
    for token in TOKENS:
        assert token not in text, "a token's value leaked"


# --- reading the file ----------------------------------------------------------------------


def test_one_platform_is_read_with_its_tokens(tmp_path: Path, tokens: dict[str, str]) -> None:
    configs = load_config(write(tmp_path, SLACK), tokens)
    assert list(configs) == ["slack"]
    slack = configs["slack"]
    assert slack.platform == "slack"
    assert slack.allowed_users == frozenset({ALICE})
    assert set(slack.token_envs.values()) == {SLACK_BOT_ENV, SLACK_APP_ENV}
    assert set(slack.tokens.values()) == {SLACK_BOT, SLACK_APP}
    assert_no_token(repr(slack))


def test_several_platforms_are_read_and_only_those(tmp_path: Path, tokens: dict[str, str]) -> None:
    configs = load_config(write(tmp_path, SLACK + TELEGRAM_TABLE), tokens)
    assert set(configs) == {"slack", "telegram"}
    # Telegram's ids are numbers in the file; strings, as `Incoming.user_id`, once read.
    assert configs["telegram"].allowed_users == frozenset({"123456789"})
    assert set(configs["telegram"].tokens.values()) == {TELEGRAM}


BAD = {
    "variable-not-set": (
        "slack",
        SLACK.replace(SLACK_APP_ENV, "DATA_AGENT_SLACK_UNSET_TOKEN"),
    ),
    "allowed-users-empty": ("slack", SLACK.replace(f'["{ALICE}"]', "[]")),
    "allowed-users-missing": ("slack", SLACK.replace(f'allowed_users = ["{ALICE}"]', "")),
    "model-key-as-token": ("slack", SLACK.replace(SLACK_BOT_ENV, "DATA_AGENT_API_KEY")),
    "other-platforms-prefix": ("slack", SLACK.replace(SLACK_BOT_ENV, TELEGRAM_ENV)),
    "second-platform-bad": ("telegram", SLACK + TELEGRAM_TABLE.replace("[123456789]", "[]")),
    "not-a-platform": ("teams", SLACK + '\n[teams]\nbot_token_env = "DATA_AGENT_TEAMS_X"\n'),
}


@pytest.mark.parametrize("case", list(BAD))
def test_a_bad_table_is_refused_naming_its_platform(
    tmp_path: Path, tokens: dict[str, str], case: str
) -> None:
    platform, text = BAD[case]
    with pytest.raises(ImConfigError) as raised:
        load_config(write(tmp_path, text), tokens)
    assert platform in str(raised.value).lower()
    assert_no_token(str(raised.value))


def test_a_file_without_a_platform_is_refused(tmp_path: Path, tokens: dict[str, str]) -> None:
    with pytest.raises(ImConfigError):
        load_config(write(tmp_path, "# nothing yet\n"), tokens)


# --- `data-agent web --config` with `[im.*]` (was `--im`) --------------------------------


def im_file(tmp_path: Path, text: str) -> Path:
    """The old `--im` file `text` as a config file's `[im.<platform>]` tables (ticket 141)."""
    return im_config(tmp_path, write(tmp_path, text))


def serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: Sequence[str]
) -> tuple[dict[str, Any], object, list[tuple[Any, ...]]]:
    """`data-agent web` with `create_app`, `uvicorn.run` and `service.start` stood in for, as
    tests/acceptance/test_mcp_web.py does; returns `create_app`'s arguments (bound to its
    signature), the app it returned, and every call of `service.start`."""
    import uvicorn

    seen: list[dict[str, Any]] = []
    app = object()
    started: list[tuple[Any, ...]] = []
    signature = inspect.signature(create_app)

    def fake_create_app(*args: Any, **kwargs: Any) -> object:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        seen.append(dict(bound.arguments))
        return app

    start_signature = inspect.signature(service.start)

    def fake_start(*args: Any, **kwargs: Any) -> None:
        started.append(tuple(start_signature.bind(*args, **kwargs).arguments.values()))

    monkeypatch.setattr(web_app, "create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "start", fake_start)
    # No user-level config file of this machine's (ticket 141: `data-agent web` reads one).
    monkeypatch.setenv("HOME", str(fake_home(tmp_path)))
    monkeypatch.delenv("DATA_AGENT_CONFIG", raising=False)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    try:
        cli.main(["web", "--runs-dir", str(runs_dir), *extra])
    except SystemExit as exc:
        raise AssertionError(f"data-agent web exited {exc.code!r}") from exc
    if len(seen) != 1:
        raise RuntimeError("create_app was not called once")
    return seen[0], app, started


def test_data_agent_web_im_starts_the_adapters_with_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tokens: dict[str, str]
) -> None:
    _, app, started = serve(tmp_path, monkeypatch, ["--config", str(im_file(tmp_path, SLACK))])
    assert len(started) == 1
    given_app, configs = started[0]
    assert given_app is app
    assert set(configs) == {"slack"}
    assert isinstance(configs["slack"], PlatformConfig)


def test_data_agent_web_without_im_starts_no_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, started = serve(tmp_path, monkeypatch, [])
    assert started == []


def test_a_run_starts_without_the_im_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tokens: dict[str, str]
) -> None:
    called, _, _ = serve(tmp_path, monkeypatch, ["--config", str(im_file(tmp_path, SLACK))])
    popens: list[dict[str, Any]] = []

    def fake_popen(*args: Any, **kwargs: Any) -> object:
        popens.append(kwargs)
        return object()

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    called["start_run"](["--task=x"], tmp_path)
    if len(popens) != 1:
        raise RuntimeError("start_run did not call Popen once")
    env = popens[0].get("env")
    assert env is not None, "the run inherits the server's whole environment"
    assert SLACK_BOT_ENV not in env
    assert SLACK_APP_ENV not in env
    values = "\n".join(env.values())
    assert SLACK_BOT not in values
    assert SLACK_APP not in values
    # Only the IM tokens are gone: the rest of the server's environment is there as it was.
    assert env["PATH"] == os.environ["PATH"]
    assert env["DATA_AGENT_API_KEY"] == MODEL_KEY
    assert env[TELEGRAM_ENV] == TELEGRAM, "a platform the file does not name is not touched"


# --- the tokens stay out of everything -----------------------------------------------------


async def test_no_token_value_reaches_the_log_the_output_or_the_platform(
    tmp_path: Path,
    tokens: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level("DEBUG")
    configs = load_config(write(tmp_path, SLACK + TELEGRAM_TABLE), tokens)
    errors = []
    for case in BAD.values():
        try:
            load_config(write(tmp_path, case[1]), tokens)
        except ImConfigError as exc:
            errors.append(str(exc))
    assert len(errors) == len(BAD)

    bot = await start_bot(tmp_path / "runs", allowed=configs["slack"].allowed_users)
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    run_id = bot.conversations.only_run()
    append_events(bot.runs, run_id, [*header(), answer(20, "Fourteen."), result(30)])
    bot.clock.advance(60)
    await bot.adapter.poll()
    assert bot.platform.sends(), "the bot sent nothing"

    captured = capsys.readouterr()
    thread_map = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "runs" / IM_DIR).rglob("*")
        if path.is_file()
    )
    platform_given = [
        *bot.platform.texts(),
        *(button.value for _, sent in bot.platform.calls for button in sent.buttons),
        *(button.label for _, sent in bot.platform.calls for button in sent.buttons),
    ]
    for text in [
        caplog.text,
        captured.out,
        captured.err,
        thread_map,
        repr(configs),
        repr(bot.conversations.sent),
        *platform_given,
        *errors,
    ]:
        assert_no_token(text)
