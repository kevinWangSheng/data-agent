"""Harbor agent adapter (ADR-0001): runs `data-agent run` in a data-eng-bench task container.

`install()` puts this checkout's wheel and its locked dependencies into a virtualenv of its own,
so dbt stays the image's (plan §5). The dependencies are wheels for the container's platform from a
cache on this machine (evals.wheels), installed offline; the install's output and step times go to
`setup/install.log` in the agent logs directory. `run()` gives the task instruction to
`data-agent run` as a dbt task on the image's dbt project and DuckDB file, with the event file in
the agent logs directory.
After the trial Harbor calls `populate_context_post_run()`, which fills tokens and cost back into
the trial result from the event file's `result` event, and writes the run as an ATIF
`trajectory.json` in the logs directory (evals.atif), the file Harbor's uploader publishes as the
trial's `trajectory_path`. A missing or broken event file leaves the trial without a trajectory.
Both read the last event file by name (a trial runs `data-agent run` once).

Errors (plan §7): an agent that stops early (turn or spend cap, or the trial deadline the
`agent_timeout_sec` kwarg sets, ticket 54) is an agent failure the verifier scores; it raises
nothing. A failed model call raises Harbor's error for its HTTP status, or for a
dropped connection when it has none and the CLI's message says so, so the job config's retry list
(`TRANSIENT_ERRORS`) retries rate limits, server errors and disconnects only; a timed-out request
keeps Harbor's own classification, which is not retried.

Run from the repo root, with the `DATA_AGENT_*` variables in the environment:

    PYTHONPATH=. uv run harbor run --config evals/configs/data-eng-bench-duckdb.data-agent.yaml \\
        --path <data-eng-bench>/tasks -m deepseek/deepseek-flash -i dbt-fix-division-by-zero
"""

import asyncio
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Literal, override

from harbor.agents.installed.base import (
    AgentAuthenticationError,
    ApiConnectionClosedError,
    ApiInternalServerError,
    ApiOverloadedError,
    ApiRateLimitError,
    ApiUsageLimitError,
    BaseInstalledAgent,
    ModelNotFoundError,
    NonZeroAgentExitCodeError,
    with_prompt_template,
)
from harbor.agents.options import InstalledAgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext, ModelUsage
from harbor.models.trial.config import TrialConfig
from pydantic import Field, field_validator

from data_agent.run_config import CONFIG_NAMES, REPO_ROOT
from data_agent.tracing import LANGFUSE_ENV, TraceTier
from evals.atif import convert, usage
from evals.livesqlbench import BENCHMARK as LSB_BENCHMARK
from evals.livesqlbench import DATABASE_URL_ENV as LSB_DATABASE_URL_ENV
from evals.wheels import PROBE, Platform, download, wheelhouse

