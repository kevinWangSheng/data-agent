"""Helpers shared by the settings acceptance tests (.scratch/settings/spec.md "Testing Decisions"
验收测试接缝, ticket 157): test_settings_*.py.

Copied, not imported: what these tests use of tests/acceptance/product_config_support.py,
im_support.py and seam_support.py is copied in here (ticket 157 Authority), so a later edit there
cannot move these tests. The recorded snapshots (tests/fixtures/product_config/snapshots/) are
read only: `compare_snapshot` compares and never records.

Reaching what does not exist yet (ticket 157 Context "接缝"): no stub is added to `src/`. The
settings modules (`data_agent.settings.*`), their functions, new fields and new arguments are
taken through `module_of`, `attr_of`, `has_field`, `has_parameter` and `patch_attr`, which
`assert` first. A test marked `xfail(strict=True, raises=AssertionError)` then fails today on the
assertion, never on an `ImportError`, `AttributeError` or `TypeError`. A config file holding a key
today's schema does not know is loaded through `load_config`, which turns the loader's
`ConfigFileError` into an assertion that names the key.

The stand-ins (the Slack client, the OpenID provider, the platform, the conversations) never
assert: one used wrongly raises `RuntimeError`, which a marked test reports as a failure, not as
the expected one.

Placeholders: every token, key and secret here is built from parts, so no scanner takes it for a
real one; hosts are `.example` names. No test calls a model, the network or Slack.
"""

import asyncio
import fnmatch
import hashlib
import importlib
import inspect
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import uuid
from collections.abc import AsyncIterable, Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from types import ModuleType, SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, CLIConnectionError

from data_agent import cli

# --- Paths (seam_support.py) -----------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SETTINGS_FIXTURES = FIXTURES / "settings"
SHOP_SQL = FIXTURES / "shop.sql"
DBT_SHOP = FIXTURES / "dbt_shop"
FAKE_DAB = FIXTURES / "fake_dab"
SNAPSHOTS = FIXTURES / "product_config" / "snapshots"
FAKE_CHAT_RUN = FIXTURES / "chat" / "fake_chat_run.py"


def build_shop_duckdb(path: Path) -> Path:
    import duckdb  # pyright: ignore[reportMissingImports]

    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute(SHOP_SQL.read_text())
    finally:
        con.close()
    return path


def make_dbt_workspace(root: Path) -> tuple[Path, Path]:
    """A copy of the mini dbt project plus its private DuckDB file under root."""
    root.mkdir(parents=True, exist_ok=True)
    db = build_shop_duckdb(root / "shop.duckdb")
    project = root / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    (project / "profiles.yml").write_text(
        "shop:\n"
        "  target: dev\n"
        "  outputs:\n"
        "    dev:\n"
        "      type: duckdb\n"
        f"      path: {db}\n"
        "      schema: main\n"
        "      threads: 1\n"
    )
    return project, db


# --- Placeholders --------------------------------------------------------------------------

API_KEY = "unused-test-key-" + "Hq3v"
PG_PASSWORD = "hunter2-" + "t6Yd"
PG_URL = f"postgresql://agent:{PG_PASSWORD}@localhost:5432/shop"
ADMIN_TOKEN_ENV = "DATA_AGENT_ADMIN_TOKEN"
ADMIN_TOKEN = "admin-" + "Rk7q" + "-placeholder"
SESSION_KEY_ENV = "DATA_AGENT_SESSION_KEY"
SESSION_KEY = "session-key-" + "Vb2m" + "-placeholder"
OIDC_CLIENT_ID_ENV = "DATA_AGENT_SLACK_OIDC_CLIENT_ID"
OIDC_CLIENT_SECRET_ENV = "DATA_AGENT_SLACK_OIDC_CLIENT_SECRET"
OIDC_CLIENT_ID = "1234." + "5678"
OIDC_CLIENT_SECRET = "oidc-secret-" + "Jd4w"
SLACK_BOT_TOKEN_ENV = "DATA_AGENT_SLACK_BOT_TOKEN"
SLACK_APP_TOKEN_ENV = "DATA_AGENT_SLACK_APP_TOKEN"
SLACK_BOT_TOKEN = "bot-" + "Pq9z" + "-placeholder"
SLACK_APP_TOKEN = "app-" + "Lm3x" + "-placeholder"
# What the stand-in OpenID provider hands out as the user's Slack access token.
SLACK_ACCESS_TOKEN = "user-access-" + "Tt5n" + "-placeholder"
MCP_TOKEN_ENV = "DATA_AGENT_MCP_ACME_TOKEN"
MCP_TOKEN = "acme-" + "Wz81" + "-fake"
PUBLIC_URL = "https://data-agent.example"
EXIT_CONFIG_ERROR = 2
CONFIG_DIR = ".data-agent"
CONFIG_FILE = "config.toml"
AMBIENT_ENV = (
    "DATA_AGENT_BASE_URL",
    "DATA_AGENT_MODEL",
    "DATA_AGENT_CONFIG",
    "DATA_AGENT_MODE",
    "CLAUDE_CODE_EFFORT_LEVEL",
    "DATA_AGENT_LANGFUSE_PUBLIC_KEY",
    "DATA_AGENT_LANGFUSE_SECRET_KEY",
    "DATA_AGENT_LANGFUSE_BASE_URL",
    ADMIN_TOKEN_ENV,
    SESSION_KEY_ENV,
    OIDC_CLIENT_ID_ENV,
    OIDC_CLIENT_SECRET_ENV,
)

# People. Slack user ids as Slack gives them; the settings' user ids carry the platform
# (contract 2: `slack:U…`).
TEAM = "T0ACME001"
OTHER_TEAM = "T0GLOBEX2"
UNREGISTERED_TEAM = "T0EVIL999"
ROOT = "U0ROOT001"  # in `[server].org_admins`
ADMIN = "U0ADMIN01"  # Slack workspace admin (`is_admin`)
OWNER = "U0OWNER01"  # Slack workspace owner (`is_owner`)
ALICE = "U0ALICE01"  # a member of SALES
BOB = "U0BOB0002"  # a member of SALES
EVE = "U0EVE0099"  # in the workspace, in no channel
SALES = "C0SALES01"
ENG = "C0ENG0002"
TELEGRAM_BOT = "7001"
TELEGRAM_GROUP = "-100777"
TELEGRAM_USER = "42424242"
DISCORD_GUILD = "G0GUILD01"
DISCORD_CHANNEL = "D0CHAN001"
DISCORD_USER = "990001"

# --- Scope ids (contract 3) ----------------------------------------------------------------

ORG = "org"


def workspace(team: str = TEAM, platform: str = "slack") -> str:
    return f"workspace:{platform}:{team}"


def channel(chat: str = SALES, team: str = TEAM, platform: str = "slack") -> str:
    return f"channel:{platform}:{team}:{chat}"


def user(person: str, platform: str = "slack") -> str:
    return f"user:{platform}:{person}"


def platform_user(person: str, platform: str = "slack") -> str:
    """A person as `[server].org_admins`, a change record's `user` and an identity name them."""
    return f"{platform}:{person}"


# --- Reaching what does not exist yet ------------------------------------------------------


