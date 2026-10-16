"""The Harbor adapter (evals.harbor_agent): what Harbor sees from one `data-agent run`.

The adapter's real shell command runs locally, in place of the task container, against a stub
`data-agent` that writes a scripted event file and exits with a scripted code. A real run through
Harbor is in the ticket's Comments (spec: the adapter is proved by a real run).
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
import yaml
from harbor.agents.installed.base import (
    AgentAuthenticationError,
    ApiRateLimitError,
    NonZeroAgentExitCodeError,
)
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig, RetryConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
# evals/ is a directory of the repo, not an installed package.
sys.path.insert(0, str(REPO_ROOT))

from evals import harbor_agent  # noqa: E402
from evals.harbor_agent import DataAgent  # noqa: E402

JOB_CONFIG = REPO_ROOT / "evals" / "configs" / "data-eng-bench-duckdb.data-agent.yaml"

STUB = """\
import json, os, pathlib, sys
args = sys.argv[1:]
pathlib.Path(os.environ["STUB_ARGS"]).write_text(json.dumps(args))
events = pathlib.Path(args[args.index("--events-dir") + 1])
events.mkdir(parents=True, exist_ok=True)
(events / "run-1.jsonl").write_text(os.environ["STUB_EVENTS"])
print("the answer")
print("error: " + os.environ.get("STUB_STDERR", ""), file=sys.stderr)
sys.exit(int(os.environ["STUB_EXIT"]))
"""


def result_event(**overrides: Any) -> dict[str, Any]:
    return {
        "seq": 3,
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 4,
        "terminal_reason": "completed",
        "api_error_status": None,
        "uncached_input_tokens": 1_000,
        "cached_input_tokens": 9_000,
        "output_tokens": 500,
        "cost_usd": 0.0123,
        "sdk_cost_usd": 0.5,
        "cost_basis": "price_table",
        "errors": [],
    } | overrides


VERSION_EVENT = {
    "seq": 0,
    "type": "version",
    "git_commit": "abc123",
    "skill_version": "def456",
    "config": "full",
    "model": "deepseek-flash",
}


def jsonl(*events: dict[str, Any]) -> str:
    return "".join(json.dumps(event) + "\n" for event in events)


class LocalEnvironment:
    """Runs commands with bash on this machine, as the task container would."""

    default_user = None

    def __init__(self, env: dict[str, str]) -> None:
        self.env = env
        self.commands: list[str] = []
        self.uploads: dict[str, Path] = {}

    async def exec(
        self,
        command: str,
        user: str | int | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> ExecResult:
        self.commands.append(command)
        proc = subprocess.run(
            ["bash", "-c", command],
            env=os.environ | self.env | (env or {}),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return ExecResult(stdout=proc.stdout, stderr=proc.stderr, return_code=proc.returncode)

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        self.uploads[target_path] = Path(source_path)


# The adapter class installed under a test directory, and that directory.
type Local = tuple[type[DataAgent], Path]


@pytest.fixture
def local(tmp_path: Path) -> Local:
    """A DataAgent installed under tmp_path, with a stub in place of the real `data-agent`."""
    agent_dir = tmp_path / "installed-agent"
    bin_dir = agent_dir / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    stub = bin_dir / "data-agent"
    stub.write_text(f"#!{sys.executable}\n{STUB}")
    stub.chmod(0o755)
    cls = type("LocalDataAgent", (DataAgent,), {"AGENT_DIR": PurePosixPath(agent_dir)})
    return cls, tmp_path


def make_agent(
    cls: type[DataAgent],
    tmp_path: Path,
    model: str | None = "deepseek/deepseek-flash",
    **kwargs: Any,
) -> DataAgent:
    logs = tmp_path / "logs" / "agent"
    logs.mkdir(parents=True, exist_ok=True)
    return cls(
        logs_dir=logs,
        model_name=model,
        environment_logs_dir=PurePosixPath(logs),
        extra_env={"DATA_AGENT_API_KEY": "sk-test", "DATA_AGENT_BASE_URL": "https://llm.example"},
        **kwargs,
    )


def container_env(tmp_path: Path, exit_code: int, events: str, **extra: str) -> dict[str, str]:
    return {
        "DB_TYPE": "duckdb",
        "DUCKDB_PATH": "/app/database/retail.duckdb",
        "DBT_PROJECT_DIR_DUCKDB": "/app/dbt_models_duckdb",
        "STUB_ARGS": str(tmp_path / "args.json"),
        "STUB_EVENTS": events,
        "STUB_EXIT": str(exit_code),
        **extra,
    }


def run(agent: DataAgent, env: LocalEnvironment, instruction: str = "fix the model") -> None:
    asyncio.run(agent.run(instruction, env, AgentContext()))  # type: ignore[arg-type]


def stub_args(tmp_path: Path) -> list[str]:
    return json.loads((tmp_path / "args.json").read_text())


def test_runs_the_dbt_task_on_the_images_project_and_database(local: Local) -> None:
    cls, tmp_path = local
    agent = make_agent(cls, tmp_path, run_config="no-skill")
    env = LocalEnvironment(container_env(tmp_path, 0, jsonl(VERSION_EVENT, result_event())))
    run(agent, env, instruction="Fix `rpt_x`; don't touch $HOME")
    args = stub_args(tmp_path)

    def value(flag: str) -> str:
        return args[args.index(flag) + 1]

    assert value("--type") == "dbt"
    assert value("--workspace") == "/app"
    assert value("--duckdb") == "/app/database/retail.duckdb"
    assert value("--config") == "no-skill"
    assert value("--task") == "Fix `rpt_x`; don't touch $HOME"
    assert Path(value("--events-dir")).is_relative_to(agent.logs_dir)
    assert "the answer" in (agent.logs_dir / "data-agent.txt").read_text()


def test_model_settings_reach_the_agent_and_the_model_comes_from_harbor(local: Local) -> None:
    cls, tmp_path = local
    agent = make_agent(cls, tmp_path, model="deepseek/deepseek-v4-pro")
    env = LocalEnvironment(container_env(tmp_path, 0, jsonl(result_event())))
    seen: dict[str, str] = {}
    original = env.exec

    async def spy(command: str, **kwargs: Any) -> ExecResult:
        seen.update(kwargs.get("env") or {})
        return await original(command, **kwargs)

    env.exec = spy  # type: ignore[method-assign]
    run(agent, env)
    assert seen["DATA_AGENT_API_KEY"] == "sk-test"
    assert seen["DATA_AGENT_BASE_URL"] == "https://llm.example"
    assert seen["DATA_AGENT_MODEL"] == "deepseek-v4-pro"


def test_an_unfinished_agent_is_left_to_the_verifier(local: Local) -> None:
    """Exit 1 (turn or budget cap): an agent failure, scored by the verifier, never retried."""
    cls, tmp_path = local
    events = jsonl(result_event(subtype="error_max_turns", is_error=True))
    run(make_agent(cls, tmp_path), LocalEnvironment(container_env(tmp_path, 1, events)))


@pytest.mark.parametrize(
    ("status", "error"),
    [(429, ApiRateLimitError), (401, AgentAuthenticationError)],
)
def test_a_failed_model_call_raises_harbors_error_for_its_status(
    local: Local, status: int, error: type[Exception]
) -> None:
    cls, tmp_path = local
    events = jsonl(result_event(is_error=True, api_error_status=status))
    env = LocalEnvironment(container_env(tmp_path, 2, events, STUB_STDERR="API Error"))
    with pytest.raises(error):
        run(make_agent(cls, tmp_path), env)


def test_a_model_call_that_lost_its_connection_is_retried(local: Local) -> None:
    """No HTTP status: the connection dropped (agent.run's api_error without a status)."""
    cls, tmp_path = local
    final = result_event(is_error=True, terminal_reason="api_error")
    env = LocalEnvironment(container_env(tmp_path, 2, jsonl(final), STUB_STDERR="Connection"))
    with pytest.raises(NonZeroAgentExitCodeError) as raised:
        run(make_agent(cls, tmp_path), env)
    assert type(raised.value).__name__ in harbor_agent.TRANSIENT_ERRORS


def test_a_cut_short_event_line_does_not_hide_the_status(local: Local) -> None:
    """A run killed mid-write leaves half a line after the result event."""
    cls, tmp_path = local
    events = jsonl(result_event(is_error=True, api_error_status=429)) + '{"seq": 9, "ty'
    env = LocalEnvironment(container_env(tmp_path, 2, events))
    with pytest.raises(ApiRateLimitError):
        run(make_agent(cls, tmp_path), env)


def test_a_run_that_broke_without_a_status_keeps_harbors_own_classification(local: Local) -> None:
    cls, tmp_path = local
    env = LocalEnvironment(container_env(tmp_path, 2, "", STUB_STDERR="DATA_AGENT_API_KEY"))
    with pytest.raises(NonZeroAgentExitCodeError) as raised:
        run(make_agent(cls, tmp_path), env)
    assert type(raised.value).__name__ not in harbor_agent.TRANSIENT_ERRORS


def test_only_the_duckdb_variant_is_supported(local: Local) -> None:
    cls, tmp_path = local
    env = LocalEnvironment(container_env(tmp_path, 0, jsonl(result_event()), DB_TYPE="snowflake"))
    with pytest.raises(NonZeroAgentExitCodeError, match="DB_TYPE"):
        run(make_agent(cls, tmp_path), env)
    assert not (tmp_path / "args.json").exists()


def test_an_unknown_run_config_is_refused_before_any_trial() -> None:
    with pytest.raises(ValueError, match="run_config"):
        DataAgent.preflight({"run_config": "nope"})
    DataAgent.preflight({"run_config": "baseline"})


def test_tokens_and_cost_are_filled_back_into_harbors_result(tmp_path: Path) -> None:
    logs = tmp_path / "agent"
    (logs / "events").mkdir(parents=True)
    (logs / "events" / "run-1.jsonl").write_text(jsonl(VERSION_EVENT, result_event()))
    agent = DataAgent(logs_dir=logs, model_name="deepseek/deepseek-flash")
    context = AgentContext()
    agent.populate_context_post_run(context)
    # Harbor counts cached tokens inside n_input_tokens (AgentContext).
    assert (context.n_input_tokens, context.n_cache_tokens) == (10_000, 9_000)
    assert context.n_output_tokens == 500
    assert context.cost_usd == pytest.approx(0.0123)
    assert context.model_usage is not None
    assert context.model_usage["deepseek-flash"].cost_usd == pytest.approx(0.0123)
    assert context.metadata is not None
    assert context.metadata["git_commit"] == "abc123"
    assert context.metadata["config"] == "full"
    assert context.metadata["cost_basis"] == "price_table"


def test_a_run_without_a_result_leaves_the_context_empty(tmp_path: Path) -> None:
    logs = tmp_path / "agent"
    (logs / "events").mkdir(parents=True)
    (logs / "events" / "run-1.jsonl").write_text(jsonl(VERSION_EVENT))
    agent = DataAgent(logs_dir=logs, model_name="deepseek/deepseek-flash")
    context = AgentContext()
    agent.populate_context_post_run(context)
    assert context.is_empty()


def test_job_config_retries_only_transient_model_errors() -> None:
    """plan §7: retry rate limits, 5xx and disconnects; Harbor's default exclusions stay."""
    config = JobConfig.model_validate(yaml.safe_load(JOB_CONFIG.read_text()))
    assert config.retry.max_retries >= 1
    assert config.retry.include_exceptions == set(harbor_agent.TRANSIENT_ERRORS)
    assert config.retry.exclude_exceptions == RetryConfig().exclude_exceptions
    excluded = RetryConfig().exclude_exceptions or set()
    assert not set(harbor_agent.TRANSIENT_ERRORS) & excluded
    (agent,) = config.agents
    assert agent.import_path == "evals.harbor_agent:DataAgent"
    assert (agent.override_setup_timeout_sec or 0) > DataAgent.DEFAULT_SETUP_TIMEOUT_SEC
    assert config.environment.env == {"DB_TYPE": "duckdb"}


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 529])
def test_rate_limits_and_server_errors_map_to_retried_errors(status: int) -> None:
    error = harbor_agent.status_error(status)
    assert error is not None and error.__name__ in harbor_agent.TRANSIENT_ERRORS


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 413])
def test_client_errors_are_never_retried(status: int) -> None:
    error = harbor_agent.status_error(status)
    assert error is None or error.__name__ not in harbor_agent.TRANSIENT_ERRORS


def test_install_puts_the_locked_agent_in_its_own_virtualenv(tmp_path: Path) -> None:
    recorded = LocalEnvironment({})

    async def no_exec(command: str, **_: Any) -> ExecResult:
        recorded.commands.append(command)
        return ExecResult(stdout="", stderr="", return_code=0)

    recorded.exec = no_exec  # type: ignore[method-assign]
    agent = DataAgent(logs_dir=tmp_path, model_name="deepseek/deepseek-flash")
    seen: dict[str, str] = {}

    async def upload(source_path: Path | str, target_path: str) -> None:
        seen[target_path] = Path(source_path).read_text(errors="replace")

    recorded.upload_file = upload  # type: ignore[method-assign]
    asyncio.run(agent.install(recorded))  # type: ignore[arg-type]
    wheel = next(path for path in seen if path.endswith(".whl"))
    requirements = seen[next(path for path in seen if path.endswith("requirements.txt"))]
    assert "claude-agent-sdk==0.2.168" in requirements
    assert "--hash=sha256:" in requirements
    assert "dbt-core" not in requirements  # the image's dbt, not ours (plan §5)
    script = "\n".join(recorded.commands)
    assert f"{DataAgent.AGENT_DIR}/venv" in script
    assert wheel in script
