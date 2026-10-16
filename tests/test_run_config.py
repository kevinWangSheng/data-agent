"""The named run configs (ADR-0004): which layers each one turns on, and what must stay equal."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import ResultMessage

from data_agent import agent, cli, run_config
from data_agent.agent import QUERY_SKILLS, SKILLS_DIR, Task
from data_agent.run_config import CONFIG_NAMES, load_config

ADR_CONFIGS = {"baseline", "full", "no-skill", "no-verifiers", "no-context", "full-old-skill"}
ALL_ON = {"skill": True, "verifiers": True, "context": True}


def layers(name: str) -> dict[str, bool]:
    return load_config(name).layers()


def test_the_six_adr_configs_exist() -> None:
    assert set(CONFIG_NAMES) == ADR_CONFIGS


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("baseline", {"skill": False, "verifiers": False, "context": False}),
        ("full", ALL_ON),
        ("no-skill", ALL_ON | {"skill": False}),
        ("no-verifiers", ALL_ON | {"verifiers": False}),
        ("no-context", ALL_ON | {"context": False}),
        ("full-old-skill", ALL_ON),
    ],
)
def test_each_config_switches_its_layers(name: str, expected: dict[str, bool]) -> None:
    assert layers(name) == expected


def test_every_config_has_the_same_turns_and_budget() -> None:
    """An ablation changes one layer; model, tools and budget stay equal (CONTEXT: Ablation)."""
    configs = [load_config(name) for name in CONFIG_NAMES]
    assert {(tuple(sorted(c.max_turns.items())), c.max_budget_usd) for c in configs} == {
        ((("dbt", 200), ("large", 75), ("query", 50)), 1.0)
    }


def test_full_loads_the_current_skills() -> None:
    assert load_config("full").skills_dir == SKILLS_DIR


def test_config_naming_a_missing_skill_directory_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Skill version absent from this install (e.g. not packaged into the wheel) must not run
    silently with no Skills."""
    text = (run_config.CONFIGS_DIR / "full.toml").read_text()
    (tmp_path / "full.toml").write_text(text.replace('"skills"', '"skills-v0"'))
    monkeypatch.setattr(run_config, "CONFIGS_DIR", tmp_path)
    with pytest.raises(ValueError, match="skills-v0"):
        load_config("full")


@pytest.mark.parametrize("name", ["nope", "FULL", "../full"])
def test_unknown_config_is_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="unknown run config"):
        load_config(name)


def test_skill_version_is_a_hash_of_the_skill_directory(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    (skills / "a").mkdir(parents=True)
    (skills / "a" / "SKILL.md").write_text("step 1")
    before = agent.skill_version(skills)
    assert agent.skill_version(skills) == before
    (skills / "a" / "SKILL.md").write_text("step 1, then 2")
    edited = agent.skill_version(skills)
    (skills / "a" / "notes.md").write_text("extra")
    assert len({before, edited, agent.skill_version(skills)}) == 3


async def run_scripted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: str
) -> tuple[Any, list[dict[str, Any]]]:
    """One run with the SDK replaced; returns the options it was given and the events."""
    seen: list[Any] = []

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        seen.append(options)
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            result="42",
        )

    monkeypatch.setattr(agent, "query", fake_query)
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    task = Task(text="q", type="query", workspace=tmp_path, duckdb=db)
    env = {"DATA_AGENT_API_KEY": "sk-test"}
    outcome = await agent.run(task, load_config(config), tmp_path / "events", env)
    events = [json.loads(line) for line in outcome.events_path.read_text().splitlines()]
    return seen[0], events


async def test_full_run_loads_skills_and_records_their_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    options, events = await run_scripted(monkeypatch, tmp_path, "full")
    assert agent.load_skills(QUERY_SKILLS, SKILLS_DIR) in options.system_prompt
    header = events[0]
    assert header["type"] == "version"
    assert header["config"] == "full"
    assert header["skill_version"] == agent.skill_version(SKILLS_DIR)
    assert header["layers"] == ALL_ON


@pytest.mark.parametrize("config", ["baseline", "no-skill"])
async def test_skill_off_loads_no_skill_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: str
) -> None:
    options, events = await run_scripted(monkeypatch, tmp_path, config)
    for name in QUERY_SKILLS:
        assert f"name: {name}" not in options.system_prompt, name
    assert "skill" not in options.system_prompt.lower()
    assert events[0]["skill_version"] == "none"
    assert events[0]["layers"] == layers(config)


@pytest.mark.parametrize("config", sorted(ADR_CONFIGS))
async def test_every_config_gets_the_same_turns_budget_and_mcp_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: str
) -> None:
    options, _ = await run_scripted(monkeypatch, tmp_path, config)
    assert (options.max_turns, options.max_budget_usd) == (50, 1.0)
    args = options.mcp_servers[agent.MCP_SERVER]["args"]
    assert args[args.index("--config") + 1] == config


@pytest.mark.parametrize("command", ["run", "mcp-server"])
def test_cli_reports_a_broken_config_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    text = (run_config.CONFIGS_DIR / "full.toml").read_text()
    (tmp_path / "full.toml").write_text(text.replace('"skills"', '"skills-v0"'))
    monkeypatch.setattr(run_config, "CONFIGS_DIR", tmp_path)
    args = [command, "--config", "full", "--duckdb", str(tmp_path / "x.duckdb")]
    if command == "run":
        args += ["--task", "q", "--type", "query", "--workspace", str(tmp_path)]
        args += ["--events-dir", str(tmp_path / "events")]
    with pytest.raises(SystemExit) as exit_info:
        cli.main(args)
    assert exit_info.value.code == 2
    assert "skills-v0" in capsys.readouterr().err
