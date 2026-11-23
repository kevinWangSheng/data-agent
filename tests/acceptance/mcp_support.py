"""Helpers shared by the external-MCP acceptance tests (.scratch/mcp/spec.md "Testing Decisions",
ticket 115): test_mcp_config.py, test_mcp_launch.py, test_mcp_web.py, test_mcp_eval_unchanged.py.

The seam is the whole `data-agent run`, stopped at the SDK's transport: `SubprocessCLITransport
.connect` is replaced, records the options it was given, and raises `CLIConnectionError`, as
tests/acceptance/test_chat_eval_unchanged.py does. No model is called and no Claude Code CLI is
started. The `version` event is written before `build_options` runs (`agent.run`), so the event
file is there when the transport is stopped.

The runs use `--sandbox local`. The transport is stopped before any sandbox could start, so the
backend does not matter; local is the one that needs no Docker, so the tests pass on a machine
whose Docker daemon is off.

Taking a value out of something today's code does not produce (`servers["mongo"]`,
`version["external_mcp_servers"]`, `args.index("--mcp-servers")`) goes through the helpers
below, which `assert` it is there first. A test marked `xfail(strict=True, raises=AssertionError)`
then fails on the assertion, which counts as the expected failure, and not on a `KeyError` or
`ValueError`, which would be reported as a failure.
"""

import json
import re
import shutil
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, CLIConnectionError
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from seam_support import FIXTURES, build_shop_duckdb

from data_agent import cli

MCP_FIXTURES = FIXTURES / "mcp"
PRINT_ENV = MCP_FIXTURES / "print_env.py"
SUPPORT_TICKETS = MCP_FIXTURES / "support_tickets.json"
FAKE_DAB = FIXTURES / "fake_dab"
# Our own server's name (`agent.MCP_SERVER`); a config may not use it.
DATA_SERVER = "data"
# A placeholder: the transport is stopped before anything is sent.
API_KEY = "unused-test-key-" + "q7Wm"
# Config errors exit 2 (seam A, tests/acceptance/test_seam_a_run_cli.py).
EXIT_CONFIG_ERROR = 2

Probe = Callable[[ClaudeAgentOptions], Any]


@dataclass
class Session:
    """A session the code under test started, stopped at the transport."""

    options: ClaudeAgentOptions
    # What the test's probe returned while the session's files still existed, or what it raised.
    probed: Any = None
    probe_error: BaseException | None = None

    def probe_result(self) -> Any:
        if self.probe_error is not None:
            raise self.probe_error
        return self.probed


@dataclass
class Capture:
    sessions: list[Session] = field(default_factory=list)
    # Run inside `connect`, while whatever the run made for its servers still exists.
    probe: Probe | None = None