# Harbor error types worth retrying: rate limits, server errors and dropped connections. The job
# config retries exactly these; Harbor's default exclusions (timeouts, refusals, auth, usage
# limits) stay excluded.
TRANSIENT_ERRORS = (
    "ApiRateLimitError",
    "ApiInternalServerError",
    "ApiOverloadedError",
    "ApiConnectionClosedError",
    "ApiResponseStalledError",
)
STATUS_ERRORS: dict[int, type[NonZeroAgentExitCodeError]] = {
    401: AgentAuthenticationError,
    402: ApiUsageLimitError,  # DeepSeek: insufficient balance
    403: AgentAuthenticationError,
    404: ModelNotFoundError,
    429: ApiRateLimitError,
    503: ApiOverloadedError,
    529: ApiOverloadedError,
}
# The CLI's messages for a model call that lost its connection (its connection-error formatter);
# "Request timed out" and "No response from API" are timeouts, and SSL errors do not pass on retry.
DISCONNECTED = re.compile(
    r"^API Error: (?:Connection (?:dropped|lost|closed|refused|error\.|to the API was lost)"
    r"|Unable to connect to API(?!: SSL)|Can't reach the API server|No internet route)"
)
# The variables `data-agent run` reads its model from (agent.ModelSettings).
MODEL_ENV = ("DATA_AGENT_API_KEY", "DATA_AGENT_BASE_URL", "DATA_AGENT_MODEL")
# The eval split (dev / holdout), which picks a traced trial's tier (observability spec "覆盖范围").
# Read from this module when the adapter runs, so a test points it at a stand-in manifest.
MANIFEST = REPO_ROOT / "evals" / "manifest.json"
# The file a traced trial creates in its job directory to claim its task's one export: a plain
# file, since `harbor job resume` deletes the job's directories that hold no result.json.
TRACE_CLAIM = ".data-agent-trace-"
# data-eng-bench builds its image with `dbt ... --target dev` (base-image/Dockerfile:64), and the
# target is part of dbt's partial-parse key: a dbt call without `--target` re-parsed the whole
# project on each trial's first call. DBT_TARGET is dbt's environment variable for `--target`;
# dbt_execute and the local sandbox's commands (validate_project.py) inherit it, as they keep
# every DBT_* variable (data_agent.dbt_execute, data_agent.sandbox.LOCAL_TASK_PREFIXES). Set here
# only, for data-eng-bench (ADR-0005).
DBT_ENV = {"DBT_TARGET": "dev"}
# Where a LiveSQLBench agent writes its SQL in the workspace (upstream README_TASK.md:19-27).
LSB_PREDICTION = "pred.json"
# Pinned like the other tools; the container has no uv of its own. install() uploads uv's static
# Linux build for the container's `uname -m`, fetched here and checked against the release's
# `<archive>.sha256`.
UV_VERSION = "0.10.8"
UV_URL = "https://github.com/astral-sh/uv/releases/download/{version}/{name}.tar.gz"
UV_ARCHIVES: dict[str, tuple[str, str]] = {
    "aarch64": (
        "uv-aarch64-unknown-linux-musl",
        "2ef0d0489e9e2a32f134ca80097fa36be4b486c4ab004706a1d6d0d57980ff07",
    ),
    "x86_64": (
        "uv-x86_64-unknown-linux-musl",
        "a4e6ad1aecac61077de548d2cc9ccf2c2f1848863312b3b59fb0d2eb8d8a043c",
    ),
}

_build_lock = threading.Lock()
_built: tuple[Path, Path] | None = None
_uv_lock = threading.Lock()
_uv_archives: dict[str, Path] = {}


def task_name(trial_dir: Path) -> str | None:
    """The trial's task as the manifest names it, from the `config.json` Harbor writes before the
    agent runs; a registry task's `<org>/<task>` loses its org. None when it cannot be read."""
    try:
        config = TrialConfig.model_validate_json((trial_dir / "config.json").read_text())
        name = config.task.get_task_id().get_name()
    except (OSError, ValueError):  # pydantic's ValidationError is a ValueError
        return None
    return name.rsplit("/", 1)[-1] or None


def split_tier(task: str | None, manifest: Path) -> TraceTier:
    """The export's tier by the task's split (observability spec "覆盖范围"): `full` for a dev
    task, `structure` for a held-out one, one not in the manifest, or a manifest that cannot be
    read (as `da-remote pull` errs on the safe side)."""
    try:
        dev = json.loads(manifest.read_text())["dev"]
    except (OSError, ValueError, LookupError, TypeError):
        return "structure"
    return "full" if task is not None and isinstance(dev, list) and task in dev else "structure"


