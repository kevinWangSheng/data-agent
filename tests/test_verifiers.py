"""The verifier subagent: offered only to dbt runs whose config turns the layer on, read-only,
one definition working through the v2 tools, recorded as verifier_start events (its end is
verifier_v2's `verifier_end`)."""

import json
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, HookContext, HookMatcher
from claude_agent_sdk.types import HookEvent

from data_agent import agent
from data_agent.agent import ModelSettings, Task
from data_agent.events import EventLog
from data_agent.run_config import load_config
from data_agent.verifier_v2 import hooks, parse_report
from data_agent.verifier_v2.dispatch import UNVERIFIED_HEADING
from data_agent.verifier_v2.render import render_compare
from data_agent.verifiers import (
    VERIFIER_MAX_TURNS,
    VERIFIER_NAMES,
    VERIFIERS_PROMPT,
    verifier_hooks,
)

SETTINGS = ModelSettings.from_env({"DATA_AGENT_API_KEY": "k"})
V2_TOOLS = ("model_diff", "contract_check", "grain_facts", "compare_by_key")


def options(tmp_path: Path, task_type: str, config: str) -> ClaudeAgentOptions:
    task = Task(text="t", type=task_type, workspace=tmp_path, duckdb=tmp_path / "x.duckdb")  # pyright: ignore[reportArgumentType]
    return agent.build_options(task, load_config(config), SETTINGS, [], lambda *_, **__: None)


def test_dbt_runs_offer_one_read_only_verifier(tmp_path: Path) -> None:
    built = options(tmp_path, "dbt", "full")
    assert set(built.agents or {}) == set(VERIFIER_NAMES) == {"verifier"}
    assert "Agent" in (built.tools or []) and "Agent" in built.allowed_tools
    definition = (built.agents or {})["verifier"]
    tools = set(definition.tools or [])
    assert tools >= {"Read", "mcp__data__query_database"}
    assert tools >= {f"mcp__data__{tool}" for tool in V2_TOOLS}
    assert not tools & {"Write", "Edit", "Bash", "Agent"}
    assert "NEEDS_INFO" in definition.prompt and "N/A" in definition.prompt
    assert "SubagentStart" in (built.hooks or {})
    # The v2 hooks (dispatch rewrite, verifier_end, Stop) need the run's snapshot and task file.
    assert not {"SubagentStop", "Stop"} & set(built.hooks or {})
    with_run = agent.build_options(
        Task(text="t", type="dbt", workspace=tmp_path, duckdb=tmp_path / "x.duckdb"),  # pyright: ignore[reportArgumentType]
        load_config("full"),
        SETTINGS,
        [],
        lambda *_, **__: None,
        task_file=tmp_path / "task.md",
        snapshot=tmp_path / "snapshot",
    )
    assert {"PreToolUse", "SubagentStart", "SubagentStop", "Stop"} <= set(with_run.hooks or {})
    # A background verifier ends the main turn before its report is in, splitting the run.
    assert built.env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    servers: Any = built.mcp_servers
    server = servers["data"]
    assert server["args"][-2:] == ["--workspace", str(tmp_path.resolve())]
    assert "--no-verifier-tools" not in server["args"]
    assert VERIFIERS_PROMPT in str(built.system_prompt)


def test_verifier_turn_cap_is_fifty_and_the_hooks_read_the_same_number(tmp_path: Path) -> None:
    """Spec "验证器": maxTurns 50; the SubagentStop hook calls a report at that many turns
    NEEDS_INFO, so the two constants must agree."""
    definition = (options(tmp_path, "dbt", "full").agents or {})["verifier"]
    assert definition.maxTurns == VERIFIER_MAX_TURNS == 50
    assert hooks.VERIFIER_MAX_TURNS == VERIFIER_MAX_TURNS


@pytest.mark.parametrize(
    ("task_type", "config"), [("dbt", "no-verifiers"), ("dbt", "baseline"), ("query", "full")]
)
def test_verifiers_are_absent_when_off_or_for_query_runs(
    tmp_path: Path, task_type: str, config: str
) -> None:
    built = options(tmp_path, task_type, config)
    assert not built.agents
    assert "Agent" not in (built.tools or []) and "Agent" not in built.allowed_tools
    assert not {"SubagentStart", "SubagentStop"} & set(built.hooks or {})
    assert VERIFIERS_PROMPT not in str(built.system_prompt)
    # The server leaves the verifier tools out too, so the ablation holds for query runs.
    servers: Any = built.mcp_servers
    assert "--no-verifier-tools" in servers["data"]["args"]


