"""Skills, MCP servers and plugins in the product (.scratch/product-config/spec.md "Skill、MCP、
插件", "产品默认与入口", "已定" 1; decisions.md Q3, Q8).

Written by ticket 141. Every test here is xfail(strict) until ticket 147 makes it pass and removes
the marker, changing nothing else. The seam: `data-agent run` in-process, sessions stopped at the
SDK's client (tests/acceptance/product_config_support.py); a conversation turn's arguments from
the web app, run in-process the same way.

The contract fixed here (for the human review). Plugin and Skill names follow the spec; ticket 142
confirmed the form, a local plugin's Skills listed as `<plugin>:<Skill>`
(docs/research/2026-11-27-skill-tool-spike.md): the built-in `skills/` is the local plugin
`data-agent`, its Skills `data-agent:<name>` (`skill_name`, one place below).

1. Product default (no config): the options' `skills` lists the Skills found (not `[]`), `tools`
   holds `Skill` (ticket 142: the CLI goes by `--tools`, and `skills` alone leaves the Skill tool
   out of the session), and `plugins` holds `{"type": "local", "path": <the built-in skills/
   directory>}`. Only the task type's workflow Skill is preloaded: its whole text is in the system
   prompt (`sql-workflow` on a query run, `dbt-workflow` on a dbt run), no other Skill's is. A
   conversation turn likewise.
2. `[skills].disabled` (Skill names as their directories are named) are left out of `skills`;
   `[skills].preload` names the Skills whose whole text goes into the system prompt.
3. `[skills].dirs`: each directory is a local plugin of the run (made in a temp directory of the
   run's), its Skills in `skills`; `[plugins].dirs`: each directory is a local plugin as it is,
   every one when `[plugins].enabled` is empty.
4. `[mcp].servers` go into `mcp_servers`, their allow lists into `allowed_tools` (ticket 116's
   rules, test_mcp_config.py). With `claude_code_config = true`, the servers of the user's Claude
   Code config (`~/.claude.json` `mcpServers`) join too, every tool allowed
   (`mcp__<server>` or `mcp__<server>__*` in `allowed_tools`), unless a `[mcp.servers.<name>]`
   table of the same name gives `allowedTools`, which then narrows it. `strict_mcp_config` is not
   True on a product run, yet `mcp_servers` holds only ours and those two sources: a `.mcp.json`
   in the workspace adds nothing.
5. Unchanged: `Bash` is in `disallowed_tools`; `setting_sources` has no `project`.
6. A plugin manifest (`.claude-plugin/plugin.json`) is not part of the Skill version: the same
   Skill directory with and without one has the same `agent.skill_version`.

Not asserted: no Skill text changes (decisions.md Q8 moves those to the migration's second step);
the event of each Skill tool call (no model call on this seam: ticket 147's unit tests and the
main session's real run).
"""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from product_config_support import (
    CLAUDE_CODE_MCP_SERVER,
    MCP_TOKEN,
    MCP_TOKEN_ENV,
    USER_MCP_SERVER,
    USER_PLUGIN,
    USER_PLUGIN_SKILL_MARKER,
    USER_SKILL_MARKER,
    USER_SKILLS,
    Capture,
    assert_session,
    claude_code_mcp,
    dbt_args,
    fake_home,
    no_docker,
    query_args,
    replay,
    run_cli,
    servers_of,
    start_turn,
    stop_sessions,
    system_prompt_of,
    toml_path,
    web_app,
    write_file,
)
from seam_support import REPO_ROOT

from data_agent import agent

TICKET_147 = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="ticket 147: Skills, MCP and plugins are not loaded from the config yet",
)
pytestmark = TICKET_147

SKILLS = REPO_ROOT / "skills"
PLUGIN = "data-agent"


def skill_name(name: str) -> str:
    """A built-in Skill's name as the SDK lists it (`<plugin>:<Skill>`, ticket 142)."""
    return f"{PLUGIN}:{name}"