def stop_at_transport(monkeypatch: pytest.MonkeyPatch) -> Capture:
    """Replaces the SDK transport's `connect` for the rest of the test; every session started is
    recorded in the returned `Capture`. The probe's exceptions are kept, not raised: raised in
    `connect`, the run would take them for an infrastructure error and hide them."""
    capture = Capture()

    async def connect(self: SubprocessCLITransport) -> None:
        options: ClaudeAgentOptions = self._options  # pyright: ignore[reportPrivateUsage]
        session = Session(options)
        if capture.probe is not None:
            try:
                session.probed = capture.probe(options)
            except BaseException as exc:
                session.probe_error = exc
        capture.sessions.append(session)
        raise CLIConnectionError("stopped by the external-MCP acceptance tests before the CLI")

    monkeypatch.setattr(SubprocessCLITransport, "connect", connect)
    monkeypatch.setenv("DATA_AGENT_API_KEY", API_KEY)
    for name in ("DATA_AGENT_BASE_URL", "DATA_AGENT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    return capture


# --- Running the CLI ----------------------------------------------------------------------


@dataclass
class CliRun:
    code: int | str | None
    stdout: str
    stderr: str
    events_dir: Path


def run_cli(capsys: pytest.CaptureFixture[str], argv: Sequence[str], events_dir: Path) -> CliRun:
    """`data-agent run` in-process; argparse errors and the run's own exit both end in
    SystemExit."""
    capsys.readouterr()
    try:
        cli.main(list(argv))
    except SystemExit as exc:
        code = exc.code
    else:
        code = 0
    out = capsys.readouterr()
    return CliRun(code, out.out, out.err, events_dir)


def run_args(
    root: Path,
    database: Sequence[str] | None = None,
    mcp_servers: Path | None = None,
    config: str = "full",
) -> tuple[list[str], Path]:
    """A query run's arguments under `root` (its own workspace and events dir), on a fresh copy of
    the shop DuckDB unless `database` names another; with `--mcp-servers` when given."""
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    events_dir = root / "events"
    if database is None:
        database = ("--duckdb", str(build_shop_duckdb(root / "shop.duckdb")))
    argv = [
        "run",
        *("--task", "How many orders are there?", "--type", "query"),
        *("--workspace", str(workspace), *database),
        *("--config", config, "--events-dir", str(events_dir)),
        *("--sandbox", "local"),
        *(("--mcp-servers", str(mcp_servers)) if mcp_servers is not None else ()),
    ]
    return argv, events_dir


def write_config(path: Path, servers: Mapping[str, Any]) -> Path:
    """A Claude Code / Cursor `mcpServers` file (plus `allowedTools`), as a user would keep it
    outside the repository."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")
    return path


def only_session(capture: Capture) -> Session:
    assert len(capture.sessions) == 1, f"expected one session, got {len(capture.sessions)}"
    return capture.sessions[0]


# --- Reading the options ------------------------------------------------------------------


def servers_of(options: ClaudeAgentOptions) -> dict[str, Any]:
    servers = options.mcp_servers
    assert isinstance(servers, dict), f"mcp_servers is not a dict: {servers!r}"
    return dict(servers)


def server_of(options: ClaudeAgentOptions, name: str) -> dict[str, Any]:
    servers = servers_of(options)
    assert name in servers, f"no server {name!r} in mcp_servers: {sorted(servers)}"
    return dict(servers[name])


def server_argv(server: Mapping[str, Any]) -> list[str]:
    assert "command" in server, f"stdio server without a command: {server!r}"
    return [str(server["command"]), *(str(arg) for arg in server.get("args", []))]


def external_entries(allowed: Sequence[str]) -> list[str]:
    """The `allowed_tools` entries for MCP servers other than ours."""
    ours = f"mcp__{DATA_SERVER}"
    return [
        name
        for name in allowed
        if name.startswith("mcp__") and name != ours and not name.startswith(f"{ours}__")
    ]


def entries_for(allowed: Sequence[str], server: str) -> list[str]:
    prefix = f"mcp__{server}"
    return [name for name in allowed if name == prefix or name.startswith(f"{prefix}__")]


def flag_value(args: Sequence[str], name: str) -> str:
    assert name in args, f"{name} not in the arguments: {list(args)}"
    index = list(args).index(name)
    assert index + 1 < len(args), f"{name} has no value"
    return args[index + 1]


# --- Reading the run's output -------------------------------------------------------------


def events_text(events_dir: Path) -> str:
    files = sorted(events_dir.glob("*.jsonl"))
    assert len(files) == 1, f"expected one event file in {events_dir}, found {files}"
    return files[0].read_text()


def version_event(events_dir: Path) -> dict[str, Any]:
    events = [json.loads(line) for line in events_text(events_dir).splitlines() if line.strip()]
    assert events and events[0]["type"] == "version", "the event file does not start with version"
    return events[0]


def external_servers(version: Mapping[str, Any]) -> list[dict[str, Any]]:
    assert "external_mcp_servers" in version, f"no external_mcp_servers in {sorted(version)}"
    servers = version["external_mcp_servers"]
    assert isinstance(servers, list), f"external_mcp_servers is not a list: {servers!r}"
    return servers


def mentions(text: str, word: str) -> bool:
    """Whether `text` names `word` on its own: not as part of `--data-sources` or `data-agent`
    (the usage line argparse prints names both)."""
    return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", text) is not None


def assert_refused_before_session(run: CliRun, capture: Capture, *words: str) -> None:
    """A config error: stderr names each of `words`, no session started, exit code 2."""
    for word in words:
        assert mentions(run.stderr, word), f"stderr does not name {word!r}: {run.stderr!r}"
    assert capture.sessions == [], "a session started despite the config error"
    assert run.code == EXIT_CONFIG_ERROR, f"exit code {run.code!r}, expected 2"


def warning_naming(stderr: str, server: str) -> list[str]:
    return [line for line in stderr.splitlines() if "warning" in line.lower() and server in line]


# --- DAB, as tests/acceptance/test_chat_eval_unchanged.py drives it (copied, not imported) ---


def make_dab(root: Path) -> Path:
    """A DAB checkout with one dataset: a SQLite and a mongo database, one query (as
    tests/test_dab.py builds it)."""
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
