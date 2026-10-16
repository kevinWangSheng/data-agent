"""One `data-agent run`: a single Claude Agent SDK `query()` (ADR-0001), recorded as an event file.

The model is whatever `DATA_AGENT_*` names; those values go only to the SDK's subprocess, mapped to
the `ANTHROPIC_*` variables it reads, so the user's own Claude Code setup is untouched.
"""

import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncGenerator, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast

import claude_agent_sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

# Not re-exported by the SDK; the pinned SDK version fixes where they live.
from claude_agent_sdk._cli_version import __cli_version__
from claude_agent_sdk._internal.client import InternalClient
from claude_agent_sdk.types import HookEvent, McpStdioServerConfig

from data_agent.command_guard import command_guard
from data_agent.events import EventLog, known_secrets
from data_agent.run_config import REPO_ROOT, RunConfig, skills_path
from data_agent.verifiers import AGENT_TOOL, VERIFIERS_PROMPT, Emit, verifier_agents, verifier_hooks
from data_agent.workspace_guard import GUARDED_TOOLS, WRITE_TOOLS, workspace_guard

DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "deepseek-flash"

# The current Skills; a run config may name another version (RunConfig.skills_dir).
SKILLS_DIR = skills_path("skills")
QUERY_SKILLS = ("sql-workflow", "duckdb-sql")
# dbt-workflow first (plan §3); its Step 2 Skills are preloaded with it, as there is no Skill tool.
DBT_SKILLS = ("dbt-workflow", "dbt-write", "dbt-debugging", "duckdb-sql")
# The scripts dbt-workflow runs; its text names them through ${CLAUDE_SKILL_DIR}.
DBT_SCRIPTS = ("scan_project.py", "validate_project.py")
# Written into a wheel at build time (hatch_build.py); absent in a source checkout.
BUILD_COMMIT = Path(__file__).resolve().parent / "BUILD_COMMIT"

# The MCP server's name in the SDK session; its tools appear as mcp__data__<tool>.
MCP_SERVER = "data"
QUERY_TOOL = f"mcp__{MCP_SERVER}__query_database"
# Query tasks only read: the data tools plus reading the workspace. No Bash, Write or Edit, so the
# read-only gateway is the only way to the data; the workspace guard keeps the file tools in it.
QUERY_BUILTIN_TOOLS: list[str] = list(GUARDED_TOOLS)
# dbt tasks also edit the project and run dbt (CONTEXT: dbt write). The workspace guard keeps Write
# and Edit in the workspace and off the database file; the command guard lets Bash run only dbt and
# the dbt-workflow scripts, so reads still go through the gateway.
DBT_BUILTIN_TOOLS: list[str] = [*GUARDED_TOOLS, *WRITE_TOOLS, "Bash"]

SYSTEM_PROMPT = """\
You are a data agent. You answer questions about a user's data by exploring the database and \
running SQL, then reply with the answer.

All database access goes through the `{server}` MCP tools, which are read-only. Work only from \
what the tools show you; do not guess table names, column names or values.
"""

# Appended only when the run config turns the Skill layer on.
SKILLS_PROMPT = """
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

End with a final message that gives the answer first, then the assumptions you made.
"""

DBT_SYSTEM_PROMPT = """\
You are a data agent. You build and fix the models of a user's dbt project, materialize them with \
`dbt run`, and check the results.

All database access goes through the `{server}` MCP tools, which are read-only. The only way to \
change data is to edit the dbt project and run dbt. Work only from what the tools and the project \
files show you; do not guess table names, column names or values.
"""

