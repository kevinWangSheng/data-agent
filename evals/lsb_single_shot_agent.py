"""A Harbor agent for LiveSQLBench's official single-call baseline (ticket 138, spec D2).

It runs on the host, not in the task container: `run()` reads the task's database assets from the
agent's environment (`/app/db_assets/`, where the task mounts them), asks the model once with
upstream's prompt (evals.lsb_baseline), takes the SQL out of the reply as upstream does and writes
`/app/pred.json` in upstream's format. Harbor then collects that artifact and the task's own
verifier scores it in its separate environment, as for the data agent (evals.harbor_agent).

What it reads: the instruction (for the database and the question, `question`), the trial's
`config.json` (for the task id) and the three public asset files. Never the task's `tests/` (the
answers), and the prompt holds no `external_knowledge` (spec U8). A task that is not in the
manifest's dev set is refused before any model call (ADR-0004; spec D2 runs the dev set only).

The model and its settings (ticket 138 Context 1, 3): the data agent's model and endpoint
(`DATA_AGENT_BASE_URL`, `DATA_AGENT_API_KEY`, the job config's `deepseek/deepseek-flash`), with the
reasoning settings the agent's Claude Code CLI sends when no effort is set, as captured from a
`data-agent run` request (ticket 138 Comments): adaptive thinking (`display: updates`), effort
`high` and 32000 output tokens. That is two departures from upstream's call (`call_api.py`:
max_tokens 6000, no thinking): the comparison is "the same model", and 6000 tokens would be eaten
by the thinking. Temperature 0 as upstream; the CLI sends none.

Spend: the call's tokens priced at `data_agent.agent.PRICES` (peak prices) go into the trial's
context and into an event file in the agent logs directory (`events/run-<UTC stamp>-<id>.jsonl`)
of a `version` and a `result` event, the fields `evals spend` and `evals report` read, so a run is
totalled and billed (off-peak half price) as the data agent's runs are.

Errors: a failed model call raises Harbor's error for its HTTP status (evals.harbor_agent
`status_error`; a 400, the endpoint refusing the parameters, as `UnsupportedModelParametersError`,
a config error that stops the run), for a dropped connection `ApiConnectionClosedError`, and for a
call that did not finish within `CALL_DEADLINE_S` (retries included, under the task's limit)
`ApiResponseStalledError`, so the job config's retry list reruns the transient ones and a report
counts them as infrastructure. Each is raised with the key masked and without the SDK's exception
chained (Harbor writes it into the trial). No `pred.json` is written then. A reply with no SQL
in it (one cut off while thinking) is the model's own failure: `pred.json` is written with an
empty list, which the verifier scores 0.
"""

import asyncio
import json
import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, override

import anthropic
from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import (
    ApiConnectionClosedError,
    ApiResponseStalledError,
    NonZeroAgentExitCodeError,
)
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext, ModelUsage
from pydantic import Field

from data_agent.agent import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    PRICES,
    Tokens,
    git_commit,
    priced_usd,
    usage_tokens,
)
from evals.harbor_agent import LSB_PREDICTION, MANIFEST, status_error, task_name
from evals.livesqlbench import TASK_PREFIX
from evals.lsb_baseline import build_prompt, extract_sql_from_response
from evals.report import check_dev_list

# The config name the trials' version events record (evals.report's merge checks read it).
CONFIG_NAME = "single-shot"
MAX_TOKENS = 32000
THINKING: dict[str, Any] = {"type": "adaptive", "display": "updates"}
OUTPUT_CONFIG: dict[str, Any] = {"effort": "high"}
TEMPERATURE = 0
MODEL_RETRIES = 2
# The whole call, retries included, ends under the task's agent limit (evals.livesqlbench
# AGENT_TIMEOUT_SEC, 1800 s), leaving time to read the assets and write pred.json.
CALL_DEADLINE_S = 1500.0
MODEL_TIMEOUT_S = CALL_DEADLINE_S
ASSETS_DIR = "/app/db_assets"
PREDICTION = f"/app/{LSB_PREDICTION}"
EVENTS_DIR = "events"
RESPONSE_FILE = "response.txt"
# evals.livesqlbench `instruction`: the database, then the question, then the asset files.
QUESTION = re.compile(
    r"^Database: `(?P<db>[A-Za-z_][A-Za-z0-9_]*)` \(PostgreSQL\)\. Answer this question about "
    r"it:\n\n(?P<query>.*)\n\nFiles describing the database:\n- `/app/db_assets/(?P=db)_schema",
    re.DOTALL | re.MULTILINE,
)


