"""One `data-agent run`: one prompt on a Claude Agent SDK `ClaudeSDKClient` (ADR-0001 and its
Update 2026-11-17), recorded as an event file. A run is a single task, or a turn of a conversation
(`ConversationTurn`) that resumes the previous turn's session and takes the messages sent while it
runs (stdin steering, `query`).

The model is whatever `DATA_AGENT_*` names; those values go only to the SDK's subprocess, mapped to
the `ANTHROPIC_*` variables it reads, so the user's own Claude Code setup is untouched.
"""

import asyncio
import dataclasses
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import AsyncGenerator, Container, Iterator, Mapping, Sequence
from contextlib import ExitStack, aclosing, contextmanager, nullcontext
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast, get_args

import claude_agent_sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookCallback,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    ModelUsage,
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
from claude_agent_sdk.types import EffortLevel, HookEvent, McpStdioServerConfig

from data_agent import sandbox as sandboxes
from data_agent import tracing, verifier_v2
from data_agent.config import (
    BASE_URL_ENV,
    DEFAULT_MODELS,
    MODEL_ENV,
    PRICE_KEYS,
    ConfigFileError,
    EffectiveConfig,
    Key,
    load_effective_config,
)
from data_agent.context_tools import DictionaryBuild, build_interruptible
from data_agent.dbt_execute import DbtExecuteError, DbtRun, Target, check_outside
from data_agent.events import EventLog, known_secrets, url_names, url_secrets
from data_agent.external_mcp import ExternalServers
from data_agent.gateway import (
    BLOCKED_PREFIX,
    TIMEOUT_SECONDS,
    DuckDBGateway,
    Gateway,
    PostgresGateway,
)
from data_agent.run_config import REPO_ROOT, RunConfig, skills_path
from data_agent.sandbox import (
    SKILLS_MOUNT,
    WORKDIR,
    Backend,
    SandboxRun,
    docker_lifecycle,
    reaches_local_commands,
)
from data_agent.tool_annotations import CHAT_ENV
from data_agent.tool_spend import LEDGER_ENV, Ledger, RunState
from data_agent.tracing import (
    CliTrace,
    Emitter,
    LangfuseSettings,
    RunExport,
    RunFacts,
    TraceTier,
    langfuse_keys,
)
from data_agent.verifier_tools import SNAPSHOT_ENV
from data_agent.verifiers import (
    AGENT_TOOL,
    VERIFIER_NAMES,
    VERIFIERS_PROMPT,
    Emit,
    verifier_agents,
    verifier_hooks,
)
from data_agent.web.sources import DataSource
from data_agent.workspace_guard import (
    FILE_TOOLS,
    credential_files,
    is_duckdb_name,
    named_or_linked,
    workspace_guard,
)

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
# Written into a wheel at build time (hatch_build.py); absent in a source checkout.
BUILD_COMMIT = Path(__file__).resolve().parent / "BUILD_COMMIT"

# The MCP server's name in the SDK session; its tools appear as mcp__data__<tool>.
MCP_SERVER = "data"
QUERY_TOOL = f"mcp__{MCP_SERVER}__query_database"
# Served only to a conversation turn (spec decision 8); calling it ends the turn
# (`clarification_hooks`).
CLARIFICATION_TOOL = f"mcp__{MCP_SERVER}__clarification"
# Every run's built-in tools (ADR-0010): the file tools, kept in the workspace and off credential
# files by the workspace guard. Shell commands and code go to the MCP server's sandbox_exec, and
# dbt reaches the database only through its dbt_execute.
BUILTIN_TOOLS: list[str] = list(FILE_TOOLS)
# Never loaded, and denied outright: the shell runs in the sandbox, not on the agent's machine.
DENIED_TOOLS: list[str] = ["Bash"]
# Loaded only with the run-level `network` switch (`data-agent run --network`, ticket 98), which the
# product's entries pass and no evaluation does. Without it they are denied outright as well as left
# out of the tools: benchmark solutions are public, and the official data-eng-bench configs turn web
# lookup off the same way (`disallowed_tools: WebSearch,WebFetch`).
WEB_TOOLS: list[str] = ["WebSearch", "WebFetch"]
# The run-level reasoning effort (`--effort`, ticket 104): a value of the main model's `efforts`
# (`ModelSettings.effort_error`; any value for a model without them). These are the default
# models' (`config.DEEPSEEK_EFFORTS`), the values DeepSeek's Anthropic format takes as
# `output_config.effort`. Unset, the options pass none and the Claude Code CLI sends its own: the
# `effortLevel` of the user settings the session loads, else `high` (CLI 2.1.301).
Effort = Literal["low", "high", "max"]
EFFORTS: tuple[Effort, ...] = get_args(Effort)
# The CLI's effort variable, which wins over `--effort` (CLI 2.1.301): a run given an effort sets it
# to the same value, so one the shell exported cannot change what is sent while the record says
# otherwise. A run without an effort leaves it as inherited.
EFFORT_ENV = "CLAUDE_CODE_EFFORT_LEVEL"

SYSTEM_PROMPT = """\
You are a data agent. You answer questions about a user's data by exploring the database and \
running SQL, then reply with the answer.

All database access goes through the `{server}` MCP tools, which are read-only. Work only from \
what the tools show you; do not guess table names, column names or values.
"""

# Added only when the run has several data sources (ticket 89), after nao's
# `ConfiguredDatabasesBlock` (two databases or more): their names and kinds, never where they are.
DATABASES_HEADING = "## Databases"
DATABASES_PROMPT = (
    """
"""
    + DATABASES_HEADING
    + """

This run has several databases. Every `{server}` tool that reads data takes a `database` argument, \
which must be one of these names:
{databases}
Pick the database from these names and what the tools show you of each. One query runs on one \
database: SQL cannot join tables of two databases. When a question needs data from more than one, \
query each database on its own and combine the results.
"""
)

# Appended only when the run config turns the Skill layer on.
SKILLS_PROMPT = """
Follow these skills.

{skills}
"""

# Appended to a conversation turn's system prompt only (spec decision 8, after nao's system prompt,
# `apps/backend/src/components/ai/system-prompt.tsx`): single runs and evaluations have no
# clarification tool, and their prompts stay as they were (decision 19).
CLARIFICATION_PROMPT = """
This is a conversation with the user, and you may ask them a question. When the question can be \
read in ways that give different results (which rows count, how a measure is defined, which \
period) and neither the conversation nor the data settles which one is meant, call \
`clarification` with one short question and 2 to 5 options instead of answering. That ends your \
turn; the option the user picks comes as the next message. Look at the data first when it can \
tell you whether the readings differ. Ask only then: when every reading gives the same result, or \
the conversation already says which one is meant, answer and state the reading you chose among \
your assumptions. Never ask a question in plain text.
"""

# The charting rule (ticket 119, .scratch/charts/spec.md "画图规则", after the "Chart Rules" of
# nao's system prompt, apps/backend/src/components/ai/system-prompt.tsx), in a `--charts` run
# only: no evaluation passes the switch.
CHARTS_PROMPT = """
You can show charts in your answer with `display_chart`. It draws the rows of one query_database \
result by the id at the end of that result (`query_id`); only the ids of this run's queries are \
valid (in a conversation, this turn's), so query again rather than cite an earlier turn's id. \
Chart only to compare categories, show a trend over time, or show how a whole splits into parts \
or how values are spread, and only when the result has enough points to show it. Do not chart a \
single number or a result of two or three rows. Show at most one or two charts in an answer. \
The text answer must be complete without the charts: they only add to it, so never write "see \
the chart" in place of a result.
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

# The query prompts' line on code (ticket 100), shared with the DAB runner's task prompt.
SANDBOX_RULE = """\
When query results need computing, combining or plotting, do it in `sandbox_exec` and pass it the \
results with `data_files`.
"""

# After the workspace line of a run whose sandbox_exec is the docker backend (ticket 100): the
# container has the workspace at WORKDIR, the other tools see it on the host. The local backend
# runs commands on the host, at the same path.
DOCKER_PATHS_RULE = (
    f"In `sandbox_exec` the workspace is at `{WORKDIR}`: write paths in sandbox commands under "
    f"`{WORKDIR}` or relative to it, and the workspace path above with Read, Glob, Grep, Write "
    "and Edit.\n"
)


# What a query run with `--charts` says in place of SANDBOX_RULE (ticket 119): a chart is
# display_chart's, not a plot made in the sandbox. SANDBOX_RULE stays as it is for every other
# run, the DAB prompt included.
CHARTS_SANDBOX_RULE = """\
When query results need computing or combining, do it in `sandbox_exec` and pass it the results \
with `data_files`. Show a chart with `display_chart`, not by plotting in `sandbox_exec`.
"""


def docker_paths_note(sandbox: Backend | None) -> str:
    return DOCKER_PATHS_RULE if sandbox == "docker" else ""


QUERY_PROMPT = (
    """\
Task type: query
Database: {database}, reachable only through the `{server}` MCP tools.
Workspace (your working directory): {workspace}
{docker_paths_note}{turn_limit}
"""
    + SANDBOX_RULE
    + """
<task>
{task}
</task>

"""
    + ANSWER_RULE
)
# The query prompt of a run with `--charts`.
CHARTS_QUERY_PROMPT = QUERY_PROMPT.replace(SANDBOX_RULE, CHARTS_SANDBOX_RULE)

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
change data is to edit the dbt project and run dbt with the `dbt_execute` tool. Work only from \
what the tools and the project files show you; do not guess table names, column names or values.
"""

