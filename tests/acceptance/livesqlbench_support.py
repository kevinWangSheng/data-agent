"""Helpers shared by the LiveSQLBench acceptance tests (.scratch/livesqlbench/spec.md "Testing
Decisions" and U1-U11, ticket 127): test_livesqlbench_tasks.py, test_livesqlbench_split.py,
test_livesqlbench_scoring.py, test_livesqlbench_entry.py, test_livesqlbench_leaks.py,
test_livesqlbench_readonly.py. No database, model, download or server.

Fixtures (tests/fixtures/livesqlbench/): `public.jsonl` holds three invented records with the
public fields only (two `Query`, one `Management`), and `db_assets/<db>/` each invented database's
public assets. Nothing comes from the Base-Lite data: its license is unclear, a copied task could
be a holdout task (ADR-0004), and tests carry no benchmark content (ADR-0005). The answers
(`sol_sql`, `test_cases`, `external_knowledge`) are stand-ins built here, distinct sentinel
strings per record, written to a file under the test's tmp_path; the tests look for them, and for
that file's path, where the agent or a pulled run could see them.

The Harbor scaffolding (`LocalEnvironment`, `make_agent`, `jsonl`, `result_event`) comes from
tests/test_harbor_agent.py and the `da-remote` one (`Env`, `pulled_whole`) from
tests/test_da_remote.py, imported with `tests/` put on `sys.path` (neither directory is a
package). Their stub `data-agent` writes only what the test hands it, so the one here also
records its own argv and environment, and writes both into its event file, as a real run's event
file carries the task it was given.

Values read out of something today's code does not produce go through `assert` first (as
tests/acceptance/mcp_support.py does), so an expected failure is an `AssertionError` and not a
`KeyError` or `ValueError`.
"""

import asyncio
import json
import os
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext
from harbor.models.task.task import Task

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_da_remote import Env, pulled_whole  # noqa: E402
from test_harbor_agent import LocalEnvironment, make_agent  # noqa: E402

from evals import livesqlbench  # noqa: E402
from evals.harbor_agent import DataAgent  # noqa: E402

__all__ = ["Env", "pulled_whole"]

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "livesqlbench"
PUBLIC = FIXTURE / "public.jsonl"
DB_ASSETS = FIXTURE / "db_assets"
# Upstream's default credential (root/123123): LiveSQLBench-CLI/adapters/livesqlbench/template/
# environment/docker-compose.yaml:13-14 gives it to the agent's `main` service, :24-25 to the
# database; documents/db_env.sh exports it too.
UPSTREAM_PASSWORD = "123123"
# The write credential's stand-in given to `generate_tasks` and planted in the adapter's agent env.
WRITE_PASSWORD = "lsb-write-" + "Zq81" + "-standin"
# Host variables a write credential could sit in; the adapter is given the stand-in under each.
WRITE_PASSWORD_VARS = ("PGPASSWORD", "LSB_PG_WRITE_PASSWORD", "POSTGRES_PASSWORD")
MODEL_ENV = {"DATA_AGENT_API_KEY": "sk-test", "DATA_AGENT_BASE_URL": "https://llm.example"}


def records() -> list[dict[str, Any]]:
    return [json.loads(line) for line in PUBLIC.read_text().splitlines() if line.strip()]


def query_records() -> list[dict[str, Any]]:
    return [r for r in records() if r["category"] == "Query"]


def management_records() -> list[dict[str, Any]]:
    return [r for r in records() if r["category"] == "Management"]


def answer(instance_id: str) -> dict[str, Any]:
    """The hidden fields' stand-ins for one record: sentinel strings, distinct per record."""
    tag = instance_id.upper()
    return {
        "instance_id": instance_id,
        "sol_sql": [f"SELECT 'SOLSQL-{tag}-x91' AS v"],
        "test_cases": [f"TESTCASE-{tag}-k27"],
        "external_knowledge": [f"EXTKNOW-{tag}-m53"],
    }


def sentinels(instance_id: str) -> list[str]:
    a = answer(instance_id)
    return [f"SOLSQL-{instance_id.upper()}-x91", *a["test_cases"], *a["external_knowledge"]]


def all_sentinels() -> list[str]:
    return [s for r in records() for s in sentinels(r["instance_id"])]


