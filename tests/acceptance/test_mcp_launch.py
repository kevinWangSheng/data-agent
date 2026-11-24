"""The stdio wrapper (.scratch/mcp/spec.md "Testing Decisions": stdio 包装; "stdio server 的环境",
Q7): every stdio server of the config is started through a wrapper that begins from a clean
environment, so the server gets none of our credentials.

Written by ticket 115. Each test here is xfail(strict) until ticket 116 makes it pass and removes
the marker, changing nothing else.

How: `data-agent run ... --mcp-servers FILE` (in-process, stopped at the SDK's transport,
tests/acceptance/mcp_support.py), where the config's one stdio server is
`sys.executable tests/fixtures/mcp/print_env.py OUT`. While the session's files still exist (in
the transport's `connect`), the test starts that server as the options name it, the way the CLI
starts an stdio server: `command` + `args`, in a scratch directory, with the environment
`{**dirty parent, **options.env, **server.get("env", {})}`. Whether 116 has the expanded values
reach the server through its `env` or through `options.env`, this holds either way. The wrapper's
name and form (the spec's `python -m data_agent mcp-launch -- <command>` is an example) are not
asserted, only what the server's process sees.

The dirty parent: the test process's own environment, plus fake values of the model key and
endpoint (`DATA_AGENT_*`, and the `ANTHROPIC_*` the run gives the CLI), the `--postgres-env`
variable or a `--data-sources` file's Postgres `env` variable (both runs are parametrised), the
Langfuse keys and an `OTEL_EXPORTER_OTLP_HEADERS` (the observability work gives the CLI `OTEL_*`).
The runs use `--config no-context`, so nothing tries to reach the fake Postgres before the session.

The contract fixed here (ticket 115 Context item 6; human-reviewed):

6. The server's environment has only keys from `BASE_ENV`, the config `env`'s keys, and the keys
   this interpreter adds on its own in an empty environment (macOS adds `__CF_USER_TEXT_ENCODING`
   and others; the baseline is measured by running print_env.py with `env={}`, on macOS and on
   Linux). `PATH` is kept. `BASE_ENV` is ticket 115's proposal (the spec says "PATH、HOME、LANG
   等基本变量"); if ticket 116 needs another basic variable it stops and hands back, and the human
   changes this test.
   The server reads stdin and writes stdout untouched: a line written in comes back out alone,
   nothing before or after it (an MCP server speaks JSON-RPC over stdio).

Not a fake MCP server: the spec says unit tests start none. print_env.py's echo stands in for the
JSON-RPC stream.
"""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from mcp_support import (
    PRINT_ENV,
    Capture,
    only_session,
    run_args,
    run_cli,
    server_argv,
    server_of,
    stop_at_transport,
    write_config,
)
from seam_support import build_shop_duckdb

BASE_ENV = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "TZ",
    }
)

MONGO_URL = "mongodb://reader:" + "pw-" + "Hk27" + "@mongo.example:27017/support"
CONFIG_ENV = {
    "MDB_MCP_CONNECTION_STRING": "${DATA_AGENT_MCP_MONGO_URL}",
    "MDB_MCP_READ_ONLY": "true",
}

PG_ENV = "DA_TEST_PG_URL"
SOURCES_PG_ENV = "DA_TEST_WAREHOUSE_URL"
# The credentials a server must never see, all fake.
DIRTY = {
    "DATA_AGENT_API_KEY": "sk-da-" + "Fq91x",
    "DATA_AGENT_BASE_URL": "https://llm.example/" + "v1-Rt5",
    "ANTHROPIC_API_KEY": "sk-ant-" + "Wd30p",
    "ANTHROPIC_BASE_URL": "https://anthropic.example/" + "Lm62",
    PG_ENV: "postgresql://agent:" + "pg-" + "Xc84" + "@warehouse.example:5432/wh",
    SOURCES_PG_ENV: "postgresql://agent:" + "pg-" + "Vb19" + "@warehouse2.example:5432/wh",
    "LANGFUSE_PUBLIC_KEY": "pk-lf-" + "Ty70",
    "LANGFUSE_SECRET_KEY": "sk-lf-" + "Gu53",
    "LANGFUSE_BASE_URL": "https://langfuse.example/" + "Ns48",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Basic%20" + "b3RlbC1Lk66",
}
ECHO_LINE = '{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n'
TIMEOUT = 60