DBT_PROMPT = """\
Task type: dbt
Database: {database}. dbt writes to it only through the `dbt_execute` tool; you read it only \
through the `{server}` MCP tools.
Workspace (your working directory; the dbt project is this directory or one inside it): {workspace}
{docker_paths_note}\
Run dbt against the database with `dbt_execute`, which finds the project in the workspace (name \
it with `project_dir` when the workspace holds several). Run \
every other command (Python, scripts, `dbt parse`, `dbt ls`, `dbt deps`) with `sandbox_exec`, \
which starts in the workspace. Do not edit an existing `profiles.yml`; create one when the task \
asks for it. When the task asks you to print `DB_TYPE`, run `echo $DB_TYPE` as a sandbox_exec \
call of its own. List, read and search files with the Glob, Read and Grep tools, and change them \
with Write and Edit, not with shell commands.

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
The task above is also saved, read-only, at {path}. Open it with the Read tool (Glob cannot \
reach it), and re-read it there whenever you need its exact wording, for example after \
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
def prebuilt_dictionary(
    gateway: Gateway, workspace: Path, emit: Emit, database: str | None = None
) -> Iterator[Path]:
    """The value dictionary (ticket 42), built before the agent starts and written as JSON
    outside the workspace for the MCP server to read; its coverage is recorded as the
    `dictionary` event, which names the `database` of a run with several. The build counts
    toward the run's time, not the agent's first tool call; a signal during it cancels the
    build and ends the run."""
    with run_directory(workspace, "data-agent-dictionary-") as directory:
        dictionary = build_interruptible(DictionaryBuild(gateway))
        coverage = dictionary.coverage
        emit(
            "dictionary",
            **({"database": database} if database is not None else {}),
            **{k: v for k, v in asdict(coverage).items() if k != "not_profiled"},
            tables_not_profiled=len(coverage.not_profiled),
        )
        path = directory / "dictionary.json"
        dictionary.save(path)
        yield path


@contextmanager
def prebuilt_dictionaries(
    sources: Sequence[DataSource], workspace: Path, env: Mapping[str, str], emit: Emit
) -> Iterator[dict[str, Path]]:
    """`prebuilt_dictionary` for each of several data sources, in order: the files by name."""
    with ExitStack() as stack:
        yield {
            source.name: stack.enter_context(
                prebuilt_dictionary(source_gateway(source, env), workspace, emit, source.name)
            )
            for source in sources
        }


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    cached_input: float
    output: float


def price_of(entry: Mapping[str, Any]) -> Price | None:
    """A `[models.<name>]` table's price; None without one (`ModelSettings.from_config` refuses a
    table with only some of the three)."""
    if not all(entry.get(key) is not None for key in PRICE_KEYS):
        return None
    return Price(*(float(entry[key]) for key in PRICE_KEYS))


# The code defaults' prices (`config.DEFAULT_MODELS`, where their sources are noted), by model.
PRICES: dict[str, Price] = {
    name: price for name, entry in DEFAULT_MODELS.items() if (price := price_of(entry)) is not None
}

# What the bundled CLI charges a model it has no price for: its fallback table ($5 input, $25
# output, $6.25 5-minute cache write, $0.50 cache read per 1M, in the CLI's own source; ticket 09
# measured SDK ~$1 for ~$0.035 at PRICES). Uncached input takes the cache-write rate, the higher
# of the two rates it covers.
SDK_UNKNOWN_MODEL_PRICE = Price(input=6.25, cached_input=0.5, output=25)


def priced_usd(price: Price, uncached: int, cached: int, output: int) -> float:
    return (uncached * price.input + cached * price.cached_input + output * price.output) / 1e6


def backstop_usd(budget: float, price: Price) -> float | None:
    """The cap handed to the SDK for a run capped by `Spend` at `price`: a backstop, scaled by the
    largest ratio of the SDK's rate to ours, so the SDK's figure cannot reach it while ours is
    under `budget`. No factor bounds the SDK's charge for tokens free at `price`: with any rate 0
    there is no backstop (None), and `Spend` is the only cap."""
    sdk = SDK_UNKNOWN_MODEL_PRICE
    rates = list(
        zip(
            (sdk.input, sdk.cached_input, sdk.output),
            (price.input, price.cached_input, price.output),
            strict=True,
        )
    )
    if any(rate <= 0 for _, rate in rates):
        return None
    return budget * max(sdk_rate / rate for sdk_rate, rate in rates)


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

    The format is the CLI's own and undocumented (a real sample is
    tests/fixtures/subagent_transcript.jsonl, written by CLI 2.1.301): one JSON object per line; a
    model call is one `"type": "assistant"` line per content block, each with `message.id` and
    `message.usage`, the last with the call's final usage and its `message.stop_reason`. Up to
    2.1.306 the earlier lines had no output yet and a null stop_reason; from 2.1.307 every line
    carries the final usage and stop reason (docs/research/2026-11-23-sdk-upgrade-0.2.187.md §2.3).
    Taking each call's last line reads both the same.
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

    With a `ledger` (`tool_ledger`), the model calls the tool servers make (classify_text) count
    too: this cap publishes its own spend there for the tools to keep to the budget, and adds the
    tools' tokens to its spend and to every result event (`tool_fields`). A tool call that is
    still running when the run ends is counted as far as it got.
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
    ledger: Ledger | None = None
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
                self.publish()
            elif event.get("type") == "message_delta" and self._call is not None:
                final = {k: v for k, v in (event.get("usage") or {}).items() if v is not None}
                self.main[self._call] = usage_tokens(self._call_start | final)
                # A response cut at its output limit is no final answer: the CLI calls again.
                cut = (event.get("delta") or {}).get("stop_reason") == "max_tokens"
                self.between_calls = self._call_uses_tools or cut
                self.publish()
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
                self.publish()
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

    def tool_tokens(self) -> Tokens:
        if self.ledger is None:
            return Tokens(0, 0, 0)
        return Tokens(*self.ledger.tool_tokens())

    def model_cost_usd(self) -> float:
        """The run's own model calls (main thread and verifiers), the tools' not included."""
        return priced_usd(self.price, *self.tokens())

    def cost_usd(self) -> float:
        return self.model_cost_usd() + priced_usd(self.price, *self.tool_tokens())

    def publish(self) -> None:
        """Write the budget, the price and the run's own spend to the ledger for the tools."""
        if self.ledger is None:
            return
        self.ledger.publish(
            RunState(
                budget_usd=self.budget,
                spent_usd=self.model_cost_usd(),
                input_price=self.price.input,
                cached_input_price=self.price.cached_input,
                output_price=self.price.output,
            )
        )

    def tool_fields(self) -> dict[str, Any]:
        """The tools' tokens and cost for a result event; nothing when no tool called a model."""
        tokens = self.tool_tokens()
        if not any(tokens):
            return {}
        return {
            "tool_uncached_input_tokens": tokens.uncached,
            "tool_cached_input_tokens": tokens.cached,
            "tool_output_tokens": tokens.output,
            "tool_cost_usd": priced_usd(self.price, *tokens),
        }

    def with_tools(self, fields: dict[str, Any]) -> dict[str, Any]:
        """A result event of a run the SDK ended (its cost is the SDK's tokens only), with the
        tools' spend added to `cost_usd`."""
        tools = self.tool_fields()
        if not tools:
            return fields
        return fields | tools | {"cost_usd": fields["cost_usd"] + tools["tool_cost_usd"]}

    def passed(self) -> bool:
        """Whether spend is over the budget, the cap being on."""
        return not self.transcript_failed and self.cost_usd() > self.budget

    def check(self) -> bool:
        """Whether the run is to stop now: the cap may be checked and spend is over it."""
        if self.running or not self.between_calls:
            return False
        self.stopped = self.passed()
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
            **self.tool_fields(),
        }


@contextmanager
def tool_ledger(spend: Spend | None, workspace: Path) -> Iterator[None]:
    """The run's spend ledger (`tool_spend`), in a directory of its own outside the workspace,
    for the tool servers' model calls. None without a price-table cap: a tool could not keep to
    a budget it has no price for, so it refuses to call a model."""
    if spend is None:
        yield
        return
    with run_directory(workspace, "data-agent-spend-") as directory:
        spend.ledger = Ledger(directory)
        spend.publish()
        yield


def spend_cap(model: "str | ModelSettings", budget: float) -> Spend | None:
    """The price-table cap: a run's `ModelSettings` at its price (`ModelSettings.price`), a model
    by name (the evaluation entries) at the code defaults' (`PRICES`). None without a price: the
    run has no cap at all (decisions.md Q8)."""
    price = PRICES.get(model) if isinstance(model, str) else model.price()
    return None if price is None else Spend(price, budget)


async def query(
    *,
    prompt: str,
    options: ClaudeAgentOptions,
    steering: AsyncGenerator[str] | None = None,
    emit: Emit | None = None,
    spend: Spend | None = None,
) -> AsyncGenerator[Any]:
    """One turn on a `ClaudeSDKClient` (spec decision 15, ADR-0001 Update 2026-11-17): connect,
    send `prompt`, and yield the session's messages up to and including the turn's
    ResultMessage. With background tasks off (`sdk_env`) a prompt gets exactly one result.
    Closing the stream, or its end, disconnects the client, which ends the CLI subprocess before
    `aclose` returns (the SDK's own `query` left that to the event loop's async-generator
    finalizer, later).

    `steering`, the stdin lines of a `--chat` run, is sent into the running turn
    (`SteeredTurn`, to `emit`) from when `prompt` is sent until the turn ends; a line still
    unsent then is never sent. `spend` is the run's price-table cap, which the caller applies
    (`capped`) and a step's end keeps to too."""
    if steering is not None:
        # The CLI echoes each user message, with its uuid, when the model takes it in.
        options = dataclasses.replace(
            options, extra_args={**options.extra_args, "replay-user-messages": None}
        )
    client = ClaudeSDKClient(options)
    steered: SteeredTurn | None = None
    steering_task: asyncio.Task[None] | None = None
    try:
        await client.connect()
        if steering is None:
            await client.query(prompt)
            async with aclosing(cast(AsyncGenerator[Any], client.receive_response())) as messages:
                async for message in messages:
                    yield message
                    if isinstance(message, ResultMessage):
                        return
            return
        assert emit is not None, "steering writes its events through emit"
        steered = SteeredTurn(client, emit, options.max_turns, spend)
        await client.query(user_message(prompt, steered.prompt_id))
        steering_task = asyncio.create_task(steered.steer(steering))
        ended = over_limit = False
        async with aclosing(cast(AsyncGenerator[Any], client.receive_messages())) as messages:
            async for message in messages:
                if isinstance(message, ResultMessage):
                    if ended := steered.end_step(message):
                        break
                    continue
                if not steered.absorb(message):
                    yield message
                if over_limit := steered.count_turns(message):
                    break
        await stop_steering(steering_task)
        if over_limit:
            raise TurnLimitPassed(steered.max_turns or 0)
        if ended:
            yield steered_result(steered.results)
        elif steered.results:
            # The CLI ended (exit 0) inside a step it took a steering message in for: the earlier
            # step's result is not this turn's.
            raise RuntimeError("the session ended before the step reading a steering message did")
    finally:
        try:
            # Re-raises a cancellation of this run (its deadline), which must not skip the
            # disconnect.
            await stop_steering(steering_task)
        finally:
            if steered is not None:
                steered.close()
            await client.disconnect()