def test_verifier_definition_names_only_tools_the_server_has() -> None:
    built = options(Path("."), "dbt", "full")
    for definition in (built.agents or {}).values():
        for tool in definition.tools or []:
            if tool.startswith("mcp__"):
                assert tool.removeprefix("mcp__data__") in {
                    "list_tables",
                    "query_database",
                    *V2_TOOLS,
                }


def verifier_prompt() -> str:
    agents = options(Path("."), "dbt", "full").agents or {}
    return agents["verifier"].prompt


def step(number: int) -> str:
    prompt = verifier_prompt()
    text = prompt.split(f"### Step {number}")[1].split("\n### ")[0].split("\n## ")[0]
    return " ".join(text.split())


def test_verifier_runs_the_four_steps_in_the_specs_order() -> None:
    """Spec "验证器": contract_check copied (CHECK 1, 2, 4); the grain key from the task into
    grain_facts (CHECK 3); model_diff judged against the task (CHECK 5, 6); a reference query
    from the task into compare_by_key with the Step 2 key (CHECK 7)."""
    prompt = verifier_prompt()
    positions = [prompt.index(f"### Step {n}") for n in (1, 2, 3, 4)]
    assert positions == sorted(positions)
    assert "contract_check" in step(1) and "CHECK 1, 2, 4" in step(1)
    assert "grain_facts" in step(2) and "CHECK 3" in step(2)
    assert "model_diff" in step(3) and "CHECK 5, 6" in step(3)
    assert "compare_by_key" in step(4) and "CHECK 7" in step(4)
    assert "Step 2" in step(4)
    for tool in V2_TOOLS:
        assert f"`{tool}" in prompt
    # The replaced tools are gone with the old checks (the modification note at the top names them).
    body = prompt.split("-->", 1)[1]
    for old in ("map_columns", "check_model_schema", "audit_model_sources", "verify_model_values"):
        assert old not in body


def test_verifier_copies_contract_check_and_does_not_rederive_it() -> None:
    text = step(1)
    assert "Copy the tool's three results" in text
    assert "Do not re-derive" in text
    assert "YML the main agent wrote or changed is not a contract" in text


def test_verifier_picks_the_grain_key_from_the_task_and_applies_the_null_duplicate_rule() -> None:
    text = step(2)
    assert "From the task text, decide each model's grain key" in text
    assert "never from the model's SQL" in text
    assert "NULL rows > 0 or duplicate rows > 0 is FAIL" in text
    assert "cannot exempt" in text and "technical_spec.md" in text


def test_verifier_takes_the_grain_key_from_the_task_only_and_never_reads_yml_for_it() -> None:
    """ADR-0009: the verifier can only Read the project's YML, where
    the main agent's new `unique` tests are indistinguishable from the original ones, so YML is
    not a key source; a task that names no grain is NEEDS_INFO, and the only YML exemption is
    the "exemption candidate" line grain_facts itself reads from the snapshot."""
    text = step(2)
    assert "Take the key from the task text only" in text
    assert "not from any YML file in the project" in text
    assert "If the task text does not name a grain, CHECK 3 = NEEDS_INFO" in text
    assert "the exemption candidate the tool reports" in text
    assert "The only exemptions are the task text" in text
    assert "Do not read YML files to find a key or an exemption" in text
    assert "original YML" not in text


def test_verifier_judges_only_the_changes_model_diff_lists() -> None:
    text = step(3)
    assert "Logic the main agent did not touch is not in the output and is not reviewed" in text
    assert "do not read the model's SQL to audit the rest of it" in text
    assert "A removed filter is a change too" in text
    assert "未解析" in text


def test_verifier_writes_the_reference_from_the_task_not_the_model() -> None:
    text = step(4)
    assert "from the task text alone" in text
    assert "do not copy its expressions, CTEs or `technical_spec.md`" in text
    assert "must not read the model or any model this run created or changed" in text
    assert "any key only on one side or any value mismatch is FAIL" in text
    assert "do not report the refusal as a model defect" in text


