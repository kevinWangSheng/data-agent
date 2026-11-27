"""Evaluations keep their configuration (.scratch/chat-ui/spec.md "Testing Decisions", no-model
group 3; decisions 15, 19, 20): the chat work must not reach what the agent is given on the
evaluation paths.

Written by ticket 88. These tests pass now and must keep passing; no ticket removes or edits them.

The two evaluation entries, each driven the way the evaluation drives it, with the SDK's transport
stopped at its `connect` (so whatever starts the session, a one-shot `query()` or a
`ClaudeSDKClient`, the options it would send the CLI are captured and no model is called):

- Harbor (data-eng-bench, dbt): `data-agent run --type dbt --duckdb ... --config full` with the
  arguments `evals/harbor_agent.py` `_run_command` gives it, run in-process through the CLI.
  Since ticket 141 the arguments are read from `_run_command` itself, not written out here.
- DAB (query): `evals.dab.run_query`, which calls `build_options` itself (`evals/dab.py:417`), on
  a stand-in DAB checkout (tests/fixtures/fake_dab) with the config the DAB tests use. Since
  ticket 141 reached through DAB's own command line (`python -m evals dab --config no-context`).

For each, the options hold no clarification tool, no `resume`, and no `CLAUDE_CONFIG_DIR` in
`env`; and the tools, `allowed_tools`, `disallowed_tools`, permission settings, MCP servers, and
the tools each MCP server lists with their parameters (input schemas, listed by starting the
server exactly as the options name it), equal what main gave at the start of ticket 88, recorded
in tests/fixtures/chat/eval-unchanged.json. With one data source, the query and context tools
take no data-source argument (decision 20).
"""

import json
import os
import shutil
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, CLIConnectionError
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from product_config_support import harbor_agent, harbor_command_args, no_docker, substitute
from seam_support import FIXTURES, make_dbt_workspace

from data_agent import cli
from evals import dab

RECORDED = FIXTURES / "chat" / "eval-unchanged.json"
FAKE_DAB = FIXTURES / "fake_dab"
# A placeholder: the transport is stopped before anything is sent.
API_KEY = "unused-test-key"
# What evals/configs/data-eng-bench-duckdb.data-agent.yaml runs, and the config the DAB tests use
# (DAB's tool server has no context tools, so `full` is refused there).
HARBOR_CONFIG = "full"
DAB_CONFIG = "no-context"
HARBOR_TIMEOUT_SEC = "4000"


@dataclass
class Captured:
    options: ClaudeAgentOptions
    # Per MCP server: each tool it lists, with its input schema.
    mcp_tools: dict[str, list[dict[str, Any]]]