async def user_message(text: str, id_: str) -> AsyncGenerator[dict[str, Any]]:
    """`text` as the stream-json user message the CLI echoes with `id_` as its uuid
    (SignalPilot's `_steering_prompt`, claude_agent.py:357-370)."""
    yield {
        "type": "user",
        "uuid": id_,
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
    }


class SteeredTurn:
    """A turn steered through stdin, reconciled by the CLI's echoes as SignalPilot does
    (claude_agent.py:355-408, claude_agent_events.py:157-163 and 326-339). Each steering message
    goes in as one more `query()` with its `id` as the uuid; the CLI echoes it, that uuid kept,
    when the model takes it in, and only then is `steer_delivered` emitted. A message the model
    has not taken in by the end of a step is not lost: the CLI takes it in as the next step, which
    ends with a result of its own (probes of 2026-11-17, ticket 92). So the turn ends at the
    first result with no message waiting for its echo; an error result, or one a hook stopped
    (a clarification), ends it anyway, and a message still waiting is then an `error` event, as
    it is when the run stops the turn itself.

    Each step gets the CLI's whole turn limit afresh, so the limit of the turn (`max_turns`,
    spec decision 13) is kept here over the steps after the first (`count_turns`). A step's end
    is a point between two model calls, where the price-table cap (`spend`) applies too."""

    def __init__(
        self, client: ClaudeSDKClient, emit: Emit, max_turns: int | None, spend: Spend | None
    ) -> None:
        self.client = client
        self.emit = emit
        self.max_turns = max_turns
        self.spend = spend
        self.prompt_id = str(uuid.uuid4())
        self.waiting: dict[str, str] = {}  # sent and not yet echoed: id -> text
        self.sent: set[str] = set()
        self.results: list[ResultMessage] = []  # of the steps ended so far
        self.started = False
        # The step running: its turns as the CLI counts them, its main thread's tool calls
        # still unanswered (tool_use id -> the response that made them), and the responses
        # counted.
        self.step_turns = 1
        self.open_calls: dict[str, str] = {}
        self.counted: set[str] = set()

    async def steer(self, lines: AsyncGenerator[str]) -> None:
        """Send each steering message among `lines` (`steer_message`). A line that is no
        message, or repeats the id of one already sent (the CLI drops a user message whose uuid
        it has seen), is an `error` event and is skipped; a blank line is ignored. stdin closing
        ends steering, not the turn. A message the session cannot take (its CLI gone) is an
        `error` event and ends steering; the turn's own stream reports the session's failure."""

        def skip(number: int, reason: str) -> None:
            self.emit("error", source="steer", error=f"stdin line {number} skipped: {reason}")

        number = 0
        async with aclosing(lines) as stdin:
            async for line in stdin:
                number += 1
                if not line.strip():
                    continue
                message = steer_message(line)
                if isinstance(message, str):
                    skip(number, message)
                    continue
                if message.id in self.sent:
                    skip(number, f"id {message.id!r} already sent")
                    continue
                # Counted before it is sent, so a result that comes meanwhile waits for it.
                self.sent.add(message.id)
                self.waiting[message.id] = message.text
                try:
                    await self.client.query(user_message(message.text, message.id))
                except Exception as exc:
                    del self.waiting[message.id]
                    self.emit("error", source="steer", error=f"stdin line {number} not sent: {exc}")
                    return

    def absorb(self, message: Any) -> bool:
        """Take in `message` if it belongs to the steering rather than to the turn, and say
        whether it did: an echo (of the prompt, or of a steering message, which emits
        `steer_delivered` with its `id` and `text`) or the init message of a step after the
        first, in the same session."""
        if isinstance(message, UserMessage) and message.parent_tool_use_id is None:
            if message.uuid == self.prompt_id:
                return True
            if message.uuid is not None and message.uuid in self.waiting:
                text = self.waiting.pop(message.uuid)
                self.emit("steer_delivered", id=message.uuid, text=text)
                return True
        if isinstance(message, SystemMessage) and message.subtype == "init":
            seen, self.started = self.started, True
            return seen
        return False

    def turns_used(self) -> int:
        """The turns of the steps ended so far, as the CLI counted each."""
        return sum(result.num_turns for result in self.results)

    def end_step(self, result: ResultMessage) -> bool:
        """End the step `result` ends, and say whether the turn ends with it. It goes on while a
        message waits for its echo, unless the step failed, a hook stopped it (the agent asked
        the user, `clarification_hooks`: a step reading the message would answer past the
        question), or the turn has no turn or budget left for another step. Out of budget, the
        step's answer is kept, as `Spend` keeps a final answer that passes the cap."""
        self.results.append(result)
        self.step_turns, self.open_calls, self.counted = 1, {}, set()
        stopped = result.terminal_reason == "hook_stopped"
        if result.is_error or result.subtype != "success" or stopped or not self.waiting:
            return True
        if self.spend is not None and self.spend.passed():
            return True
        return self.max_turns is not None and self.turns_used() >= self.max_turns

    def count_turns(self, message: Any) -> bool:
        """Count the turn `message` may end, and say whether a step after the first has taken
        the turn past `max_turns` with it.
        The CLI counts a step's turns from 1, one more once a response's tool calls are all
        answered, and ends the step when that count would pass its limit (`TurnBudget`); the
        steps together are held to that here. A response counts once: the CLI may run its tools
        while it still streams, so a call's result can come before its next call."""
        if isinstance(message, AssistantMessage) and message.parent_tool_use_id is None:
            calls = [block.id for block in message.content if isinstance(block, ToolUseBlock)]
            for call in calls:
                self.open_calls[call] = message.message_id or calls[0]
        elif (
            isinstance(message, UserMessage)
            and message.parent_tool_use_id is None
            and isinstance(message.content, list)
        ):
            for block in message.content:
                if not isinstance(block, ToolResultBlock):
                    continue
                response = self.open_calls.pop(block.tool_use_id, None)
                if response is None or response in self.open_calls.values():
                    continue
                if response not in self.counted:
                    self.counted.add(response)
                    self.step_turns += 1
        over = self.max_turns is not None and self.turns_used() + self.step_turns > self.max_turns
        return bool(self.results) and over

    def close(self) -> None:
        """Emit an `error` event for each message sent and never taken in."""
        waiting, self.waiting = self.waiting, {}
        for id_ in waiting:
            self.emit(
                "error", source="steer", error=f"message {id_!r} was not read: the turn ended"
            )


class TurnLimitPassed(Exception):
    """A steered turn's steps together passed its turn limit (`SteeredTurn.count_turns`); the
    run stopped them."""

    def __init__(self, max_turns: int) -> None:
        super().__init__(f"the steps of the turn passed its limit of {max_turns} turns")
        self.max_turns = max_turns


# What a result of the CLI counts, per step of a steered turn (probes of 2026-11-17 against the
# bundled CLI 2.1.301): the step alone for these; the whole process so far for `total_cost_usd`,
# `model_usage` and `duration_api_ms`.
STEP_USAGE_TOKENS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


def steered_result(results: Sequence[ResultMessage]) -> ResultMessage:
    """The result of a turn whose steps each ended with one of `results`: the last, with the
    counts each step makes alone (`num_turns`, `duration_ms`, `usage` tokens) summed."""
    last = results[-1]
    if len(results) == 1:
        return last
    usage = dict(last.usage or {})
    for key in STEP_USAGE_TOKENS:
        usage[key] = sum(int((result.usage or {}).get(key) or 0) for result in results)
    return dataclasses.replace(
        last,
        num_turns=sum(result.num_turns for result in results),
        duration_ms=sum(result.duration_ms for result in results),
        usage=usage,
    )