def question(instruction: str) -> tuple[str, str]:
    """The database and the question of a generated task's instruction."""
    found = QUESTION.search(instruction)
    if found is None:
        raise ValueError("the instruction is not a generated LiveSQLBench task's")
    return found["db"], found["query"]


def dev_tasks(manifest: Path) -> list[str]:
    """The manifest's LiveSQLBench dev tasks. A manifest whose dev list names a task it or the
    repo's evals/manifest.json holds out is refused (evals.report `check_dev_list`), so another
    manifest cannot move a held-out task into the dev set."""
    split = json.loads(manifest.read_text())
    check_dev_list(split, manifest)
    return [t for t in split["dev"] if isinstance(t, str) and t.startswith(TASK_PREFIX)]


class UnsupportedModelParametersError(NonZeroAgentExitCodeError):
    """The endpoint refused the call's parameters (HTTP 400): the settings are wrong, not the
    model. Named as the error Harbor excludes from retries and evals.ablate's watcher stops the
    run for (`CONFIG_ERRORS`, matched by type name); Harbor 0.24 has no class of its own."""


def masked(text: str, secret: str) -> str:
    return text.replace(secret, "<redacted>") if secret else text


def stamp(at: datetime) -> str:
    return at.isoformat(timespec="microseconds")


class SingleShotOptions(AgentOptions):
    # The manifest whose dev set the task must be in; a test or a local stand-in task points it
    # elsewhere (`--ak manifest=...`).
    manifest: str = Field(default=str(MANIFEST), description="The manifest with the dev set.")


