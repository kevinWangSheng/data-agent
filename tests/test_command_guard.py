"""Bash in a dbt run may only run dbt and the dbt-workflow scripts, so dbt stays the only write path
and ad-hoc SQL still has to go through the read-only gateway."""

from pathlib import Path
from typing import Any

import pytest

from data_agent.command_guard import command_guard

SCRIPTS_DIR = Path("/opt/skills/dbt-workflow")
SCRIPTS = (SCRIPTS_DIR / "scan_project.py", SCRIPTS_DIR / "validate_project.py")


async def decide(
    workspace: Path,
    command: str,
    tool: str = "Bash",
    scripts: tuple[Path, ...] = SCRIPTS,
    background: bool = False,
) -> tuple[str, str]:
    guard = command_guard(workspace, scripts)
    hook_input: Any = {
        "hook_event_name": "PreToolUse",
        "session_id": "s",
        "transcript_path": "t",
        "cwd": str(workspace),
        "tool_name": tool,
        "tool_input": {"command": command, **({"run_in_background": True} if background else {})},
        "tool_use_id": "u",
    }
    output: Any = await guard(hook_input, "u", {"signal": None})
    specific = output.get("hookSpecificOutput", {})
    return specific.get("permissionDecision", "none"), specific.get("permissionDecisionReason", "")


@pytest.mark.parametrize(
    "command",
    [
        "dbt run --select customer_ltv",
        "dbt run --select +customer_ltv stg_orders",
        "dbt parse",
        "dbt compile --select customer_ltv",
        "dbt ls --resource-type model",
        "dbt test --select customer_ltv",
        "dbt deps",
        "dbt run --no-use-colors --select customer_ltv",
        "dbt run --select customer_ltv 2>&1 | tail -50",
        "dbt run --select customer_ltv 2>&1 | tail -n 50",
        "dbt run --select customer_ltv | head -20",
        "dbt run --project-dir . --profiles-dir {ws}",
        "dbt parse --log-path logs --target-path={ws}/target",
        f'python3 "{SCRIPTS[0]}" "{{ws}}"',
        f"python3 {SCRIPTS[1]} . 120",
    ],
)
async def test_dbt_and_the_workflow_scripts_run(tmp_path: Path, command: str) -> None:
    decision, _ = await decide(tmp_path, command.replace("{ws}", str(tmp_path)))
    assert decision == "none"


@pytest.mark.parametrize(
    "command",
    [
        'duckdb ../shop.duckdb "DELETE FROM raw.orders"',
        'python3 -c \'import duckdb; duckdb.connect("shop.duckdb").execute("DROP TABLE x")\'',
        "python3 other_script.py",
        f"python3 {SCRIPTS_DIR}/../evil.py",
        f"python3 {SCRIPTS[0]} /tmp/elsewhere",
        f"python3 {SCRIPTS[1]} ~/other_project 60",
        f"python3 {SCRIPTS[0]} ..",
        "rm -rf models",
        "cat ~/.ssh/id_ed25519",
        "dbt run; rm -rf models",
        "dbt run && cat /etc/passwd",
        "dbt run || true",
        "dbt run & sleep 1",
        "dbt run > out.txt",
        "dbt run --select $(cat x)",
        "dbt run --select `whoami`",
        "dbt run 2>&1 | sh",
        "dbt run | tail -50 /etc/passwd",
        "dbt show --inline 'select * from raw.orders'",
        "dbt compile --inline 'select 1'",
        "dbt run-operation drop_everything",
        # Subcommands that write outside `dbt run` of the project's models.
        "dbt build -s customer_ltv",
        "dbt seed",
        "dbt snapshot",
        "dbt test --store-failures",
        "dbt test --select customer_ltv --store-failures-as table",
        "dbt test --store-failures-as=view",
        # Bash expands braces and globs after the guard has read the words.
        "dbt compile {--inline,} 'select 1'",
        "dbt run --profiles-dir={.,/etc}",
        "dbt run --target-path=/tmp/[a]",
        "dbt run --log-path /tm?",
        # Global flags before the subcommand: their values must not pass for the subcommand.
        "dbt --log-level debug run-operation x",
        "dbt --log-level debug show --select x",
        "dbt --no-use-colors run",
        "dbt run --select x#; printf BYPASSED",
        "dbt run --select x #; printf BYPASSED",
        "dbt parse --log-path /tmp/outside",
        "dbt parse --target-path=/tmp/outside",
        "dbt run --project-dir /tmp/elsewhere",
        "dbt run --profiles-dir=/tmp/elsewhere",
        "DBT_PROFILES_DIR=/tmp dbt run",
        "cd models && dbt run",
        "dbt",
        "",
        "dbt run 'unclosed",
    ],
)
async def test_everything_else_is_denied_with_a_reason(tmp_path: Path, command: str) -> None:
    decision, reason = await decide(tmp_path, command)
    assert decision == "deny"
    assert "dbt" in reason


async def test_a_background_command_is_denied(tmp_path: Path) -> None:
    """The guard cannot see a background command finish, and dbt holds the database write lock."""
    decision, reason = await decide(tmp_path, "dbt run --select customer_ltv", background=True)
    assert decision == "deny"
    assert "background" in reason


async def test_the_rule_names_only_the_allowed_subcommands(tmp_path: Path) -> None:
    _, reason = await decide(tmp_path, "rm -rf models")
    assert "dbt" in reason
    for subcommand in ("build", "seed", "snapshot"):
        assert subcommand not in reason


async def test_without_the_skill_layer_no_script_runs_or_is_named(tmp_path: Path) -> None:
    """With the Skill layer off the scripts are not offered, and the denial must not mention them
    (a layer that is off leaves no trace)."""
    decision, reason = await decide(tmp_path, f"python3 {SCRIPTS[0]} .", scripts=())
    assert decision == "deny"
    assert "dbt" in reason
    assert "script" not in reason.lower()
    assert "dbt-workflow" not in reason


async def test_other_tools_are_left_alone(tmp_path: Path) -> None:
    assert (await decide(tmp_path, "rm -rf /", tool="Read"))[0] == "none"
