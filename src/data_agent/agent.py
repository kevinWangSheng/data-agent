"""One `data-agent run`: a single Claude Agent SDK `query()` (ADR-0001), recorded as an event file.

The model is whatever `DATA_AGENT_*` names; those values go only to the SDK's subprocess, mapped to
the `ANTHROPIC_*` variables it reads, so the user's own Claude Code setup is untouched.
"""

import hashlib
import subprocess
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import claude_agent_sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

# Not re-exported by the SDK; the pinned SDK version fixes where it lives.
from claude_agent_sdk._cli_version import __cli_version__
from claude_agent_sdk.types import McpStdioServerConfig

from data_agent.events import EventLog, known_secrets
from data_agent.run_config import MAX_TURNS, RunConfig

DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "deepseek-flash"

REPO_ROOT = Path(__file__).resolve().parents[2]
# A wheel ships the Skills inside the package (pyproject force-include); a checkout has them at the
# repo root.
PACKAGED_SKILLS = Path(__file__).resolve().parent / "skills"
SKILLS_DIR = PACKAGED_SKILLS if PACKAGED_SKILLS.is_dir() else REPO_ROOT / "skills"
QUERY_SKILLS = ("sql-workflow", "duckdb-sql")
# Written into a wheel at build time (hatch_build.py); absent in a source checkout.
BUILD_COMMIT = Path(__file__).resolve().parent / "BUILD_COMMIT"

# The MCP server's name in the SDK session; its tools appear as mcp__data__<tool>.
MCP_SERVER = "data"
QUERY_TOOL = f"mcp__{MCP_SERVER}__query_database"
# Query tasks only read: the data tools plus reading the workspace. No Bash, Write or Edit, so the
# read-only gateway is the only way to the data.
QUERY_BUILTIN_TOOLS = ["Read", "Glob", "Grep"]

SYSTEM_PROMPT = """\
You are a data agent. You answer questions about a user's data by exploring the database and \
running SQL, then reply with the answer.

All database access goes through the `{server}` MCP tools, which are read-only. Work only from \
what the tools show you; do not guess table names, column names or values.

Follow these skills.

{skills}
"""

QUERY_PROMPT = """\
Task type: query
Database: DuckDB, reachable only through the `{server}` MCP tools.
Workspace (your working directory): {workspace}

<task>
{task}
</task>

This run has no file-writing tools, so do not save result.sql or result.csv. End with a final \
message that gives the answer first, then the assumptions you made.
"""


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    cached_input: float
    output: float


# ADR-0001 Update (official pages, 2026-10-05), upper end of each range so spend is never
# understated. DeepSeek bills cache writes as plain input. deepseek-v4-pro has no published
# cache-read price there, so cache reads are charged at its input price.
PRICES = {
    "deepseek-flash": Price(input=0.30, cached_input=0.006, output=1.2),
    "deepseek-v4-pro": Price(input=1.32, cached_input=1.32, output=3.96),
}


class ConfigError(Exception):
    """The run cannot start: a missing setting or input. Nothing has been recorded."""


@dataclass(frozen=True)
class Task:
    text: str
    type: Literal["dbt", "query"]
    workspace: Path
    duckdb: Path


@dataclass(frozen=True)
class Outcome:
    """`finished` is false when the agent stopped early; `infra_error` when the run itself broke."""

    answer: str
    finished: bool
    infra_error: bool
    errors: list[str]
    events_path: Path


@dataclass(frozen=True)
class ModelSettings:
    base_url: str
    api_key: str
    model: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "ModelSettings":
        api_key = env.get("DATA_AGENT_API_KEY", "")
        if not api_key:
            raise ConfigError("DATA_AGENT_API_KEY is not set")
        return cls(
            base_url=env.get("DATA_AGENT_BASE_URL") or DEFAULT_BASE_URL,
            api_key=api_key,
            model=env.get("DATA_AGENT_MODEL") or DEFAULT_MODEL,
        )

    def sdk_env(self) -> dict[str, str]:
        """Variables for the SDK subprocess only; they override what it inherits."""
        return {
            "ANTHROPIC_BASE_URL": self.base_url,
            "ANTHROPIC_AUTH_TOKEN": self.api_key,
            # Blank, so an inherited Anthropic key or login is never sent to another endpoint.
            "ANTHROPIC_API_KEY": "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            # Every model slot, so background calls do not ask the endpoint for a Claude model.
            "ANTHROPIC_MODEL": self.model,
            "ANTHROPIC_SMALL_FAST_MODEL": self.model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": self.model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": self.model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": self.model,
            "CLAUDE_CODE_SUBAGENT_MODEL": self.model,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }


def skill_version() -> str:
    """Hash of everything under skills/: any Skill edit changes it."""
    digest = hashlib.sha256()
    for path in sorted(p for p in SKILLS_DIR.rglob("*") if p.is_file()):
        digest.update(path.relative_to(SKILLS_DIR).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def git_commit() -> str:
    """The agent's own commit, with `-dirty` when the checkout had uncommitted changes. A wheel
    carries the commit it was built from; a source checkout asks git."""
    if BUILD_COMMIT.is_file():
        return BUILD_COMMIT.read_text().strip() or "unknown"
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "describe", "--always", "--dirty", "--abbrev=40"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError:
        return "unknown"
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else "unknown"


def load_skills(names: tuple[str, ...]) -> str:
    """Skills go into the system prompt, so the workflow is loaded before the first turn instead
    of depending on the model choosing to call a Skill tool."""
    return "\n\n".join((SKILLS_DIR / name / "SKILL.md").read_text() for name in names)


def build_options(
    task: Task, config: RunConfig, settings: ModelSettings, stderr: list[str]
) -> ClaudeAgentOptions:
    server: McpStdioServerConfig = {
        "type": "stdio",
        "command": sys.executable,
        "args": [
            *("-m", "data_agent", "mcp-server"),
            *("--config", config.name, "--duckdb", str(task.duckdb.resolve())),
        ],
    }
    return ClaudeAgentOptions(
        model=settings.model,
        env=settings.sdk_env(),
        system_prompt=SYSTEM_PROMPT.format(server=MCP_SERVER, skills=load_skills(QUERY_SKILLS)),
        cwd=str(task.workspace.resolve()),
        tools=QUERY_BUILTIN_TOOLS,
        allowed_tools=[*QUERY_BUILTIN_TOOLS, f"mcp__{MCP_SERVER}"],
        # Anything not allowed above is denied rather than prompted for.
        permission_mode="dontAsk",
        mcp_servers={MCP_SERVER: server},
        strict_mcp_config=True,
        # ADR-0001: user settings only, never this repo's .claude/; Skills come from the prompt.
        setting_sources=["user"],
        skills=[],
        max_turns=MAX_TURNS[task.type],
        max_budget_usd=config.max_budget_usd,
        stderr=stderr.append,
    )


def events_file(events_dir: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return events_dir / f"run-{stamp}-{uuid.uuid4().hex[:8]}.jsonl"


def record_message(log: EventLog, message: Any, tools: dict[str, str]) -> None:
    """Translate one SDK message into events. `tools` maps tool_use ids to tool names."""
    if isinstance(message, SystemMessage) and message.subtype == "init":
        log.emit(
            "status",
            state="started",
            model=message.data.get("model"),
            tools=message.data.get("tools"),
            mcp_servers=message.data.get("mcp_servers"),
        )
    elif isinstance(message, AssistantMessage):
        if message.error:
            log.emit("error", source="model", error=message.error)
        for block in message.content:
            if isinstance(block, TextBlock):
                log.emit("text", text=block.text)
            elif isinstance(block, ThinkingBlock):
                log.emit("thinking", text=block.thinking)
            elif isinstance(block, ToolUseBlock):
                tools[block.id] = block.name
                log.emit("tool_start", tool=block.name, tool_use_id=block.id, input=block.input)
                if block.name == QUERY_TOOL:
                    log.emit("sql", tool_use_id=block.id, sql=block.input.get("sql"))
    elif isinstance(message, UserMessage) and isinstance(message.content, list):
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                log.emit(
                    "tool_end",
                    tool=tools.get(block.tool_use_id),
                    tool_use_id=block.tool_use_id,
                    is_error=bool(block.is_error),
                    content=block.content,
                )


def result_fields(message: ResultMessage | None, model: str) -> dict[str, Any]:
    """Token counts and cost from the SDK's ResultMessage. Uncached input counts the tokens
    written to the cache too, since those are billed as fresh input.

    The SDK's own `total_cost_usd` (kept as `sdk_cost_usd`) is priced at Claude rates even for a
    non-Claude model, so `cost_usd` prices the tokens from PRICES instead. A model missing there
    (Claude among them, which the SDK prices correctly) falls back to the SDK figure, and
    `cost_basis` says which one was used. `max_budget_usd` still caps the SDK figure.
    """
    if message is None:
        return {
            "subtype": None,
            "is_error": True,
            "num_turns": 0,
            "terminal_reason": None,
            "uncached_input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "sdk_cost_usd": 0.0,
            "cost_basis": "sdk",
            "errors": [],
        }
    usage = message.usage or {}
    uncached = int(usage.get("input_tokens") or 0) + int(
        usage.get("cache_creation_input_tokens") or 0
    )
    cached = int(usage.get("cache_read_input_tokens") or 0)
    output = int(usage.get("output_tokens") or 0)
    sdk_cost = message.total_cost_usd or 0.0
    price = PRICES.get(model)
    if price is None:
        cost, basis = sdk_cost, "sdk"
    else:
        tokens = uncached * price.input + cached * price.cached_input + output * price.output
        cost, basis = tokens / 1_000_000, "price_table"
    return {
        "subtype": message.subtype,
        "is_error": message.is_error,
        "num_turns": message.num_turns,
        "terminal_reason": message.terminal_reason,
        "uncached_input_tokens": uncached,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "cost_usd": cost,
        "sdk_cost_usd": sdk_cost,
        "cost_basis": basis,
        "errors": message.errors or [],
    }


async def run(task: Task, config: RunConfig, events_dir: Path, env: Mapping[str, str]) -> Outcome:
    if task.type != "query":
        raise ConfigError(f"task type {task.type!r} is not supported yet; only 'query' runs")
    settings = ModelSettings.from_env(env)
    events_dir.mkdir(parents=True, exist_ok=True)
    path = events_file(events_dir)
    stderr: list[str] = []
    final: ResultMessage | None = None
    with EventLog(path, secrets=known_secrets(env)) as log:
        log.emit(
            "version",
            git_commit=git_commit(),
            skill_version=skill_version(),
            config=config.name,
            model=settings.model,
            sdk_version=claude_agent_sdk.__version__,
            cli_version=__cli_version__,
        )
        prompt = QUERY_PROMPT.format(
            server=MCP_SERVER, workspace=task.workspace.resolve(), task=task.text
        )
        tools: dict[str, str] = {}
        try:
            async for message in query(
                prompt=prompt, options=build_options(task, config, settings, stderr)
            ):
                if isinstance(message, ResultMessage):
                    final = message
                else:
                    record_message(log, message, tools)
        except Exception as exc:
            # A failed run ends with a ResultMessage and then a ResultError, whose details the
            # result event already holds. Anything else (the SDK also raises plain Exceptions,
            # e.g. an `initialize` timeout) means the run itself broke.
            if final is None:
                log.emit("error", source="sdk", error=str(exc), stderr_tail=stderr[-20:])
        log.emit("result", **result_fields(final, settings.model))
    # Connection failures and timeouts end with an api_error that has no HTTP status.
    infra_error = (
        final is None or final.api_error_status is not None or final.terminal_reason == "api_error"
    )
    finished = final is not None and final.subtype == "success" and not final.is_error
    return Outcome(
        answer=(final.result or "") if final else "",
        finished=finished and not infra_error,
        infra_error=infra_error,
        errors=(final.errors or []) if final else ["the agent SDK failed before the run ended"],
        events_path=path,
    )
