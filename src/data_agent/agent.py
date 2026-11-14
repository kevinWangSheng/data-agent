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
import tempfile
import time
import uuid
from collections.abc import AsyncGenerator, Iterator, Mapping
from contextlib import aclosing, contextmanager, nullcontext
from dataclasses import asdict, dataclass, field
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

from data_agent import verifier_v2
from data_agent.command_guard import command_guard
from data_agent.context_tools import DictionaryBuild, build_interruptible
from data_agent.events import EventLog, known_secrets, url_names, url_secrets
from data_agent.gateway import TIMEOUT_SECONDS, DuckDBGateway, Gateway, PostgresGateway
from data_agent.run_config import REPO_ROOT, RunConfig, skills_path
from data_agent.verifier_tools import SNAPSHOT_ENV
from data_agent.verifiers import (
    AGENT_TOOL,
    VERIFIER_NAMES,
    VERIFIERS_PROMPT,
    Emit,
    verifier_agents,
    verifier_hooks,
)
from data_agent.workspace_guard import GUARDED_TOOLS, WRITE_TOOLS, workspace_guard

DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "deepseek-flash"

# The current Skills; a run config may name another version (RunConfig.skills_dir).
SKILLS_DIR = skills_path("skills")
QUERY_SKILLS = ("sql-workflow", "duckdb-sql")
# dbt-workflow first (plan §3); its Step 2 Skills are preloaded with it, as there is no Skill tool.
DBT_SKILLS = ("dbt-workflow", "dbt-write", "dbt-debugging", "duckdb-sql")
# The lists above name DuckDB's dialect Skill; a run on another database swaps in its own.
DIALECT_SKILLS = {"duckdb": "duckdb-sql", "postgres": "postgres-sql"}
DATABASE_NAMES = {"duckdb": "DuckDB", "postgres": "PostgreSQL"}
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
# Environment variables Bash may print (`echo $VAR`, `printenv VAR`): the benchmark tasks ask the
# agent to print DB_TYPE first; no task asks for another.
PRINTABLE_VARIABLES = ("DB_TYPE",)
# Denied outright as well as left out of the tools: benchmark solutions are public, and the official
# data-eng-bench configs turn web lookup off the same way (`disallowed_tools: WebSearch,WebFetch`).
WEB_TOOLS: list[str] = ["WebSearch", "WebFetch"]

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

# The final-answer rule of query tasks, shared with the DAB runner's task prompt (ticket 68):
# correct answers were hard to read when another column sat between a name and its value, a name
# was cut short, or a table of other candidates stood beside the answer (ADR-0005 Update
# 2026-11-14). Both prompts go through `str.format`, so the rule holds no braces.
ANSWER_RULE = """\
End with a final message that gives the answer first. The answer holds only what the task asks \
for: no rows, columns or entities it did not ask about. Put each value right after its name, with \
no other column between them. Write names and text exactly as they appear in the data; do not \
shorten them or add an ellipsis. After the answer, state your assumptions in short sentences: no \
tables, no alternative answers or alternative methods.
"""

QUERY_PROMPT = (
    """\
Task type: query
Database: {database}, reachable only through the `{server}` MCP tools.
Workspace (your working directory): {workspace}
{turn_limit}

<task>
{task}
</task>

"""
    + ANSWER_RULE
)

# The query prompts' turn line (ticket 69); TurnBudget reminds the agent as the turns run out.
TURN_LIMIT = (
    "Turn limit: {max_turns}. Each response of yours that calls a tool uses one turn; once all "
    "{max_turns} are used, the run ends with no answer. A response with no tool calls is your "
    "final answer."
)

DBT_SYSTEM_PROMPT = """\
You are a data agent. You build and fix the models of a user's dbt project, materialize them with \
`dbt run`, and check the results.

All database access goes through the `{server}` MCP tools, which are read-only. The only way to \
change data is to edit the dbt project and run dbt. Work only from what the tools and the project \
files show you; do not guess table names, column names or values.
"""

