"""The verifier subagents (ADR-0002, plan §3): structure and value auditors, read-only, defined
with the SDK's `agents=` from the copied signalpilot-plugin prompts in `agents/`.

They are offered only to dbt runs whose config turns the verifier layer on (query runs use
sql-workflow's own checks). SubagentStart / SubagentStop hooks record each one's start and end,
with the per-check verdicts read from its report, as `verifier_start` / `verifier_end` events.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AgentDefinition,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
)
from claude_agent_sdk.types import HookEvent

AGENTS_DIR = Path(__file__).resolve().parent / "agents"
VERIFIER_NAMES = ("verifier", "value-verifier")
# The Agent tool (Task before Claude Code 2.1) that dispatches subagents.
AGENT_TOOL = "Agent"
READ_TOOLS = ("Read", "Glob", "Grep")
# MCP tools per verifier, by the server's tool names; the checks in each prompt name these.
DATA_TOOLS = {
    "verifier": (
        *("list_tables", "query_database", "map_columns", "check_model_schema"),
        "audit_model_sources",
    ),
    "value-verifier": ("list_tables", "query_database", "verify_model_values"),
}
STATUSES = ("PASS", "FAIL", "NEEDS_INFO", "N/A")
# "CHECK 2: FAIL", "**CHECK 2**: FAIL", "**CHECK 2 - Aggregate Cross-Validation: FAIL**".
CHECK_RE = re.compile(r"(CHECK\s*\d+)\b[^:\n]{0,80}:[\s*]*(PASS|FAIL|NEEDS_INFO|N/A)\b")
MODEL_RE = re.compile(r"^###\s+(.+?)\s*$")

# Appended to a dbt run's system prompt when the verifier layer is on, so the protocol holds with
# or without the Skill layer.
VERIFIERS_PROMPT = """
## Verification before delivery
Before your final message, dispatch the `verifier` (structure) and `value-verifier` (values) \
agents IN PARALLEL with the Agent tool, both in one turn. Give each the dbt project directory and \
the names of the models you created or changed; for a model you changed, also the tables its \
SQL read from before your change. Do not tell them what the columns mean or how you wrote the SQL.

Each report gives every check one of PASS, FAIL, NEEDS_INFO or N/A.
- FAIL: first reproduce the failure yourself with one read-only query that shows the defect. Then \
make the smallest change that fixes it, rebuild only the changed models with dbt, and send just \
those models back to the verifier whose check failed.
- NEEDS_INFO: give that verifier what it asked for and run it again, or run the check yourself.
- Stop when every check is PASS or N/A, or when your turns run out. Do not change models after \
that.
"""

Emit = Callable[..., None]


def load_definition(name: str, server: str) -> AgentDefinition:
    """One verifier from its markdown file: frontmatter description, body as the prompt."""
    _, frontmatter, body = (AGENTS_DIR / f"{name}.md").read_text().split("---\n", 2)
    fields = dict(line.split(":", 1) for line in frontmatter.splitlines() if ":" in line)
    return AgentDefinition(
        description=fields["description"].strip().strip('"'),
        prompt=body.strip(),
        tools=[*READ_TOOLS, *(f"mcp__{server}__{tool}" for tool in DATA_TOOLS[name])],
        model="inherit",
        # In the foreground: a background verifier ends the main turn before its report is in,
        # and the run then continues over several SDK results.
        background=False,
    )


def verifier_agents(server: str) -> dict[str, AgentDefinition]:
    return {name: load_definition(name, server) for name in VERIFIER_NAMES}


def parse_checks(report: str) -> list[dict[str, str]]:
    """Each `CHECK n: STATUS` line, under the `### <model>` heading it follows."""
    checks: list[dict[str, str]] = []
    model = ""
    for line in report.splitlines():
        if heading := MODEL_RE.match(line.strip()):
            model = heading.group(1)
        elif found := CHECK_RE.search(line):
            checks.append({"model": model, "check": found.group(1), "status": found.group(2)})
    return checks


def verifier_hooks(emit: Emit) -> dict[HookEvent, list[HookMatcher]]:
    """Hooks that record the verifiers' start and end; other subagents leave no event."""

    async def record(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        fields: dict[str, Any] = dict(hook_input)
        if fields.get("agent_type") not in VERIFIER_NAMES:
            return {}
        common = {"verifier": fields["agent_type"], "agent_id": fields.get("agent_id")}
        if fields["hook_event_name"] == "SubagentStart":
            emit("verifier_start", **common)
        elif fields["hook_event_name"] == "SubagentStop":
            report = str(fields.get("last_assistant_message") or "")
            emit("verifier_end", **common, checks=parse_checks(report), report=report)
        return {}

    return {
        "SubagentStart": [HookMatcher(hooks=[record])],
        "SubagentStop": [HookMatcher(hooks=[record])],
    }