async def stop_steering(task: asyncio.Task[None] | None) -> None:
    """Cancel steering and wait for it to end."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # Re-raised only when this task is being cancelled too, not for steering's own end.
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise


async def stdin_lines(fd: int) -> AsyncGenerator[str]:
    """The lines read from file descriptor `fd` (a `--chat` run's stdin), each with its newline,
    and the unterminated rest once `fd` closes. Nothing is read before the first line is asked
    for. A daemon thread reads the descriptor itself (`os.read`, not `sys.stdin`, whose buffer
    lock a thread blocked in a read keeps at interpreter shutdown), so a run whose stdin stays
    open still ends; bytes that are not UTF-8 are replaced."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def put(line: str | None) -> bool:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, line)
        except RuntimeError:  # the event loop is closed: the run has ended
            return False
        return True

    def read() -> None:
        pending = b""
        try:
            while chunk := os.read(fd, 65536):
                pending += chunk
                while (end := pending.find(b"\n")) >= 0:
                    if not put(pending[: end + 1].decode(errors="replace")):
                        return
                    pending = pending[end + 1 :]
        except OSError:
            pass
        if pending:
            put(pending.decode(errors="replace"))
        put(None)

    threading.Thread(target=read, name="steering-stdin", daemon=True).start()
    while (line := await queue.get()) is not None:
        yield line


@dataclass(frozen=True)
class SteerMessage:
    """A message sent while a turn runs: `id` is the chat backend's message id, `text` its text."""

    id: str
    text: str


def steer_message(line: str) -> SteerMessage | str:
    """The steering message one stdin line holds (`{"id": str, "text": str}`, contract item 6 of
    tests/acceptance/test_chat_api.py), else why the line is none. Other keys are ignored."""
    try:
        value = json.loads(line)
    except ValueError:
        return "not JSON"
    if not isinstance(value, dict):
        return "not a JSON object"
    fields = cast(dict[str, Any], value)
    id_, text = fields.get("id"), fields.get("text")
    if not isinstance(id_, str) or not id_:
        return 'no "id" string'
    if not isinstance(text, str) or not text.strip():
        return 'no "text" string'
    return SteerMessage(id_, text)


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


def source_kind(source: DataSource) -> Literal["duckdb", "postgres"]:
    return "postgres" if source.postgres_env else "duckdb"


def source_location(source: DataSource) -> str:
    """The DuckDB file's absolute path, or the Postgres variable's name."""
    if source.postgres_env:
        return source.postgres_env
    assert source.duckdb is not None, "a data source names a DuckDB file or a Postgres variable"
    return str(source.duckdb.resolve())


def source_gateway(source: DataSource, env: Mapping[str, str]) -> Gateway:
    """A gateway to `source`; a Postgres source's URL is read from `env` under its variable."""
    if source.postgres_env:
        return PostgresGateway(env[source.postgres_env])
    assert source.duckdb is not None, "a data source names a DuckDB file or a Postgres variable"
    return DuckDBGateway(source.duckdb)


@dataclass(frozen=True)
class Task:
    """The database is a DuckDB file, or a Postgres URL held in the variable `postgres_env`
    names: the run passes the name on, so the URL reaches no argv or MCP config. `data_source` is
    the name the `task` event gives the data source, when the caller has one of its own.

    A query run may instead have `sources`, two or more data sources by name (ticket 89): the
    agent sees them all and each query names the one it runs on."""

    text: str
    type: Literal["dbt", "query"]
    workspace: Path
    duckdb: Path | None = None
    postgres_env: str | None = None
    data_source: str | None = None
    sources: tuple[DataSource, ...] = ()

    def __post_init__(self) -> None:
        if self.sources and (len(self.sources) < 2 or self.duckdb or self.postgres_env):
            raise ValueError("`sources` holds two or more data sources, in place of the one")

    @property
    def several(self) -> bool:
        """Whether the run has several data sources, so each tool call names one."""
        return bool(self.sources)

    @property
    def databases(self) -> tuple[DataSource, ...]:
        """Every data source of the run, by name; a run with one has it alone."""
        if self.sources:
            return self.sources
        only = DataSource(self.data_source_name, duckdb=self.duckdb, postgres_env=self.postgres_env)
        return (only,)

    @property
    def dialects(self) -> tuple[Literal["duckdb", "postgres"], ...]:
        """The data sources' dialects, each once, in the order of the sources."""
        return tuple(dict.fromkeys(source_kind(source) for source in self.databases))

    @property
    def postgres_envs(self) -> tuple[str, ...]:
        """The variables holding the run's Postgres URLs."""
        return tuple(s.postgres_env for s in self.databases if s.postgres_env)

    @property
    def data_source_name(self) -> str:
        """The name given, else the Postgres variable's name or the DuckDB file's: never the
        connection string or the file's path."""
        if self.data_source:
            return self.data_source
        if self.postgres_env:
            return self.postgres_env
        assert self.duckdb is not None, "a task names a DuckDB file or a Postgres variable"
        return self.duckdb.name

    def database_args(self) -> tuple[str, ...]:
        """The MCP server's arguments naming the data sources: names, kinds, the DuckDB files and
        the Postgres variables, never a URL."""
        if self.sources:
            return tuple(
                arg
                for source in self.sources
                for arg in ("--source", source.name, source_kind(source), source_location(source))
            )
        if self.postgres_env:
            return ("--postgres-env", self.postgres_env)
        assert self.duckdb is not None, "a task names a DuckDB file or a Postgres variable"
        return ("--duckdb", str(self.duckdb.resolve()))

    def gateway(self, env: Mapping[str, str]) -> Gateway:
        """The harness's own gateway to the data source (the dictionary build); the MCP server
        opens its own."""
        [only] = self.databases
        return source_gateway(only, env)


@dataclass(frozen=True)
class ConversationTurn:
    """A turn of a conversation (`data-agent run --chat`, .scratch/chat-ui/spec.md decisions
    14-18). `directory` is the conversation's directory, named by its id; the SDK keeps the
    session's record in its `claude-config/`, the conversation's own `CLAUDE_CONFIG_DIR`, never the
    user's `~/.claude`. `number` counts from 1. `resume` is the session id of the latest earlier
    turn that has one; the first turn has none and starts a new session."""

    directory: Path
    number: int
    resume: str | None = None

    @property
    def conversation(self) -> str:
        return self.directory.name

    @property
    def config_dir(self) -> Path:
        return (self.directory / "claude-config").resolve()


@dataclass(frozen=True)
class Outcome:
    """`finished` is false when the agent stopped early; `infra_error` when the run itself broke."""

    answer: str
    finished: bool
    infra_error: bool
    errors: list[str]
    events_path: Path


def model_table(config: EffectiveConfig, name: str) -> dict[str, Any]:
    """`[models.<name>]`'s keys, empty for a model without a table."""
    table = config.value(Key(("models", name)))
    return table if isinstance(table, dict) else {}


# What a model without `context_window` or `compact_window` gets (spec Q2; `sdk_env`).
CONTEXT_WINDOW = 1_000_000
COMPACT_WINDOW = 400_000


@dataclass(frozen=True)
class ModelSettings:
    """The run's models (`[models.<name>]`, `[roles]`; spec Q2): the main model's endpoint, key
    and name, and what the other roles and the main model's table add. `from_config` reads them
    from the effective config, `from_env` from the code defaults and the environment alone (the
    evaluation entries, which read no config file)."""

    base_url: str
    api_key: str
    model: str
    # The `small` and `verifier` roles' models, "" for the main one. The Claude Code CLI sends
    # every call to one endpoint with one key: theirs are only model names sent there.
    small: str = ""
    verifier: str = ""
    # The `classify` role's own endpoint, key and model (`tool_env`), None for the main model's.
    classify: "ModelSettings | None" = None
    context_window: int = CONTEXT_WINDOW
    compact_window: int = COMPACT_WINDOW
    # The main model's `efforts`; None takes any effort.
    efforts: tuple[str, ...] | None = None
    # Each role's model's price, None for a model without one; None for the whole: `PRICES`.
    prices: Mapping[str, Price | None] | None = None
    # `[roles].main` as a file wrote it, and the file, when `DATA_AGENT_MODEL` overrides it
    # (`EffectiveConfig.overridden`): the run warns.
    main_overridden: tuple[str, str] | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str], require_key: bool = True) -> "ModelSettings":
        """The code defaults' models, `DATA_AGENT_MODEL` and `DATA_AGENT_BASE_URL` over them; the
        key is `DATA_AGENT_API_KEY`."""
        given = {name: env[name] for name in (MODEL_ENV, BASE_URL_ENV) if env.get(name)}
        try:
            config = load_effective_config((), given, {})
        except ConfigFileError as exc:
            raise ConfigError(str(exc)) from exc
        return cls.from_config(config, env, require_key)

    @classmethod
    def from_config(
        cls, config: EffectiveConfig, env: Mapping[str, str], require_key: bool = True
    ) -> "ModelSettings":
        """The models `[roles]` names, each from its `[models.<name>]` table (`DATA_AGENT_MODEL`
        and `DATA_AGENT_BASE_URL` already over them, `config.load_effective_config`). A model
        without a table, or a table without `base_url` or `api_key`, gets the default endpoint
        and `DATA_AGENT_API_KEY`. ConfigError for a table with only some of the three prices, and
        for a role with no key (`check_key`; with `require_key` False, left to the run)."""
        main = str(config.value("roles.main"))
        roles = {
            role: str(config.value(f"roles.{role}") or main)
            for role in ("small", "verifier", "classify")
        }
        tables = {name: model_table(config, name) for name in (main, *roles.values())}
        prices: dict[str, Price | None] = {}
        for name, table in tables.items():
            given = [key for key in PRICE_KEYS if table.get(key) is not None]
            if given and len(given) < len(PRICE_KEYS):
                missing = [key for key in PRICE_KEYS if key not in given]
                raise ConfigError(
                    f"[models.{name}] gives {', '.join(given)} but not {', '.join(missing)}: "
                    "give all three prices, or none (the spend is then unknown)"
                )
            for key in given:
                value = float(table[key])
                if not math.isfinite(value) or value < 0:
                    raise ConfigError(
                        f"[models.{name}].{key} is {table[key]!r}: a price is a finite number "
                        "of dollars per million tokens, 0 or more"
                    )
            prices[name] = price_of(table)

        def endpoint(name: str) -> tuple[str, str]:
            table = tables[name]
            api_key = str(table.get("api_key") or env.get("DATA_AGENT_API_KEY") or "")
            return str(table.get("base_url") or DEFAULT_BASE_URL), api_key

        # The CLI sends every call to one endpoint with one key: a `small` or `verifier` model
        # whose table gives another would be sent to main's, so it is refused, not ignored. Only
        # what the user wrote counts: a code default (the default models' `base_url`) does not.
        base_url, api_key = endpoint(main)
        for role in ("small", "verifier"):
            name = roles[role]
            written = {
                key: tables[name].get(key)
                for key in ("base_url", "api_key")
                if config.source(Key(("models", name, key))) not in (None, "default")
            }
            if name != main and (
                written.get("base_url") not in (None, base_url)
                or written.get("api_key") not in (None, api_key)
            ):
                raise ConfigError(
                    f"[roles].{role} = {name!r}: [models.{name}] gives a base_url or api_key "
                    f"other than the main model's ({main}); the Claude Code CLI sends every "
                    "call to the main model's endpoint with its key, so give it the same ones "
                    "or leave them out"
                )
        table = tables[main]
        efforts = table.get("efforts")
        classify = roles["classify"]
        was = config.overridden.get("roles.main")
        settings = cls(
            base_url,
            api_key,
            main,
            small="" if roles["small"] == main else roles["small"],
            verifier="" if roles["verifier"] == main else roles["verifier"],
            classify=None if classify == main else cls(*endpoint(classify), classify),
            context_window=int(table.get("context_window") or CONTEXT_WINDOW),
            compact_window=int(table.get("compact_window") or COMPACT_WINDOW),
            efforts=None if efforts is None else tuple(efforts),
            prices=prices,
            main_overridden=None if was is None else (str(was[0]), was[1]),
        )
        if require_key:
            settings.check_key()
        return settings

    def check_key(self) -> None:
        """ConfigError when the main or the `classify` model has no key."""
        for model in (self, self.classify):
            if model is not None and not model.api_key:
                raise ConfigError(
                    f"DATA_AGENT_API_KEY is not set (nor [models.{model.model}].api_key, the key "
                    "of the model the run uses)"
                )

    def models(self) -> list[str]:
        """The models the run calls: main, small, verifier, classify; each once."""
        names = [self.model, self.small, self.verifier]
        names.append(self.model if self.classify is None else self.classify.model)
        return list(dict.fromkeys(name for name in names if name))

    def model_prices(self) -> Mapping[str, Price | None]:
        """Each model's price: `prices`, or `PRICES` for settings built without them."""
        return PRICES if self.prices is None else self.prices

    def unpriced(self) -> list[str]:
        """The run's models without a price."""
        prices = self.model_prices()
        return [name for name in self.models() if prices.get(name) is None]

    def price(self) -> Price | None:
        """The price the run's spend is counted at (`Spend`, `result_fields`, the tools' ledger):
        with one model its price; with several each rate at its highest among them, so the spend
        is never understated. None when any of them has no price: the spend is unknown."""
        if self.unpriced():
            return None
        table = self.model_prices()
        prices = [price for name in self.models() if (price := table.get(name)) is not None]
        return Price(
            input=max(p.input for p in prices),
            cached_input=max(p.cached_input for p in prices),
            output=max(p.output for p in prices),
        )

    def effort_error(self, effort: str) -> str | None:
        """Why the main model does not take `effort` (its `efforts`), None when it does."""
        if self.efforts is None or effort in self.efforts:
            return None
        return (
            f"{effort!r} is not one of the efforts of the model {self.model} "
            f"({', '.join(self.efforts)}; [models.{self.model}].efforts)"
        )

    def secrets(self) -> list[str]:
        """The keys, and the endpoints with their hosts (`events.known_secrets`) but the code
        default's public one: masked out of the event file whatever variable or table they came
        from."""
        secrets: list[str] = []
        for model in (self, self.classify):
            if model is None:
                continue
            if model.api_key:
                secrets.append(model.api_key)
            if model.base_url != DEFAULT_BASE_URL:
                secrets += known_secrets({BASE_URL_ENV: model.base_url})
        return list(dict.fromkeys(secrets))

    def tool_env(self) -> dict[str, str]:
        """The model settings for the tool servers' own model calls (classify_text): the
        `classify` role's."""
        model = self.classify or self
        return {
            "DATA_AGENT_BASE_URL": model.base_url,
            "DATA_AGENT_API_KEY": model.api_key,
            "DATA_AGENT_MODEL": model.model,
        }

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
            "ANTHROPIC_SMALL_FAST_MODEL": self.small or self.model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": self.small or self.model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": self.model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": self.model,
            # Subagents with no model of their own. The verifiers name theirs (`build_options`):
            # the CLI does not let this variable override a definition's model (2.1.330).
            "CLAUDE_CODE_SUBAGENT_MODEL": self.verifier or self.model,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            # Subagents (the verifiers) run in the foreground. Launched in the background, they
            # end the main turn before their reports are in and split the run into several results.
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
            # Ticket 110: the CLI does not know the model; 2.1.301 assumed a 200,000-token
            # window and stopped the session at 177,000, and from 2.1.323 an unknown model's
            # default is 1M. The main model's `context_window` (DeepSeek documents 1M, the
            # default) is set either way. Compaction runs at `compact_window` less 33,000
            # (367,000 by default). Digits work on every version (2.1.301 refused `400k`; 2.1.304
            # and later also take the suffix).
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(self.context_window),
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": str(self.compact_window),
            # Blank, so an exported one never turns compaction off: the CLI reads both as true
            # only for "1", "true", "yes" or "on". `build_options` turns it on (`settings`).
            "DISABLE_AUTO_COMPACT": "",
            "DISABLE_COMPACT": "",
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


