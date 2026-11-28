"""Helpers shared by the cloud-deploy acceptance tests (.scratch/cloud-deploy/spec.md "Testing
Decisions" 验收测试接缝, ticket 151): test_cloud_startup.py, test_cloud_web_token.py,
test_cloud_locks.py and test_cloud_git_sources.py.

Helpers of the earlier acceptance files are copied here, not imported (ticket 151 Authority), so
a later change to those files cannot move these tests.

The seams (ticket 151 Context "接缝"; no stand-in in `src/`):

- `data-agent web --config F` runs in-process with `uvicorn.run` stood in for: it records the app
  and the host it is given and serves nothing. `create_app` runs as it is; a spy only keeps what
  it returned, for the IM turn (`app.state.conversations`). The HTTP tests use the app
  `uvicorn.run` was given, so how the token reaches the app is ticket 153's choice.
- The runs the server starts: `subprocess.Popen` is swapped, while the server is served, for a
  subclass that turns a `<python> -m data_agent run <args>` command into
  tests/fixtures/chat/fake_chat_run.py (reused read-only) with the same arguments, the same
  working directory and the same `env=`, and records the arguments and the environment the run
  would get (`env=`, or the server's whole environment when none is given). Every other command
  runs as given. The fake run waits on a gate file, so its arguments are read while it goes; the
  test ends it.
- `data-agent run --config F` runs in-process, its session stopped at the SDK transport's
  `connect` (as tests/acceptance/test_chat_eval_unchanged.py does); no model is called and no
  Claude Code CLI starts. Docker calls succeed with no output (`sandbox.run_docker`, which its
  docstring says tests replace), so a run on the docker backend reaches its session without a
  daemon.

Taking a value out of something today's code does not produce goes through helpers that `assert`
it is there first: a test marked `xfail(strict=True, raises=AssertionError)` then fails on the
assertion (the expected failure), never on a `KeyError` or an `ImportError`. A fixture of the
tests' own (a fake run, a git repository) that does not come up raises `RuntimeError` instead, so
it never hides as an expected failure.
"""

import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, CLIConnectionError
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from data_agent import cli

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SHOP_SQL = FIXTURES / "shop.sql"
DBT_SHOP = FIXTURES / "dbt_shop"
FAKE_CHAT_RUN = FIXTURES / "chat" / "fake_chat_run.py"

# The deployment's variables (decisions.md Q8; "建 settings 工单时主会话定的细节" 1 and 7).
ADMIN_TOKEN_ENV = "DATA_AGENT_ADMIN_TOKEN"
MODE_ENV = "DATA_AGENT_MODE"
SESSION_KEY_ENV = "DATA_AGENT_SESSION_KEY"
# Made up here, and in no real token's shape (the secret scan of pre-commit stops those).
ADMIN_TOKEN = "adm-" + "Vq8n" + "-made-up-token"
SESSION_KEY = "ses-" + "Jr2w" + "-made-up-key"
API_KEY = "unused-test-key-" + "Hq3v"
# Cleared for every test: they would change what a run or the server is given.
AMBIENT_ENV = (
    ADMIN_TOKEN_ENV,
    MODE_ENV,
    SESSION_KEY_ENV,
    "DATA_AGENT_CONFIG",
    "DATA_AGENT_BASE_URL",
    "DATA_AGENT_MODEL",
    "CLAUDE_CODE_EFFORT_LEVEL",
    "DATA_AGENT_LANGFUSE_PUBLIC_KEY",
    "DATA_AGENT_LANGFUSE_SECRET_KEY",
    "DATA_AGENT_LANGFUSE_BASE_URL",
)
# Startup refusals and config errors exit 2 (ticket 151 contract item 1).
EXIT_REFUSED = 2
LOOPBACK = "127.0.0.1"
ANY_ADDRESS = "0.0.0.0"
TIMEOUT = 60
# The run form without a `config` field: the form picks no ablation (product-config ticket 141
# contract item 5, ticket 144). `network: false` unless a test asks for it.
FORM = {"task": "Count orders", "task_type": "query", "data_source": "shop", "network": False}


