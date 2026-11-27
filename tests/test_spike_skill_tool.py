"""Ticket 142 spike (scripts/spikes/skill_tool/probe.py): the parts that need no CLI or model —
the probe plugin, the options and the argv the SDK builds from them, the stub endpoint's script,
and how the probe reads its evidence. The CLI and model runs are the probe's own."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from data_agent.agent import BUILTIN_TOOLS, ModelSettings

PROBE_PATH = Path(__file__).parents[1] / "scripts" / "spikes" / "skill_tool" / "probe.py"


def load_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("spike_skill_probe", PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered first: dataclasses look the module up by name.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = load_probe()
SETTINGS = ModelSettings("http://127.0.0.1:1", "sk-test", "deepseek-flash")
PROBE = "spike-probe:skill-probe"
HIDDEN = "spike-probe:hidden-probe"


def cli_args(built: ClaudeAgentOptions) -> list[str]:
    """The Claude Code CLI's argv for these options, as the SDK builds it."""
    transport = SubprocessCLITransport(prompt="p", options=built)
    transport._cli_path = "claude"  # pyright: ignore[reportPrivateUsage]  (set by connect)
    return transport._build_command()  # pyright: ignore[reportPrivateUsage]


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


# --- The probe plugin ------------------------------------------------------------------------


def test_the_probe_plugin_names_its_skills_plugin_colon_skill() -> None:
    assert probe.plugin_name(probe.PLUGIN_DIR) == "spike-probe"
    assert probe.plugin_skills(probe.PLUGIN_DIR) == [HIDDEN, PROBE]


def test_the_canaries_are_only_in_skill_bodies_never_a_description() -> None:
    for name, canary in (("skill-probe", probe.CANARY), ("hidden-probe", probe.HIDDEN_CANARY)):
        text = (probe.PLUGIN_DIR / "skills" / name / "SKILL.md").read_text()
        _, frontmatter, body = text.split("---\n", 2)
        assert canary not in frontmatter
        assert canary in body
    probe_md = (probe.PLUGIN_DIR / "skills" / "skill-probe" / "SKILL.md").read_text()
    assert "SKILL_DIR: ${CLAUDE_SKILL_DIR}" in probe_md


def test_a_manifest_name_that_is_not_kebab_case_is_refused(tmp_path: Path) -> None:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "Data Agent"}))
    with pytest.raises(ValueError, match="kebab-case"):
        probe.plugin_name(tmp_path)


def test_a_linked_plugin_copies_the_manifest_and_links_each_skill(tmp_path: Path) -> None:
    linked = probe.linked_plugin(tmp_path / "linked", probe.PLUGIN_DIR)
    assert probe.plugin_skills(linked) == [HIDDEN, PROBE]
    assert not (linked / probe.MANIFEST).is_symlink()
    skill = linked / "skills" / "skill-probe"
    assert skill.is_symlink()
    assert skill.resolve() == (probe.PLUGIN_DIR / "skills" / "skill-probe").resolve()


# --- Options and argv ------------------------------------------------------------------------


def test_the_options_keep_build_options_shape_and_add_the_skill_tool(tmp_path: Path) -> None:
    built = probe.probe_options(SETTINGS, [probe.PLUGIN_DIR], [PROBE], tmp_path)
    assert built.tools == [*BUILTIN_TOOLS, "Skill"]
    assert built.skills == [PROBE]
    assert built.plugins == [{"type": "local", "path": str(probe.PLUGIN_DIR)}]
    assert (built.permission_mode, built.strict_mcp_config) == ("dontAsk", True)
    assert built.setting_sources == ["user"]
    assert "CLAUDE_CONFIG_DIR" not in built.env
    assert built.env["ANTHROPIC_API_KEY"] == ""


def test_a_conversation_config_dir_goes_to_the_cli_env(tmp_path: Path) -> None:
    config_dir = tmp_path / "claude-config"
    built = probe.probe_options(SETTINGS, [probe.PLUGIN_DIR], [PROBE], tmp_path, config_dir)
    assert built.env["CLAUDE_CONFIG_DIR"] == str(config_dir)


def test_the_sdk_turns_skills_and_plugins_into_allow_rules_and_plugin_dirs(tmp_path: Path) -> None:
    argv = cli_args(probe.probe_options(SETTINGS, [probe.PLUGIN_DIR], [PROBE], tmp_path))
    assert flag(argv, "--plugin-dir") == str(probe.PLUGIN_DIR)
    assert f"Skill({PROBE})" in flag(argv, "--allowedTools").split(",")
    assert "Skill" in flag(argv, "--tools").split(",")
    assert "--setting-sources=user" in argv


def test_skills_alone_leaves_the_skill_tool_out_of_an_explicit_tools_list(tmp_path: Path) -> None:
    """Why `probe_options` names the tool: `build_options` passes `tools`, and the SDK's
    `skills` only adds allow rules, so the CLI is started without the Skill tool."""
    built = ClaudeAgentOptions(tools=list(BUILTIN_TOOLS), skills=[PROBE], cwd=str(tmp_path))
    argv = cli_args(built)
    assert "Skill" not in flag(argv, "--tools").split(",")
    assert f"Skill({PROBE})" in flag(argv, "--allowedTools").split(",")


# --- The stub endpoint -----------------------------------------------------------------------


def tool_result_message(n: int) -> dict[str, Any]:
    results = [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "x"} for i in range(n)]
    return {"role": "user", "content": results}