def load_skills(names: tuple[str, ...], skills_dir: Path, root: str | None = None) -> str:
    """Skills go into the system prompt, so the workflow is loaded before the first turn instead
    of depending on the model choosing to call a Skill tool. `${CLAUDE_SKILL_DIR}` becomes the
    Skill's own directory, as the Skill tool would set it: under `root` when sandbox commands see
    the Skill directory there (the docker backend's mount), else its real path."""
    return "\n\n".join(
        (skills_dir / name / "SKILL.md")
        .read_text()
        .replace("${CLAUDE_SKILL_DIR}", f"{root}/{name}" if root else str(skills_dir / name))
        for name in names
    )


def skill_names(task_type: str, dialects: Sequence[str]) -> tuple[str, ...]:
    """The task type's Skills, DuckDB's dialect Skill swapped for those of `dialects`."""
    names = DBT_SKILLS if task_type == "dbt" else QUERY_SKILLS
    return tuple(
        skill
        for name in names
        for skill in ([DIALECT_SKILLS[d] for d in dialects] if name == "duckdb-sql" else [name])
    )


def injected_skills(task_type: str, config: RunConfig, dialects: Sequence[str]) -> tuple[str, ...]:
    """The Skills the main agent's system prompt holds, in order; none when the layer is off."""
    return skill_names(task_type, dialects) if config.skill else ()


def database_list(task: Task) -> str:
    """The data sources by name and kind, one `- name (Kind)` line each."""
    return "".join(
        f"- {source.name} ({DATABASE_NAMES[source_kind(source)]})\n" for source in task.databases
    )


def database_line(task: Task) -> str:
    """The task prompt's `Database:` value: the database's kind, or each source with its own."""
    kinds = [DATABASE_NAMES[source_kind(source)] for source in task.databases]
    if not task.several:
        return kinds[0]
    named = (f"{source.name} ({kind})" for source, kind in zip(task.databases, kinds, strict=True))
    return f"one of {', '.join(named)}"


def system_prompt(
    task: Task,
    config: RunConfig,
    chat: bool = False,
    skills_root: str | None = None,
    charts: bool = False,
) -> str:
    """`chat`: a conversation turn, which may ask the user (`CLARIFICATION_PROMPT`).
    `skills_root`: where sandbox commands see the Skill directory (`load_skills`). `charts`: a
    run with display_chart (`CHARTS_PROMPT`)."""
    base = DBT_SYSTEM_PROMPT if task.type == "dbt" else SYSTEM_PROMPT
    names = injected_skills(task.type, config, task.dialects)
    prompt = base.format(server=MCP_SERVER)
    if task.several:
        prompt += DATABASES_PROMPT.format(server=MCP_SERVER, databases=database_list(task))
    if names:
        prompt += SKILLS_PROMPT.format(skills=load_skills(names, config.skills_dir, skills_root))
    if uses_verifiers(task.type, config):
        prompt += VERIFIERS_PROMPT
    if chat:
        prompt += CLARIFICATION_PROMPT
    if charts:
        prompt += CHARTS_PROMPT
    return prompt


def uses_verifiers(task_type: str, config: RunConfig) -> bool:
    """The verifier subagents check dbt work only; query runs use sql-workflow's own checks."""
    return task_type == "dbt" and config.verifiers


