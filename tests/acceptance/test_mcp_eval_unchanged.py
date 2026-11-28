"""Evaluations mount no external MCP server (.scratch/mcp/spec.md "Testing Decisions": 入口;
"挂到哪些入口", Q2): `build_options` mounts none by default and no evaluation entry passes a
config, so a run there has only our own server, whatever the environment holds.

Written by ticket 115. These tests pass now and must keep passing; no ticket removes or edits
them. Every test sets a fake `DATA_AGENT_MCP_DEMO_TOKEN`: a variable of the kind a config would
reference, in the environment, still mounts nothing.

- `data-agent run` without an `[mcp]` config (in-process, stopped at the SDK's transport,
  tests/acceptance/mcp_support.py): `mcp_servers` is `data` only, `allowed_tools` names no other
  server, and the `version` event has no `external_mcp_servers`.
- Harbor: the command `evals/harbor_agent.py` `_run_command` builds has no `--mcp-servers`. The
  agent is built directly and the string read, instead of copying tests/test_harbor_agent.py's
  scaffolding (its `--network` check is the same kind).
- DAB: its own command line, `python -m evals dab ... --config no-context`, in-process (as
  tests/acceptance/product_config_support.py `dab_entry` drives it), gives the session DAB's own
  server only (`dab.py` replaces `mcp_servers` with it).

Ticket 141 moved the calls to the config files (.scratch/product-config/spec.md): the
`data-agent run` takes its layers from a config file and is `TICKET_143` until ticket 143 reads
it; DAB is driven through its command line, where the ablation name stays (product-config spec
Q4), in place of `run_query(..., load_config(...))`, which ticket 146 removes.
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
from product_config_support import no_docker

from evals import __main__ as evals_main
from evals import dab
from evals.harbor_agent import DataAgent

TICKET_143 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 143: the config loader is not built yet"
)

DEMO_TOKEN = "demo-" + "Rj52" + "-fake"
# The config the DAB tests use (DAB's tool server has no context tools).
DAB_CONFIG = "no-context"


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> Capture:
    monkeypatch.setenv("DATA_AGENT_MCP_DEMO_TOKEN", DEMO_TOKEN)
    return stop_at_transport(monkeypatch)


@TICKET_143
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


def test_a_dab_query_session_has_only_dabs_server(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_dab(tmp_path / "dab")
    # A clean commit whatever state this checkout is in (tests/test_dab.py does the same).
    monkeypatch.setattr(dab, "git_commit", lambda: "c0ffee")
    # DAB's command line checks Docker first; the session is stopped before a container starts.
    no_docker(monkeypatch)
    # The runs get the process's environment: the fixture set the model key and the demo token.
    argv = ["dab", "--dab-root", str(root), "--dataset", "toyset", "--query", "1", "--runs", "1"]
    evals_main.main([*argv, "--config", DAB_CONFIG, "--out", str(tmp_path / "out"), "--no-hints"])
    options = only_session(capture).options

    servers = servers_of(options)
    assert list(servers) == [dab.MCP_SERVER]
    # DAB's own tool server, not the DuckDB one `build_options` makes.
    assert "mcp-server" not in servers[dab.MCP_SERVER].get("args", [])
    assert f"mcp__{dab.MCP_SERVER}" in options.allowed_tools
    assert external_entries(options.allowed_tools) == []