# --- Isolation and files --------------------------------------------------------------------


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Used by every test (`pytestmark`): an empty fake HOME (no `~/.data-agent/`), the working
    directory in `tmp_path` (`data-agent web` looks for a project-level config there), none of
    AMBIENT_ENV, and the model key a run needs before its session."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    for name in AMBIENT_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATA_AGENT_API_KEY", API_KEY)


def write_file(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def toml_path(path: Path) -> str:
    """A path as a TOML basic string."""
    return json.dumps(str(path))


def build_shop_duckdb(path: Path) -> Path:
    """A fresh DuckDB file built from tests/fixtures/shop.sql (copied from seam_support.py)."""
    import duckdb  # pyright: ignore[reportMissingImports]

    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute(SHOP_SQL.read_text())
    finally:
        con.close()
    return path


def make_dbt_workspace(root: Path) -> tuple[Path, Path]:
    """A copy of the mini dbt project plus its private DuckDB file under root (copied from
    seam_support.py)."""
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


def shop_source(root: Path) -> str:
    """A config's `[sources.shop]`, a fresh shop DuckDB under `root`: the run form's source."""
    db = build_shop_duckdb(root / "sources" / "shop.duckdb")
    return f'[sources.shop]\nkind = "duckdb"\npath = {toml_path(db)}\n'


def server_table(mode: str | None = None, host: str | None = None) -> str:
    """A config's `[server]` with the keys given; empty when none is."""
    lines = [f'{key} = "{value}"' for key, value in (("mode", mode), ("host", host)) if value]
    return "[server]\n" + "\n".join(lines) + "\n" if lines else ""


def set_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deployment's token, and the session key decisions detail 7 makes cloud ask for later
    (so a cloud server is refused for nothing but what a test asks about)."""
    monkeypatch.setenv(ADMIN_TOKEN_ENV, ADMIN_TOKEN)
    monkeypatch.setenv(SESSION_KEY_ENV, SESSION_KEY)


def bearer(token: str = ADMIN_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- The CLI's output -----------------------------------------------------------------------


@dataclass
class CliRun:
    code: int | str | None
    stdout: str
    stderr: str


def reason(stderr: str) -> str:
    """stderr without argparse's usage block, whose option list names `--network`, `--sandbox
    {docker,local}` and the like whatever the error is."""
    kept: list[str] = []
    in_usage = False
    for line in stderr.splitlines():
        if line.startswith("usage:"):
            in_usage = True
            continue
        if in_usage and line.startswith(" "):
            continue
        in_usage = False
        kept.append(line)
    return "\n".join(kept)


def assert_names(stderr: str, *words: str) -> None:
    """stderr gives the reason: it names one of `words` outside the usage block."""
    text = reason(stderr)
    assert any(word in text for word in words), f"stderr names none of {words}: {text!r}"


# --- `data-agent web` -----------------------------------------------------------------------


@dataclass
class Started:
    """A run the server started: its `data-agent run` arguments, the environment it would get,
    and the fake run's process."""

    args: list[str]
    env: dict[str, str]
    process: "subprocess.Popen[bytes]"


@dataclass
class Served:
    """`data-agent web <argv>` with `uvicorn.run` stood in for."""

    code: int | str | None
    stderr: str
    # Every `uvicorn.run` call: its positional and keyword arguments.
    uvicorn: list[tuple[tuple[Any, ...], dict[str, Any]]]
    # What `create_app` returned (the app's state), None when it was not called.
    created: Any
    started: list[Started] = field(default_factory=list[Started])

    @property
    def ran(self) -> bool:
        return bool(self.uvicorn)

    @property
    def app(self) -> Any:
        """The app `uvicorn.run` was given: the server started."""
        assert len(self.uvicorn) == 1, (
            f"data-agent web did not start (exit {self.code!r}): {reason(self.stderr)!r}"
        )
        args, kwargs = self.uvicorn[0]
        return args[0] if args else kwargs["app"]

    @property
    def host(self) -> Any:
        """The address `uvicorn.run` was told to listen on."""
        assert len(self.uvicorn) == 1, (
            f"data-agent web did not start (exit {self.code!r}): {reason(self.stderr)!r}"
        )
        args, kwargs = self.uvicorn[0]
        return kwargs["host"] if "host" in kwargs else args[1] if len(args) > 1 else None

    def only(self) -> Started:
        assert len(self.started) == 1, f"expected one run, got {len(self.started)}"
        return self.started[0]


def fake_run_popen(gates: Path, started: list[Started]) -> type:
    """A `subprocess.Popen` that starts the fake run in place of `-m data_agent run` (module
    docstring) and records it; any other command as given."""
    real: Any = subprocess.Popen

    class FakeRunPopen(real):  # pyright: ignore[reportUntypedBaseClass]
        def __init__(self, args: Any, *rest: Any, **kwargs: Any) -> None:
            argv = [str(arg) for arg in args] if isinstance(args, (list, tuple)) else None
            if argv is None or argv[1:4] != ["-m", "data_agent", "run"]:
                super().__init__(args, *rest, **kwargs)
                return
            env = kwargs.get("env")
            seen = dict(os.environ if env is None else env)
            command = [sys.executable, str(FAKE_CHAT_RUN), "--gate-dir", str(gates), *argv[4:]]
            super().__init__(command, *rest, **kwargs)
            started.append(Started(argv[4:], seen, self))

    return FakeRunPopen


@contextmanager
def served(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: Sequence[str],
) -> Iterator[Served]:
    """`data-agent web --runs-dir <root>/runs <argv>`, in-process (module docstring). The runs
    it starts are ended when the block ends; `subprocess.Popen` is swapped only inside it."""
    import uvicorn

    from data_agent.web import app as web_module

    runs_dir = root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    gates = root / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    created: dict[str, Any] = {}
    started: list[Started] = []
    real_create_app = web_module.create_app

    def create_app(*args: Any, **kwargs: Any) -> Any:
        created["app"] = real_create_app(*args, **kwargs)
        return created["app"]

    def run(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    with monkeypatch.context() as patch:
        patch.setattr(web_module, "create_app", create_app)
        patch.setattr(uvicorn, "run", run)
        patch.setattr(subprocess, "Popen", fake_run_popen(gates, started))
        capsys.readouterr()
        try:
            cli.main(["web", "--runs-dir", str(runs_dir), *argv])
        except SystemExit as exc:
            code = exc.code
        else:
            code = None
        stderr = capsys.readouterr().err
        try:
            yield Served(code, stderr, calls, created.get("app"), started)
        finally:
            for run_ in started:
                if run_.process.poll() is None:
                    run_.process.terminate()
                run_.process.wait(timeout=TIMEOUT)


def assert_refused(server: Served, *words: str) -> None:
    """Ticket 151 contract item 1: exit code 2, stderr naming the reason (one of `words`), and
    `uvicorn.run` never called. Exit code 2 alone proves nothing: a config key today's code does
    not know is refused with 2 too."""
    assert not server.ran, f"the server started (host {server.uvicorn[0]!r}) despite the problem"
    assert_names(server.stderr, *words)
    assert server.code == EXIT_REFUSED, f"exit code {server.code!r}, expected 2"


def write_run(runs_dir: Path, run_id: str) -> str:
    """A finished run in the runs directory (a `version` and a `result` event), so its event
    stream sends the backlog and closes."""
    now = datetime.now(UTC).isoformat()
    events = [
        {"seq": 0, "type": "version", "ts": now, "config": "default", "model": "fake-model"},
        {"seq": 1, "type": "result", "ts": now, "answer": "42", "terminal_reason": "completed"},
    ]
    write_file(
        runs_dir / run_id / "run-20261128T000000Z.jsonl",
        "".join(json.dumps(event) + "\n" for event in events),
    )
    return run_id


# --- `data-agent run` -----------------------------------------------------------------------


@dataclass
class Sessions:
    options: list[ClaudeAgentOptions] = field(default_factory=list[ClaudeAgentOptions])

    def only(self) -> ClaudeAgentOptions:
        assert len(self.options) == 1, f"expected one session, got {len(self.options)}"
        return self.options[0]


def stop_sessions(monkeypatch: pytest.MonkeyPatch) -> Sessions:
    """Every session the code under test starts, stopped at the transport's `connect` (as
    test_chat_eval_unchanged.py's `captured`); docker calls succeed with no output."""
    from data_agent import sandbox

    seen = Sessions()

    async def connect(self: SubprocessCLITransport) -> None:
        seen.options.append(self._options)  # pyright: ignore[reportPrivateUsage]
        raise CLIConnectionError("stopped by the cloud-deploy acceptance tests")

    def run_docker(args: Sequence[str], timeout: float = 0) -> sandbox.Execution:
        return sandbox.Execution(0, "", "", False)

    monkeypatch.setattr(SubprocessCLITransport, "connect", connect)
    monkeypatch.setattr(sandbox, "run_docker", run_docker)
    return seen


def run_cli(capsys: pytest.CaptureFixture[str], argv: Sequence[str]) -> CliRun:
    """`data-agent ...` in-process; argparse errors and the run's own exit both end in
    SystemExit."""
    capsys.readouterr()
    try:
        cli.main(list(argv))
    except SystemExit as exc:
        code = exc.code
    else:
        code = 0
    out = capsys.readouterr()
    return CliRun(code, out.out, out.err)


def query_args(root: Path, config: Path, *extra: str) -> list[str]:
    """A query run under `root` on the docker backend (`--sandbox` left to the config: the
    session stops before a container would start) with its own shop DuckDB."""
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return [
        "run",
        *("--task", "How many orders are there?", "--type", "query"),
        *("--workspace", str(workspace)),
        *("--duckdb", str(build_shop_duckdb(root / "shop.duckdb"))),
        *("--events-dir", str(root / "events"), "--config", str(config)),
        *extra,
    ]


def dbt_args(root: Path, config: Path, *extra: str) -> list[str]:
    """As `query_args`, a dbt run on a copy of the mini dbt project."""
    project, db = make_dbt_workspace(root / "trial")
    return [
        "run",
        *("--task", "Add a dbt model named customer_ltv with one row per customer.", "--type"),
        *("dbt", "--workspace", str(project), "--duckdb", str(db)),
        *("--events-dir", str(root / "events"), "--config", str(config)),
        *extra,
    ]


def assert_run_refused(run: CliRun, sessions: Sessions, *words: str) -> None:
    """A refused `data-agent run` (contract item 1's run side): no session started, stderr naming
    the reason, exit code 2."""
    assert sessions.options == [], "a session started despite the refusal"
    assert_names(run.stderr, *words)
    assert run.code == EXIT_REFUSED, f"exit code {run.code!r}, expected 2"


# --- Reading values -------------------------------------------------------------------------


def module_attr(module: str, name: str) -> Any:
    """`module.name`, asserted to exist first (ticket 151 contract item 5): a module that is not
    written yet fails as an assertion, not as an ImportError."""
    assert importlib.util.find_spec(module) is not None, f"no module {module}"
    loaded: ModuleType = importlib.import_module(module)
    assert hasattr(loaded, name), f"{module} has no {name!r}"
    return getattr(loaded, name)


def key_of(mapping: Any, key: str) -> Any:
    assert isinstance(mapping, Mapping), f"not a mapping: {mapping!r}"
    assert key in mapping, f"no {key!r} in {sorted(map(str, mapping))}"
    return mapping[key]