DBT_PROMPT = """\
Task type: dbt
Database: DuckDB. dbt writes to it through the project's profile; you read it only through the \
`{server}` MCP tools.
dbt project (your working directory): {workspace}
Bash runs only dbt commands{scripts}, from the working directory.

<task>
{task}
</task>

End with a final message that lists the models you created or changed, how the last dbt run went, \
then the assumptions you made.
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

# What the bundled CLI charges a model it has no price for: its fallback table ($5 input, $25
# output, $6.25 5-minute cache write, $0.50 cache read per 1M, in the CLI's own source; ticket 09
# measured SDK ~$1 for ~$0.035 at PRICES). Uncached input takes the cache-write rate, the higher
# of the two rates it covers.
SDK_UNKNOWN_MODEL_PRICE = Price(input=6.25, cached_input=0.5, output=25)


def priced_usd(price: Price, uncached: int, cached: int, output: int) -> float:
    return (uncached * price.input + cached * price.cached_input + output * price.output) / 1e6


def sdk_budget_usd(budget: float, model: str) -> float:
    """The cap handed to the SDK. A priced model is capped by `Spend` at our prices; the SDK's cap
    is then a backstop, scaled by the largest ratio of the SDK's rate to ours, so the SDK's figure
    cannot reach it while ours is under `budget`. A model without a price keeps the SDK's cap."""
    price = PRICES.get(model)
    if price is None:
        return budget
    sdk = SDK_UNKNOWN_MODEL_PRICE
    return budget * max(
        sdk.input / price.input, sdk.cached_input / price.cached_input, sdk.output / price.output
    )


class Tokens(NamedTuple):
    """Tokens of one model call, or of several summed."""

    uncached: int  # input, including tokens written to the cache (billed as fresh input)
    cached: int
    output: int