DBT_PROMPT = """\
Task type: dbt
Database: {database}. dbt writes to it through the project's profile; you read it only through the \
`{server}` MCP tools.
Workspace (your working directory; the dbt project is this directory or one inside it): {workspace}
Bash runs only dbt commands{scripts}. When the project is in a subdirectory, run them as \
`cd <project_dir> && dbt ...` so that relative paths in the profile resolve from the project \
directory. Do not edit an existing `profiles.yml`; create one when the task asks for it. When the \
task asks you to print `DB_TYPE`, run `echo $DB_TYPE` as a Bash call of its own. List, read and \
search files with the Glob, Read and Grep tools, not `ls` or `cat`.

<task>
{task}
</task>
{task_file_note}
End with a final message that lists the models you created or changed, how the last dbt run went, \
then the assumptions you made.
"""


# Added to DBT_PROMPT only when the verifier layer is on (ticket 32); otherwise the prompt is the
# same as before, so runs without the layer stay comparable with earlier ones.
TASK_FILE_NOTE = """
The task above is also saved, read-only, at {path}. Open it with the Read tool (Glob and Bash \
cannot reach it), and re-read it there whenever you need its exact wording, for example after \
your context is compacted.
"""


@contextmanager
def run_directory(workspace: Path, prefix: str) -> Iterator[Path]:
    """A directory of this run's own outside the workspace (whose contents the grader reads),
    removed when the run ends. Raises ConfigError when the temp dir is the workspace or inside
    it (`--workspace /tmp`)."""
    base = Path(tempfile.gettempdir()).resolve()
    root = workspace.resolve()
    if base == root or root in base.parents:
        raise ConfigError(
            f"the temp dir {base} is inside the workspace {root}, so the run's files would be "
            "too; set TMPDIR to a directory outside the workspace"
        )
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@contextmanager
def saved_task(text: str, workspace: Path) -> Iterator[Path]:
    """The task text, byte for byte, in a read-only file of its own outside the workspace. The
    verifiers judge the models against it."""
    with run_directory(workspace, "data-agent-task-") as directory:
        path = directory / "task.md"
        path.write_bytes(text.encode())
        path.chmod(0o444)
        yield path


@contextmanager
def taken_snapshot(workspace: Path) -> Iterator[Path]:
    """The dbt projects' logic files as they are before the agent starts (ticket 45, spec
    "快照"), kept outside the workspace for the MCP tools; the agent's Read cannot reach it.
    `take_snapshot` is looked up on the package so a test can replace it."""
    with run_directory(workspace, "data-agent-snapshot-") as directory:
        yield verifier_v2.take_snapshot(workspace, directory / "snapshot")