def module_of(name: str) -> ModuleType:
    """The module `name`, asserted to exist. Only a missing `name` (or a missing package of it)
    becomes the assertion; any other import error is raised as it is, so a broken module never
    passes for an unbuilt one."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        parts = name.split(".")
        missing = {".".join(parts[:n]) for n in range(1, len(parts) + 1)}
        if exc.name in missing:
            raise AssertionError(f"no module {name} yet") from exc
        raise


def attr_of(obj: Any, name: str) -> Any:
    assert hasattr(obj, name), f"{getattr(obj, '__name__', type(obj).__name__)} has no {name!r}"
    return getattr(obj, name)


def settings_attr(module: str, name: str) -> Any:
    """`data_agent.settings.<module>.<name>`, asserted to be there."""
    return attr_of(module_of(f"data_agent.settings.{module}"), name)


def key_of(mapping: Any, key: str) -> Any:
    assert isinstance(mapping, Mapping), f"not a mapping: {mapping!r}"
    assert key in mapping, f"no {key!r} in {sorted(map(str, mapping))}"
    return mapping[key]


def has_field(cls: type, name: str) -> None:
    assert name in {f.name for f in fields(cls)}, f"{cls.__name__} has no field {name!r}"


def has_parameter(function: Any, name: str) -> None:
    parameters = inspect.signature(function).parameters
    assert name in parameters, f"{function.__qualname__} takes no {name!r}"


def patch_attr(monkeypatch: pytest.MonkeyPatch, obj: Any, name: str, value: Any) -> None:
    attr_of(obj, name)
    monkeypatch.setattr(obj, name, value)


def raised(call: Callable[[], Any]) -> BaseException | None:
    """What `call` raised, None when it returned: the error tests assert on afterwards (ticket
    157 Context: no `pytest.raises`, which would raise its own exception type)."""
    try:
        call()
    except Exception as exc:
        return exc
    return None


def settings_error(exc: BaseException | None, key: str) -> None:
    """`exc` is the settings' error (`SettingsError`, contract 1) and names `key`."""
    error = settings_attr("store", "SettingsError")
    assert exc is not None, f"nothing refused {key}"
    assert isinstance(exc, error), f"{key}: {type(exc).__name__}: {exc}"
    assert getattr(exc, "key", None) == key or key in str(exc), f"the error names no {key}: {exc}"


# --- Config files --------------------------------------------------------------------------


def toml_path(path: Path) -> str:
    return json.dumps(str(path))