def has_whole_text(prompt: str, name: str) -> bool:
    """Whether every line of the Skill's SKILL.md body is in `prompt` (lines naming
    `${CLAUDE_SKILL_DIR}` left out: the path in them is the run's)."""
    text = (SKILLS / name / "SKILL.md").read_text()
    body = text.split("---", 2)[2] if text.startswith("---") else text
    lines = [line for line in body.splitlines() if line.strip() and "${" not in line]
    assert lines, f"{name} has no text"
    return all(line in prompt for line in lines)


def local_plugins(options: Any) -> list[Path]:
    plugins = options.plugins or []
    return [Path(str(plugin["path"])) for plugin in plugins if plugin.get("type") == "local"]


def plugin_texts(options: Any) -> list[str]:
    """Every SKILL.md under each local plugin, read while the run's temp directories exist."""
    return [
        path.read_text()
        for root in local_plugins(options)
        for path in sorted(root.rglob("SKILL.md"))
    ]


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return fake_home(tmp_path)


@pytest.fixture
def capture(home: Path, monkeypatch: pytest.MonkeyPatch) -> Capture:
    no_docker(monkeypatch)
    capture = stop_sessions(monkeypatch, home)
    monkeypatch.setenv(MCP_TOKEN_ENV, MCP_TOKEN)
    return capture


def assert_unchanged_safety(options: Any) -> None:
    assert "Bash" in options.disallowed_tools
    assert "project" not in (options.setting_sources or [])