@contextmanager
def prebuilt_dictionary(gateway: Gateway, workspace: Path, emit: Emit) -> Iterator[Path]:
    """The value dictionary (ticket 42), built before the agent starts and written as JSON
    outside the workspace for the MCP server to read; its coverage is recorded as the
    `dictionary` event. The build counts toward the run's time, not the agent's first tool
    call; a signal during it cancels the build and ends the run."""
    with run_directory(workspace, "data-agent-dictionary-") as directory:
        dictionary = build_interruptible(DictionaryBuild(gateway))
        coverage = dictionary.coverage
        emit(
            "dictionary",
            **{k: v for k, v in asdict(coverage).items() if k != "not_profiled"},
            tables_not_profiled=len(coverage.not_profiled),
        )
        path = directory / "dictionary.json"
        dictionary.save(path)
        yield path


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

    The verifiers stream no partial messages and only some of their calls, so each is priced
    when it stops, from the transcript the CLI wrote (`hooks`). The CLI's own subagents
    (activity summaries, compaction) are not counted here: a run the SDK ends prices them from
    `model_usage`, and its result says what this cap missed (`unmetered`). A transcript that
    cannot be read is recorded as an error event and turns this cap off: what is left to count
    would understate the spend, so the run ends through the SDK, under the SDK's cap, and its
    result prices the SDK's `model_usage` (`result_fields`), as every run the SDK ends does.

    The cap is checked only between two main-thread calls, after a call that used tools or hit
    its output limit, and not while a subagent runs: a run may pass it by its last main call and
    the subagents it started (human decision 2026-10-16), and a final answer that passes it is
    kept.
    """

    price: Price
    budget: float
    main: dict[str, Tokens] = field(default_factory=dict)
    subagents: dict[str, Tokens] = field(default_factory=dict)  # from transcripts
    transcript_failed: bool = False
    running: set[str] = field(default_factory=set)  # subagents started and not yet stopped
    # After a main call that used tools or hit its output limit, until the next starts: the cap
    # may be checked then.
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
                # A response cut at its output limit is no final answer: the CLI calls again.
                cut = (event.get("delta") or {}).get("stop_reason") == "max_tokens"
                self.between_calls = self._call_uses_tools or cut
        elif isinstance(message, AssistantMessage) and message.parent_tool_use_id is None:
            if any(isinstance(block, ToolUseBlock) for block in message.content):
                self._call_uses_tools = True

    def hooks(self, emit: Emit) -> dict[HookEvent, list[HookMatcher]]:
        """Hooks that follow the verifiers and price each one from its transcript when it
        stops."""

        async def start(
            hook_input: HookInput, tool_use_id: str | None, context: HookContext
        ) -> HookJSONOutput:
            fields = dict(hook_input)
            if fields.get("agent_type") in VERIFIER_NAMES:
                self.running.add(str(fields.get("agent_id")))
            return {}

        async def stop(
            hook_input: HookInput, tool_use_id: str | None, context: HookContext
        ) -> HookJSONOutput:
            fields = dict(hook_input)
            if fields.get("agent_type") not in VERIFIER_NAMES:
                return {}
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

    def unmetered(self, result: dict[str, Any]) -> dict[str, Any]:
        """For a run the SDK ended: `unmetered_cost_usd`, the spend in the result (the SDK's
        `model_usage`, every subagent included) that this cap did not count, if there is any.
        After a failed transcript the gap is expected and already an error event."""
        if self.transcript_failed or result["cost_basis"] != "price_table":
            return {}
        counted = self.tokens()
        reported = Tokens(
            result["uncached_input_tokens"], result["cached_input_tokens"], result["output_tokens"]
        )
        if reported == counted:
            return {}
        gap = (r - c for r, c in zip(reported, counted, strict=True))
        return {"unmetered_cost_usd": priced_usd(self.price, *gap)}

    def result_fields(self) -> dict[str, Any]:
        """The result event of a run this cap stopped; the SDK never sent its own."""
        cost = self.cost_usd()
        return self.stopped_fields(
            "error_max_budget_usd",
            "budget_exhausted",
            f"spend ${cost:.4f} at the price table passed the ${self.budget} budget",
        )

    def stopped_fields(self, subtype: str, terminal_reason: str, error: str) -> dict[str, Any]:
        """The result event of a run the harness stopped itself, priced from what this cap
        counted."""
        uncached, cached, output = self.tokens()
        return {
            "subtype": subtype,
            "is_error": True,
            "num_turns": len(self.main),
            "terminal_reason": terminal_reason,
            "uncached_input_tokens": uncached,
            "cached_input_tokens": cached,
            "output_tokens": output,
            "cost_usd": self.cost_usd(),
            "sdk_cost_usd": None,
            "cost_basis": "price_table",
            "errors": [error],
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
    """The database is a DuckDB file, or a Postgres URL held in the variable `postgres_env`
    names: the run passes the name on, so the URL reaches no argv or MCP config."""

    text: str
    type: Literal["dbt", "query"]
    workspace: Path
    duckdb: Path | None = None
    postgres_env: str | None = None

    @property
    def dialect(self) -> Literal["duckdb", "postgres"]:
        return "postgres" if self.postgres_env else "duckdb"

    def database_args(self) -> tuple[str, ...]:
        if self.postgres_env:
            return ("--postgres-env", self.postgres_env)
        assert self.duckdb is not None, "a task names a DuckDB file or a Postgres variable"
        return ("--duckdb", str(self.duckdb.resolve()))

    def gateway(self, env: Mapping[str, str]) -> Gateway:
        """The harness's own gateway to the data source (the dictionary build); the MCP server
        opens its own."""
        if self.postgres_env:
            return PostgresGateway(env[self.postgres_env])
        assert self.duckdb is not None, "a task names a DuckDB file or a Postgres variable"
        return DuckDBGateway(self.duckdb)


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
            # Bash waits longer than validate_project.py's 300-second cap on dbt parse (which is
            # a full re-parse whenever the target changes), so the CLI never moves a running dbt
            # to the background, where it would keep the database lock.
            "BASH_DEFAULT_TIMEOUT_MS": "360000",
            "BASH_MAX_TIMEOUT_MS": "600000",
            # Every Bash call starts in the workspace: a `cd dir && dbt ...` does not carry into
            # the next call, so the command guard can resolve paths from the workspace root.
            "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR": "1",
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


def skill_names(task_type: str, dialect: str) -> tuple[str, ...]:
    names = DBT_SKILLS if task_type == "dbt" else QUERY_SKILLS
    return tuple(DIALECT_SKILLS[dialect] if name == "duckdb-sql" else name for name in names)


def system_prompt(task_type: str, config: RunConfig, dialect: str = "duckdb") -> str:
    base = DBT_SYSTEM_PROMPT if task_type == "dbt" else SYSTEM_PROMPT
    names = skill_names(task_type, dialect)
    prompt = base.format(server=MCP_SERVER)
    if config.skill:
        prompt += SKILLS_PROMPT.format(skills=load_skills(names, config.skills_dir))
    if uses_verifiers(task_type, config):
        prompt += VERIFIERS_PROMPT
    return prompt


def uses_verifiers(task_type: str, config: RunConfig) -> bool:
    """The verifier subagents check dbt work only; query runs use sql-workflow's own checks."""
    return task_type == "dbt" and config.verifiers


