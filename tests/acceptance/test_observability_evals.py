"""The evaluation entries' export (.scratch/observability/spec.md "覆盖范围"; "Testing Decisions":
评测入口).

Written by ticket 111. Ticket 112 implements it and removes `TICKET_112`; nothing else here changes.

- Harbor (data-eng-bench): the adapter `evals.harbor_agent.DataAgent`, run against a recording
  stand-in for the task container. The three Langfuse variables are in the host's environment
  (`monkeypatch.setenv`), where the adapter reads them as it reads `MODEL_ENV` (Harbor's `_get_env`:
  `extra_env`, then `os.environ`): Harbor adds a job's `extra_env` to every exec of the agent phase
  (`harbor/environments/base.py` `scoped_exec_env`), so only `exec_as_agent(env=...)` can give them
  to one trial and not another. The task's name comes from `<trial>/config.json`, which Harbor
  writes before the agent runs (`TrialConfig.task.get_task_id().get_name()`); the split from
  `harbor_agent.MANIFEST`, here tests/fixtures/eval_manifest.json (dev `dbt-alpha`, holdout
  `dbt-delta`). Harbor gives the adapter no trial number, so "only the first trial" is checked as
  "exactly one trial".
- `evals ablate` and `evals board`: the `harbor run` arguments (`harbor_run_args`) with and
  without `--trace`.
- DAB: `evals.dab.run_query` with a scripted session, the relay `FakeRelay` and the parent span an
  in-memory exporter.
"""

import json
import shutil
import sqlite3
from collections.abc import AsyncIterator, Iterator
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import TaskConfig, TrialConfig
from observability_support import (
    CONTENT_SWITCHES,
    EVAL_MANIFEST,
    LANGFUSE_ENV,
    ExportStandIns,
    FakeLangfuse,
    result_message,
)

from data_agent.run_config import load_config
from evals import ablate, board, dab, harbor_agent
from evals.__main__ import build_parser
from evals.harbor_agent import DataAgent

TICKET_112 = pytest.mark.xfail(strict=True, reason="ticket 112: the evaluation entries' --trace")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FAKE_DAB = FIXTURES / "fake_dab"


@pytest.fixture
def langfuse(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeLangfuse]:
    """A fake Langfuse, its three variables in the host's environment."""
    fake = FakeLangfuse().start()
    for name, value in fake.env().items():
        monkeypatch.setenv(name, value)
    yield fake
    fake.stop()


# --- Harbor -------------------------------------------------------------------------------------


class Container:
    """The task container: every exec succeeds and is recorded with the env it was given."""

    default_user = None

    def __init__(self) -> None:
        self.execs: list[tuple[str, dict[str, str]]] = []

    async def exec(
        self,
        command: str,
        user: str | int | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> ExecResult:
        del user, cwd, timeout_sec
        self.execs.append((command, dict(env or {})))
        return ExecResult(stdout="", stderr="", return_code=0)

    def run(self) -> tuple[str, dict[str, str]]:
        """The one `data-agent run` exec."""
        [found] = [
            (c, e) for c, e in self.execs if "data-agent' run " in c or "data-agent run " in c
        ]
        return found


def trial(job: Path, task: str, number: int, **options: Any) -> DataAgent:
    """An adapter for trial `number` of `task` in `job`, laid out as Harbor lays a trial out."""
    directory = job / f"{task}__{number:07d}"
    logs = directory / "agent"
    logs.mkdir(parents=True)
    config = TrialConfig(task=TaskConfig(path=Path(f"tasks/{task}")), trial_name=directory.name)
    (directory / "config.json").write_text(config.model_dump_json(indent=2))
    return DataAgent(
        logs_dir=logs,
        model_name="deepseek/deepseek-flash",
        environment_logs_dir=PurePosixPath(logs),
        extra_env={"DATA_AGENT_API_KEY": "sk-test", "DATA_AGENT_BASE_URL": "https://llm.example"},
        **options,
    )


async def run_trial(adapter: DataAgent) -> tuple[str, dict[str, str]]:
    container = Container()
    await adapter.run("fix the model", container, AgentContext())  # type: ignore[arg-type]
    return container.run()


def langfuse_vars(env: dict[str, str]) -> dict[str, str]:
    return {name: value for name, value in env.items() if name in LANGFUSE_ENV}


def tier_flag(command: str) -> str | None:
    words = command.split()
    for i, word in enumerate(words):
        if word == "--trace-tier":
            return words[i + 1]
        if word.startswith("--trace-tier="):
            return word.removeprefix("--trace-tier=")
    return None


@pytest.fixture
def manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harbor_agent, "MANIFEST", EVAL_MANIFEST)


@TICKET_112
@pytest.mark.usefixtures("manifest")
async def test_harbor_passes_the_langfuse_variables_only_with_trace(
    tmp_path: Path, langfuse: FakeLangfuse
) -> None:
    command, env = await run_trial(trial(tmp_path / "traced", "dbt-alpha", 1, trace=True))
    assert langfuse_vars(env) == langfuse.env()
    assert tier_flag(command) == "full"
    _, env = await run_trial(trial(tmp_path / "plain", "dbt-alpha", 1))
    assert langfuse_vars(env) == {}