def test_verifier_aliases_reference_columns_and_needs_info_when_none_compared() -> None:
    """compare_by_key compares only same-named columns, so a reference whose metric is aliased
    differently compares nothing (or not the metric) and three zero counts would read as a hollow
    PASS. The strings the prompt quotes are the ones render_compare prints."""
    text = step(4)
    assert "alias every non-key output column to the model's column name" in text
    assert "Read the contract_check output or the task's column list for the names" in text
    # A `LIMIT 0` query prints no header (format_rows says only "Query returned 0 rows"), so the
    # fallback column source is list_tables, which prints every column name.
    assert 'list_tables(name_contains="<model>")' in text
    assert "LIMIT 0" not in text
    none_compared = render_compare(_compare_result(compared=[], unmatched=["revenue"]))
    assert "compared columns: (none)" in none_compared
    assert "`compared columns: (none)`" in text
    assert "reference columns the model lacks (not compared)" in none_compared
    assert '"reference columns the model lacks (not compared)"' in text
    assert "CHECK 7 = NEEDS_INFO, not PASS" in text
    assert "All zero with at least one compared column: PASS, but only when every metric" in text


def _compare_result(compared: list[str], unmatched: list[str]) -> dict[str, Any]:
    return {
        "accepted": True,
        "relation": "main.m",
        "dialect": "duckdb",
        "compared_columns": compared,
        "unmatched_reference_columns": unmatched,
        "only_in_model": 0,
        "only_in_reference": 0,
        "mismatched": 0,
        "samples": {"only_in_model": [], "only_in_reference": [], "mismatched": []},
    }


def test_verifier_compares_keys_alone_when_the_task_defines_no_non_key_column() -> None:
    """A task that only defines a row set (dedup, filter, "one row per X") gives a reference with
    key columns only; `compared columns: (none)` is then the expected shape, and the key counts
    alone decide, not NEEDS_INFO."""
    text = step(4)
    assert "unless the task defines no non-key column at all" in text
    assert "the three key counts alone decide" in text
    # A key-only reference renders as "(none)" with the three key counts; nothing else to read.
    rendered = render_compare(_compare_result(compared=[], unmatched=[]))
    assert "compared columns: (none)" in rendered


def test_verifier_maps_the_tasks_grain_words_to_column_names_without_reading_sql() -> None:
    """grain_facts resolves the key by the built model's column names (an unknown name is a
    ToolError), and a task says "per customer and month" without naming columns, so Step 2 must
    say where the names come from and that names are not logic."""
    text = step(2)
    assert "Column names are not the model's logic" in text
    assert "contract_check output, the task's column list" in text
    assert 'list_tables(name_contains="<model>")' in text


def test_verifier_exemption_candidate_needs_only_no_contradiction_from_the_task() -> None:
    """Spec: the snapshot YML can exempt on its own; a task silent on grain cannot "confirm"
    anything, so confirming means the task text does not contradict the candidate."""
    text = step(2)
    assert "the task text does not contradict it" in text
    assert "says nothing about a finer grain does not" in text
    assert "confirm a candidate against the task" not in text


def test_verifier_treats_the_main_agents_counter_evidence_as_unverified() -> None:
    prompt = " ".join(verifier_prompt().split())
    heading = UNVERIFIED_HEADING.split("（")[0]  # noqa: RUF001
    assert heading in prompt
    section = prompt.split("## Task file")[1].split("##")[0]
    assert "word for word" in section and "Read it before any check" in section
    assert "technical_spec.md" in section and "the task text wins" in section
    assert "unverified" in section and "never as a result" in section


def test_verifier_report_template_parses_as_the_hooks_expect() -> None:
    """The output format gives CHECK 1-7 one per line under `### <model>`, in the shape
    `parse_report` reads; every line with a real status is a verdict."""
    template = verifier_prompt().split("## Output Format")[1].split("### Summary")[0]
    lines = [line for line in template.splitlines() if line.startswith("CHECK ")]
    assert [int(line.split()[1].rstrip(":")) for line in lines] == [1, 2, 3, 4, 5, 6, 7]
    placeholder = "<PASS|FAIL|NEEDS_INFO|N/A>"
    assert all(placeholder in line for line in lines)
    # The template copied verbatim is no verdict; each real status is.
    assert not parse_report("### m\n" + "\n".join(lines))["parsed"]
    for status in ("PASS", "FAIL", "NEEDS_INFO", "N/A"):
        report = "### m\n" + "\n".join(line.replace(placeholder, status) for line in lines)
        assert parse_report(report)["models"] == {"m": dict.fromkeys(range(1, 8), status)}