def usage_tokens(usage: Mapping[str, Any]) -> Tokens:
    """Uncached input counts the tokens written to the cache too, billed as fresh input."""
    return Tokens(
        int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0),
        int(usage.get("cache_read_input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
    )


class TranscriptError(Exception):
    """A subagent transcript is missing or not in the line format `transcript_usage` reads."""


class TranscriptUnfinished(TranscriptError):
    """The transcript's last call has no final usage yet: the CLI is still writing it."""


# How long the SubagentStop hook waits for the CLI to finish writing the transcript; the final
# usage line of the subagent's last call is written after the hook is called (ticket 15 run).
TRANSCRIPT_WAIT_S = 5.0


def transcript_usage(path: Path) -> dict[str, Tokens]:
    """The model calls in a subagent's transcript (the CLI's `subagents/agent-<id>.jsonl`), by
    message id.

    The format is the CLI's own and undocumented (CLI 2.1.301; a real sample is
    tests/fixtures/subagent_transcript.jsonl): one JSON object per line; a model call is one
    `"type": "assistant"` line per content block, each with `message.id` and `message.usage`, the
    last with the call's final usage and its `message.stop_reason` (the earlier ones have no
    output yet and a null stop_reason).
    """
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        raise TranscriptError(f"cannot read the subagent transcript {path}: {exc}") from exc
    calls: dict[str, Tokens] = {}
    finished: dict[str, bool] = {}
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if entry.get("type") != "assistant":
                continue
            message = entry["message"]
            usage = message["usage"]
            if not {"input_tokens", "output_tokens"} <= usage.keys():
                raise KeyError("input_tokens/output_tokens")
            calls[str(message["id"])] = usage_tokens(usage)
            finished[str(message["id"])] = message.get("stop_reason") is not None
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise TranscriptError(f"subagent transcript {path} line {number}: {exc!r}") from exc
    if not calls:
        raise TranscriptError(f"no model calls in the subagent transcript {path}")
    if not all(finished.values()):
        raise TranscriptUnfinished(f"the subagent transcript {path} has an unfinished call")
    return calls


async def finished_transcript_usage(path: Path) -> dict[str, Tokens]:
    """`transcript_usage` once the CLI has finished writing the transcript."""
    deadline = time.monotonic() + TRANSCRIPT_WAIT_S
    while True:
        try:
            return transcript_usage(path)
        except TranscriptUnfinished:
            if time.monotonic() >= deadline:
                raise
        await asyncio.sleep(0.05)


@dataclass
class Spend:
    """A run's spend at PRICES while it runs, capped at the run config's `max_budget_usd`.

    Main-thread calls are priced from the SDK's partial messages (`include_partial_messages`):
    message_start, then message_delta with the call's final usage. The per-block
    AssistantMessages carry the usage from the start of the response, with no output.

    Subagents (the verifiers) stream no partial messages and only some of their calls, so each is
    priced when it stops, from the transcript the CLI wrote (`hooks`). A transcript that
    cannot be read is recorded as an error event and turns this cap off: what is left to count
    would understate the spend, so the run ends through the SDK, under the SDK's cap, and its
    result prices the SDK's `model_usage` (`result_fields`), as every run the SDK ends does.

    The cap is checked only between two main-thread calls, after a call that used tools, and not
    while a subagent runs: a run may pass it by its last main call and the subagents it started
    (human decision 2026-10-16), and a final answer that passes it is kept.
    """

    price: Price
    budget: float
    main: dict[str, Tokens] = field(default_factory=dict)
    subagents: dict[str, Tokens] = field(default_factory=dict)  # from transcripts
    transcript_failed: bool = False
    running: set[str] = field(default_factory=set)  # subagents started and not yet stopped
    # After a main call that used tools, until the next starts: the cap may be checked then.
    between_calls: bool = False
    stopped: bool = False
    # The main call in progress: its id, its usage at message_start, whether it used tools.
    _call: str | None = None
    _call_start: dict[str, Any] = field(default_factory=dict)
    _call_uses_tools: bool = False

    def observe(self, message: Any) -> None:
        if isinstance(message, StreamEvent) and message.parent_tool_use_id is None:
            event = message.event
            if event.get("type") == "message_start":
                self._call = str(event["message"]["id"])
                self._call_start = dict(event["message"].get("usage") or {})
                self._call_uses_tools = False
                self.between_calls = False
                self.running.clear()  # the main thread waits for its subagents
                self.main[self._call] = usage_tokens(self._call_start)
            elif event.get("type") == "message_delta" and self._call is not None:
                final = {k: v for k, v in (event.get("usage") or {}).items() if v is not None}
                self.main[self._call] = usage_tokens(self._call_start | final)
                self.between_calls = self._call_uses_tools
        elif isinstance(message, AssistantMessage) and message.parent_tool_use_id is None:
            if any(isinstance(block, ToolUseBlock) for block in message.content):
                self._call_uses_tools = True

    def hooks(self, emit: Emit) -> dict[HookEvent, list[HookMatcher]]:
        """Hooks that follow the subagents and price each one from its transcript when it
        stops."""

        async def start(
            hook_input: HookInput, tool_use_id: str | None, context: HookContext
        ) -> HookJSONOutput:
            self.running.add(str(dict(hook_input).get("agent_id")))
            return {}

        async def stop(
            hook_input: HookInput, tool_use_id: str | None, context: HookContext
        ) -> HookJSONOutput:
            fields = dict(hook_input)
            path = fields.get("agent_transcript_path")
            try:
                if not isinstance(path, str) or not path:
                    raise TranscriptError("SubagentStop gave no agent_transcript_path")
                self.subagents |= await finished_transcript_usage(Path(path))
            except TranscriptError as exc:
                self.transcript_failed = True
                emit(
                    "error",
                    source="spend",
                    error=f"{exc}; the price-table cap is off for the rest of the run, which "
                    "ends through the SDK (its cap still on) and is priced from its model_usage",
                )
            finally:
                self.running.discard(str(fields.get("agent_id")))
            return {}

        return {
            "SubagentStart": [HookMatcher(hooks=[start])],
            "SubagentStop": [HookMatcher(hooks=[stop])],
        }

    def tokens(self) -> Tokens:
        calls = (self.subagents | self.main).values()
        return Tokens(
            sum(c.uncached for c in calls),
            sum(c.cached for c in calls),
            sum(c.output for c in calls),
        )

    def cost_usd(self) -> float:
        return priced_usd(self.price, *self.tokens())

    def check(self) -> bool:
        """Whether the run is to stop now: the cap may be checked and spend is over it."""
        if self.transcript_failed or self.running or not self.between_calls:
            return False
        self.stopped = self.cost_usd() > self.budget
        return self.stopped

    def result_fields(self) -> dict[str, Any]:
        """The result event of a run this cap stopped; the SDK never sent its own."""
        uncached, cached, output = self.tokens()
        cost = self.cost_usd()
        return {
            "subtype": "error_max_budget_usd",
            "is_error": True,
            "num_turns": len(self.main),
            "terminal_reason": "budget_exhausted",
            "uncached_input_tokens": uncached,
            "cached_input_tokens": cached,
            "output_tokens": output,
            "cost_usd": cost,
            "sdk_cost_usd": None,
            "cost_basis": "price_table",
            "errors": [f"spend ${cost:.4f} at the price table passed the ${self.budget} budget"],
        }


def spend_cap(model: str, budget: float) -> Spend | None:
    """The price-table cap for a priced model; a model without a price keeps the SDK's cap."""
    price = PRICES.get(model)
    return None if price is None else Spend(price, budget)


async def query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncGenerator[Any]:
    """`claude_agent_sdk.query`, except that closing it closes the SDK session (and its CLI
    subprocess) before `aclose` returns. The SDK's own `query` does not close the session's
    generator, which leaves that to the event loop's async-generator finalizer, later."""
    session = InternalClient().process_query(prompt=prompt, options=options)
    async with aclosing(cast(AsyncGenerator[Any], session)) as messages:
        async for message in messages:
            yield message


async def capped(messages: AsyncGenerator[Any], spend: Spend | None) -> AsyncGenerator[Any]:
    """The SDK's messages, ending early (`spend.stopped`) once `spend` passes its cap. Each check
    comes after the caller has handled the message. Closing the SDK's stream ends its
    subprocess, so nothing more is spent."""
    async with aclosing(messages) as stream:
        async for message in stream:
            if spend is not None:
                spend.observe(message)
            yield message
            if spend is not None and not isinstance(message, ResultMessage) and spend.check():
                return


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
            # Subagents (the verifiers) run in the foreground. Launched in the background, they
            # end the main turn before their reports are in and split the run into several results.
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
        }