# Ticket 69: a query agent is reminded once at each of these counts of turns left.
REMINDER_POINTS = (10, 3)
# The agent reads it after a response's tool results, so the count names the next response as the
# first of those left and says the response whose tools ran has used its turn.
REMINDER_TEXT = (
    "Turn budget: {left} {turns} left, your next response included: you can send {left} more "
    "tool-calling {responses}, and after the last of them the run ends with no answer. The "
    "response whose tool results you are reading has already used its turn. Give your final "
    "answer, a response with no tool calls, before then."
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
        singular = left == 1
        text = REMINDER_TEXT.format(
            left=left,
            turns="turn" if singular else "turns",
            responses="response" if singular else "responses",
        )
        return turn, left, text

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


def clarification_hooks(emit: Emit) -> dict[HookEvent, list[HookMatcher]]:
    """PostToolUse on the clarification tool (a conversation turn's only): a call that went
    through, the tool having checked its question and options, is recorded as the
    `clarification` event and ends the turn, as nao's `hasToolCall('clarification')` stop
    condition does (`apps/backend/src/agents/stop-conditions.ts`). `continue: false` makes the
    CLI call the model no more and end the turn with a `success` result, `terminal_reason:
    "hook_stopped"` and an empty `result`, so the turn has no answer (probed, CLI 2.1.301). A
    call the tool refused goes to PostToolUseFailure instead: the agent reads the error and goes
    on (probed too). A turn asks once: when one response holds several calls, which the CLI may
    run together (the tool is read-only), only the first to finish is recorded."""
    asked = False

    async def ask(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        nonlocal asked
        stop: HookJSONOutput = {"continue_": False, "stopReason": "the agent asked the user"}
        if asked:
            return stop
        asked = True
        fields: dict[str, Any] = dict(hook_input)
        arguments: dict[str, Any] = fields.get("tool_input") or {}
        call = str(fields.get("tool_use_id", tool_use_id))
        emit(
            "clarification",
            tool_use_id=call,
            question=arguments.get("question"),
            options=arguments.get("options"),
        )
        return stop

    return {"PostToolUse": [HookMatcher(matcher=CLARIFICATION_TOOL, hooks=[ask])]}


def guard_recorder(guard: HookCallback, guarded: set[str] | None) -> HookCallback:
    """`guard` as is, adding the id of each call it denies to `guarded` (when given), so the
    call's `tool_end` can say a guard rejected it. What the guard returns is unchanged."""
    if guarded is None:
        return guard

    async def recorded(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        output = await guard(hook_input, tool_use_id, context)
        decision = cast(dict[str, Any], output).get("hookSpecificOutput") or {}
        call = cast(dict[str, Any], hook_input).get("tool_use_id") or tool_use_id
        if decision.get("permissionDecision") == "deny" and call:
            guarded.add(str(call))
        return output

    return recorded


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
    dictionaries: Mapping[str, Path] | None = None,
    snapshot: Path | None = None,
    deadline: float | None = None,
    turn_budget: TurnBudget | None = None,
    guarded: set[str] | None = None,
    turn: ConversationTurn | None = None,
    sandbox: SandboxRun | None = None,
    network: bool = False,
    dbt: DbtRun | None = None,
    effort: str | None = None,
    trace: CliTrace | None = None,
    external_mcp: ExternalServers | None = None,
    charts: bool = False,
) -> ClaudeAgentOptions:
    """`emit` records events from hooks (the verifiers' start and end). `guarded` collects the
    ids of the calls the workspace guard denies (`guard_recorder`). With `spend` (the
    price-table cap, which the caller enforces with `capped`), the SDK streams what it needs and
    its own cap becomes the backstop (`backstop_usd`). `database_env` holds the Postgres
    variable: it reaches the MCP server through the SDK subprocess's environment. `task_file`
    (`saved_task`, verifier runs only) is named in the verifiers' prompts and may be Read.
    `dictionary` (`prebuilt_dictionary`, context runs only) is the value dictionary file the
    MCP server serves dictionary_search from; a run with several data sources gives them as
    `dictionaries`, by source name. `snapshot` (`taken_snapshot`, verifier runs only)
    is the directory the v2 tools compare the workspace against; it reaches the MCP server the
    way the Postgres URL does, through the SDK subprocess's environment. `deadline` (monotonic
    seconds, ticket 54) is when the trial ends: the v2 hooks stop dispatching verifiers once too
    little of it is left. A query run always gets the turn-budget hooks (ticket 69), whatever its
    layers; `turn_budget` holds the run's limit and is the one the caller feeds every SDK message
    (`TurnBudget.observe`), so a caller that streams the session passes its own. Without one the
    limit is the run config's, and nothing feeds the budget, so it reminds no one. A conversation
    `turn` resumes its previous session (`ConversationTurn.resume`) and keeps the session's record
    in the conversation's `CLAUDE_CONFIG_DIR`; without one (a single run, every evaluation) the
    options set neither. With `sandbox` the MCP server also serves sandbox_exec (ticket 97) on
    that backend, for that run id; without it the server's tools are what they were before. `dbt`
    (a dbt run with a sandbox, ticket 99) has the server serve dbt_execute on that target.
    `network` (ticket 98) loads WebSearch and WebFetch; without it, as on every evaluation, they
    are denied. Bash is denied on every run (ADR-0010). With the docker backend the Skills name
    their scripts as the container sees them (`SKILLS_MOUNT`). `effort` (ticket 104) is the
    session's reasoning effort; None leaves it to the CLI (`EFFORTS`), as before. With `trace`
    (ticket 112) the CLI exports its spans to the run's relay, at that tier's content; without
    it the options set no export variable at all.
    `external_mcp` (ticket 116, `data-agent run --mcp-servers`) adds the user's servers and the
    tools their allow lists name; without it, as on every evaluation, the session has only ours.
    `charts` (ticket 119) has the server serve display_chart, which `mcp__data` allows, and adds
    the charting rule to the system prompt."""
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
            *("--config", config.server_arg(), *task.database_args()),
            # A run without the verifier subagents gets no verifier tools either (the ablation).
            *(() if uses_verifiers(task.type, config) else ("--no-verifier-tools",)),
            *("--workspace", str(task.workspace.resolve())),
            *(("--dictionary", str(dictionary)) if dictionary else ()),
            *(
                arg
                for name, path in (dictionaries or {}).items()
                for arg in ("--source-dictionary", name, str(path))
            ),
            *(sandbox.server_args() if sandbox else ()),
            *(dbt.server_args() if dbt else ()),
            *(("--charts",) if charts else ()),
        ],
    }
    tools = BUILTIN_TOOLS
    guard = guard_recorder(
        workspace_guard(task.workspace, readable=(task_file,) if task_file else ()), guarded
    )
    hooks = [HookMatcher(matcher="|".join(FILE_TOOLS), hooks=[guard])]
    all_hooks: dict[HookEvent, list[HookMatcher]] = {"PreToolUse": hooks}
    agents = None
    if uses_verifiers(task.type, config):
        tools = [*tools, AGENT_TOOL]
        # The verifier role's model in each definition: the CLI lets no variable override a
        # definition's model (`CLAUDE_CODE_SUBAGENT_MODEL` did not, 2.1.330; ticket 145).
        agents = verifier_agents(MCP_SERVER, task_file, settings.verifier or settings.model)
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
    # The run-level switch (ticket 98): the web tools are loaded, or denied outright.
    web, denied = (WEB_TOOLS, DENIED_TOOLS) if network else ([], [*DENIED_TOOLS, *WEB_TOOLS])
    tools = [*tools, *web]
    for event, matchers in tool_timing_hooks(emit).items():
        all_hooks.setdefault(event, []).extend(matchers)
    if budget is not None:
        for event, matchers in budget.hooks(emit).items():
            all_hooks.setdefault(event, []).extend(matchers)
    if spend is not None:
        for event, matchers in spend.hooks(emit).items():
            all_hooks.setdefault(event, []).extend(matchers)
    if turn is not None:
        for event, matchers in clarification_hooks(emit).items():
            all_hooks.setdefault(event, []).extend(matchers)
    # The clarification tool's switch, set either way: a value the shell exported never reaches
    # a single run or an evaluation.
    server_env = dict(database_env or {}) | {CHAT_ENV: "" if turn is None else "1"}
    if snapshot is not None:
        server_env[SNAPSHOT_ENV] = str(snapshot)
    # Like the Postgres URL, through the SDK subprocess's environment: never argv or the MCP
    # config. The key is the `classify` role's (the one `sdk_env` gives the CLI, by default).
    # Without a ledger (a model without a price) the tools' calls are not counted (classify).
    server_env |= settings.tool_env()
    if spend is not None and spend.ledger is not None:
        server_env |= {LEDGER_ENV: str(spend.ledger.directory)}
    session_env = {} if turn is None else {"CLAUDE_CONFIG_DIR": str(turn.config_dir)}
    effort_env = {} if effort is None else {EFFORT_ENV: effort}
    # The variables the stdio wrapper reads (`external_mcp.launch`); first, so none can override
    # a setting below.
    external_env = {} if external_mcp is None else external_mcp.env()
    external_servers = {} if external_mcp is None else external_mcp.sdk_servers()
    external_tools = [] if external_mcp is None else external_mcp.allowed_tools()
    # Last, so no variable above can redirect the CLI's export (`tracing.cli_env`).
    trace_env = tracing.cli_env(trace, os.environ)
    return ClaudeAgentOptions(
        model=settings.model,
        # A value of the main model's `efforts` (checked before the run), which the SDK passes to
        # the CLI as given; its type names only the values Claude models take.
        effort=cast(EffortLevel, effort),
        env=external_env | settings.sdk_env() | server_env | session_env | effort_env | trace_env,
        resume=None if turn is None else turn.resume,
        system_prompt=system_prompt(
            task,
            config,
            chat=turn is not None,
            skills_root=SKILLS_MOUNT if sandbox and sandbox.backend == "docker" else None,
            charts=charts,
        ),
        cwd=str(task.workspace.resolve()),
        tools=tools,
        allowed_tools=[*tools, f"mcp__{MCP_SERVER}", *external_tools],
        disallowed_tools=denied,
        # Anything not allowed above is denied rather than prompted for.
        permission_mode="dontAsk",
        hooks=all_hooks,
        agents=agents,
        mcp_servers={MCP_SERVER: server, **external_servers},
        strict_mcp_config=True,
        # ADR-0001: user settings only, never this repo's .claude/; Skills come from the prompt.
        setting_sources=["user"],
        # Ticket 110: auto-compaction on, whatever the machine's user settings say (the eval
        # server's turn it off). `--settings` is the CLI's flagSettings, which it reads before
        # userSettings and ~/.claude.json; only managed policy settings come first.
        settings=json.dumps({"autoCompactEnabled": True}),
        skills=[],
        max_turns=max_turns,
        # Without a price there is no cap at all (decisions.md Q8): the SDK's own would price
        # the model at its fallback rates and stop the run at about 1/30 of the budget.
        max_budget_usd=(
            None if spend is None else backstop_usd(config.max_budget_usd, spend.price)
        ),
        include_partial_messages=spend is not None,
        stderr=stderr.append,
    )


# The fixed start of the gateway's error for a statement cut off at its timeout
# (`gateway.QueryTimedOut`; a test pins the two together). Only this run's MCP tools reach the
# gateway, so only their errors or responses are read for it: a shell grep, a Read of gateway.py or
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


# How Claude Code words the error of an MCP tool call (`Error executing tool <name>: <message>`,
# as real event files record it) whose server refused the statement at the gateway.
GATEWAY_BLOCKED = re.compile(rf"Error executing tool \S+: {re.escape(BLOCKED_PREFIX)}:")


def result_text(content: Any) -> str:
    """A tool result's text: the string itself, or its text blocks joined."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or "")
            for block in cast(list[Any], content)
            if isinstance(block, dict) and cast(dict[str, Any], block).get("type") == "text"
        )
    return ""


def rejected_by(
    block: ToolResultBlock, tool: str | None, guarded: Container[str]
) -> Literal["guard", "gateway"] | None:
    """`guard` when a guard hook denied the call (`guarded` holds those calls' ids), `gateway`
    when the read-only gateway refused its statement, else None."""
    if block.tool_use_id in guarded:
        return "guard"
    if (
        block.is_error
        and (tool or "").startswith(GATEWAY_TOOLS)
        and GATEWAY_BLOCKED.match(result_text(block.content))
    ):
        return "gateway"
    return None


def record_message(
    log: Emitter,
    message: Any,
    tools: dict[str, str],
    guarded: Container[str] = (),
    sources: Sequence[str] = (),
) -> None:
    """Translate one SDK message into events. `tools` maps tool_use ids to tool names; `guarded`
    holds the ids of the calls a guard denied (`guard_recorder`). `sources` names the run's data
    sources, and a `sql` event names the one its query ran on: a run's only source, or with
    several the query's `database` argument when it is one of them (the server refuses any
    other). A caller that names no sources gets no `database` field."""
    if isinstance(message, SystemMessage) and message.subtype == "init":
        log.emit(
            "status",
            state="started",
            model=message.data.get("model"),
            tools=message.data.get("tools"),
            mcp_servers=message.data.get("mcp_servers"),
            # A conversation's next turn resumes this session (`ConversationTurn.resume`).
            session_id=message_session(message),
        )
    elif isinstance(message, AssistantMessage):
        if message.error:
            log.emit("error", source="model", error=message.error)
        for block in message.content:
            if isinstance(block, TextBlock):
                log.emit("text", text=block.text, parent=message.parent_tool_use_id)
            elif isinstance(block, ThinkingBlock):
                log.emit("thinking", text=block.thinking, parent=message.parent_tool_use_id)
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
                    queried = sources[0] if len(sources) == 1 else block.input.get("database")
                    log.emit(
                        "sql",
                        tool_use_id=block.id,
                        sql=block.input.get("sql"),
                        **({"database": queried} if queried in sources else {}),
                    )
    elif isinstance(message, UserMessage) and isinstance(message.content, list):
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                tool = tools.get(block.tool_use_id)
                rejected = rejected_by(block, tool, guarded)
                log.emit(
                    "tool_end",
                    tool=tool,
                    tool_use_id=block.tool_use_id,
                    parent=message.parent_tool_use_id,
                    is_error=bool(block.is_error),
                    content=block.content,
                    **({"rejected_by": rejected} if rejected else {}),
                )


def message_session(message: Any) -> str | None:
    """The SDK session's id, as the init message and the result give it."""
    if isinstance(message, SystemMessage) and message.subtype == "init":
        return message.data.get("session_id")
    if isinstance(message, ResultMessage):
        return message.session_id
    return None