def claim_trace(job_dir: Path, task: str) -> bool:
    """Whether this trial is the first of `task` in the job to ask: the claim file is created
    atomically (`O_EXCL`), so of trials running at once exactly one gets it. A trial `harbor job
    resume` reruns finds the claim taken and exports nothing."""
    try:
        fd = os.open(job_dir / f"{TRACE_CLAIM}{task}", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError:
        return False
    os.close(fd)
    return True


def status_error(status: int | None) -> type[NonZeroAgentExitCodeError] | None:
    """Harbor's error type for a failed model call's HTTP status; None leaves Harbor's own
    classification of the output in place."""
    if status is None:
        return None
    if status in STATUS_ERRORS:
        return STATUS_ERRORS[status]
    return ApiInternalServerError if 500 <= status < 600 else None


def result_error(result: dict[str, Any]) -> type[NonZeroAgentExitCodeError] | None:
    """Harbor's error type for a run's `result` event. A model call that failed with no HTTP
    status lost its connection or timed out on the request (agent.run); only the first, told by
    the CLI's message, is a dropped connection and retried."""
    if (status := result.get("api_error_status")) is not None:
        return status_error(status)
    message = result.get("api_error") or ""
    if result.get("terminal_reason") == "api_error" and DISCONNECTED.match(message):
        return ApiConnectionClosedError
    return None


def parse_events(text: str) -> Iterator[dict[str, Any]]:
    """The events of an event file's text; a line cut short by a killed run is skipped."""
    for line in text.splitlines():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def build_artifacts() -> tuple[Path, Path]:
    """This checkout's wheel and its locked runtime requirements, built once per process."""
    global _built
    with _build_lock:
        if _built is None:
            out = Path(tempfile.mkdtemp(prefix="data-agent-harbor-"))
            uv = ["uv", "--directory", str(REPO_ROOT)]
            # Without the web UI (hatch_build.py): no pnpm and no frontend build here.
            subprocess.run(
                [*uv, "build", "--wheel", "--out-dir", str(out)],
                env=os.environ | {"DATA_AGENT_WHEEL_UI": "0"},
                check=True,
            )
            requirements = out / "requirements.txt"
            subprocess.run(
                [
                    *uv,
                    *("export", "--frozen", "--no-dev", "--no-emit-project"),
                    *("--format", "requirements.txt", "--output-file", str(requirements)),
                ],
                check=True,
                capture_output=True,
            )
            (wheel,) = out.glob("*.whl")
            _built = (wheel, requirements)
        return _built


def uv_archive(arch: str) -> Path:
    """uv's release archive for a container architecture (`uname -m`), fetched once per process."""
    with _uv_lock:
        if arch not in _uv_archives:
            if arch not in UV_ARCHIVES:
                raise RuntimeError(f"no uv build for container architecture {arch!r}")
            name, sha256 = UV_ARCHIVES[arch]
            path = Path(tempfile.mkdtemp(prefix="data-agent-uv-")) / f"{name}.tar.gz"
            download(UV_URL.format(version=UV_VERSION, name=name), path)
            if (digest := hashlib.sha256(path.read_bytes()).hexdigest()) != sha256:
                raise RuntimeError(f"{path.name}: sha256 {digest}, expected {sha256}")
            _uv_archives[arch] = path
        return _uv_archives[arch]


class DataAgentOptions(InstalledAgentOptions):
    run_config: str = Field(
        default="full", description="Run config name, from src/data_agent/configs."
    )
    # Harbor's own timeout (`asyncio.wait_for` around `run()`) never reaches the agent, so the
    # trial's limit is a plain kwarg, as Harbor's Cline agent has it (cline.py:83); the
    # leaderboard's static check rejects only the override and multiplier settings. The default
    # is data-eng-bench's task.toml limit.
    agent_timeout_sec: float = Field(
        default=4000,
        gt=0,
        description="The trial's time limit in seconds; data-agent stops itself before it.",
    )
    # Harbor's `--ak trace=true` (`evals ablate/board --trace`): export the trial's trace to
    # Langfuse, one trial per task (observability spec "覆盖范围").
    trace: bool = Field(default=False, description="Export this task's first trial to Langfuse.")
    # Harbor's `--ak benchmark=livesqlbench` (.scratch/livesqlbench/spec.md U5): which benchmark's
    # tasks the trial runs. `livesqlbench` runs a `--type query` task on the task's Postgres
    # (evals.livesqlbench, `_query_command`).
    benchmark: Literal["data-eng-bench", "livesqlbench"] = Field(
        default="data-eng-bench", description="The benchmark whose task the trial runs."
    )

    @field_validator("run_config")
    @classmethod
    def _known_config(cls, value: str) -> str:
        if value not in CONFIG_NAMES:
            raise ValueError(f"unknown run config {value!r}; choose from {', '.join(CONFIG_NAMES)}")
        return value


class DataAgent(BaseInstalledAgent):
    options_model = DataAgentOptions
    # Where the agent's virtualenv lives in the container.
    AGENT_DIR: ClassVar[PurePosixPath] = PurePosixPath("/installed-agent")
    # The task image's WORKDIR, where the official configs' agents work too.
    WORKSPACE: ClassVar[PurePosixPath] = PurePosixPath("/app")
    # Under the agent logs directory, which Harbor copies back to the trial directory.
    EVENTS_DIR: ClassVar[str] = "events"
    OUTPUT_FILE: ClassVar[str] = "data-agent.txt"
    # Where Harbor's own agents write their ATIF trajectory (claude_code.py:1669, pi.py:461) and
    # where its uploader looks for one (upload/uploader.py:680).
    TRAJECTORY_FILE: ClassVar[str] = "trajectory.json"
    # The instruction the last run() was given: the trajectory's user step.
    instruction: str | None = None

    @staticmethod
    @override
    def name() -> str:
        return "data-agent"

    @property
    def venv(self) -> PurePosixPath:
        return self.AGENT_DIR / "venv"

    @override
    def get_version_command(self) -> str | None:
        # The commit the wheel was built from (agent.git_commit): Harbor records it as the version.
        python = shlex.quote(str(self.venv / "bin" / "python"))
        return f"{python} -c 'from data_agent.agent import git_commit; print(git_commit())'"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        started = time.monotonic()
        wheel, requirements = await asyncio.to_thread(build_artifacts)
        probe = await environment.exec(command=f"python3 -c {shlex.quote(PROBE)}")
        if probe.return_code != 0:
            raise RuntimeError(f"cannot read the container's platform: {probe.stderr}")
        platform = Platform.from_probe(probe.stdout or "")
        archive = await asyncio.to_thread(uv_archive, platform.machine)
        house = await asyncio.to_thread(wheelhouse, requirements, REPO_ROOT / "uv.lock", platform)
        prepared = time.monotonic()
        remote_wheel = self.AGENT_DIR / wheel.name
        remote_requirements = self.AGENT_DIR / "requirements.txt"
        remote_archive = self.AGENT_DIR / archive.name
        remote_house = self.AGENT_DIR / "wheels"
        await environment.upload_file(wheel, str(remote_wheel))
        await environment.upload_file(requirements, str(remote_requirements))
        await environment.upload_file(archive, str(remote_archive))
        await environment.upload_dir(house, str(remote_house))
        uploaded = time.monotonic()
        uv = shlex.quote(str(self.AGENT_DIR / "uv"))
        python = shlex.quote(str(self.venv / "bin" / "python"))
        # No index and no network: every dependency is a wheel in the uploaded directory.
        offline = f"--offline --no-index --find-links {shlex.quote(str(remote_house))}"
        log = self.environment_logs_dir / "setup" / "install.log"
        host_line = (
            f"host: wheels for {platform} ready in {prepared - started:.1f} s, "
            f"uploaded in {uploaded - prepared:.1f} s"
        )
        await self.exec_as_root(
            environment,
            command=(
                "set -euo pipefail; "
                f"mkdir -p {shlex.quote(str(log.parent))}; "
                # Each step's command and wall time, in the log with its output.
                "TIMEFORMAT='  %R s'; "
                'step() { echo "$ $*" >&2; time "$@"; }; '
                f"{{ echo {shlex.quote(host_line)}; "
                # uv from the uploaded archive: fetching it inside the container from astral.sh
                # or GitHub failed the TLS handshake in 2 of 5 local trials.
                f"step tar -xzf {shlex.quote(str(remote_archive))} --strip-components 1 "
                f"-C {shlex.quote(str(self.AGENT_DIR))}; "
                # The image's Python; uv installs into the venv, so the venv needs no pip.
                f"step python3 -m venv --without-pip {shlex.quote(str(self.venv))}; "
                f"step {uv} pip install --python {python} {offline} --require-hashes "
                f"-r {shlex.quote(str(remote_requirements))}; "
                f"step {uv} pip install --python {python} {offline} --no-deps "
                f"{shlex.quote(str(remote_wheel))}; "
                f"step {shlex.quote(str(self.venv / 'bin' / 'data-agent'))} --help > /dev/null; "
                'echo "installed in $SECONDS s"; '
                f"}} 2>&1 | tee {shlex.quote(str(log))}"
            ),
        )

    def _model_env(self) -> dict[str, str]:
        env = {name: value for name in MODEL_ENV if (value := self._get_env(name))}
        if self._parsed_model_name:
            env["DATA_AGENT_MODEL"] = self._parsed_model_name
        return env

    def _benchmark_env(self) -> dict[str, str]:
        """The benchmark's own variables: dbt's target for data-eng-bench only (ADR-0005)."""
        assert isinstance(self.options, DataAgentOptions)
        return {} if self.options.benchmark == LSB_BENCHMARK else DBT_ENV

    def _trace(self) -> tuple[TraceTier | None, dict[str, str]]:
        """With `trace`: the tier of the trial's task, and the Langfuse variables when the host has
        all three and this trial claims its task's export (`claim_trace`); without, nothing."""
        assert isinstance(self.options, DataAgentOptions)
        if not self.options.trace:
            return None, {}
        trial_dir = self.logs_dir.parent
        task = task_name(trial_dir)
        tier = split_tier(task, MANIFEST)
        env = {name: value for name in LANGFUSE_ENV if (value := self._get_env(name))}
        if len(env) < len(LANGFUSE_ENV) or task is None or not claim_trace(trial_dir.parent, task):
            return tier, {}
        return tier, env

    def _run_command(self, instruction: str, trace_tier: TraceTier | None = None) -> str:
        assert isinstance(self.options, DataAgentOptions)
        if self.options.benchmark == LSB_BENCHMARK:
            return self._query_command(instruction, trace_tier)
        logs = self.environment_logs_dir
        return (
            # data-eng-bench's Snowflake variant needs a Snowflake gateway, which there is not.
            'if [ "$DB_TYPE" != duckdb ]; then '
            'echo "data-agent runs only DB_TYPE=duckdb, got DB_TYPE=$DB_TYPE" >&2; exit 2; fi; '
            f"{shlex.quote(str(self.venv / 'bin' / 'data-agent'))} run --type dbt "
            # The image's working directory and database (data-eng-bench base-image/Dockerfile).
            # Tasks edit the dbt project in it or create one beside it (/app/dbt_project, ...).
            f"--workspace {shlex.quote(str(self.WORKSPACE))} "
            '--duckdb "${DUCKDB_PATH:-/app/database/retail.duckdb}" '
            f"--config {shlex.quote(self.options.run_config)} "
            # The task container is the isolated environment (ADR-0010): sandbox_exec runs in it.
            # No --network: an evaluation stays offline. --dbt-target task: dbt writes the task's
            # own database, where it is graded (the CLI's default is scratch, ticket 140).
            "--sandbox local --dbt-target task "
            f"--events-dir {shlex.quote(str(logs / self.EVENTS_DIR))} "
            f"--agent-timeout-sec {shlex.quote(str(self.options.agent_timeout_sec))} "
            f"--task {shlex.quote(instruction)} "
            # Given on every traced trial, so a held-out task never falls to the default `full`.
            + (f"--trace-tier {trace_tier} " if trace_tier else "")
            + f"2>&1 | tee {shlex.quote(str(logs / self.OUTPUT_FILE))}; "
            # Exit 1 is an agent that did not finish: the verifier scores it like any attempt.
            'rc=$?; if [ "$rc" -eq 1 ]; then exit 0; fi; exit "$rc"'
        )

    def _query_command(self, instruction: str, trace_tier: TraceTier | None) -> str:
        """A LiveSQLBench trial (evals.livesqlbench): a query run on the task's Postgres, read
        through the read-only role whose URL the task container holds in `LSB_DATABASE_URL`
        (`--postgres-env`, so it is never on the command line). The agent writes its SQL to
        `/app/pred.json`, which is copied into the logs directory afterwards (ticket 128 Context
        9.1b: the trial keeps the agent's own answer; the verifier reads the original)."""
        assert isinstance(self.options, DataAgentOptions)
        logs = self.environment_logs_dir
        prediction = self.WORKSPACE / LSB_PREDICTION
        return (
            f"{shlex.quote(str(self.venv / 'bin' / 'data-agent'))} run --type query "
            f"--workspace {shlex.quote(str(self.WORKSPACE))} "
            f"--postgres-env {LSB_DATABASE_URL_ENV} "
            f"--config {shlex.quote(self.options.run_config)} "
            # As for dbt: the task container is the isolated environment, and no --network.
            "--sandbox local "
            f"--events-dir {shlex.quote(str(logs / self.EVENTS_DIR))} "
            f"--agent-timeout-sec {shlex.quote(str(self.options.agent_timeout_sec))} "
            f"--task {shlex.quote(instruction)} "
            + (f"--trace-tier {trace_tier} " if trace_tier else "")
            + f"2>&1 | tee {shlex.quote(str(logs / self.OUTPUT_FILE))}; "
            "rc=$?; "
            f"cp {shlex.quote(str(prediction))} {shlex.quote(str(logs / LSB_PREDICTION))} "
            "2>/dev/null || true; "
            'if [ "$rc" -eq 1 ]; then exit 0; fi; exit "$rc"'
        )

    async def _last_result(self, environment: BaseEnvironment) -> dict[str, Any] | None:
        events = shlex.quote(str(self.environment_logs_dir / self.EVENTS_DIR))
        found = await environment.exec(command=f"cat {events}/*.jsonl")
        if found.return_code != 0 or not found.stdout:
            return None
        results = [e for e in parse_events(found.stdout) if e.get("type") == "result"]
        return results[-1] if results else None

    @override
    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        self.instruction = instruction
        tier, trace_env = self._trace()
        try:
            await self.exec_as_agent(
                environment,
                command=self._run_command(instruction, tier),
                env=self._model_env() | self._benchmark_env() | trace_env,
            )
        except NonZeroAgentExitCodeError as exc:
            result = await self._last_result(environment)
            error = result_error(result) if result else None
            if error is None:
                raise
            raise error(str(exc)) from exc

    def _read_events(self) -> tuple[str, list[dict[str, Any]]] | None:
        """The last event file's name and events; None when there is none or it is unreadable."""
        paths = sorted((self.logs_dir / self.EVENTS_DIR).glob("*.jsonl"))
        if not paths:
            return None
        try:
            text = paths[-1].read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self.logger.warning(f"no trajectory: cannot read {paths[-1].name}: {exc}")
            return None
        return paths[-1].stem, [e for e in parse_events(text) if isinstance(e, dict)]

    def _write_trajectory(self, session_id: str, events: list[dict[str, Any]]) -> None:
        path = self.logs_dir / self.TRAJECTORY_FILE
        try:
            path.unlink(missing_ok=True)
            trajectory = convert(events, self.instruction, session_id)
            if trajectory is None:
                self.logger.warning("no trajectory: the event file holds no events")
                return
            text = json.dumps(trajectory.to_json_dict(), indent=2, ensure_ascii=False)
            path.write_text(text, encoding="utf-8")
        # Any failure leaves the trial without a trajectory, as Harbor's own agents do
        # (claude_code.py:1659); pydantic's ValidationError is the usual one.
        except Exception as exc:
            self.logger.warning(f"no trajectory: {type(exc).__name__}: {exc}")

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        read = self._read_events()
        if read is None:
            self.logger.warning("no trajectory: the run left no readable event file")
            return
        session_id, events = read
        self._write_trajectory(session_id, events)
        version: dict[str, Any] = {}
        result: dict[str, Any] | None = None
        for event in events:
            if event.get("type") == "version":
                version = event
            elif event.get("type") == "result":
                result = event
        if result is None:
            return
        try:
            totals = usage(result)
        except (ValueError, TypeError, ArithmeticError) as exc:
            self.logger.warning(f"no tokens or cost: corrupt result event: {exc}")
            return
        total_input, cached = totals.input_tokens, totals.cached_tokens
        output, cost = totals.output_tokens, totals.cost_usd
        context.n_input_tokens = total_input
        context.n_cache_tokens = cached
        context.n_output_tokens = output
        context.cost_usd = cost
        model = version.get("model") or self._parsed_model_name or "unknown"
        context.model_usage = {
            model: ModelUsage(
                n_input_tokens=total_input,
                n_cache_tokens=cached,
                n_output_tokens=output,
                cost_usd=cost,
            )
        }
        context.metadata = {
            **{key: version.get(key) for key in ("git_commit", "skill_version", "config")},
            **{
                key: result.get(key)
                for key in ("subtype", "num_turns", "terminal_reason", "cost_basis", "sdk_cost_usd")
            },
        }