def write_answers(path: Path) -> Path:
    """The answer file: a JSONL of every record's hidden fields, under a directory of its own."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(answer(r["instance_id"])) + "\n" for r in records()))
    return path


@dataclass(frozen=True)
class Generated:
    tasks: list[Path]
    answers: Path


def generate(tmp_path: Path) -> Generated:
    """The fixture's tasks, generated under tmp_path/tasks; the answers are in tmp_path/hidden."""
    answers = write_answers(tmp_path / "hidden" / "lsb-answers.jsonl")
    tasks = livesqlbench.generate_tasks(
        PUBLIC, answers, DB_ASSETS, tmp_path / "tasks", write_password=WRITE_PASSWORD
    )
    return Generated(tasks=[Path(t) for t in tasks], answers=answers)


def task_of(generated: Generated, instance_id: str) -> Path:
    name = f"{livesqlbench.TASK_PREFIX}{instance_id}"
    found = [t for t in generated.tasks if t.name == name]
    assert len(found) == 1, f"no generated task {name} in {[t.name for t in generated.tasks]}"
    return found[0]


def files_under(root: Path) -> Iterator[Path]:
    return (p for p in sorted(root.rglob("*")) if p.is_file())


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def agent_visible(task_dir: Path) -> dict[str, str]:
    """What the agent can see of a task: the instruction and every file under `environment/` (its
    image, its compose services and what they mount, `db_assets` among them), by relative path,
    plus its container's variables (`agent_container_env`) under the key `container env`.
    `tests/` (the verifier's, uploaded after the agent) and `solution/` (the oracle's) are left
    out, and of `task.toml` only `[environment] env` reaches the agent."""
    seen = {"instruction.md": read(task_dir / "instruction.md")}
    for path in files_under(task_dir / "environment"):
        seen[str(path.relative_to(task_dir))] = read(path)
    seen["container env"] = json.dumps(agent_container_env(task_dir))
    return seen


def compose_main(task_dir: Path) -> dict[str, Any]:
    """The agent's compose service (`main`, Harbor's name for it), or {} without a compose file."""
    compose = task_dir / "environment" / "docker-compose.yaml"
    if not compose.exists():
        return {}
    services = (yaml.safe_load(compose.read_text()) or {}).get("services") or {}
    return services.get("main") or {}


def main_env(task_dir: Path) -> dict[str, str]:
    env = compose_main(task_dir).get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) if "=" in item else (item, "") for item in env)
    return {str(k): "" if v is None else str(v) for k, v in env.items()}


def agent_container_env(task_dir: Path) -> dict[str, str]:
    """The variables the agent's container gets from the task: `[environment] env` in task.toml
    and the compose `main` service's `environment`."""
    return dict(Task(task_dir).config.environment.env) | main_env(task_dir)


def leaks(texts: Mapping[str, str], needles: list[str]) -> list[tuple[str, str]]:
    return [(where, n) for where, text in texts.items() for n in needles if n in text]


# The stub `data-agent`: records its argv and environment, and writes both into its event file.
STUB = """\
import json, os, pathlib, sys
args = sys.argv[1:]
out = pathlib.Path(os.environ["STUB_OUT"])
(out / "args.json").write_text(json.dumps(args))
(out / "environ.json").write_text(json.dumps(dict(os.environ)))
if "--events-dir" in args:
    events = pathlib.Path(args[args.index("--events-dir") + 1])
    events.mkdir(parents=True, exist_ok=True)
    lines = [{"seq": 0, "type": "version", "config": "full"},
             {"seq": 1, "type": "stub", "argv": args, "environ": dict(os.environ)},
             {"seq": 2, "type": "result", "subtype": "success", "is_error": False, "errors": []}]
    (events / "run-1.jsonl").write_text("".join(json.dumps(e) + "\\n" for e in lines))
print("the answer")
"""