# Ticket 69: a query agent is reminded once at each of these counts of turns left.
REMINDER_POINTS = (10, 3)
REMINDER_TEXT = (
    "Turn budget: {left} {turns} left, counting this response. A response that calls a tool uses "
    "a turn, and after the last one the run ends with no answer; give your final answer, a "
    "response with no tool calls, before then."
)


class TurnBudget:
    """What is left of a query run's turn limit, told to the agent (ticket 69).

    The bundled CLI counts a turn per main-thread response whose tool calls ran: the count starts
    at 1 and goes up after each such response's tools; once it would pass `max_turns` the run ends
    with `error_max_turns` (`num_turns` then reports limit + 1). So after the tools of response k,
    `max_turns - k` responses are left, counting the next. The budget counts the same responses:
    each main-thread `message_id` whose blocks hold a tool call, in order of arrival (`observe`,
    fed every SDK message). The PostToolUse hooks put a reminder after the first tool result of the
    response at which a point in REMINDER_POINTS is reached or passed (a point is passed when its
    hook ran before the response was observed); the next response reads it. A point the run can
    never reach (above `max_turns - 1`) is dropped: the prompt already states the limit.

    A wrong count is worse than none, so the budget goes quiet when it cannot follow the CLI: a
    tool call it has not seen gets no reminder, and a response without an id, or a main-thread
    response without tool calls that another response follows, stops the reminders. The CLI
    counts that response when a Stop hook blocked the end of the run and not when it retried
    the response (a thinking-only or truncated one); the stream does not say which."""

    def __init__(self, max_turns: int) -> None:
        self.max_turns = max_turns
        self._responses: dict[str, int] = {}
        self._calls: dict[str, int] = {}
        self._reminded = {point for point in REMINDER_POINTS if point > max_turns - 1}
        self._last: str | None = None
        self._last_called = False
        self._lost = False

    def observe(self, message: Any) -> None:
        if not isinstance(message, AssistantMessage) or message.parent_tool_use_id is not None:
            return
        calls = [block.id for block in message.content if isinstance(block, ToolUseBlock)]
        response = message.message_id
        if response is None:
            self._lost = self._lost or bool(calls)
            return
        if response != self._last:
            if self._last is not None and not self._last_called:
                self._lost = True
            self._last, self._last_called = response, False
        if not calls:
            return
        self._last_called = True
        turn = self._responses.setdefault(response, len(self._responses) + 1)
        self._calls |= dict.fromkeys(calls, turn)

    def take_reminder(self, tool_use_id: str) -> tuple[int, int, str] | None:
        """The turn of this call, the turns left after it and the reminder, when one is due; the
        points it covers are used up."""
        turn = self._calls.get(tool_use_id)
        if turn is None or self._lost:
            return None
        left = self.max_turns - turn
        due = {point for point in REMINDER_POINTS if left <= point} - self._reminded
        if left < 1 or not due:
            return None
        self._reminded |= due
        return turn, left, REMINDER_TEXT.format(left=left, turns="turn" if left == 1 else "turns")

    def hooks(self, emit: Emit) -> dict[HookEvent, list[HookMatcher]]:
        """PostToolUse and PostToolUseFailure on every tool; subagents' calls are left alone."""

        async def remind(
            hook_input: HookInput, tool_use_id: str | None, context: HookContext
        ) -> HookJSONOutput:
            fields: dict[str, Any] = dict(hook_input)
            if fields.get("agent_id"):
                return {}
            call = str(fields.get("tool_use_id", tool_use_id))
            due = self.take_reminder(call)
            if due is None:
                return {}
            turn, left, text = due
            emit(
                "turn_budget",
                turn=turn,
                turns_left=left,
                max_turns=self.max_turns,
                tool_use_id=call,
                session_id=fields.get("session_id"),
                text=text,
            )
            event = fields.get("hook_event_name")
            return cast(
                HookJSONOutput,
                {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}},
            )

        return {
            "PostToolUse": [HookMatcher(hooks=[remind])],
            "PostToolUseFailure": [HookMatcher(hooks=[remind])],
        }