def write_file(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_config(directory: Path, text: str) -> Path:
    """`<directory>/.data-agent/config.toml`."""
    return write_file(directory / CONFIG_DIR / CONFIG_FILE, text)


def fake_home(root: Path) -> Path:
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    return home


def load_config(paths: Sequence[Path], environ: Mapping[str, str] | None = None) -> Any:
    """The effective config of `paths` (data_agent.config, ticket 143). A key today's schema does
    not know is the feature not built yet: the assertion names it."""
    from data_agent.config import ConfigFileError, load_effective_config

    env = dict(os.environ if environ is None else environ)
    try:
        return load_effective_config(list(paths), env, {})
    except ConfigFileError as exc:
        raise AssertionError(f"the config loader refuses the file: {exc}") from exc


def shop_config(root: Path, extra: str = "") -> Path:
    """A config file whose one source is a fresh shop DuckDB, then `extra`."""
    db = build_shop_duckdb(root / "shop.duckdb")
    text = f'[sources.shop]\nkind = "duckdb"\npath = {toml_path(db)}\n\n{extra}'
    return write_file(root / "config" / "config.toml", text)


def read_toml_leaves(path: Path) -> dict[str, Any]:
    """A config file as dotted keys to values."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    leaves: dict[str, Any] = {}

    def walk(node: Mapping[str, Any], prefix: str) -> None:
        for key, value in node.items():
            dotted = f"{prefix}{key}"
            if isinstance(value, dict):
                walk(value, dotted + ".")
            else:
                leaves[dotted] = value

    walk(data, "")
    return leaves


# --- The settings store (contract 1) ---------------------------------------------------------


def open_store(path: Path, config: Any, **kwargs: Any) -> Any:
    """`data_agent.settings.store.open_store(path, config)`."""
    return settings_attr("store", "open_store")(path, config, **kwargs)


def sqlite_tables(path: Path) -> list[str]:
    """The tables of the store's file, read directly (the store is one sqlite file, spec S9)."""
    if not path.is_file():
        raise RuntimeError(f"no store file {path}")
    with sqlite3.connect(path) as con:
        return [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]


def sqlite_version(path: Path) -> int:
    with sqlite3.connect(path) as con:
        return int(con.execute("PRAGMA user_version").fetchone()[0])


def plain_sqlite(path: Path) -> Path:
    """A sqlite file that is no store of ours (the evaluation isolation guard)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE IF NOT EXISTS settings (scope TEXT, key TEXT, value TEXT)")
        con.execute(
            "INSERT INTO settings VALUES ('org', 'limits.max_turns.query', '3'), "
            "('org', 'roles.main', '\"deepseek-v4-pro\"')"
        )
    if not path.is_file():
        raise RuntimeError(f"could not write {path}")
    return path


# --- The stand-in Slack (Web API and OpenID provider) -----------------------------------------


class SlackResponse(dict[str, Any]):
    """What a slack_sdk call returns, as far as callers read it: a mapping, `.data`,
    `.status_code`, `.validate()`."""

    status_code = 200

    @property
    def data(self) -> dict[str, Any]:
        return dict(self)

    def validate(self) -> "SlackResponse":
        return self


@dataclass
class SlackPerson:
    team: str
    is_admin: bool = False
    is_owner: bool = False


class FakeSlack:
    """One Slack for every workspace: people with their roles, channels with their members, and
    the OpenID provider of Sign in with Slack. Every call is recorded as (method, arguments).

    As a client it is handed to `Access` directly (contract 1); for the web app its methods are
    put on `slack_sdk.web.async_client.AsyncWebClient` (`patch_async_web_client`), where the
    client's own `token` says which workspace's bot asks."""

    def __init__(self) -> None:
        self.people: dict[str, SlackPerson] = {
            ROOT: SlackPerson(TEAM),
            ADMIN: SlackPerson(TEAM, is_admin=True),
            OWNER: SlackPerson(TEAM, is_owner=True),
            ALICE: SlackPerson(TEAM),
            BOB: SlackPerson(TEAM),
            EVE: SlackPerson(TEAM),
        }
        self.members: dict[str, list[str]] = {SALES: [ALICE, BOB, ADMIN], ENG: [EVE]}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # code -> (user, team) of a sign-in the provider will complete.
        self.logins: dict[str, tuple[str, str]] = {}

    def count(self, method: str, **match: Any) -> int:
        return sum(
            1
            for name, args in self.calls
            if name == method and all(args.get(k) == v for k, v in match.items())
        )

    # --- Web API ---------------------------------------------------------------------------

    async def users_info(self, *, user: str, **kwargs: Any) -> SlackResponse:
        self.calls.append(("users.info", {"user": user, **kwargs}))
        person = self.people.get(user)
        if person is None:
            return SlackResponse(ok=False, error="user_not_found")
        return SlackResponse(
            ok=True,
            user={
                "id": user,
                "team_id": person.team,
                "is_admin": person.is_admin,
                "is_owner": person.is_owner,
                "is_primary_owner": False,
                "deleted": False,
            },
        )

    async def conversations_members(self, *, channel: str, **kwargs: Any) -> SlackResponse:
        self.calls.append(("conversations.members", {"channel": channel, **kwargs}))
        if channel not in self.members:
            return SlackResponse(ok=False, error="channel_not_found")
        return SlackResponse(
            ok=True, members=list(self.members[channel]), response_metadata={"next_cursor": ""}
        )

    async def chat_postEphemeral(self, **kwargs: Any) -> SlackResponse:
        self.calls.append(("chat.postEphemeral", kwargs))
        return SlackResponse(ok=True, message_ts="1.000001")

    async def chat_postMessage(self, **kwargs: Any) -> SlackResponse:
        self.calls.append(("chat.postMessage", kwargs))
        return SlackResponse(ok=True, ts="1.000002", channel=kwargs.get("channel"))

    # --- OpenID Connect (Sign in with Slack) -------------------------------------------------
    # Claim names as Slack documents them (checked 2026-11-28):
    # https://docs.slack.dev/reference/methods/openid.connect.userInfo gives `sub`,
    # `https://slack.com/user_id`, `https://slack.com/team_id`, `email`, `name`, ...;
    # https://docs.slack.dev/reference/methods/openid.connect.token gives `ok`, `access_token`,
    # `token_type`, `id_token`. The id_token here is no valid JWT: contract 5 takes the identity
    # from `openid.connect.userInfo`, never from the id_token.

    def will_sign_in(self, person: str, team: str = TEAM) -> str:
        code = f"code-{person}-{team}-{len(self.logins)}"
        self.logins[code] = (person, team)
        return code

    async def openid_connect_token(self, **kwargs: Any) -> SlackResponse:
        self.calls.append(("openid.connect.token", kwargs))
        code = kwargs.get("code")
        if code not in self.logins:
            return SlackResponse(ok=False, error="invalid_code")
        return SlackResponse(
            ok=True,
            access_token=f"{SLACK_ACCESS_TOKEN}:{code}",
            token_type="Bearer",
            id_token="not-a-jwt",
        )

    async def openid_connect_userInfo(self, **kwargs: Any) -> SlackResponse:
        self.calls.append(("openid.connect.userInfo", kwargs))
        token = str(kwargs.get("token") or "")
        code = token.split(":", 1)[1] if ":" in token else ""
        if code not in self.logins:
            return SlackResponse(ok=False, error="invalid_auth")
        person, team = self.logins[code]
        return SlackResponse(
            {
                "ok": True,
                "sub": person,
                "https://slack.com/user_id": person,
                "https://slack.com/team_id": team,
                "email": f"{person.lower()}@acme.example",
                "email_verified": True,
                "name": person,
                "https://slack.com/team_name": "Acme",
                "https://slack.com/team_domain": "acme",
            }
        )


SLACK_METHODS = (
    "users_info",
    "conversations_members",
    "chat_postEphemeral",
    "chat_postMessage",
    "openid_connect_token",
    "openid_connect_userInfo",
)


def patch_async_web_client(monkeypatch: pytest.MonkeyPatch, slack: FakeSlack) -> None:
    """Every `AsyncWebClient` (and `WebClient`) call of `SLACK_METHODS` answered by `slack`
    (contract 5: the web side calls Slack through slack_sdk's clients, nothing else)."""
    from slack_sdk import WebClient
    from slack_sdk.web.async_client import AsyncWebClient

    for method in SLACK_METHODS:
        fake = getattr(slack, method)

        async def async_call(self: Any, *args: Any, _fake: Any = fake, **kwargs: Any) -> Any:
            return await _fake(**kwargs)

        def sync_call(self: Any, *args: Any, _fake: Any = fake, **kwargs: Any) -> Any:
            return asyncio.run(_fake(**kwargs))

        monkeypatch.setattr(AsyncWebClient, method, async_call)
        monkeypatch.setattr(WebClient, method, sync_call)


class FakeClock:
    """Seconds, as `time.monotonic` gives them; only `advance` moves it."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- Running the CLI (product_config_support.py) ---------------------------------------------


@dataclass
class Session:
    options: ClaudeAgentOptions
    prompt: str
    probed: Any = None


@dataclass
class Capture:
    sessions: list[Session] = field(default_factory=list[Session])
    probe: Callable[[ClaudeAgentOptions], Any] | None = None

    def only(self) -> Session:
        assert len(self.sessions) == 1, f"expected one session, got {len(self.sessions)}"
        return self.sessions[0]


async def prompt_text(prompt: str | AsyncIterable[dict[str, Any]]) -> str:
    if isinstance(prompt, str):
        return prompt
    texts: list[str] = []
    async for message in prompt:
        content = message.get("message", {}).get("content")
        texts.append(content if isinstance(content, str) else json.dumps(content))
    return "\n".join(texts)


def stop_sessions(monkeypatch: pytest.MonkeyPatch, home: Path | None = None) -> Capture:
    """Stops every session at `ClaudeSDKClient` and records it (product_config_support.py's
    seam); sets the model key, clears AMBIENT_ENV, points HOME at `home`."""
    capture = Capture()

    async def connect(self: ClaudeSDKClient, prompt: Any = None) -> None:
        await asyncio.sleep(0)

    async def query(self: ClaudeSDKClient, prompt: Any, session_id: str = "default") -> None:
        probed: Any = None
        if capture.probe is not None:
            try:
                probed = capture.probe(self.options)
            except Exception as exc:
                probed = exc
        capture.sessions.append(Session(self.options, await prompt_text(prompt), probed))
        raise CLIConnectionError("stopped by the settings acceptance tests")

    monkeypatch.setattr(ClaudeSDKClient, "connect", connect)
    monkeypatch.setattr(ClaudeSDKClient, "query", query)
    monkeypatch.setenv("DATA_AGENT_API_KEY", API_KEY)
    for name in AMBIENT_ENV:
        monkeypatch.delenv(name, raising=False)
    if home is not None:
        monkeypatch.setenv("HOME", str(home))
    return capture


def no_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    from data_agent import sandbox

    def run_docker(args: Sequence[str], timeout: float = 0) -> sandbox.Execution:
        return sandbox.Execution(0, "", "", False)

    monkeypatch.setattr(sandbox, "run_docker", run_docker)


@dataclass
class CliRun:
    code: int | str | None
    stdout: str
    stderr: str


def run_cli(capsys: pytest.CaptureFixture[str], argv: Sequence[str]) -> CliRun:
    capsys.readouterr()
    try:
        cli.main(list(argv))
    except SystemExit as exc:
        code = exc.code
    else:
        code = 0
    out = capsys.readouterr()
    return CliRun(code, out.out, out.err)


def query_args(root: Path, *extra: str) -> list[str]:
    """A query run under `root` on the config's source, `--sandbox local`."""
    workspace_dir = root / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return [
        "run",
        *("--task", "How many orders are there?", "--type", "query"),
        *("--workspace", str(workspace_dir)),
        *("--events-dir", str(root / "events"), "--sandbox", "local"),
        *extra,
    ]


def flag_values(args: Sequence[str], name: str) -> list[str]:
    args = list(args)
    return [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == name]


def flag_value(args: Sequence[str], name: str) -> str:
    assert name in args, f"{name} not in the arguments: {list(args)}"
    index = list(args).index(name)
    assert index + 1 < len(args), f"{name} has no value"
    return args[index + 1]


def server_config_probe(options: ClaudeAgentOptions) -> dict[str, Any]:
    """The effective config a `data-agent run` session was given, as dotted keys: the file our
    MCP server gets as `--config` (`cli.server_config`, written to the run's scratch directory and
    read here while it exists). What ticket 144 wires into the session's options."""
    servers = options.mcp_servers if isinstance(options.mcp_servers, dict) else {}
    server = servers.get("data")
    if not isinstance(server, dict):
        raise RuntimeError(f"no data server in {sorted(servers)}")
    leaves: dict[str, Any] = {}
    for path in flag_values([str(a) for a in server.get("args", [])], "--config"):
        if Path(path).is_file():
            leaves |= read_toml_leaves(Path(path))
    return leaves


# --- The web app ------------------------------------------------------------------------------

WEB_TIMEOUT = 60
FORM = {"task": "Count orders", "task_type": "query", "data_source": "shop"}


@dataclass
class Started:
    args: list[str]
    process: Any
    env: dict[str, str] | None = None


@dataclass
class Web:
    app: Any
    started: list[Started]
    gates: Path

    def only_args(self) -> list[str]:
        assert len(self.started) == 1, f"expected one run, got {len(self.started)}"
        return self.started[0].args

    def finish(self) -> None:
        """Lets every fake run started so far write its result and exit, and waits for it."""
        for run in self.started:
            self.gates.joinpath(Path(flag_value(run.args, "--events-dir")).name).touch()
        for run in self.started:
            run.process.wait(timeout=WEB_TIMEOUT)


def recording_start_run(gates: Path, started: list[Started]) -> Any:
    def start_run(args: Sequence[str], cwd: Path) -> Any:
        process = subprocess.Popen(
            [sys.executable, str(FAKE_CHAT_RUN), "--gate-dir", str(gates), *args],
            cwd=cwd,
            stdin=subprocess.PIPE,
        )
        started.append(Started(list(args), process))
        return process

    return start_run


def stop_runs(started: Sequence[Started]) -> None:
    """Only the fake runs this test started, by their own process handles."""
    for run in started:
        if run.process.poll() is None:
            run.process.terminate()
        run.process.wait(timeout=WEB_TIMEOUT)


def run_config_leaves(args: Sequence[str]) -> dict[str, Any]:
    """The config a web or IM run was started with: its `--config` files merged in order, as
    dotted keys (`Launcher.config_args`; what ticket 146's `config` event records)."""
    leaves: dict[str, Any] = {}
    paths = flag_values(args, "--config")
    if not paths:
        raise RuntimeError(f"the run has no --config: {list(args)}")
    for path in paths:
        leaves |= read_toml_leaves(Path(path))
    return leaves


@dataclass
class Served:
    """`data-agent web` run in-process with `uvicorn.run` and the IM service stood in for."""

    code: int | str | None
    stderr: str
    web: Web | None
    uvicorn: dict[str, Any] | None
    im_started: list[Any]

    def app(self) -> Any:
        assert self.web is not None, f"the server did not start (exit {self.code!r}): {self.stderr}"
        return self.web.app


@contextmanager
def served(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: Sequence[str],
) -> Iterator[Served]:
    """`data-agent web --runs-dir <root>/runs <argv>` (product_config_support.py `served`):
    `create_app` is the real one with a recording `start_run`; `uvicorn.run` records its app;
    `data_agent.im.service.start` records its call and starts no platform."""
    import uvicorn

    from data_agent.im import service
    from data_agent.web import app as web_module

    runs = root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    real = web_module.create_app
    signature = inspect.signature(real)
    gates = root / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    started: list[Started] = []
    built: dict[str, Any] = {}
    im_started: list[Any] = []

    def create_app(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind(*args, **kwargs)
        bound.arguments["start_run"] = recording_start_run(gates, started)
        built["app"] = real(*bound.args, **bound.kwargs)
        return built["app"]

    def run(app: Any, **kwargs: Any) -> None:
        built["uvicorn"] = kwargs

    def start(app: Any, configs: Any) -> None:
        im_started.append(configs)

    capsys.readouterr()
    with monkeypatch.context() as patch:
        patch.setattr(web_module, "create_app", create_app)
        patch.setattr(uvicorn, "run", run)
        patch.setattr(service, "start", start)
        try:
            cli.main(["web", "--runs-dir", str(runs), *argv])
        except SystemExit as exc:
            code = exc.code
        else:
            code = None
    err = capsys.readouterr().err
    web = Web(built["app"], started, gates) if "app" in built else None
    try:
        yield Served(code, err, web, built.get("uvicorn"), im_started)
    finally:
        stop_runs(started)


def cloud_env(monkeypatch: pytest.MonkeyPatch, *, slack: bool = True) -> None:
    """The variables a cloud server with Sign in with Slack needs (contract 5): the session key,
    the OpenID client, the bot's tokens. Each test that checks a missing one deletes it after."""
    monkeypatch.setenv(SESSION_KEY_ENV, SESSION_KEY)
    if slack:
        monkeypatch.setenv(OIDC_CLIENT_ID_ENV, OIDC_CLIENT_ID)
        monkeypatch.setenv(OIDC_CLIENT_SECRET_ENV, OIDC_CLIENT_SECRET)
        monkeypatch.setenv(SLACK_BOT_TOKEN_ENV, SLACK_BOT_TOKEN)
        monkeypatch.setenv(SLACK_APP_TOKEN_ENV, SLACK_APP_TOKEN)


def cloud_config_text(*, slack: bool = True, extra: str = "") -> str:
    """A cloud deployment (contract 2, 5): its public address, one org admin, and with `slack`
    one Slack workspace (`[im.slack].team_id`, the default workspace before ticket 161's
    `[im.slack.workspaces.*]`), which turns on Sign in with Slack."""
    lines = [
        "[server]",
        'mode = "cloud"',
        f'public_url = "{PUBLIC_URL}"',
        f'org_admins = ["{platform_user(ROOT)}"]',
        "",
    ]
    if slack:
        lines += [
            "[im.slack]",
            f'team_id = "{TEAM}"',
            f'bot_token_env = "{SLACK_BOT_TOKEN_ENV}"',
            f'app_token_env = "{SLACK_APP_TOKEN_ENV}"',
            f'allowed_users = ["{ALICE}", "{BOB}", "{EVE}", "{ADMIN}", "{ROOT}"]',
            "",
        ]
    return "\n".join(lines) + extra


# The session cookie (contract 5).
COOKIE = "data_agent_session"
LOGIN = "/auth/slack/login"
CALLBACK = "/auth/slack/callback"
TOKEN_LOGIN = "/auth/token"
ME = "/api/me"
REDIRECTS = (302, 303, 307)


def https_client(app: Any) -> Any:
    """A TestClient on `https://testserver`: httpx sends a `Secure` cookie back only there."""
    from fastapi.testclient import TestClient

    return TestClient(app, base_url="https://testserver")


def start_login(client: Any) -> dict[str, list[str]]:
    """`GET /auth/slack/login`: a redirect to Slack's authorize endpoint; its query."""
    response = client.get(LOGIN, follow_redirects=False)
    assert response.status_code in REDIRECTS, f"{LOGIN}: {response.status_code} {response.text}"
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        "https://slack.com/openid/connect/authorize"
    ), f"{LOGIN} sends the browser to {response.headers['location']}"
    return parse_qs(location.query)


def sign_in(client: Any, slack: FakeSlack, person: str, team: str = TEAM) -> Any:
    """Sign in with Slack through the app's own routes: the login redirect, then the callback
    with its `state` and a code the stand-in provider completes as `person` of `team`. Returns the
    callback's response."""
    query = start_login(client)
    state = key_of(query, "state")[0]
    code = slack.will_sign_in(person, team)
    return client.get(CALLBACK, params={"code": code, "state": state}, follow_redirects=False)


def signed_in(client: Any, slack: FakeSlack, person: str, team: str = TEAM) -> None:
    response = sign_in(client, slack, person, team)
    assert response.status_code in (200, *REDIRECTS), f"sign-in: {response.status_code}"
    assert client.cookies.get(COOKIE), f"no {COOKIE} cookie after signing in"


def set_cookies(response: Any) -> list[str]:
    return list(response.headers.get_list("set-cookie"))


def cookie_attributes(header: str) -> tuple[str, str, dict[str, str]]:
    """`name=value; Attr; Attr=v` as (name, value, attributes by lower-case name)."""
    first, *rest = [part.strip() for part in header.split(";")]
    name, _, value = first.partition("=")
    attributes: dict[str, str] = {}
    for part in rest:
        key, _, attr_value = part.partition("=")
        attributes[key.strip().lower()] = attr_value.strip()
    return name, value, attributes


def me(client: Any, **kwargs: Any) -> dict[str, Any]:
    response = client.get(ME, **kwargs)
    assert response.status_code == 200, f"{ME}: {response.status_code} {response.text}"
    body = response.json()
    assert isinstance(body, dict), body
    return body


# --- Runs in a runs directory ------------------------------------------------------------------

T0 = datetime(2026, 11, 24, 9, 0, 0, tzinfo=UTC)


def at(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def finished_run(runs_dir: Path, run_id: str, task: str = "How many orders?") -> str:
    """A run that has ended: its directory, record and an event file ending in `result` (so an
    event stream of it closes)."""
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    events = [
        {"type": "version", "ts": at(0), "config": "full", "model": "deepseek-flash"},
        {"type": "task", "ts": at(0), "task": task, "task_type": "query"},
        {"type": "answer", "ts": at(5), "text": "12"},
        {
            "type": "result",
            "ts": at(6),
            "subtype": "success",
            "is_error": False,
            "num_turns": 2,
            "terminal_reason": "completed",
            "api_error_status": None,
            "api_error": None,
            "cost_usd": 0.01,
            "errors": [],
            "session_id": "sess-1",
            "ended_ts": at(6),
        },
    ]
    path = run_dir / f"{run_id}.jsonl"
    path.write_text("".join(json.dumps({"seq": i, **e}) + "\n" for i, e in enumerate(events)))
    record = {
        "id": run_id,
        "task": task,
        "task_type": "query",
        "config": "full",
        "status": "succeeded",
        "started_at": at(0),
        "duration_ms": 6000,
        "cost_usd": 0.01,
    }
    (run_dir / "web-run.json").write_text(json.dumps(record))
    if not path.is_file():
        raise RuntimeError(f"could not write {path}")
    return run_id


# --- IM (im_support.py, extended) ----------------------------------------------------------------

MAX_CHARS = 3000
EDIT_INTERVAL = 1.5


class PrivateRefused(Exception):
    """What the fake platform raises when it cannot write to a user privately (a Telegram user
    who never wrote to the bot)."""


@dataclass
class Shown:
    id: str
    chat_id: str
    thread_id: str | None
    text: str
    buttons: tuple[Any, ...]
    # Set for a message only one user sees (contract 7: `Platform.send_private`).
    private_to: str | None = None


class FakePlatform:
    """im_support.py's platform in memory, plus `send_private` (contract 7): a message only
    `user_id` sees, recorded with `private_to`. `unreachable` users make it raise
    `PrivateRefused`."""

    def __init__(self, name: str = "slack", *, edit: bool = True) -> None:
        from data_agent.im.platform import Capabilities, Limits

        self.name = name
        self.capabilities = Capabilities(edit=edit, buttons=True, typing=False)
        self.limits = Limits(max_chars=MAX_CHARS, edit_interval=EDIT_INTERVAL)
        self.shown: list[Shown] = []
        self.unreachable: set[str] = set()
        self.on_message: Any = None
        self.on_press: Any = None

    async def start(self, on_message: Any, on_press: Any) -> None:
        self.on_message = on_message
        self.on_press = on_press

    async def stop(self) -> None:
        self.on_message = None
        self.on_press = None

    async def send(self, chat_id: str, thread_id: str | None, message: Any) -> str:
        shown = Shown(f"m{len(self.shown) + 1}", chat_id, thread_id, message.text, message.buttons)
        self.shown.append(shown)
        return shown.id

    async def send_private(
        self, chat_id: str, thread_id: str | None, user_id: str, message: Any
    ) -> str:
        if user_id in self.unreachable:
            raise PrivateRefused(f"{user_id} never wrote to the bot")
        shown = Shown(
            f"p{len(self.shown) + 1}", chat_id, thread_id, message.text, message.buttons, user_id
        )
        self.shown.append(shown)
        return shown.id

    async def edit(self, chat_id: str, message_id: str, message: Any) -> None:
        shown = next((s for s in self.shown if s.id == message_id), None)
        if shown is None or shown.chat_id != chat_id:
            raise RuntimeError(f"edit of a message the bot never sent: {chat_id} {message_id}")
        shown.text = message.text
        shown.buttons = message.buttons

    async def typing(self, chat_id: str, thread_id: str | None) -> None:
        pass

    async def deliver(self, message: Any) -> None:
        if self.on_message is None:
            raise RuntimeError("the adapter has not started the platform")
        await self.on_message(message)

    def public(self, chat_id: str) -> list[Shown]:
        return [s for s in self.shown if s.chat_id == chat_id and s.private_to is None]

    def private(self, person: str) -> list[Shown]:
        return [s for s in self.shown if s.private_to == person]

    def texts(self) -> list[str]:
        return [s.text for s in self.shown]


def incoming(
    text: str,
    *,
    person: str = ALICE,
    chat: str = SALES,
    thread: str | None = "T1",
    direct: bool = False,
    mentioned: bool = True,
    platform: str = "slack",
    workspace_id: str | None = TEAM,
) -> Any:
    """An `Incoming` with its workspace id (contract 7: `Incoming.workspace_id`), asserted to be a
    field first."""
    from data_agent.im.platform import Incoming

    kwargs: dict[str, Any] = {}
    if workspace_id is not None:
        has_field(Incoming, "workspace_id")
        kwargs["workspace_id"] = workspace_id
    return Incoming(
        platform=platform,
        chat_id=chat,
        thread_id=thread,
        user_id=person,
        direct=direct,
        mentioned=mentioned,
        text=text,
        **kwargs,
    )


@dataclass(frozen=True)
class Sent:
    conversation: str
    text: str
    extra: dict[str, Any]


class FakeConversations:
    """im_support.py's `Conversations` without a launcher, its `send` taking whatever keyword
    arguments a later adapter passes (recorded in `Sent.extra`)."""

    def __init__(self, runs: Any) -> None:
        self.runs = runs
        self.created: list[str] = []
        self.sent: list[Sent] = []
        self.turns: dict[str, list[str]] = {}

    def create(self) -> str:
        conversation = f"20261124T080000Z-conv{len(self.created) + 1:02d}"
        self.created.append(conversation)
        self.turns[conversation] = []
        return conversation

    def send(self, conversation: str, text: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        from data_agent.web.launcher import Refused

        self.sent.append(Sent(conversation, text, dict(kwargs)))
        if conversation not in self.turns:
            raise Refused(404, f"no conversation {conversation}")
        number = sum(len(turns) for turns in self.turns.values()) + 1
        run_id = f"20261124T0900{number:02d}Z-imrun{number:02d}"
        (self.runs.path / run_id).mkdir()
        self.runs.write_record(
            run_id,
            {
                "id": run_id,
                "task": text,
                "task_type": "query",
                "config": "full",
                "status": "succeeded",
                "started_at": at(0),
                "duration_ms": None,
                "cost_usd": None,
            },
        )
        self.turns[conversation].append(run_id)
        return {"kind": "turn", "turn": len(self.turns[conversation]), "run_id": run_id}

    def cancel(self, conversation: str) -> dict[str, Any]:
        return {"id": conversation, "single_run": False, "running": False, "turns": []}


@dataclass
class Bot:
    adapter: Any
    platform: FakePlatform
    conversations: Any
    runs: Any


async def start_bot(
    runs_path: Path,
    platform: FakePlatform,
    conversations: Any = None,
    allowed: Iterable[str] = (ALICE, BOB, EVE, ADMIN, ROOT),
    **settings: Any,
) -> Bot:
    """The IM adapter on the fake platform, started. `settings` are the keyword arguments
    contract 7 adds (`store`, `config`), each asserted to be a parameter first."""
    from data_agent.im.adapter import Adapter
    from data_agent.web.runs import RunsDir

    for name in settings:
        has_parameter(Adapter.__init__, name)
    runs_path.mkdir(parents=True, exist_ok=True)
    runs = RunsDir(runs_path)
    conversations = conversations if conversations is not None else FakeConversations(runs)
    adapter = Adapter(platform, conversations, runs, allowed, FakeClock(), **settings)
    await adapter.start()
    return Bot(adapter, platform, conversations, runs)


def finish_im_run(runs: Any, run_id: str, answer_text: str = "There are 12 orders.") -> None:
    """Writes a whole successful turn into a run the fake conversations started."""
    path = runs.path / run_id / f"{run_id}.jsonl"
    events = [
        {"type": "version", "ts": at(0), "config": "full", "model": "deepseek-flash"},
        {"type": "task", "ts": at(0), "task": "q", "task_type": "query"},
        {"type": "answer", "ts": at(5), "text": answer_text},
        {
            "type": "result",
            "ts": at(6),
            "subtype": "success",
            "is_error": False,
            "num_turns": 2,
            "terminal_reason": "completed",
            "api_error_status": None,
            "api_error": None,
            "cost_usd": 0.01,
            "errors": [],
            "session_id": "sess-im",
            "ended_ts": at(6),
        },
    ]
    path.write_text("".join(json.dumps({"seq": i, **e}) + "\n" for i, e in enumerate(events)))


# --- The evaluation entries (product_config_support.py, compare only) --------------------------

VERSION_FIELDS = ("skill_version", "skills", "config", "layers")
MKDTEMP = re.compile(r"((?:data-agent|da)-[a-z-]*-)[A-Za-z0-9_]{8}\b")
EVENT_FILE = re.compile(r"run-\d{8}T\d{6}Z-[0-9a-f]{8}")
SNAPSHOT_CASES = (
    ("data-eng-bench", "full"),
    ("data-eng-bench", "baseline"),
    ("data-eng-bench", "full-old-skill"),
    ("livesqlbench", "full"),
    ("dab", "no-context"),
    ("jaffle", "full"),
)
DBT_TASK = "Add a dbt model named customer_ltv with one row per customer."
LSB_TASK = "List the three customers with the most orders."
LSB_URL_ENV = "LSB_DATABASE_URL"
JAFFLE_CASE = "b1_monthly_revenue"
PG_URL_ENV = "DA_TEST_JAFFLE_URL"
DATA_SERVER = "data"
PATH_BEFORE = r"(?<![\w.-])"
PATH_AFTER = r"(?![\w-]|\.[\w-])"
CONTENT_IGNORES = (
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    "*.py[cod]",
    ".DS_Store",
    "*.swp",
    "*.swo",
    "*~",
)
PLUGIN_MANIFEST_DIR = ".claude-plugin"


def snapshot(session: Session, version: Mapping[str, Any]) -> dict[str, Any]:
    options = session.options
    servers = options.mcp_servers if isinstance(options.mcp_servers, dict) else {}
    return {
        "system_prompt": options.system_prompt,
        "prompt": session.prompt,
        "tools": options.tools,
        "allowed_tools": options.allowed_tools,
        "disallowed_tools": options.disallowed_tools,
        "permission_mode": options.permission_mode,
        "mcp_servers": {
            name: {key: server.get(key) for key in ("type", "command", "args", "env", "url")}
            for name, server in servers.items()
        },
        "skills": options.skills,
        "plugins": options.plugins,
        "setting_sources": options.setting_sources,
        "strict_mcp_config": options.strict_mcp_config,
        "max_turns": options.max_turns,
        "max_budget_usd": options.max_budget_usd,
        "env": dict(sorted(options.env.items())),
        "version": {name: version[name] for name in VERSION_FIELDS if name in version},
    }


def assert_server_configs(
    values: Sequence[str], version_config: Any, kept_out: Sequence[Path]
) -> None:
    profiles = (REPO_ROOT / "evals" / "profiles").resolve()
    temp = Path(tempfile.gettempdir()).resolve()
    assert values, "the data server is given no --config"
    for value in values:
        if value == version_config and "/" not in value:
            continue
        path = Path(value)
        assert path.is_absolute(), (
            f"the data server's --config {value!r} is neither the version's config nor a path"
        )
        path = path.resolve()
        for directory in kept_out:
            assert not path.is_relative_to(directory.resolve()), (
                f"the data server's --config {value} is under {directory}"
            )
        assert path.is_relative_to(profiles) or path.is_relative_to(temp), (
            f"the data server's --config {value} is under neither evals/profiles/ nor the temp dir"
        )


def config_arg_rule(args: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--config" and i + 1 < len(args):
            if out[-2:] != ["--config", "<config>"]:
                out += ["--config", "<config>"]
            i += 2
            continue
        out.append(args[i])
        i += 1
    return out


def text_rules(roots: Sequence[Path]) -> list[tuple[str, str]]:
    pairs: dict[str, str] = {
        PG_URL: "<pg-url>",
        PG_PASSWORD: "<pg-password>",
        API_KEY: "<api-key>",
        sys.executable: "<python>",
    }
    for root in roots:
        for form in {str(root), str(root.resolve())}:
            pairs[form] = "<tmp>"
    temp = Path(tempfile.gettempdir())
    for form in {str(temp), str(temp.resolve())}:
        pairs.setdefault(form, "<tempdir>")
    for form in {str(REPO_ROOT), str(REPO_ROOT.resolve())}:
        pairs.setdefault(form, "<repo>")
    return sorted(pairs.items(), key=lambda pair: len(pair[0]), reverse=True)


def normalize_text(text: str, rules: Sequence[tuple[str, str]]) -> str:
    placeholder_of: dict[str, str] = {}
    for old, new in rules:
        placeholder_of.setdefault(old, new)
    if placeholder_of:
        pattern = "|".join(
            PATH_BEFORE + re.escape(old) + PATH_AFTER if old.startswith("/") else re.escape(old)
            for old in sorted(placeholder_of, key=len, reverse=True)
        )
        text = re.sub(pattern, lambda match: placeholder_of[match.group(0)], text)
    text = MKDTEMP.sub(r"\1<random>", text)
    return EVENT_FILE.sub("run-<stamp>", text)


def normalize(value: Any, rules: Sequence[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        return normalize_text(value, rules)
    if isinstance(value, Mapping):
        return {normalize_text(str(k), rules): normalize(v, rules) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [normalize(item, rules) for item in value]
    return value


def content_files(directory: Path) -> list[Path]:
    files = [
        path
        for path in directory.rglob("*")
        if path.is_file()
        and not any(
            fnmatch.fnmatch(part, pattern)
            for part in path.relative_to(directory).parts
            for pattern in CONTENT_IGNORES
        )
    ]
    return sorted(files, key=lambda path: path.relative_to(directory).as_posix())


def content_sha256(directory: Path) -> str:
    outer = hashlib.sha256()
    for path in content_files(directory):
        if PLUGIN_MANIFEST_DIR in path.relative_to(directory).parts:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        outer.update(f"{path.relative_to(directory).as_posix()}\0{digest}\n".encode())
    return outer.hexdigest()


def skill_fingerprint(value: str) -> Any:
    from data_agent.agent import skill_version

    if value == "none":
        return value
    frozen = sorted(
        p for p in (REPO_ROOT / "skills-frozen").iterdir() if not p.name.startswith(".")
    )
    for directory in [REPO_ROOT / "skills", *frozen]:
        if directory.is_dir() and skill_version(directory) == value:
            return {
                "directory": f"{directory.relative_to(REPO_ROOT).as_posix()}/",
                "content_sha256": content_sha256(directory),
            }
    return value


def normalized_snapshot(
    session: Session, version: Mapping[str, Any], roots: Sequence[Path]
) -> dict[str, Any]:
    raw = snapshot(session, version)
    rules = text_rules(roots)
    for name, server in raw["mcp_servers"].items():
        if isinstance(server.get("args"), list):
            args = [str(arg) for arg in server["args"]]
            rules += [(run_id, "<run-id>") for run_id in flag_values(args, "--run-id")]
            if name == DATA_SERVER and "mcp-server" in args:
                cwd = session.options.cwd
                kept_out = [Path(os.environ["HOME"]), *roots]
                if cwd is not None:
                    kept_out.append(Path(str(cwd)))
                assert_server_configs(
                    flag_values(args, "--config"), version.get("config"), kept_out
                )
            server["args"] = config_arg_rule(args)
    if "skill_version" in raw["version"]:
        raw["version"]["skill_version"] = skill_fingerprint(raw["version"]["skill_version"])
    return normalize(raw, rules)


def compare_snapshot(name: str, got: dict[str, Any]) -> None:
    """Equal to the snapshot ticket 141 recorded, key by key. Never records."""
    path = SNAPSHOTS / f"{name}.json"
    if not path.is_file():
        raise RuntimeError(f"no recorded snapshot {path}")
    recorded = json.loads(path.read_text())
    differing = sorted(key for key in {*recorded, *got} if recorded.get(key) != got.get(key))
    assert not differing, f"{name}: differs from the recorded snapshot in {differing}"
    assert got == recorded


@dataclass
class EntryRun:
    session: Session
    version: dict[str, Any]
    roots: list[Path]


def read_events(events_dir: Path) -> list[dict[str, Any]]:
    files = sorted(events_dir.glob("*.jsonl"))
    if len(files) != 1:
        raise RuntimeError(f"expected one event file in {events_dir}, found {files}")
    return [json.loads(line) for line in files[0].read_text().splitlines() if line.strip()]


def version_of(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not events or events[0].get("type") != "version":
        raise RuntimeError(f"the first event is no version event: {events[:1]}")
    return dict(events[0])


def harbor_command_args(command: str) -> list[str]:
    words = shlex.split(command)
    starts = [i for i, word in enumerate(words) if word.endswith("/bin/data-agent")]
    if not starts:
        raise RuntimeError(f"no data-agent command in {command!r}")
    start = starts[0] + 1
    end = words.index("2>&1", start)
    return words[start + 1 : end]


def profile_path(value: str) -> str:
    if Path(value).exists() or "/" not in value:
        return value
    parts = PurePosixPath(value).parts
    if "profiles" not in parts:
        raise RuntimeError(f"a --config path outside profiles/: {value}")
    at_index = len(parts) - 1 - parts[::-1].index("profiles")
    return str(REPO_ROOT / "evals" / Path(*parts[at_index:]))


def substitute(args: list[str], values: Mapping[str, str]) -> list[str]:
    out = list(args)
    for i, arg in enumerate(out[:-1]):
        if arg in values:
            out[i + 1] = values[arg]
        elif arg == "--config":
            out[i + 1] = profile_path(out[i + 1])
    return out


def harbor_agent(root: Path, layer: str, benchmark: str) -> Any:
    from evals.harbor_agent import DataAgent

    logs = root / "logs" / "agent"
    logs.mkdir(parents=True)
    kwargs: dict[str, Any] = {"run_config": layer}
    if benchmark == "livesqlbench":
        kwargs["benchmark"] = "livesqlbench"
    return DataAgent(
        logs_dir=logs,
        model_name="deepseek/deepseek-flash",
        environment_logs_dir=PurePosixPath(logs),
        extra_env={"DATA_AGENT_API_KEY": API_KEY},
        **kwargs,
    )


def run_entry_cli(capsys: pytest.CaptureFixture[str], args: list[str], capture: Capture) -> None:
    run = run_cli(capsys, ["run", *args])
    if not capture.sessions:
        raise RuntimeError(f"no session (exit {run.code!r}): {run.stderr[-3000:]}")


def dictionary_from_duckdb(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    from data_agent import agent, context_tools
    from data_agent.gateway import DuckDBGateway

    shop = build_shop_duckdb(root / "dictionary-shop.duckdb")
    real = context_tools.build_interruptible

    def build(_: Any) -> Any:
        return real(context_tools.DictionaryBuild(DuckDBGateway(shop)))

    monkeypatch.setattr(context_tools, "build_interruptible", build)
    monkeypatch.setattr(agent, "build_interruptible", build, raising=False)


def harbor_entry(
    root: Path, layer: str, capsys: pytest.CaptureFixture[str], capture: Capture
) -> EntryRun:
    agent = harbor_agent(root, layer, "data-eng-bench")
    args = harbor_command_args(agent._run_command(DBT_TASK))  # pyright: ignore[reportPrivateUsage]
    project, db = make_dbt_workspace(root / "trial")
    events_dir = root / "events"
    args = substitute(
        args, {"--workspace": str(project), "--duckdb": str(db), "--events-dir": str(events_dir)}
    )
    run_entry_cli(capsys, args, capture)
    return EntryRun(capture.only(), version_of(read_events(events_dir)), [root])


def livesqlbench_entry(
    root: Path,
    layer: str,
    capsys: pytest.CaptureFixture[str],
    capture: Capture,
    monkeypatch: pytest.MonkeyPatch,
) -> EntryRun:
    dictionary_from_duckdb(monkeypatch, root)
    monkeypatch.setenv(LSB_URL_ENV, PG_URL)
    agent = harbor_agent(root, layer, "livesqlbench")
    args = harbor_command_args(agent._run_command(LSB_TASK))  # pyright: ignore[reportPrivateUsage]
    workspace_dir = root / "app"
    workspace_dir.mkdir()
    events_dir = root / "events"
    args = substitute(args, {"--workspace": str(workspace_dir), "--events-dir": str(events_dir)})
    run_entry_cli(capsys, args, capture)
    return EntryRun(capture.only(), version_of(read_events(events_dir)), [root])


def make_dab(root: Path) -> Path:
    shutil.copytree(FAKE_DAB, root)
    dataset = root / "query_toyset"
    (dataset / "query_dataset").mkdir(parents=True)
    with sqlite3.connect(dataset / "query_dataset" / "prices.db") as conn:
        conn.execute("CREATE TABLE prices (ticker TEXT, close REAL)")
        conn.executemany("INSERT INTO prices VALUES (?, ?)", [("T001", 1.0), ("T002", 2.0)])
    (dataset / "dump").mkdir()
    (dataset / "dump" / "articles.bson").write_bytes(b"x" * 10)
    clients = {
        "prices_db": {"db_type": "sqlite", "db_path": "query_dataset/prices.db"},
        "articles_db": {"db_type": "mongo", "db_name": "articles", "dump_folder": "dump"},
    }
    (dataset / "db_config.yaml").write_text(json.dumps({"db_clients": clients}))
    (dataset / "db_description.txt").write_text("prices_db holds daily closes.\n")
    query = dataset / "query1"
    query.mkdir()
    (query / "query.json").write_text(json.dumps("Which ticker closed highest?"))
    (query / "ground_truth.csv").write_text("T002")
    (query / "validate.py").write_text("GT = 'T002'\n")
    return root


def dab_entry(
    root: Path,
    layer: str,
    capsys: pytest.CaptureFixture[str],
    capture: Capture,
    monkeypatch: pytest.MonkeyPatch,
) -> EntryRun:
    from evals import __main__ as evals_main

    no_docker(monkeypatch)
    dab_root = make_dab(root / "dab")
    out = root / "out"
    argv = ["dab", "--dab-root", str(dab_root), "--dataset", "toyset", "--query", "1"]
    argv += ["--runs", "1", "--config", layer, "--out", str(out), "--no-hints"]
    capsys.readouterr()
    code = evals_main.main(argv)
    if not capture.sessions:
        raise RuntimeError(f"no session (exit {code!r}): {capsys.readouterr().err[-3000:]}")
    [events_file] = sorted(out.rglob("events.jsonl"))
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
    return EntryRun(capture.only(), version_of(events), [root])


class JaffleStopped(Exception):
    """Raised in place of the jaffle case's `data-agent run` process, once its command is read."""


@contextmanager
def environment(monkeypatch: pytest.MonkeyPatch, env: Mapping[str, str]) -> Iterator[None]:
    with monkeypatch.context() as scoped:
        for name in list(os.environ):
            if name not in env:
                scoped.delenv(name)
        for name, value in env.items():
            scoped.setenv(name, value)
        yield


def jaffle_entry(
    root: Path,
    layer: str,
    capsys: pytest.CaptureFixture[str],
    capture: Capture,
    monkeypatch: pytest.MonkeyPatch,
) -> EntryRun:
    from evals import __main__ as evals_main
    from evals import jaffle

    no_docker(monkeypatch)
    dictionary_from_duckdb(monkeypatch, root)
    monkeypatch.setenv(PG_URL_ENV, PG_URL)
    project = root / "jaffle_project"
    shutil.copytree(DBT_SHOP, project)
    monkeypatch.setattr(jaffle, "clone_project", lambda cases, dest: project)
    monkeypatch.setattr(jaffle, "reset_schemas", lambda url: None)
    monkeypatch.setattr(jaffle, "dbt", lambda workspace, env, *args: None)
    started: list[tuple[list[str], dict[str, str]]] = []

    def run(command: Sequence[str], env: Mapping[str, str] | None = None, **_: Any) -> Any:
        started.append(([str(word) for word in command], dict(env or os.environ)))
        raise JaffleStopped

    monkeypatch.setattr(
        jaffle,
        "subprocess",
        SimpleNamespace(
            run=run,
            TimeoutExpired=subprocess.TimeoutExpired,
            CalledProcessError=subprocess.CalledProcessError,
        ),
    )
    out = root / "out"
    argv = ["jaffle", "--postgres-env", PG_URL_ENV, "--out", str(out), "--config", layer]
    argv += ["--case", JAFFLE_CASE]
    with suppress(JaffleStopped):
        evals_main.main(argv)
    if len(started) != 1:
        raise RuntimeError(f"expected one data-agent process, got {len(started)}")
    command, env = started[0]
    args = substitute(command[2:], {})
    with environment(monkeypatch, env):
        run_entry_cli(capsys, args, capture)
    events_dir = Path(flag_value(args, "--events-dir"))
    return EntryRun(capture.only(), version_of(read_events(events_dir)), [root])


def drive_entry(
    entry: str,
    layer: str,
    root: Path,
    capsys: pytest.CaptureFixture[str],
    capture: Capture,
    monkeypatch: pytest.MonkeyPatch,
) -> EntryRun:
    """Runs one evaluation entry (`SNAPSHOT_CASES`) to its stopped session, from `root/cwd`."""
    cwd = root / "cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(cwd)
    if entry == "data-eng-bench":
        return harbor_entry(root, layer, capsys, capture)
    if entry == "livesqlbench":
        return livesqlbench_entry(root, layer, capsys, capture, monkeypatch)
    if entry == "dab":
        return dab_entry(root, layer, capsys, capture, monkeypatch)
    if entry != "jaffle":
        raise RuntimeError(f"no entry {entry}")
    return jaffle_entry(root, layer, capsys, capture, monkeypatch)


def new_id() -> str:
    return uuid.uuid4().hex[:8]