class RecordingEnvironment(LocalEnvironment):
    """LocalEnvironment that also records the `env` each exec was given (the adapter's own)."""

    def __init__(self, env: dict[str, str]) -> None:
        super().__init__(env)
        self.exec_envs: list[dict[str, str]] = []

    async def exec(
        self,
        command: str,
        user: str | int | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> ExecResult:
        self.exec_envs.append(dict(env or {}))
        return await super().exec(command, user=user, env=env, cwd=cwd, timeout_sec=timeout_sec)


def local_agent_class(tmp_path: Path) -> type[DataAgent]:
    """DataAgent installed under tmp_path, with the stub above as its `data-agent`."""
    bin_dir = tmp_path / "installed-agent" / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    stub = bin_dir / "data-agent"
    stub.write_text(f"#!{sys.executable}\n{STUB}")
    stub.chmod(0o755)
    agent_dir = PurePosixPath(tmp_path / "installed-agent")
    return type("LocalDataAgent", (DataAgent,), {"AGENT_DIR": agent_dir})


@dataclass
class Trial:
    agent: DataAgent
    env: RecordingEnvironment
    out: Path

    @property
    def args(self) -> list[str]:
        path = self.out / "args.json"
        assert path.exists(), "the trial's command never reached data-agent"
        return json.loads(path.read_text())

    @property
    def environ(self) -> dict[str, str]:
        return json.loads((self.out / "environ.json").read_text())

    def value(self, flag: str) -> str:
        args = self.args
        assert flag in args, f"{flag} not in {args}"
        return args[args.index(flag) + 1]

    def events(self) -> dict[str, str]:
        """The event files the trial left in the agent logs directory, by path."""
        return {str(p): read(p) for p in files_under(self.agent.logs_dir) if p.suffix == ".jsonl"}


def new_agent(
    tmp_path: Path, agent_env: Mapping[str, str] | None = None, **kwargs: Any
) -> DataAgent:
    """The adapter with the stub `data-agent`, built by tests/test_harbor_agent.py `make_agent`
    (its model settings as Harbor's `--ae`), or with `agent_env` as the whole `--ae`; `kwargs`
    are `--ak` options."""
    cls = local_agent_class(tmp_path)
    if agent_env is None:
        return make_agent(cls, tmp_path, **kwargs)
    logs = tmp_path / "logs" / "agent"
    logs.mkdir(parents=True)
    return cls(
        logs_dir=logs,
        model_name="deepseek/deepseek-flash",
        environment_logs_dir=PurePosixPath(logs),
        extra_env=dict(agent_env),
        **kwargs,
    )


def run_trial(
    tmp_path: Path, agent: DataAgent, instruction: str, container_env: Mapping[str, str]
) -> Trial:
    """One trial of the adapter's real command, run locally with the stub `data-agent`;
    `container_env` stands for the task container's own variables. A command that fails before or
    in `data-agent` is an `AssertionError`."""
    out = tmp_path / "stub-out"
    out.mkdir()
    env = RecordingEnvironment({**container_env, "STUB_OUT": str(out)})
    try:
        asyncio.run(agent.run(instruction, env, AgentContext()))  # type: ignore[arg-type]
    except NonZeroAgentExitCodeError as exc:
        raise AssertionError(f"the trial's command failed: {exc}") from exc
    return Trial(agent=agent, env=env, out=out)


def without_host(environ: Mapping[str, str]) -> dict[str, str]:
    """A stub's environment minus what it inherited unchanged from this test process (the local
    stand-in for a container merges `os.environ`, which a container would not have)."""
    return {k: v for k, v in environ.items() if os.environ.get(k) != v}


# da-remote pull, as tests/test_da_remote.py drives it.
def lsb_server_manifest(dev: list[str], holdout: list[str]) -> dict[str, Any]:
    """The server checkout's manifest: data-eng-bench's entries plus LiveSQLBench ids (spec U2)."""
    return {"seed": 0, "dev": ["dbt-alpha", *dev], "holdout": ["dbt-delta", *holdout]}


def ablate_run(env: Env, manifest: dict[str, Any], jobs: dict[str, list[str]]) -> Path:
    """A finished ablate run on the server, as tests/test_da_remote.py `ablate_run` builds one
    (copied: that one fixes its manifest): per config a Harbor job over `tasks`, one finished
    trial per task, its log, and a report."""
    assert env.run("setup").returncode == 0
    (env.repo / "evals").mkdir(exist_ok=True)
    (env.repo / "evals" / "manifest.json").write_text(json.dumps(manifest))
    assert env.run("start", "smoke", "--", "ablate", "--budget", "1").returncode == 0
    env.finish(0)
    run = env.repo / "runs" / "smoke"
    for config, tasks in jobs.items():
        job = run / "jobs" / config
        job.mkdir(parents=True)
        datasets = [{"path": "[redacted-home]/lsb-tasks", "task_names": tasks}]
        (job / "config.json").write_text(json.dumps({"job_name": config, "datasets": datasets}))
        for task in tasks:
            (job / f"{task}__abc").mkdir()
            (job / f"{task}__abc" / "result.json").write_text(json.dumps({"task_name": task}))
        (run / "logs").mkdir(exist_ok=True)
        (run / "logs" / f"{config}.log").write_text("harbor output\n")
    (run / "logs" / "guards.log").write_text("spend ok\n")
    (run / "reports" / "t1").mkdir(parents=True)
    return run