def test_a_default_query_run_loads_every_skill_and_preloads_only_its_workflow(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    argv, _ = query_args(tmp_path / "run")
    options = assert_session(run_cli(capsys, argv), capture).options
    prompt = system_prompt_of(options)

    assert options.skills, "the options list no Skill"
    assert "Skill" in (options.tools or [])
    assert skill_name("sql-workflow") in options.skills
    assert skill_name("duckdb-sql") in options.skills
    assert SKILLS.resolve() in [path.resolve() for path in local_plugins(options)]
    assert has_whole_text(prompt, "sql-workflow")
    assert not has_whole_text(prompt, "duckdb-sql")
    assert_unchanged_safety(options)


def test_a_default_dbt_run_preloads_only_dbt_workflow(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    argv, _ = dbt_args(tmp_path / "run")
    options = assert_session(run_cli(capsys, argv), capture).options
    prompt = system_prompt_of(options)

    assert skill_name("dbt-write") in (options.skills or [])
    assert "Skill" in (options.tools or [])
    assert has_whole_text(prompt, "dbt-workflow")
    for other in ("dbt-write", "dbt-debugging", "duckdb-sql"):
        assert not has_whole_text(prompt, other), other
    assert_unchanged_safety(options)


def test_a_conversation_turn_loads_skills_the_same_way(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    with web_app(tmp_path / "web") as web:
        args = start_turn(web)
        assert_session(replay(capsys, args, tmp_path / "replayed"), capture)
    options = capture.only().options
    prompt = system_prompt_of(options)

    assert skill_name("sql-workflow") in (options.skills or [])
    assert "Skill" in (options.tools or [])
    assert has_whole_text(prompt, "sql-workflow")
    assert not has_whole_text(prompt, "duckdb-sql")


def test_disabled_and_preloaded_skills(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_file(
        tmp_path / "skills.toml",
        '[skills]\ndisabled = ["dbt-debugging"]\npreload = ["dbt-workflow", "dbt-write"]\n',
    )
    argv, _ = dbt_args(tmp_path / "run", "--config", str(config))
    options = assert_session(run_cli(capsys, argv), capture).options
    prompt = system_prompt_of(options)

    assert skill_name("dbt-write") in (options.skills or [])
    assert skill_name("dbt-debugging") not in (options.skills or [])
    assert has_whole_text(prompt, "dbt-write")
    assert not has_whole_text(prompt, "dbt-debugging")


def test_skill_and_plugin_directories_become_local_plugins(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    capture.probe = plugin_texts
    config = write_file(
        tmp_path / "dirs.toml",
        f"[skills]\ndirs = [{toml_path(USER_SKILLS)}]\n\n"
        f"[plugins]\ndirs = [{toml_path(USER_PLUGIN)}]\n",
    )
    argv, _ = query_args(tmp_path / "run", "--config", str(config))
    session = assert_session(run_cli(capsys, argv), capture)
    texts = session.probed
    assert isinstance(texts, list), f"the probe failed: {texts!r}"

    assert any(USER_SKILL_MARKER in text for text in texts), "the Skill directory is no plugin"
    assert USER_PLUGIN.resolve() in [path.resolve() for path in local_plugins(session.options)]
    assert any(USER_PLUGIN_SKILL_MARKER in text for text in texts)
    skills = session.options.skills or []
    assert any(name.endswith("acme-sql") for name in skills), skills
    assert any(name.endswith("acme-report") for name in skills), skills


def test_mcp_servers_and_the_users_claude_code_servers(
    home: Path, tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    claude_code_mcp(home)
    config = write_file(
        tmp_path / "mcp.toml",
        "[mcp]\nclaude_code_config = true\n\n"
        f"[mcp.servers.{USER_MCP_SERVER}]\n"
        'type = "http"\nurl = "https://mcp.example/acme"\n'
        f'headers = {{ Authorization = "Bearer ${{{MCP_TOKEN_ENV}}}" }}\n'
        'allowedTools = ["lookup"]\n',
    )
    argv, _ = query_args(tmp_path / "run", "--config", str(config))
    workspace = Path(argv[argv.index("--workspace") + 1])
    (workspace / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"repo-server": {"type": "http", "url": "https://mcp.example/r"}}}
        )
    )
    options = assert_session(run_cli(capsys, argv), capture).options
    servers = servers_of(options)

    assert sorted(servers) == sorted(["data", USER_MCP_SERVER, CLAUDE_CODE_MCP_SERVER])
    assert f"mcp__{USER_MCP_SERVER}__lookup" in options.allowed_tools
    allowed = set(options.allowed_tools)
    assert {f"mcp__{CLAUDE_CODE_MCP_SERVER}", f"mcp__{CLAUDE_CODE_MCP_SERVER}__*"} & allowed
    assert options.strict_mcp_config is not True
    assert "repo-server" not in servers
    assert_unchanged_safety(options)


def test_a_mcp_table_narrows_a_claude_code_server(
    home: Path, tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    claude_code_mcp(home)
    config = write_file(
        tmp_path / "mcp.toml",
        "[mcp]\nclaude_code_config = true\n\n"
        f'[mcp.servers.{CLAUDE_CODE_MCP_SERVER}]\nallowedTools = ["search"]\n',
    )
    argv, _ = query_args(tmp_path / "run", "--config", str(config))
    options = assert_session(run_cli(capsys, argv), capture).options
    ours = [
        name for name in options.allowed_tools if name.startswith(f"mcp__{CLAUDE_CODE_MCP_SERVER}")
    ]

    assert ours == [f"mcp__{CLAUDE_CODE_MCP_SERVER}__search"]


def test_a_plugin_manifest_is_not_part_of_the_skill_version(tmp_path: Path) -> None:
    copy = tmp_path / "skills"
    shutil.copytree(SKILLS, copy, ignore=shutil.ignore_patterns(".claude-plugin"))
    without = agent.skill_version(copy)
    manifest = copy / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"name": PLUGIN, "version": "0.0.1"}))
    with_manifest = agent.skill_version(copy)
    (copy / "sql-workflow" / "SKILL.md").write_text("edited\n")

    assert with_manifest == without
    assert agent.skill_version(copy) != without
