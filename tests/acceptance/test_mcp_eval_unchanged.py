"""Evaluations mount no external MCP server (.scratch/mcp/spec.md "Testing Decisions": 入口;
"挂到哪些入口", Q2): `build_options` mounts none by default and no evaluation entry passes a
config, so a run there has only our own server, whatever the environment holds.

Written by ticket 115. These tests pass now and must keep passing; no ticket removes or edits
them. Every test sets a fake `DATA_AGENT_MCP_DEMO_TOKEN`: a variable of the kind a config would
reference, in the environment, still mounts nothing.

- `data-agent run` without `--mcp-servers` (in-process, stopped at the SDK's transport,
  tests/acceptance/mcp_support.py): `mcp_servers` is `data` only, `allowed_tools` names no other
  server, and the `version` event has no `external_mcp_servers`.
- Harbor: the command `evals/harbor_agent.py` `_run_command` builds has no `--mcp-servers`. The
  agent is built directly and the string read, instead of copying tests/test_harbor_agent.py's
  scaffolding (its `--network` check is the same kind).
- DAB: `evals.dab.run_query`, driven as tests/acceptance/test_chat_eval_unchanged.py drives it,
  gives the session DAB's own server only (`dab.py` replaces `mcp_servers` with it).
"""

from pathlib import Path, PurePosixPath

import pytest
from mcp_support import (
    API_KEY,
    DATA_SERVER,
    Capture,
    external_entries,
    make_dab,
    only_session,
    run_args,
    run_cli,
    servers_of,
    stop_at_transport,
    version_event,
)

from data_agent.run_config import load_config
from evals import dab
from evals.harbor_agent import DataAgent

DEMO_TOKEN = "demo-" + "Rj52" + "-fake"
# The config the DAB tests use (DAB's tool server has no context tools).
DAB_CONFIG = "no-context"


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> Capture:
    monkeypatch.setenv("DATA_AGENT_MCP_DEMO_TOKEN", DEMO_TOKEN)
    return stop_at_transport(monkeypatch)


def test_a_run_without_mcp_servers_has_only_our_server(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    argv, events_dir = run_args(tmp_path)
    run_cli(capsys, argv, events_dir)
    options = only_session(capture).options

    assert list(servers_of(options)) == [DATA_SERVER]
    assert f"mcp__{DATA_SERVER}" in options.allowed_tools
    assert external_entries(options.allowed_tools) == []
    version = version_event(events_dir)
    assert version["config"] == "full"
    assert "external_mcp_servers" not in version


def test_a_harbor_trial_command_has_no_mcp_servers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_AGENT_MCP_DEMO_TOKEN", DEMO_TOKEN)
    logs = tmp_path / "logs" / "agent"
    logs.mkdir(parents=True)
    agent = DataAgent(
        logs_dir=logs,
        model_name="deepseek/deepseek-flash",
        environment_logs_dir=PurePosixPath(logs),
        extra_env={"DATA_AGENT_API_KEY": API_KEY, "DATA_AGENT_MCP_DEMO_TOKEN": DEMO_TOKEN},
    )
    command = agent._run_command("fix the model")  # pyright: ignore[reportPrivateUsage]

    assert "/bin/data-agent run --type dbt " in command
    assert "--mcp-servers" not in command
    assert "DATA_AGENT_MCP" not in command


async def test_a_dab_query_session_has_only_dabs_server(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_dab(tmp_path / "dab")
    # A clean commit whatever state this checkout is in (tests/test_dab.py does the same).
    monkeypatch.setattr(dab, "git_commit", lambda: "c0ffee")
    query = dab.load_query(root, "toyset", "1", hints=False)
    env = {"DATA_AGENT_API_KEY": API_KEY, "DATA_AGENT_MCP_DEMO_TOKEN": DEMO_TOKEN}
    await dab.run_query(root, query, load_config(DAB_CONFIG), tmp_path / "out", 1, env)
    options = only_session(capture).options

    servers = servers_of(options)
    assert list(servers) == [dab.MCP_SERVER]
    # DAB's own tool server, not the DuckDB one `build_options` makes.
    assert "mcp-server" not in servers[dab.MCP_SERVER].get("args", [])
    assert f"mcp__{dab.MCP_SERVER}" in options.allowed_tools
    assert external_entries(options.allowed_tools) == []