def skill_version(skills_dir: Path) -> str:
    """Hash of everything under a Skill directory: any Skill edit changes it."""
    digest = hashlib.sha256()
    for path in sorted(p for p in skills_dir.rglob("*") if p.is_file()):
        digest.update(path.relative_to(skills_dir).as_posix().encode())
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


def load_skills(names: tuple[str, ...], skills_dir: Path) -> str:
    """Skills go into the system prompt, so the workflow is loaded before the first turn instead
    of depending on the model choosing to call a Skill tool. `${CLAUDE_SKILL_DIR}` becomes the
    Skill's own directory, as the Skill tool would set it."""
    return "\n\n".join(
        (skills_dir / name / "SKILL.md")
        .read_text()
        .replace("${CLAUDE_SKILL_DIR}", str(skills_dir / name))
        for name in names
    )


def dbt_scripts(config: RunConfig) -> tuple[Path, ...]:
    """The dbt-workflow scripts Bash may run: part of the Skill layer, so none when it is off."""
    if not config.skill:
        return ()
    return tuple(config.skills_dir / "dbt-workflow" / name for name in DBT_SCRIPTS)


def system_prompt(task_type: str, config: RunConfig) -> str:
    base, names = (
        (DBT_SYSTEM_PROMPT, DBT_SKILLS) if task_type == "dbt" else (SYSTEM_PROMPT, QUERY_SKILLS)
    )
    prompt = base.format(server=MCP_SERVER)
    if config.skill:
        prompt += SKILLS_PROMPT.format(skills=load_skills(names, config.skills_dir))
    if uses_verifiers(task_type, config):
        prompt += VERIFIERS_PROMPT
    return prompt


def uses_verifiers(task_type: str, config: RunConfig) -> bool:
    """The verifier subagents check dbt work only; query runs use sql-workflow's own checks."""
    return task_type == "dbt" and config.verifiers