def result_fields(
    message: ResultMessage | None, model: str, price: Price | None = None
) -> dict[str, Any]:
    """Token counts and cost from the SDK's ResultMessage. Uncached input counts the tokens
    written to the cache too, since those are billed as fresh input.

    The SDK's own `total_cost_usd` (kept as `sdk_cost_usd`) is priced at Claude rates even for a
    non-Claude model, so `cost_usd` prices the tokens at `price` (the run's, `Spend.price`), or
    without one at the model's in PRICES, instead. A model with neither (Claude among them, which
    the SDK prices correctly) falls back to the SDK figure, and `cost_basis` says which one was
    used; a run of `run` without a price records its cost as unknown instead (`unknown_cost`).
    `max_budget_usd` caps `cost_usd` (`Spend`).
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
    price = price or PRICES.get(model)
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


def final_answer(message: ResultMessage | None) -> str | None:
    """The text of a run the SDK ended with a final answer: its `result`, the agent's final
    message. None when the run failed or ended without one, an API error among them (whose
    `result` is the error's text, as `run` reads an infrastructure error)."""
    if message is None or message.subtype != "success" or message.is_error:
        return None
    if message.api_error_status is not None or message.terminal_reason == "api_error":
        return None
    return message.result or None


# The token counts of a model's entry in the SDK's `model_usage` and in the CLI's cost state.
MODEL_USAGE_TOKENS = (
    "inputTokens",
    "cacheCreationInputTokens",
    "cacheReadInputTokens",
    "outputTokens",
)


@dataclass(frozen=True)
class SessionCost:
    """A session's spend as the CLI restores it when it resumes the session: `total_cost_usd` and
    the token counts per model (MODEL_USAGE_TOKENS)."""

    total_cost_usd: float
    model_usage: dict[str, dict[str, int]]


def restored_cost(config_dir: Path, session_id: str) -> SessionCost | None:
    """The cost state the CLI restores on resuming `session_id`: the last `cost-state` entry for
    that session in its record (`<config_dir>/projects/<cwd name>/<session_id>.jsonl`); None when
    there is none, and the CLI then restores nothing. Read from the CLI's bundle (2.1.301): the
    entry is `{"type": "cost-state", "sessionId", "totalCostUSD", "modelUsage": {model: {...}}}`,
    the last one wins, one that does not parse is skipped; on resume the CLI's cost tracker starts
    from it, so the session's next result (`total_cost_usd`, `model_usage`) holds the earlier
    turns' spend too. Its own `--max-budget-usd` check subtracts it."""
    found: SessionCost | None = None
    for path in sorted((config_dir / "projects").glob(f"*/{session_id}.jsonl")):
        for line in path.read_text(errors="replace").splitlines():
            if '"cost-state"' not in line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("type") != "cost-state" or entry.get("sessionId") != session_id:
                    continue
                found = SessionCost(
                    total_cost_usd=float(entry["totalCostUSD"]),
                    model_usage={
                        str(model): {key: int(fields.get(key) or 0) for key in MODEL_USAGE_TOKENS}
                        for model, fields in entry["modelUsage"].items()
                    },
                )
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
    return found


def minus_restored(message: ResultMessage, restored: SessionCost) -> ResultMessage | None:
    """The result of a resumed turn with the spend it restored (`restored_cost`) taken out of its
    totals, so its result event counts this turn alone (spec decisions 13, 18). None when the
    result holds less than the restored state: then the CLI did not restore it, and the result
    is this turn's as given."""
    cost = (message.total_cost_usd or 0.0) - restored.total_cost_usd
    short = cost < 0
    model_usage: dict[str, ModelUsage] = {}
    for model, fields in (message.model_usage or {}).items():
        before = restored.model_usage.get(model, {})
        own = {key: int(fields.get(key) or 0) - before.get(key, 0) for key in MODEL_USAGE_TOKENS}
        short = short or any(count < 0 for count in own.values())
        model_usage[model] = cast(ModelUsage, {**fields, **own})
    if short:
        return None
    if not message.model_usage:
        return dataclasses.replace(message, total_cost_usd=cost)
    return dataclasses.replace(message, model_usage=model_usage, total_cost_usd=cost)


def ended_by_sdk(message: ResultMessage | None, model: str, spend: Spend | None) -> dict[str, Any]:
    """The result event of a run the SDK ended: `result_fields`, and what `spend` missed."""
    fields = result_fields(message, model, None if spend is None else spend.price)
    return fields if spend is None else spend.with_tools(fields | spend.unmetered(fields))


def unknown_cost(fields: Mapping[str, Any]) -> dict[str, Any]:
    """The result event of a run whose models have no price (decisions.md Q8): the cost is
    unknown, whatever figure the SDK gave (still in `sdk_cost_usd`)."""
    return {**fields, "cost_usd": None, "cost_basis": "unknown"}


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
    return stopped_by_harness(spend, "error_deadline", "deadline", error) | {
        "open_tool_calls": open_tool_calls
    }


def turn_limit_fields(spend: Spend | None, passed: TurnLimitPassed) -> dict[str, Any]:
    """The result event of a steered turn stopped at its turn limit (`TurnLimitPassed`), its
    `num_turns` as the CLI reports a limit passed (limit + 1)."""
    fields = stopped_by_harness(spend, "error_max_turns", "max_turns", str(passed))
    return fields | {"num_turns": passed.max_turns + 1}


def stopped_by_harness(
    spend: Spend | None, subtype: str, terminal_reason: str, error: str
) -> dict[str, Any]:
    """The result event of a run the harness stopped itself; the SDK never sent its own. Spend
    is the price-table cap's count (`Spend.stopped_fields`); without a priced model nothing was
    counted, and the cost is unknown."""
    if spend is None:
        return unknown_cost(
            {
                **result_fields(None, ""),
                "subtype": subtype,
                "terminal_reason": terminal_reason,
                "sdk_cost_usd": None,
                "errors": [f"{error}; the model has no price, so its spend was not counted"],
            }
        )
    return spend.stopped_fields(subtype, terminal_reason, error)


def workspace_database(task: Task) -> Path | None:
    """The first database or WAL inside the workspace, including declared paths with any suffix."""
    root = task.workspace.resolve()
    for source in task.databases:
        if source.duckdb is None:
            continue
        given = Path(os.path.abspath(source.duckdb))
        if given.is_relative_to(root) or source.duckdb.resolve().is_relative_to(root):
            return source.duckdb
    for directory, _, names in root.walk():
        for name in names:
            entry = directory / name
            if named_or_linked(entry, is_duckdb_name):
                return entry
    return None


def check_databases_outside(task: Task) -> None:
    """The docker sandbox mounts the workspace writable, so keep database files outside it."""
    if (path := workspace_database(task)) is not None:
        raise ConfigError(
            f"the DuckDB file {path} is inside the workspace, which the docker sandbox mounts "
            "writable; keep it outside the workspace (--sandbox local is only for a run inside "
            "an isolated container)"
        )


def check_no_credentials(task: Task) -> None:
    """ConfigError when the workspace holds a credential file (`credential_files`): the docker
    sandbox mounts the whole workspace, and a shell in sandbox_exec is past the workspace guard.
    (A web run's workspace is a copy without them, `prepare_workspace`.)"""
    found = credential_files(task.workspace)
    if found:
        shown = ", ".join(str(path) for path in found[:5]) + (", ..." if len(found) > 5 else "")
        raise ConfigError(
            f"the workspace holds credential files ({shown}), which the docker sandbox would "
            "mount; move them out of the workspace, and have a profiles.yml read its secrets with "
            "env_var() (--sandbox local is only for a run inside an isolated container)"
        )


async def run(
    task: Task,
    config: RunConfig,
    events_dir: Path,
    env: Mapping[str, str],
    deadline: float | None = None,
    turn: ConversationTurn | None = None,
    steering: AsyncGenerator[str] | None = None,
    sandbox: Backend | None = None,
    network: bool = False,
    dbt_target: Target = "task",
    effort: str | None = None,
    trace_tier: TraceTier = "full",
    external_mcp: ExternalServers | None = None,
    charts: bool = False,
    models: ModelSettings | None = None,
) -> Outcome:
    """`deadline` is the trial's end in `time.monotonic()` seconds (ticket 54), None for none: the
    run stops itself DEADLINE_MARGIN_S before it, closing the SDK session, and records a `result`
    with `terminal_reason: "deadline"`; the outcome is an unfinished agent. `turn` makes the run a
    turn of a conversation (`ConversationTurn`): its `task` event names the conversation and the
    turn. `steering`, a `--chat` run's stdin lines, is sent into the running turn (`query`).
    `sandbox` gives the agent sandbox_exec on that backend (`SandboxRun.of_run` gives the run its
    id); `docker` needs the docker CLI. With `docker` the run removes its container when it ends,
    and the containers of killed runs before it starts (`docker_lifecycle`). `network` gives the
    agent the web tools (`build_options`) and the docker sandbox the network. A dbt run with
    `sandbox` also gets dbt_execute, writing to `dbt_target` (`DbtRun.of_run`: `scratch` keeps
    its profile in `<events_dir>/profiles/`, which must be outside the workspace). `effort`
    (ticket 104) is the session's reasoning effort, recorded in the `version` event when given.
    With all three Langfuse variables in `env` (`tracing.LangfuseSettings`, ticket 112) the run is
    exported at `trace_tier`: a relay the CLI exports to, the parent span `data-agent.run` around
    the session, and a `trace` event (with a `trace_export_error` per dropped batch) just before
    `result`; without them none of it is built.
    `external_mcp` (ticket 116) mounts the user's MCP servers (`build_options`); the `version`
    event names them and their allow lists, and the event log masks the values they expand.
    `charts` (ticket 119) gives the agent display_chart and the charting rule (`build_options`)
    and, in a query run, `CHARTS_SANDBOX_RULE` in place of SANDBOX_RULE. `models` (ticket 145)
    are the run's models, from the effective config (`ModelSettings.from_config`); None, the
    code defaults' and the environment's (`ModelSettings.from_env`). A run whose models have no
    price warns at its start (stderr and a `warning` event), passes the SDK no cap and records
    its cost as unknown."""
    if task.type == "dbt" and shutil.which("dbt", path=env.get("PATH")) is None:
        raise ConfigError("dbt is not on PATH; dbt tasks run the dbt the environment provides")
    settings = ModelSettings.from_env(env) if models is None else models
    settings.check_key()
    langfuse = LangfuseSettings.from_env(env)
    if sandbox == "docker":
        # Looked up on the module, so a test can stand in for the docker CLI (fake_docker.py).
        if not sandboxes.docker_on_path():
            raise ConfigError(
                "docker is not on PATH; the docker sandbox (the default --sandbox) needs Docker"
            )
        check_databases_outside(task)
        check_no_credentials(task)
    database_env: dict[str, str] = {}
    for variable in task.postgres_envs:
        # The URL variable goes into the SDK env on top of `sdk_env()`: a name a local sandbox
        # command keeps would let `echo $VAR` show the URL to the model; a name the SDK env sets,
        # or any BASH_/CLAUDE_ one, would override the CLI settings the harness relies on.
        if (
            reaches_local_commands(variable)
            or variable in settings.sdk_env()
            or variable in (*settings.tool_env(), LEDGER_ENV, CHAT_ENV)
            or variable.startswith(("BASH_", "CLAUDE_"))
        ):
            raise ConfigError(
                f"{variable} cannot hold the Postgres URL: the agent may print it, or "
                "it configures the Claude Code CLI; use another --postgres-env name"
            )
        if not env.get(variable):
            raise ConfigError(f"{variable} (the Postgres URL) is not set")
        # Only these variables: the server would otherwise read the parent's, or none.
        database_env[variable] = env[variable]
    if turn is not None:
        turn.config_dir.mkdir(exist_ok=True)
    # Read before the CLI resumes the session and appends to its record.
    restored = (
        None if turn is None or turn.resume is None else restored_cost(turn.config_dir, turn.resume)
    )
    events_dir.mkdir(parents=True, exist_ok=True)
    path = events_file(events_dir)
    stderr: list[str] = []
    final: ResultMessage | None = None
    spend = spend_cap(settings, config.max_budget_usd)
    secrets = known_secrets(env) + settings.secrets()
    names: list[str] = []
    for variable in task.postgres_envs:
        secrets += url_secrets(env[variable])
        names += url_names(env[variable])
    # Masked whether or not the export is on: a shell may hold them without the address.
    secrets += langfuse_keys(env)
    if external_mcp is not None:
        secrets += external_mcp.secrets()
        names += external_mcp.names()
    verified = uses_verifiers(task.type, config)
    sandbox_run = None if sandbox is None else SandboxRun.of_run(sandbox, events_dir, network)
    dbt_run = (
        DbtRun.of_run(dbt_target, events_dir)
        if sandbox_run is not None and task.type == "dbt"
        else None
    )
    if dbt_run is not None and dbt_run.profiles_dir is not None:
        # Before the session: a server that refused its arguments would leave a run without tools.
        try:
            check_outside(dbt_run.profiles_dir, task.workspace)
        except DbtExecuteError as exc:
            raise ConfigError(f"--dbt-target scratch: {exc}") from exc
    containers = (
        docker_lifecycle(sandbox_run.run_id)
        if sandbox_run is not None and sandbox_run.backend == "docker"
        else nullcontext()
    )
    saving = saved_task(task.text, task.workspace) if verified else nullcontext()
    snapshotting = taken_snapshot(task.workspace) if verified else nullcontext()
    exporting = (
        nullcontext() if langfuse is None else RunExport(langfuse, trace_tier, secrets, names)
    )
    commit = git_commit()
    skills = skill_version(config.skills_dir) if config.skill else "none"
    with (
        containers,
        saving as task_file,
        snapshotting as snapshot,
        tool_ledger(spend, task.workspace),
        EventLog(path, secrets=secrets, names=names) as log,
        exporting as export,
    ):
        log.emit(
            "version",
            git_commit=commit,
            skill_version=skills,
            skills=list(injected_skills(task.type, config, task.dialects)),
            config=config.name,
            layers=config.layers(),
            model=settings.model,
            sdk_version=claude_agent_sdk.__version__,
            cli_version=__cli_version__,
            # Only when given: a run without it records what it did before ticket 104.
            **({} if effort is None else {"effort": effort}),
            # Likewise only with --mcp-servers (ticket 116): names and allow lists, no values.
            **({} if external_mcp is None else {"external_mcp_servers": external_mcp.summary()}),
        )
        conversation = (
            {} if turn is None else {"conversation": turn.conversation, "turn": turn.number}
        )
        # With several sources the run has no one data source; each `sql` event names its own.
        data_source = None if task.several else task.data_source_name
        log.emit(
            "task",
            task=task.text,
            task_type=task.type,
            data_source=data_source,
            **conversation,
        )
        for model in settings.unpriced():
            warning = (
                f"the model {model} has no price ([models.{model}] input_price, "
                "cache_read_price, output_price): the run's spend is unknown and has no cap"
            )
            print(f"warning: {warning}", file=sys.stderr)
            log.emit("warning", model=model, warning=warning)
        if settings.main_overridden is not None:
            written, file = settings.main_overridden
            warning = (
                f"{MODEL_ENV}={settings.model} overrides [roles].main = {written!r} in {file}: "
                f"the run's main model is {settings.model}"
            )
            print(f"warning: {warning}", file=sys.stderr)
            log.emit(
                "warning",
                variable=MODEL_ENV,
                model=settings.model,
                overridden=written,
                file=file,
                warning=warning,
            )
        cli_trace = None
        if export is not None:
            facts = RunFacts(
                run_id=path.stem,
                session=path.stem if turn is None else turn.conversation,
                task_type=task.type,
                config=config.name,
                model=settings.model,
                skill_version=skills,
                git_commit=commit,
                task=task.text,
                conversation=None if turn is None else turn.conversation,
                turn=None if turn is None else turn.number,
            )
            # Off the event loop: it waits for Langfuse's project id.
            cli_trace = await asyncio.to_thread(export.start, facts)
            if cli_trace is None:
                log.emit("error", source="trace", error=export.failure)
                export = None
        source_names = tuple(source.name for source in task.databases)
        budget = None if task.type == "dbt" else TurnBudget(config.max_turns[task.type])
        query_prompt = CHARTS_QUERY_PROMPT if charts else QUERY_PROMPT
        prompt = (DBT_PROMPT if task.type == "dbt" else query_prompt).format(
            server=MCP_SERVER,
            turn_limit=TURN_LIMIT.format(max_turns=budget.max_turns) if budget else "",
            database=database_line(task),
            workspace=task.workspace.resolve(),
            docker_paths_note=docker_paths_note(sandbox),
            task=task.text,
            task_file_note=TASK_FILE_NOTE.format(path=task_file) if task_file else "",
        )
        tools: dict[str, str] = {}
        guarded: set[str] = set()
        building = (
            prebuilt_dictionary(task.gateway(env), task.workspace, log.emit)
            if config.context and not task.several
            else nullcontext()
        )
        building_each = (
            prebuilt_dictionaries(task.sources, task.workspace, env, log.emit)
            if config.context and task.several
            else nullcontext()
        )
        with building as dictionary, building_each as dictionaries:
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
                dictionaries=dictionaries,
                snapshot=snapshot,
                deadline=deadline,
                turn_budget=budget,
                guarded=guarded,
                turn=turn,
                sandbox=sandbox_run,
                network=network,
                dbt=dbt_run,
                effort=effort,
                trace=cli_trace,
                external_mcp=external_mcp,
                charts=charts,
            )
            if export is not None:
                options = dataclasses.replace(options, env=options.env | export.context_env())
            # The dev tier's export also takes the session's events (ticket 121).
            recorder = log if export is None else export.observing(log)
            open_calls: dict[str, str] = {}
            session_id: str | None = None
            over_limit: TurnLimitPassed | None = None
            # Cancels the stream at the deadline less its margin; closing the cancelled stream
            # (`aclosing`) ends the SDK session and its CLI subprocess.
            timer = asyncio.timeout(
                None
                if deadline is None
                else max(0.0, deadline - DEADLINE_MARGIN_S - time.monotonic())
            )
            # The parent span is current while the SDK starts the CLI, which it hands the span to.
            current = nullcontext() if export is None else export.active()
            with current:
                try:
                    async with timer:
                        try:
                            stream = (
                                query(prompt=prompt, options=options)
                                if steering is None
                                else query(
                                    prompt=prompt,
                                    options=options,
                                    steering=steering,
                                    emit=log.emit,
                                    spend=spend,
                                )
                            )
                            async with aclosing(capped(stream, spend)) as messages:
                                async for message in messages:
                                    session_id = message_session(message) or session_id
                                    if isinstance(message, ResultMessage):
                                        final = message
                                    else:
                                        if budget is not None:
                                            budget.observe(message)
                                        record_message(
                                            recorder, message, tools, guarded, source_names
                                        )
                                        track_open_calls(message, open_calls)
                        except TurnLimitPassed as exc:
                            over_limit = exc
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
        elif over_limit is not None:
            stopped = turn_limit_fields(spend, over_limit)
        else:
            stopped = None
        answer = final_answer(final) if stopped is None else None
        if answer is not None:
            # The agent's final message, the run's answer. The agent names no query it rests on
            # (no prompt asks it to), so the query is left empty rather than guessed.
            log.emit("answer", text=answer, sql_tool_use_id=None)
        turn_result = final
        if final is not None and restored is not None:
            turn_result = minus_restored(final, restored)
            if turn_result is None:
                log.emit(
                    "error",
                    source="spend",
                    error="the resumed session's result holds less than the cost state it "
                    f"restores ({restored.total_cost_usd} USD): the CLI did not restore it, so "
                    "the result is kept as given",
                )
                turn_result, restored = final, None
        fields = stopped or ended_by_sdk(turn_result, settings.model, spend)
        if spend is None:
            fields = unknown_cost(fields)
        # The run's end, before the export below waits for the relay to drain: readers time the
        # run by it, not by `result`'s own `ts` (web/runs.py, the run view's wall time).
        ended_ts = datetime.now(UTC).isoformat()
        if export is not None:
            # Before `result`, which readers take as the event file's end (web/runs.py). The CLI
            # has exited, so the relay has all it will get.
            for kind, values in await asyncio.to_thread(export.finish, fields, answer):
                log.emit(kind, **values)
        log.emit(
            "result",
            **fields,
            session_id=session_id,
            # The earlier turns' spend the CLI restored on resuming the session (`restored_cost`);
            # the counts above leave it out.
            **({} if restored is None else {"restored_sdk_cost_usd": restored.total_cost_usd}),
            ended_ts=ended_ts,
        )
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