def build_options(
    task: Task,
    config: RunConfig,
    settings: ModelSettings,
    stderr: list[str],
    emit: Emit,
    spend: Spend | None = None,
    database_env: Mapping[str, str] | None = None,
    task_file: Path | None = None,
    dictionary: Path | None = None,
    snapshot: Path | None = None,
    deadline: float | None = None,
    turn_budget: TurnBudget | None = None,
) -> ClaudeAgentOptions:
    """`emit` records events from hooks (the verifiers' start and end). With `spend` (the
    price-table cap, which the caller enforces with `capped`), the SDK streams what it needs and
    its own cap becomes the backstop (`sdk_budget_usd`). `database_env` holds the Postgres
    variable: it reaches the MCP server through the SDK subprocess's environment. `task_file`
    (`saved_task`, verifier runs only) is named in the verifiers' prompts and may be Read.
    `dictionary` (`prebuilt_dictionary`, context runs only) is the value dictionary file the
    MCP server serves dictionary_search from. `snapshot` (`taken_snapshot`, verifier runs only)
    is the directory the v2 tools compare the workspace against; it reaches the MCP server the
    way the Postgres URL does, through the SDK subprocess's environment. `deadline` (monotonic
    seconds, ticket 54) is when the trial ends: the v2 hooks stop dispatching verifiers once too
    little of it is left. A query run always gets the turn-budget hooks (ticket 69), whatever its
    layers; `turn_budget` holds the run's limit and is the one the caller feeds every SDK message
    (`TurnBudget.observe`), so a caller that streams the session passes its own. Without one the
    limit is the run config's, and nothing feeds the budget, so it reminds no one."""
    if task.type == "dbt":
        max_turns, budget = config.max_turns["dbt"], None
    else:
        budget = turn_budget or TurnBudget(config.max_turns[task.type])
        max_turns = budget.max_turns
    server: McpStdioServerConfig = {
        "type": "stdio",
        "command": sys.executable,
        "args": [
            *("-m", "data_agent", "mcp-server"),
            *("--config", config.name, *task.database_args()),
            # A run without the verifier subagents gets no verifier tools either (the ablation).
            *(() if uses_verifiers(task.type, config) else ("--no-verifier-tools",)),
            *("--workspace", str(task.workspace.resolve())),
            *(("--dictionary", str(dictionary)) if dictionary else ()),
        ],
    }
    if task.type == "dbt":
        tools = DBT_BUILTIN_TOOLS
        file_tools = (*GUARDED_TOOLS, *WRITE_TOOLS)
        bash_guard = command_guard(task.workspace, dbt_scripts(config), PRINTABLE_VARIABLES)
        hooks = [HookMatcher(matcher="Bash", hooks=[bash_guard])]
    else:
        tools, file_tools, hooks = QUERY_BUILTIN_TOOLS, GUARDED_TOOLS, []
    guard = workspace_guard(
        task.workspace,
        protected=(task.duckdb,) if task.duckdb else (),
        readable=(task_file,) if task_file else (),
    )
    hooks.append(HookMatcher(matcher="|".join(file_tools), hooks=[guard]))
    all_hooks: dict[HookEvent, list[HookMatcher]] = {"PreToolUse": hooks}
    agents = None
    if uses_verifiers(task.type, config):
        tools = [*tools, AGENT_TOOL]
        agents = verifier_agents(MCP_SERVER, task_file)
        all_hooks |= verifier_hooks(emit)
        # The v2 hooks need the run's snapshot and task file (`run` passes both); a caller
        # building options without them gets the verifiers but not the dispatch rewrite, the
        # Stop block or the `verifier_end` event.
        if snapshot is not None and task_file is not None:
            v2_hooks = verifier_v2.run_hooks(
                config,
                workspace=task.workspace,
                snapshot=snapshot,
                task_file=task_file,
                emit=emit,
                deadline=deadline,
            )
            for event, matchers in v2_hooks.items():
                all_hooks.setdefault(event, []).extend(matchers)
    for event, matchers in tool_timing_hooks(emit).items():
        all_hooks.setdefault(event, []).extend(matchers)
    if budget is not None:
        for event, matchers in budget.hooks(emit).items():
            all_hooks.setdefault(event, []).extend(matchers)
    if spend is not None:
        for event, matchers in spend.hooks(emit).items():
            all_hooks.setdefault(event, []).extend(matchers)
    server_env = dict(database_env or {})
    if snapshot is not None:
        server_env[SNAPSHOT_ENV] = str(snapshot)
    return ClaudeAgentOptions(
        model=settings.model,
        env=settings.sdk_env() | server_env,
        system_prompt=system_prompt(task.type, config, task.dialect),
        cwd=str(task.workspace.resolve()),
        tools=tools,
        allowed_tools=[*tools, f"mcp__{MCP_SERVER}"],
        disallowed_tools=WEB_TOOLS,
        # Anything not allowed above is denied rather than prompted for.
        permission_mode="dontAsk",
        hooks=all_hooks,
        agents=agents,
        mcp_servers={MCP_SERVER: server},
        strict_mcp_config=True,
        # ADR-0001: user settings only, never this repo's .claude/; Skills come from the prompt.
        setting_sources=["user"],
        skills=[],
        max_turns=max_turns,
        max_budget_usd=(
            config.max_budget_usd
            if spend is None
            else sdk_budget_usd(config.max_budget_usd, settings.model)
        ),
        include_partial_messages=spend is not None,
        stderr=stderr.append,
    )