class SingleShot(BaseAgent):
    options_model = SingleShotOptions

    @staticmethod
    @override
    def name() -> str:
        return "lsb-single-shot"

    @override
    def version(self) -> str | None:
        return git_commit()

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        return

    def _api_key(self) -> str:
        api_key = self._get_env("DATA_AGENT_API_KEY")
        if not api_key:
            raise ValueError("DATA_AGENT_API_KEY is not set")
        return api_key

    def _client(self) -> anthropic.AsyncAnthropic:
        # Authenticated as the CLI is (a bearer token), as data_agent.classify's calls are.
        return anthropic.AsyncAnthropic(
            base_url=self._get_env("DATA_AGENT_BASE_URL") or DEFAULT_BASE_URL,
            auth_token=self._api_key(),
            max_retries=MODEL_RETRIES,
            timeout=MODEL_TIMEOUT_S,
        )

    async def _call(self, model: str, prompt: str) -> anthropic.types.Message:
        create = self._client().messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
            thinking=THINKING,  # pyright: ignore[reportArgumentType]
            output_config=OUTPUT_CONFIG,  # pyright: ignore[reportArgumentType]
            # Not a parameter of this SDK's create(); the endpoint takes it in the body.
            extra_body={"temperature": TEMPERATURE},
        )
        try:
            # One deadline over the SDK's retries too: past the task's limit Harbor would record
            # AgentTimeoutError, which a report scores 0 (an agent failure) instead of rerunning.
            return await asyncio.wait_for(create, timeout=CALL_DEADLINE_S)
        except (TimeoutError, anthropic.APITimeoutError):
            # The endpoint never answered: infrastructure, in the job config's retry list. Raised
            # as Harbor's type, never as TimeoutError, which Harbor's own deadline catches.
            error, message = ApiResponseStalledError, "the model call did not finish in time"
        # Harbor keeps the exception and its traceback in the trial (exception.txt): each is
        # raised again with the key masked and without the SDK's exception chained, which holds
        # the endpoint's response.
        except anthropic.APIStatusError as exc:
            error = status_error(exc.status_code) or (
                UnsupportedModelParametersError if exc.status_code == 400 else RuntimeError
            )
            message = masked(f"HTTP {exc.status_code}: {exc}", self._api_key())
        except anthropic.APIConnectionError as exc:
            error, message = ApiConnectionClosedError, masked(str(exc), self._api_key())
        raise error(message) from None

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        assert isinstance(self.options, SingleShotOptions)
        task = task_name(self.logs_dir.parent)
        if task is None or task not in dev_tasks(Path(self.options.manifest)):
            raise ValueError(f"{task!r} is not a LiveSQLBench dev task; this agent runs dev only")
        db, query = question(instruction)
        instance_id = task.removeprefix(TASK_PREFIX)
        model = self._parsed_model_name or DEFAULT_MODEL
        with tempfile.TemporaryDirectory(prefix="lsb-single-shot-") as scratch:
            assets = Path(scratch)
            for suffix in ("_schema.txt", "_column_meaning_base.json", "_kb.jsonl"):
                name = f"{db}{suffix}"
                await environment.download_file(f"{ASSETS_DIR}/{name}", assets / name)
            prompt = build_prompt(assets, db, query)

        started = datetime.now(UTC)
        message = await self._call(model, prompt)
        text = "".join(block.text for block in message.content if block.type == "text")
        tokens = usage_tokens(message.usage.model_dump())
        price = PRICES.get(model)
        if price is None:
            raise ValueError(f"no price for model {model!r} (data_agent.agent.PRICES)")
        cost = priced_usd(price, *tokens)

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / RESPONSE_FILE).write_text(text)
        self._write_events(started, model, message.stop_reason, tokens, cost)
        context.n_input_tokens = tokens.uncached + tokens.cached
        context.n_cache_tokens = tokens.cached
        context.n_output_tokens = tokens.output
        context.cost_usd = cost
        context.model_usage = {
            model: ModelUsage(
                n_input_tokens=tokens.uncached + tokens.cached,
                n_cache_tokens=tokens.cached,
                n_output_tokens=tokens.output,
                cost_usd=cost,
            )
        }
        context.metadata = {"config": CONFIG_NAME, "stop_reason": message.stop_reason}

        # Upstream's extraction gives one entry; an entry with no SQL (an empty reply) becomes no
        # entry, which the verifier scores 0 as `no_prediction` without a database call.
        sqls = [sql for sql in extract_sql_from_response(text) if sql.strip()]
        prediction = self.logs_dir / LSB_PREDICTION
        prediction.write_text(json.dumps({"instance_id": instance_id, "predicted_sql": sqls}))
        await environment.upload_file(prediction, PREDICTION)

    def _write_events(
        self,
        started: datetime,
        model: str,
        stop_reason: str | None,
        tokens: Tokens,
        cost: float,
    ) -> None:
        events = self.logs_dir / EVENTS_DIR
        events.mkdir(parents=True, exist_ok=True)
        name = f"run-{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}.jsonl"
        lines = [
            {
                "type": "version",
                "ts": stamp(started),
                "model": model,
                "git_commit": git_commit(),
                "skill_version": "none",
                "config": CONFIG_NAME,
                # The SDK that makes the call (CODING_STANDARDS.md: the version event's fields).
                "sdk_version": anthropic.__version__,
            },
            {
                "type": "result",
                "ts": stamp(datetime.now(UTC)),
                "num_turns": 1,
                "stop_reason": stop_reason,
                "uncached_input_tokens": tokens.uncached,
                "cached_input_tokens": tokens.cached,
                "output_tokens": tokens.output,
                "cost_usd": cost,
                "cost_basis": "price_table",
            },
        ]
        (events / name).write_text("".join(json.dumps(line) + "\n" for line in lines))