@dataclass
class Started:
    """What the server's process saw, and what it wrote back."""

    argv: list[str]
    env: dict[str, str]
    stdout: str
    returncode: int


def start_server(name: str, out: Path, cwd: Path) -> Callable[[ClaudeAgentOptions], Started]:
    def probe(options: ClaudeAgentOptions) -> Started:
        server = server_of(options, name)
        argv = server_argv(server)
        env = {**os.environ, **options.env, **dict(server.get("env", {}))}
        proc = subprocess.run(
            argv, input=ECHO_LINE, env=env, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT
        )
        assert out.is_file(), f"the server did not run: {proc.stderr!r}"
        return Started(argv, json.loads(out.read_text()), proc.stdout, proc.returncode)

    return probe


def empty_env_keys(tmp_path: Path) -> frozenset[str]:
    """The keys this interpreter has when started with nothing in its environment."""
    out = tmp_path / "baseline-env.json"
    proc = subprocess.run(
        [sys.executable, str(PRINT_ENV), str(out)],
        env={},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"print_env.py does not run in an empty environment: {proc.stderr!r}")
    return frozenset(json.loads(out.read_text()))


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> Capture:
    capture = stop_at_transport(monkeypatch)
    for name, value in DIRTY.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATA_AGENT_MCP_MONGO_URL", MONGO_URL)
    return capture


def database_args(kind: str, root: Path) -> list[str]:
    if kind == "postgres-env":
        return ["--postgres-env", PG_ENV]
    db = build_shop_duckdb(root / "shop.duckdb")
    sources = root / "sources.toml"
    sources.write_text(
        f'[sources.shop]\nkind = "duckdb"\npath = "{db}"\n\n'
        f'[sources.warehouse]\nkind = "postgres"\nenv = "{SOURCES_PG_ENV}"\n'
    )
    return ["--data-sources", str(sources)]


@pytest.mark.parametrize("database", ["postgres-env", "data-sources"])
def test_a_stdio_server_starts_in_a_clean_environment_with_its_config_env(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str], database: str
) -> None:
    baseline = empty_env_keys(tmp_path)
    out = tmp_path / "server-env.json"
    original = [sys.executable, str(PRINT_ENV), str(out)]
    config = write_config(
        tmp_path / "config" / "mcp.json",
        {
            "mongo": {
                "type": "stdio",
                "command": original[0],
                "args": original[1:],
                "env": CONFIG_ENV,
                "allowedTools": ["find"],
            }
        },
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    capture.probe = start_server("mongo", out, scratch)
    argv, events_dir = run_args(
        tmp_path / "run",
        database=database_args(database, tmp_path),
        mcp_servers=config,
        config="no-context",
    )
    run_cli(capsys, argv, events_dir)
    started: Started = only_session(capture).probe_result()

    # The wrapper starts it, not the config's command line as it stands.
    assert started.argv != original
    env = started.env
    allowed = BASE_ENV | set(CONFIG_ENV) | baseline
    assert set(env) <= allowed, f"keys beyond the basic set: {sorted(set(env) - allowed)}"
    assert "PATH" in env
    assert env["MDB_MCP_CONNECTION_STRING"] == MONGO_URL
    assert env["MDB_MCP_READ_ONLY"] == "true"
    for name, value in DIRTY.items():
        assert name not in env, name
        assert not [key for key, seen in env.items() if value in seen], f"{name}'s value leaked"
    leaked = [key for key in env if key.startswith(("DATA_AGENT_", "ANTHROPIC_", "LANGFUSE_"))]
    assert leaked == []
    assert started.stdout == ECHO_LINE, "the wrapper changed what the server wrote on stdout"
    assert started.returncode == 0


def test_the_empty_environment_baseline_is_small_and_has_no_credentials(tmp_path: Path) -> None:
    """A guard on the baseline itself: what the interpreter adds on its own never includes the
    names the wrapper must drop, so allowing it cannot hide a leak."""
    baseline = empty_env_keys(tmp_path)
    dirty = {*DIRTY, "DATA_AGENT_MCP_MONGO_URL"}
    assert not baseline & dirty
    assert not [key for key in baseline if key.startswith(("DATA_AGENT_", "ANTHROPIC_", "OTEL_"))]