# The fixed start of the gateway's error for a statement cut off at its timeout
# (`gateway.QueryTimedOut`; a test pins the two together). Only this run's MCP tools reach the
# gateway, so only their errors or responses are read for it: a Bash grep, a Read of gateway.py or
# an Agent report quoting the error is no timeout of its own.
TIMEOUT_TEXT = "Query timed out after"
GATEWAY_TOOLS = f"mcp__{MCP_SERVER}__"


def tool_timing_hooks(emit: Emit) -> dict[HookEvent, list[HookMatcher]]:
    """PostToolUse and PostToolUseFailure on every tool: one `tool_timing` event per call with
    the SDK's `duration_ms` (execution time alone, per call, where `tool_end - tool_start` is a
    whole parallel batch's), and `agent_id` when the call ran inside a subagent."""

    async def record(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        fields: dict[str, Any] = dict(hook_input)
        failed = fields.get("hook_event_name") == "PostToolUseFailure"
        tool = str(fields.get("tool_name"))
        outcome = fields.get("error") if failed else fields.get("tool_response")
        text = outcome if isinstance(outcome, str) else json.dumps(outcome, default=str)
        emit(
            "tool_timing",
            tool=tool,
            tool_use_id=fields.get("tool_use_id", tool_use_id),
            agent_id=fields.get("agent_id"),
            duration_ms=fields.get("duration_ms"),
            failed=failed,
            timed_out=tool.startswith(GATEWAY_TOOLS) and TIMEOUT_TEXT in text,
        )
        return {}

    return {
        "PostToolUse": [HookMatcher(hooks=[record])],
        "PostToolUseFailure": [HookMatcher(hooks=[record])],
    }


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
                log.emit(
                    "tool_start",
                    tool=block.name,
                    tool_use_id=block.id,
                    parent=message.parent_tool_use_id,
                    input=block.input,
                )
                if block.name == QUERY_TOOL:
                    log.emit("sql", tool_use_id=block.id, sql=block.input.get("sql"))
    elif isinstance(message, UserMessage) and isinstance(message.content, list):
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                log.emit(
                    "tool_end",
                    tool=tools.get(block.tool_use_id),
                    tool_use_id=block.tool_use_id,
                    parent=message.parent_tool_use_id,
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
            "api_error_status": None,
            "api_error": None,
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
        # HTTP status of a failed model call; the Harbor adapter retries by it (evals.harbor_agent).
        "api_error_status": message.api_error_status,
        # The CLI's message for a failed model call ("API Error: Request timed out", ...): without
        # an HTTP status it is what tells a timeout from a dropped connection.
        "api_error": message.result if message.terminal_reason == "api_error" else None,
        "uncached_input_tokens": uncached,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "cost_usd": cost,
        "sdk_cost_usd": sdk_cost,
        "cost_basis": basis,
        "errors": message.errors or [],
    }


def ended_by_sdk(message: ResultMessage | None, model: str, spend: Spend | None) -> dict[str, Any]:
    """The result event of a run the SDK ended: `result_fields`, and what `spend` missed."""
    fields = result_fields(message, model)
    return fields if spend is None else fields | spend.unmetered(fields)


# How long before the trial deadline the run stops itself (ticket 54): the gateway's statement
# timeout, so a query started just before still ends before Harbor's own timeout.
DEADLINE_MARGIN_S = float(TIMEOUT_SECONDS)


def track_open_calls(message: Any, open_calls: dict[str, str]) -> None:
    """Keep `open_calls` (tool_use id -> tool name) to the tool calls started and not answered."""
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                open_calls[block.id] = block.name
    elif isinstance(message, UserMessage) and isinstance(message.content, list):
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                open_calls.pop(block.tool_use_id, None)


def deadline_fields(spend: Spend | None, open_calls: Mapping[str, str]) -> dict[str, Any]:
    """The result event of a run stopped at its deadline; the SDK never sent its own. Spend is
    the price-table cap's count (`Spend`): the main calls so far and the verifiers that stopped;
    a verifier still running is not counted. Without a priced model nothing was counted."""
    open_tool_calls = [{"tool_use_id": i, "tool": name} for i, name in open_calls.items()]
    error = "the run stopped itself at the trial deadline"
    if spend is None:
        return {
            **result_fields(None, ""),
            "subtype": "error_deadline",
            "terminal_reason": "deadline",
            "cost_basis": "none",
            "sdk_cost_usd": None,
            "open_tool_calls": open_tool_calls,
            "errors": [f"{error}; the model has no price, so its spend was not counted"],
        }
    return spend.stopped_fields("error_deadline", "deadline", error) | {
        "open_tool_calls": open_tool_calls
    }


async def run(
    task: Task,
    config: RunConfig,
    events_dir: Path,
    env: Mapping[str, str],
    deadline: float | None = None,
) -> Outcome:
    """`deadline` is the trial's end in `time.monotonic()` seconds (ticket 54), None for none: the
    run stops itself DEADLINE_MARGIN_S before it, closing the SDK session, and records a `result`
    with `terminal_reason: "deadline"`; the outcome is an unfinished agent."""
    if task.type == "dbt" and shutil.which("dbt", path=env.get("PATH")) is None:
        raise ConfigError("dbt is not on PATH; dbt tasks run the dbt the environment provides")
    settings = ModelSettings.from_env(env)
    database_env: dict[str, str] = {}
    if task.postgres_env:
        # The URL variable goes into the SDK env on top of `sdk_env()`: a printable name would
        # let `echo $VAR` show the URL to the model; a name the SDK env sets, or any BASH_/CLAUDE_
        # one, would override the CLI settings the harness relies on.
        if (
            task.postgres_env in PRINTABLE_VARIABLES
            or task.postgres_env in settings.sdk_env()
            or task.postgres_env.startswith(("BASH_", "CLAUDE_"))
        ):
            raise ConfigError(
                f"{task.postgres_env} cannot hold the Postgres URL: the agent may print it, or "
                "it configures the Claude Code CLI; use another --postgres-env name"
            )
        if not env.get(task.postgres_env):
            raise ConfigError(f"{task.postgres_env} (the Postgres URL) is not set")
        # Only this variable: the server would otherwise read the parent's, or none.
        database_env[task.postgres_env] = env[task.postgres_env]
    events_dir.mkdir(parents=True, exist_ok=True)
    path = events_file(events_dir)
    stderr: list[str] = []
    final: ResultMessage | None = None
    spend = spend_cap(settings.model, config.max_budget_usd)
    secrets = known_secrets(env)
    names: list[str] = []
    if task.postgres_env:
        secrets += url_secrets(env[task.postgres_env])
        names = url_names(env[task.postgres_env])
    verified = uses_verifiers(task.type, config)
    saving = saved_task(task.text, task.workspace) if verified else nullcontext()
    snapshotting = taken_snapshot(task.workspace) if verified else nullcontext()
    with (
        saving as task_file,
        snapshotting as snapshot,
        EventLog(path, secrets=secrets, names=names) as log,
    ):
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
        budget = None if task.type == "dbt" else TurnBudget(config.max_turns[task.type])
        prompt = (DBT_PROMPT if task.type == "dbt" else QUERY_PROMPT).format(
            server=MCP_SERVER,
            turn_limit=TURN_LIMIT.format(max_turns=budget.max_turns) if budget else "",
            database=DATABASE_NAMES[task.dialect],
            workspace=task.workspace.resolve(),
            task=task.text,
            scripts=" and the dbt-workflow scripts" if dbt_scripts(config) else "",
            task_file_note=TASK_FILE_NOTE.format(path=task_file) if task_file else "",
        )
        tools: dict[str, str] = {}
        building = (
            prebuilt_dictionary(task.gateway(env), task.workspace, log.emit)
            if config.context
            else nullcontext()
        )
        with building as dictionary:
            options = build_options(
                task,
                config,
                settings,
                stderr,
                log.emit,
                spend,
                database_env=database_env,
                task_file=task_file,
                dictionary=dictionary,
                snapshot=snapshot,
                deadline=deadline,
                turn_budget=budget,
            )
            open_calls: dict[str, str] = {}
            # Cancels the stream at the deadline less its margin; closing the cancelled stream
            # (`aclosing`) ends the SDK session and its CLI subprocess.
            timer = asyncio.timeout(
                None
                if deadline is None
                else max(0.0, deadline - DEADLINE_MARGIN_S - time.monotonic())
            )
            try:
                async with timer:
                    try:
                        stream = query(prompt=prompt, options=options)
                        async with aclosing(capped(stream, spend)) as messages:
                            async for message in messages:
                                if isinstance(message, ResultMessage):
                                    final = message
                                else:
                                    if budget is not None:
                                        budget.observe(message)
                                    record_message(log, message, tools)
                                    track_open_calls(message, open_calls)
                    except Exception as exc:
                        # A failed run ends with a ResultMessage and then a ResultError, whose
                        # details the result event already holds. Anything else (the SDK also
                        # raises plain Exceptions, e.g. an `initialize` timeout) means the run
                        # itself broke.
                        if final is None:
                            log.emit(
                                "error", source="sdk", error=str(exc), stderr_tail=stderr[-20:]
                            )
            except TimeoutError:
                if not timer.expired():
                    raise
        if spend is not None and spend.stopped:
            stopped = spend.result_fields()
        elif timer.expired() and final is None:
            stopped = deadline_fields(spend, open_calls)
        else:
            stopped = None
        log.emit("result", **(stopped or ended_by_sdk(final, settings.model, spend)))
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
