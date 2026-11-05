"""The verifier subagent (ADR-0002, ADR-0009): one read-only auditor, defined with the SDK's
`agents=` from the prompt in `agents/verifier.md`, which works through the v2 tools
(`model_diff`, `contract_check`, `grain_facts`, `compare_by_key`) and judges only against the
task text.

It is offered only to dbt runs whose config turns the verifier layer on (query runs use
sql-workflow's own checks). A SubagentStart hook records each call's start as a `verifier_start`
event; its end, with the report parsed and the turns counted, is `verifier_v2.hooks`'s
`verifier_end`. The FAIL and NEEDS_INFO rules for the main agent live in `VERIFIERS_PROMPT` and
nowhere else (the Skill's Step 8 refers to it).
"""

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
VERIFIER_NAMES = ("verifier",)
# The Agent tool (Task before Claude Code 2.1) that dispatches subagents.
AGENT_TOOL = "Agent"
READ_TOOLS = ("Read", "Glob", "Grep")
# The verifier's turn cap (spec "验证器"); `verifier_v2.hooks` reads a call that reaches it as
# NEEDS_INFO.
VERIFIER_MAX_TURNS = 50
# MCP tools per verifier, by the server's tool names; the steps in the prompt name these.
DATA_TOOLS = {
    "verifier": (
        "list_tables",
        "query_database",
        "model_diff",
        "contract_check",
        "grain_facts",
        "compare_by_key",
    ),
}
# Ends the verifier's prompt in a run with a task file; the prompt's "Task file" section says how
# to use it.
TASK_FILE_LINE = "\n\nTask file (read it first, with Read): `{path}`\n"

# Appended to a dbt run's system prompt when the verifier layer is on, so the protocol holds with
# or without the Skill layer. The only place the FAIL and NEEDS_INFO rules are written.
VERIFIERS_PROMPT = """
## Verification before delivery
Before your final message, dispatch the `verifier` agent with the Agent tool. It is read-only \
and judges against the task text, which it reads itself. The harness replaces your first \
dispatch message with the task file, the project directory and the models `model_diff` lists \
as new or changed, so write only a short request. Do not tell it what the columns mean or how \
you wrote the SQL; `technical_spec.md` is your intent only and the verifier does not need it. \
The run cannot end while a model you created or changed has no verifier report, was changed \
after its last report, or still has a FAIL.

Its report gives every check (CHECK 1-7) of every model one of PASS, FAIL, NEEDS_INFO or N/A.
- FAIL: first reproduce the failure yourself with one read-only query that shows the defect. \
If it reproduces, make the smallest change that fixes it, rebuild only the changed models with \
dbt, and dispatch the verifier again. If it does not reproduce, dispatch the verifier again and \
put the counter-evidence (the query and its result) in your dispatch message.
- NEEDS_INFO: give the verifier the information it asked for and dispatch it again, or run \
that check yourself, read-only.
- Stop when every check is PASS or N/A, or when your turns run out. Do not change models after \
that.
"""

Emit = Callable[..., None]


def load_definition(name: str, server: str, task_file: Path | None = None) -> AgentDefinition:
    """One verifier from its markdown file: frontmatter description, body as the prompt, and the
    path of the run's task file (`agent.saved_task`), which the prompt's checks judge against."""
    _, frontmatter, body = (AGENTS_DIR / f"{name}.md").read_text().split("---\n", 2)
    fields = dict(line.split(":", 1) for line in frontmatter.splitlines() if ":" in line)
    prompt = body.strip()
    if task_file is not None:
        prompt += TASK_FILE_LINE.format(path=task_file)
    return AgentDefinition(
        description=fields["description"].strip().strip('"'),
        prompt=prompt,
        tools=[*READ_TOOLS, *(f"mcp__{server}__{tool}" for tool in DATA_TOOLS[name])],
        model="inherit",
        maxTurns=VERIFIER_MAX_TURNS,
    )


def verifier_agents(server: str, task_file: Path | None = None) -> dict[str, AgentDefinition]:
    return {name: load_definition(name, server, task_file) for name in VERIFIER_NAMES}


def verifier_hooks(emit: Emit) -> dict[HookEvent, list[HookMatcher]]:
    """The hook that records a verifier's start; other subagents leave no event."""

    async def record(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        fields: dict[str, Any] = dict(hook_input)
        if fields.get("agent_type") in VERIFIER_NAMES:
            emit("verifier_start", verifier=fields["agent_type"], agent_id=fields.get("agent_id"))
        return {}

    return {"SubagentStart": [HookMatcher(hooks=[record])]}