@pytest.mark.parametrize(
    ("results", "expected"),
    [(0, ("tool_use", PROBE)), (1, ("tool_use", HIDDEN)), (2, ("end_turn", None))],
)
def test_the_stub_calls_the_probe_then_the_hidden_skill_then_answers(
    results: int, expected: tuple[str, str | None]
) -> None:
    messages = [{"role": "user", "content": "go"}]
    messages += [tool_result_message(1) for _ in range(results)]
    blocks, stop = probe.scripted_reply({"messages": messages}, PROBE, HIDDEN)
    skill = blocks[0]["input"]["skill"] if blocks[0]["type"] == "tool_use" else None
    assert (stop, skill) == expected


def test_the_stub_streams_a_tool_call_as_the_messages_api_does() -> None:
    blocks, stop = probe.scripted_reply({"messages": []}, PROBE, HIDDEN)
    events = list(probe.sse_events("m", blocks, stop))
    names = [name for name, _ in events]
    assert names == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert json.loads(events[2][1]["delta"]["partial_json"]) == {"skill": PROBE}
    assert events[4][1]["delta"]["stop_reason"] == "tool_use"


# --- Reading the evidence --------------------------------------------------------------------


LISTING = f"""<system-reminder>
{probe.LISTING_HEAD}

- {PROBE}: Spike probe. Load this Skill when asked to run the skill probe.
- dataviz: Charts.

Today's date is 2026-11-27.
</system-reminder>"""


def test_the_listing_is_read_from_the_first_requests_messages() -> None:
    request = {"body": {"messages": [{"role": "user", "content": [{"text": LISTING}]}]}}
    assert probe.sent_listing([request]) == [PROBE, "dataviz"]
    assert probe.sent_listing([]) == []


def test_the_skill_dir_is_the_probe_skills_filled_line() -> None:
    text = (
        f"{probe.BASE_DIR_LINE}/a\n\nCANARY: x\nSKILL_DIR: /a/skills/skill-probe\nPLUGIN_ROOT: /a"
    )
    assert probe.skill_dir_in(text) == "/a/skills/skill-probe"
    assert probe.skill_dir_in("SKILL_DIR:") is None


def test_init_evidence_keeps_only_the_probe_plugins_entries() -> None:
    session = probe.Session(
        "s",
        init={
            "tools": ["Read", "Skill"],
            "skills": ["my-own-skill", PROBE, HIDDEN],
            "plugins": [{"name": "someone-elses", "path": "/x"}, {"name": "spike-probe"}],
        },
    )
    assert session.probe_init("spike-probe") == {
        "tools_has_Skill": True,
        "skills": [PROBE, HIDDEN],
        "plugins": [{"name": "spike-probe"}],
        "skills_total": 3,
        "plugin_errors": None,
    }


def passing_session(label: str, answer: str = "") -> Any:
    return probe.Session(
        label,
        init={"tools": ["Skill"], "skills": [PROBE, HIDDEN], "plugins": [{"name": "spike-probe"}]},
        calls=[
            probe.SkillCall(PROBE, "Launching skill: x", False),
            probe.SkillCall(HIDDEN, "not in this session's skills allowlist", True),
        ],
        loaded=[f"{probe.BASE_DIR_LINE}/p\n{probe.CANARY}\nSKILL_DIR: /p/skills/skill-probe"],
        answer=answer,
        listing=[PROBE],
    )


def test_a_stub_run_answers_the_cli_side_and_leaves_the_model_unmeasured() -> None:
    text = probe.report([passing_session("a")], [], "stub")
    assert (
        "Q1 Skill tool, list, full SKILL.md — CLI side: 是; model follows it (stub): 未实测" in text
    )
    assert "a=是; unlisted Skill kept out: a=是" in text


def test_a_model_run_that_never_echoes_the_canary_is_a_no() -> None:
    text = probe.report([passing_session("a", answer="I could not find it")], [], "model")
    assert "model follows it (model): 否" in text


def test_a_hidden_skill_that_loads_is_not_kept_out() -> None:
    session = passing_session("a")
    session.calls[1] = probe.SkillCall(HIDDEN, "Launching skill", False)
    assert "unlisted Skill kept out: a=否" in probe.report([session], [], "stub")


def test_a_stub_run_whose_listing_was_not_seen_does_not_pass_the_listing() -> None:
    session = passing_session("a")
    session.listing = []
    assert "listed to the model: a=否" in probe.report([session], [], "stub")


def test_a_model_run_leaves_the_listing_unmeasured() -> None:
    text = probe.report([passing_session("a", answer=probe.CANARY)], [], "model")
    assert "listed to the model: a=未实测" in text


def test_no_call_to_the_hidden_skill_leaves_its_refusal_unmeasured() -> None:
    session = passing_session("a", answer=probe.CANARY)
    session.calls = session.calls[:1]
    assert "unlisted Skill kept out: a=未实测" in probe.report([session], [], "model")
    failed = probe.Session("failed", error="TimeoutError")
    assert "unlisted Skill kept out: failed=未实测" in probe.report([failed], [], "stub")


def test_the_precondition_reports_whether_the_skill_tool_came_without_tools_naming_it() -> None:
    bare = probe.Session("init-only", init={"tools": ["Read"]})
    text = probe.report([passing_session("a")], [], "stub", bare)
    assert "Skill tool in the session when `tools` omits it: 否" in text
    failed = probe.Session("init-only", error="TimeoutError")
    text = probe.report([passing_session("a")], [], "stub", failed)
    assert "Skill tool in the session when `tools` omits it: 未实测" in text