def build_options(
    task: Task,
    config: RunConfig,
    settings: ModelSettings,
    stderr: list[str],
    emit: Emit,
    spend: Spend | None = None,
) -> ClaudeAgentOptions:
    """`emit` records events from hooks (the verifiers' start and end). With `spend` (the
    price-table cap, which the caller enforces with `capped`), the SDK streams what it needs and
    its own cap becomes the backstop (`sdk_budget_usd`)."""
    server: McpStdioServerConfig = {
        "type": "stdio",
        "command": sys.executable,
        "args": [
            *("-m", "data_agent", "mcp-server"),
            *("--config", config.name, "--duckdb", str(task.duckdb.resolve())),
            # A run without the verifier subagents gets no verifier tools either (the ablation).
            *(() if uses_verifiers(task.type, config) else ("--no-verifier-tools",)),
            *("--workspace", str(task.workspace.resolve())),
        ],
    }
    if task.type == "dbt":
        tools = DBT_BUILTIN_TOOLS
        file_tools = (*GUARDED_TOOLS, *WRITE_TOOLS)
        bash_guard = command_guard(task.workspace, dbt_scripts(config))
        hooks = [HookMatcher(matcher="Bash", hooks=[bash_guard])]
    else:
        tools, file_tools, hooks = QUERY_BUILTIN_TOOLS, GUARDED_TOOLS, []
    guard = workspace_guard(task.workspace, protected=(task.duckdb,))
    hooks.append(HookMatcher(matcher="|".join(file_tools), hooks=[guard]))
    all_hooks: dict[HookEvent, list[HookMatcher]] = {"PreToolUse": hooks}
    agents = None
    if uses_verifiers(task.type, config):
        tools = [*tools, AGENT_TOOL]
        agents = verifier_agents(MCP_SERVER)
        all_hooks |= verifier_hooks(emit)
    if spend is not None:
        for event, matchers in spend.hooks(emit).items():
            all_hooks.setdefault(event, []).extend(matchers)
    return ClaudeAgentOptions(
        model=settings.model,
        env=settings.sdk_env(),
        system_prompt=system_prompt(task.type, config),
        cwd=str(task.workspace.resolve()),
        tools=tools,
        allowed_tools=[*tools, f"mcp__{MCP_SERVER}"],
        # Anything not allowed above is denied rather than prompted for.
        permission_mode="dontAsk",
        hooks=all_hooks,
        agents=agents,
        mcp_servers={MCP_SERVER: server},
        strict_mcp_config=True,
        # ADR-0001: user settings only, never this repo's .claude/; Skills come from the prompt.
        setting_sources=["user"],
        skills=[],
        max_turns=config.max_turns[task.type],
        max_budget_usd=(
            config.max_budget_usd
            if spend is None
            else sdk_budget_usd(config.max_budget_usd, settings.model)
        ),
        include_partial_messages=spend is not None,
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
    `cost_basis` says which one was used. `max_budget_usd` caps `cost_usd` (`Spend`).
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
    if message.model_usage:
        # Per model, for the whole session: subagents (the verifiers) included, unlike `usage`.
        models = message.model_usage.values()
        uncached = sum(m["inputTokens"] + m["cacheCreationInputTokens"] for m in models)
        cached = sum(m["cacheReadInputTokens"] for m in models)
        output = sum(m["outputTokens"] for m in models)
    else:
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
        cost, basis = priced_usd(price, uncached, cached, output), "price_table"
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
    if task.type == "dbt" and shutil.which("dbt", path=env.get("PATH")) is None:
        raise ConfigError("dbt is not on PATH; dbt tasks run the dbt the environment provides")
    settings = ModelSettings.from_env(env)
    events_dir.mkdir(parents=True, exist_ok=True)
    path = events_file(events_dir)
    stderr: list[str] = []
    final: ResultMessage | None = None
    spend = spend_cap(settings.model, config.max_budget_usd)
    with EventLog(path, secrets=known_secrets(env)) as log:
        log.emit(
            "version",
            git_commit=git_commit(),
            skill_version=skill_version(config.skills_dir) if config.skill else "none",
            config=config.name,
            layers=config.layers(),
            model=settings.model,
            sdk_version=claude_agent_sdk.__version__,
            cli_version=__cli_version__,
        )
        prompt = (DBT_PROMPT if task.type == "dbt" else QUERY_PROMPT).format(
            server=MCP_SERVER,
            workspace=task.workspace.resolve(),
            task=task.text,
            scripts=" and the dbt-workflow scripts" if dbt_scripts(config) else "",
        )
        tools: dict[str, str] = {}
        options = build_options(task, config, settings, stderr, log.emit, spend)
        try:
            stream = query(prompt=prompt, options=options)
            async with aclosing(capped(stream, spend)) as messages:
                async for message in messages:
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
        stopped = spend.result_fields() if spend is not None and spend.stopped else None
        log.emit("result", **(stopped or result_fields(final, settings.model)))
    if stopped is not None:
        return Outcome(
            answer="", finished=False, infra_error=False, errors=stopped["errors"], events_path=path
        )
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