@TICKET_112
@pytest.mark.usefixtures("manifest")
@pytest.mark.parametrize(
    ("task", "tier"),
    [("dbt-alpha", "full"), ("dbt-delta", "structure"), ("dbt-zulu", "structure")],
    ids=["dev", "holdout", "not-in-the-manifest"],
)
async def test_harbor_picks_the_tier_by_the_tasks_split(
    tmp_path: Path, langfuse: FakeLangfuse, task: str, tier: str
) -> None:
    command, env = await run_trial(trial(tmp_path / "job", task, 1, trace=True))
    assert langfuse_vars(env) == langfuse.env()
    assert tier_flag(command) == tier


@TICKET_112
@pytest.mark.usefixtures("manifest")
async def test_harbor_exports_exactly_one_trial_per_task_and_job(
    tmp_path: Path, langfuse: FakeLangfuse
) -> None:
    for job, config in (("job-full", "full"), ("job-no-skill", "no-skill")):
        # Two tasks of one job, their trials interleaved, as Harbor runs them concurrently.
        envs: dict[str, list[dict[str, str]]] = {"dbt-alpha": [], "dbt-delta": []}
        for n in range(1, 4):
            for task in envs:
                adapter = trial(tmp_path / job, task, n, trace=True, run_config=config)
                _, env = await run_trial(adapter)
                envs[task].append(env)
        for task, task_envs in envs.items():
            exported = [env for env in task_envs if langfuse_vars(env)]
            assert len(exported) == 1, (job, task)
            assert langfuse_vars(exported[0]) == langfuse.env()


@TICKET_112
@pytest.mark.parametrize("command", ["ablate", "board"])
def test_ablate_and_board_ask_harbor_for_trace_only_with_trace(
    tmp_path: Path, command: str
) -> None:
    common = ["--out", str(tmp_path / "out"), "--budget", "1"]
    if command == "ablate":
        argv = ["ablate", "--tasks-dir", str(tmp_path), "--manifest", str(EVAL_MANIFEST), *common]
        build = ablate.harbor_run_args
    else:
        argv = ["board", "--config", "full", *common]
        build = board.harbor_run_args

    def run_args(*flags: str) -> list[str]:
        args = build_parser().parse_args([*argv, *flags])
        return build(args, tmp_path / "jobs", "full", ["dbt-alpha"], 3)

    def pairs(words: list[str]) -> list[tuple[str, str]]:
        return list(pairwise(words))

    assert ("--ak", "trace=true") in pairs(run_args("--trace"))
    assert ("--ak", "trace=true") not in pairs(run_args())


# --- DAB ----------------------------------------------------------------------------------------


def make_dab(root: Path) -> Path:
    """A DAB checkout with one dataset and one query (as tests/test_dab.py builds it)."""
    shutil.copytree(FAKE_DAB, root)
    dataset = root / "query_toyset"
    (dataset / "query_dataset").mkdir(parents=True)
    with sqlite3.connect(dataset / "query_dataset" / "prices.db") as conn:
        conn.execute("CREATE TABLE prices (ticker TEXT, close REAL)")
        conn.executemany("INSERT INTO prices VALUES (?, ?)", [("T001", 1.0), ("T002", 2.0)])
    (dataset / "dump").mkdir()
    (dataset / "dump" / "articles.bson").write_bytes(b"x" * 10)
    clients = {
        "prices_db": {"db_type": "sqlite", "db_path": "query_dataset/prices.db"},
        "articles_db": {"db_type": "mongo", "db_name": "articles", "dump_folder": "dump"},
    }
    (dataset / "db_config.yaml").write_text(json.dumps({"db_clients": clients}))
    (dataset / "db_description.txt").write_text("prices_db holds daily closes.\n")
    query = dataset / "query1"
    query.mkdir()
    (query / "query.json").write_text(json.dumps("Which ticker closed highest?"))
    (query / "ground_truth.csv").write_text("T002")
    (query / "validate.py").write_text("GT = 'T002'\n")
    return root


async def dab_sessions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, langfuse: FakeLangfuse, out: str, **kwargs: Any
) -> list[ClaudeAgentOptions]:
    seen: list[ClaudeAgentOptions] = []

    async def fake_query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        del prompt
        seen.append(options)
        yield result_message(f"answer {len(seen)}")

    monkeypatch.setattr(dab, "query", fake_query)
    monkeypatch.setattr(dab, "git_commit", lambda: "c0ffee")
    root = tmp_path / "dab"
    if not root.exists():
        make_dab(root)
    query = dab.load_query(root, "toyset", "1", hints=False)
    env = {"DATA_AGENT_API_KEY": "sk-test-secret"} | langfuse.env()
    await dab.run_query(root, query, load_config("no-context"), tmp_path / out, 2, env, **kwargs)
    return seen


def otel_keys(options: ClaudeAgentOptions) -> list[str]:
    return [name for name in options.env if name.startswith("OTEL_")]


@TICKET_112
async def test_dab_exports_the_first_run_of_a_query_on_the_dev_tier_only_with_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, langfuse: FakeLangfuse
) -> None:
    traced = ExportStandIns(monkeypatch)
    first, second = await dab_sessions(monkeypatch, tmp_path, langfuse, "traced", trace=True)
    assert {name: first.env.get(name) for name in CONTENT_SWITCHES} == dict.fromkeys(
        CONTENT_SWITCHES, "1"
    )
    assert first.env["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"].startswith("http://127.0.0.1:")
    [relay] = traced.relays
    assert relay.tier == "full"
    assert otel_keys(second) == []
    plain = await dab_sessions(monkeypatch, tmp_path, langfuse, "plain")
    assert [otel_keys(options) for options in plain] == [[], []]
    assert len(traced.relays) == 1