async def list_tools(server: dict[str, Any], env: dict[str, str]) -> list[dict[str, Any]]:
    """The tools a stdio MCP server lists, started as the options name it, with the environment
    the SDK's CLI would give it."""
    from mcp import ClientSession, StdioServerParameters  # pyright: ignore[reportMissingImports]
    from mcp.client.stdio import stdio_client  # pyright: ignore[reportMissingImports]

    params = StdioServerParameters(
        command=server["command"],
        args=list(server["args"]),
        env={**os.environ, **env, **server.get("env", {})},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
    return sorted(
        ({"name": tool.name, "parameters": tool.input_schema} for tool in listed.tools),
        key=lambda tool: tool["name"],
    )


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Captured]]:
    """Every session the code under test starts, stopped at the transport's `connect`; the MCP
    servers are listed there, while the files the run made for them still exist."""
    seen: list[Captured] = []

    async def connect(self: SubprocessCLITransport) -> None:
        options: ClaudeAgentOptions = self._options  # pyright: ignore[reportPrivateUsage]
        servers = options.mcp_servers if isinstance(options.mcp_servers, dict) else {}
        tools = {
            name: await list_tools(dict(server), options.env)
            for name, server in servers.items()
            if server.get("type", "stdio") == "stdio"
        }
        seen.append(Captured(options, tools))
        raise CLIConnectionError("stopped by test_chat_eval_unchanged before the CLI starts")

    monkeypatch.setattr(SubprocessCLITransport, "connect", connect)
    monkeypatch.setenv("DATA_AGENT_API_KEY", API_KEY)
    for name in ("DATA_AGENT_BASE_URL", "DATA_AGENT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    yield seen


def harbor_dbt(tmp_path: Path, seen: list[Captured]) -> Captured:
    """The arguments are the ones `_run_command` gives the trial's `data-agent run` (as
    tests/acceptance/product_config_support.py `harbor_entry` reads them), the trial container's
    workspace, database and events dir swapped for the test's: whatever ticket 146 makes the
    Harbor command pass as its config, this runs it."""
    project, db = make_dbt_workspace(tmp_path / "trial")
    trial = harbor_agent(tmp_path, HARBOR_CONFIG, "data-eng-bench")
    task = "Add a dbt model named customer_ltv with one row per customer."
    command = trial._run_command(task)  # pyright: ignore[reportPrivateUsage]
    values = {"--workspace": str(project), "--duckdb": str(db)}
    values |= {"--events-dir": str(tmp_path / "events"), "--agent-timeout-sec": HARBOR_TIMEOUT_SEC}
    argv = ["run", *substitute(harbor_command_args(command), values)]
    with pytest.raises(SystemExit):
        cli.main(argv)
    [session] = seen
    return session


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


def dab_query(tmp_path: Path, seen: list[Captured], monkeypatch: pytest.MonkeyPatch) -> Any:
    """DAB's own command line, `python -m evals dab ... --config no-context`, in-process (as
    tests/acceptance/product_config_support.py `dab_entry` drives it): the ablation name stays
    the entry's (product-config spec Q4), and `run_query`'s config argument goes with ticket 146.
    Its runs get the process's environment, where the fixture set the model key."""
    from evals import __main__ as evals_main

    root = make_dab(tmp_path / "dab")
    # A clean commit whatever state this checkout is in (tests/test_dab.py does the same).
    monkeypatch.setattr(dab, "git_commit", lambda: "c0ffee")
    # DAB's command line checks Docker first; the session is stopped before a container starts.
    no_docker(monkeypatch)
    argv = ["dab", "--dab-root", str(root), "--dataset", "toyset", "--query", "1", "--runs", "1"]
    evals_main.main([*argv, "--config", DAB_CONFIG, "--out", str(tmp_path / "out"), "--no-hints"])
    [session] = seen
    return session


def surface(session: Captured) -> dict[str, Any]:
    """What must stay as it was: tools and permissions, and the MCP tools with their parameters."""
    options = session.options
    servers = options.mcp_servers if isinstance(options.mcp_servers, dict) else {}
    return {
        "tools": options.tools,
        "allowed_tools": options.allowed_tools,
        "disallowed_tools": options.disallowed_tools,
        "permission_mode": options.permission_mode,
        "setting_sources": options.setting_sources,
        "strict_mcp_config": options.strict_mcp_config,
        "mcp_servers": sorted(servers),
        "mcp_tools": session.mcp_tools,
    }


def recorded(entry: str) -> dict[str, Any]:
    return json.loads(RECORDED.read_text())[entry]


def assert_single_run(session: Captured) -> None:
    options = session.options
    assert options.resume is None
    assert not options.continue_conversation
    assert "CLAUDE_CONFIG_DIR" not in options.env
    tools = options.tools if isinstance(options.tools, list) else []
    names = [
        *tools,
        *options.allowed_tools,
        *(tool["name"] for listed in session.mcp_tools.values() for tool in listed),
    ]
    assert not [name for name in names if "clarification" in name.lower()]


def test_harbor_dbt_run_gets_the_same_options(tmp_path: Path, captured: list[Captured]) -> None:
    session = harbor_dbt(tmp_path, captured)
    assert_single_run(session)
    assert surface(session) == recorded("harbor-dbt")


def test_dab_query_session_gets_the_same_options(
    tmp_path: Path, captured: list[Captured], monkeypatch: pytest.MonkeyPatch
) -> None:
    session = dab_query(tmp_path, captured, monkeypatch)
    assert_single_run(session)
    assert surface(session) == recorded("dab-query")
