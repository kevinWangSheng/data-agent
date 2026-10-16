"""Harbor agent adapter (ADR-0001): runs `data-agent run` in a data-eng-bench task container.

`install()` puts this checkout's wheel and its locked dependencies into a virtualenv of its own,
so dbt stays the image's (plan §5). `run()` gives the task instruction to `data-agent run` as a dbt
task on the image's dbt project and DuckDB file, with the event file in the agent logs directory.
After the trial Harbor calls `populate_context_post_run()`, which fills tokens and cost back into
the trial result from the event file's `result` event.

Errors (plan §7): an agent that stops early (turn or spend cap) is an agent failure the verifier
scores; it raises nothing. A failed model call raises Harbor's error for its HTTP status, or for a
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
import re
import shlex
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, override

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
from pydantic import Field, field_validator

from data_agent.run_config import CONFIG_NAMES, REPO_ROOT

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
            subprocess.run([*uv, "build", "--wheel", "--out-dir", str(out)], check=True)
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


def download(url: str, dest: Path) -> None:
    # Retries every error: a TLS handshake to GitHub's release host failed intermittently here.
    subprocess.run(
        ["curl", "-sSfL", "--retry", "5", "--retry-all-errors", "-o", str(dest), url], check=True
    )


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
        wheel, requirements = await asyncio.to_thread(build_artifacts)
        arch = (await environment.exec(command="uname -m")).stdout or ""
        archive = await asyncio.to_thread(uv_archive, arch.strip())
        remote_wheel = self.AGENT_DIR / wheel.name
        remote_requirements = self.AGENT_DIR / "requirements.txt"
        remote_archive = self.AGENT_DIR / archive.name
        await environment.upload_file(wheel, str(remote_wheel))
        await environment.upload_file(requirements, str(remote_requirements))
        await environment.upload_file(archive, str(remote_archive))
        uv = shlex.quote(str(self.AGENT_DIR / "uv"))
        python = shlex.quote(str(self.venv / "bin" / "python"))
        await self.exec_as_root(
            environment,
            command=(
                "set -euo pipefail; "
                # uv from the uploaded archive: fetching it inside the container from astral.sh
                # or GitHub failed the TLS handshake in 2 of 5 local trials.
                f"tar -xzf {shlex.quote(str(remote_archive))} --strip-components 1 "
                f"-C {shlex.quote(str(self.AGENT_DIR))}; "
                # The image's Python; uv installs into the venv, so the venv needs no pip.
                f"python3 -m venv --without-pip {shlex.quote(str(self.venv))}; "
                # uv's defaults (3 retries, 30 s) lost a PyPI fetch in 1 of 5 local trials.
                "export UV_HTTP_RETRIES=10 UV_HTTP_TIMEOUT=120; "
                f"{uv} pip install --python {python} --require-hashes "
                f"-r {shlex.quote(str(remote_requirements))}; "
                f"{uv} pip install --python {python} --no-deps {shlex.quote(str(remote_wheel))}; "
                f"{shlex.quote(str(self.venv / 'bin' / 'data-agent'))} --help > /dev/null"
            ),
        )

    def _model_env(self) -> dict[str, str]:
        env = {name: value for name in MODEL_ENV if (value := self._get_env(name))}
        if self._parsed_model_name:
            env["DATA_AGENT_MODEL"] = self._parsed_model_name
        return env

    def _run_command(self, instruction: str) -> str:
        assert isinstance(self.options, DataAgentOptions)
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
            f"--events-dir {shlex.quote(str(logs / self.EVENTS_DIR))} "
            f"--task {shlex.quote(instruction)} "
            f"2>&1 | tee {shlex.quote(str(logs / self.OUTPUT_FILE))}; "
            # Exit 1 is an agent that did not finish: the verifier scores it like any attempt.
            'rc=$?; if [ "$rc" -eq 1 ]; then exit 0; fi; exit "$rc"'
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
        try:
            await self.exec_as_agent(
                environment, command=self._run_command(instruction), env=self._model_env()
            )
        except NonZeroAgentExitCodeError as exc:
            result = await self._last_result(environment)
            error = result_error(result) if result else None
            if error is None:
                raise
            raise error(str(exc)) from exc

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        version: dict[str, Any] = {}
        result: dict[str, Any] | None = None
        for path in sorted((self.logs_dir / self.EVENTS_DIR).glob("*.jsonl")):
            for event in parse_events(path.read_text()):
                if event.get("type") == "version":
                    version = event
                elif event.get("type") == "result":
                    result = event
        if result is None:
            return
        cached = int(result.get("cached_input_tokens") or 0)
        # Harbor's n_input_tokens includes the cached tokens.
        total_input = int(result.get("uncached_input_tokens") or 0) + cached
        output = int(result.get("output_tokens") or 0)
        cost = float(result.get("cost_usd") or 0.0)
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