def test_verifier_prompt_names_no_fix_or_prescription() -> None:
    """The verifier reports; the FAIL handling is the main agent's, in VERIFIERS_PROMPT."""
    prompt = verifier_prompt()
    assert "Fix nothing" in prompt
    assert "Prescribe" not in prompt and "RECOMMENDATION" not in prompt


def test_system_prompt_holds_the_fail_rules_once() -> None:
    """Spec "FAIL 处理": reproduce first, smallest change, rebuild only the changed models,
    re-dispatch; counter-evidence when it does not reproduce; NEEDS_INFO re-dispatched with the
    information or done by the main agent. The Skill only refers here."""
    flat = " ".join(VERIFIERS_PROMPT.split())
    fail = flat.split("- FAIL:")[1].split("- NEEDS_INFO:")[0]
    assert "reproduce the failure yourself with one read-only query" in fail
    assert "smallest change" in fail and "rebuild only the changed models" in fail
    assert "dispatch the verifier again" in fail
    assert "does not reproduce" in fail and "counter-evidence" in fail
    needs = flat.split("- NEEDS_INFO:")[1]
    assert "information it asked for" in needs and "run that check yourself" in needs
    # The verifier takes the grain key from the task text only, so re-dispatching with a grain
    # the main agent supplies is a loop; a grain NEEDS_INFO is the main agent's own check.
    grain = needs.split("- Stop when")[0]
    assert "run the grain check yourself with `grain_facts`" in grain
    # CHECK 7 is a value comparison; grain_facts alone does not resolve it.
    assert "`compare_by_key` and a reference query on that key (CHECK 7)" in grain
    # A NEEDS_INFO the main agent resolved itself is an exit (the Stop hook never blocks on it).
    assert "a NEEDS_INFO you resolved yourself" in needs.split("- Stop when")[1]
    assert "`verifier`" in flat and "value-verifier" not in flat
    assert "Do not tell it what the columns mean or how you wrote the SQL" in flat


def test_system_prompt_limits_the_main_agents_own_v2_checks_to_three_cases() -> None:
    """Ticket 47 return round 3: the main agent calls `compare_by_key` or `grain_facts` itself
    only without a verifier, to reproduce one FAIL (one model, one call), or for a grain
    NEEDS_INFO; never as a routine self-check before or after a dispatch."""
    flat = " ".join(VERIFIERS_PROMPT.split())
    rule = flat.split("Call `compare_by_key` or `grain_facts` yourself only in three cases", 1)[1]
    rule = rule.split("- ", 1)[0]
    assert "when no verifier is offered" in rule
    assert "to reproduce one FAIL (one call, on that one model)" in rule
    assert "for a grain NEEDS_INFO as above" in rule
    assert "Not as a routine self-check" in rule
    assert "technical_spec.md" in flat and "intent" in flat
    assert "CHECK 1-7" in flat


async def fire(
    hooks: dict[HookEvent, list[HookMatcher]], event: HookEvent, hook_input: Any
) -> None:
    for matcher in hooks[event]:
        for callback in matcher.hooks:
            assert await callback(hook_input, None, HookContext(signal=None)) == {}


async def test_hooks_record_a_verifier_start_and_no_other_subagent(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    base = {"session_id": "s", "transcript_path": "t", "cwd": str(tmp_path)}
    with EventLog(path, secrets=[]) as log:
        hooks = verifier_hooks(log.emit)
        assert set(hooks) == {"SubagentStart"}
        start = {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "verifier"}
        await fire(hooks, "SubagentStart", base | start)
        other = {"hook_event_name": "SubagentStart", "agent_id": "a2", "agent_type": "Explore"}
        await fire(hooks, "SubagentStart", base | other)
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [e["type"] for e in events] == ["verifier_start"]
    assert events[0]["verifier"] == "verifier" and events[0]["agent_id"] == "a1"


def test_parse_report_reads_a_row_logic_fail_that_quotes_sql() -> None:
    report = (
        "### fct_orders\n"
        "CHECK 5: N/A — sources: no source change\n"
        "CHECK 6: FAIL — row logic: `[filter] removed: o.status <> 'cancelled'`: neither "
        "the task nor the YML asks to keep cancelled orders\n"
    )
    assert parse_report(report)["models"] == {"fct_orders": {5: "N/A", 6: "FAIL"}}
