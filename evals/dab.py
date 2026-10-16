"""`dab`: run our agent on DataAgentBench queries (ADR-0005) and write DAB's submission format.

DAB has no license, so nothing of it lives here: its scaffold is imported from a local clone given
by `--dab-root`, and its tools are served to the agent by `evals.dab_server`. The task text is
the query's `query.json` question plus the official description files (`db_description.txt`, with
`db_description_withhint.txt` appended unless `--no-hints`). Each run is one agent session in its
own directory:

    OUT/<dataset>/query<id>/run<k>/events.jsonl        the trace (our event file)
    OUT/<dataset>/query<id>/run<k>/dab_tool_calls.jsonl DAB's own tool log
    OUT/<dataset>/query<id>/run<k>/workspace/          the agent's and execute_python's directory
    OUT/submission.json                                [{dataset, query, run, answer}, ...]

DAB's tools need extra packages, and its `execute_python` needs Docker with DAB's image (built from
DAB's Dockerfile: `docker build -t python-data:3.12 ../DataAgentBench`; the Docker SDK finds the
daemon through DOCKER_HOST). Run it as

    uv run --with-requirements evals/dab-requirements.txt python -m evals dab \
        --dab-root ../DataAgentBench --dataset stockindex --query 1 --config full --out runs/dab

Exit 0 when every run ended; 1 when `--budget` stopped the batch; 2 on a config error or when a
run broke on infrastructure (left out of the submission, to be redone).
"""

import argparse
import asyncio
import importlib
import json
import os
import subprocess
import sys
from contextlib import aclosing
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

import claude_agent_sdk
from claude_agent_sdk import ResultMessage, SystemMessage
from claude_agent_sdk._cli_version import __cli_version__
from claude_agent_sdk.types import McpStdioServerConfig

from data_agent.agent import (
    MCP_SERVER,
    SKILLS_PROMPT,
    SYSTEM_PROMPT,
    ConfigError,
    ModelSettings,
    Task,
    build_options,
    capped,
    ended_by_sdk,
    git_commit,
    load_skills,
    query,
    record_message,
    skill_version,
    spend_cap,
)
from data_agent.events import REDACTED, EventLog, known_secrets
from data_agent.run_config import CONFIG_NAMES, REPO_ROOT, RunConfig, load_config
from evals import spend
from evals.walk import walk_files

RUNS_PER_QUERY = 5  # DAB README: 5 runs per query
# A query on a large database gets the run config's `large` turn limit instead of `query`
# (spec "运行配置", SignalPilot's 75 for its known-large databases). SignalPilot keeps a list
# measured from timeouts; with no measurements yet, "large" is the data's size on disk: DAB's
# smaller datasets stay under 40 MB, its larger ones start at about 300 MB.
LARGE_DB_BYTES = 250 * 1024**2
# Connection settings DAB's tools read (its common_scaffold/config.py), masked like our secrets.
# Values set only in DAB's own .env file are not seen here: set them in the environment.
DAB_SECRET_ENV = ("PG_HOST", "PG_PASSWORD", "MONGO_URI", "MYSQL_HOST", "MYSQL_PASSWORD")
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
EXEC_IMAGE = "python-data:3.12"  # DAB's ExecTool runs code in this image
SQL_WORKFLOW = "sql-workflow"
DIALECT_SKILLS = {"duckdb": "duckdb-sql", "sqlite": "sqlite-sql", "postgres": "postgres-sql"}

TASK_PROMPT = """\
Task type: query
Databases, reachable only through the `{server}` MCP tools (pass the name as `db_name`):
{databases}
Workspace (your working directory): {workspace}

<task>
{question}
</task>

<database_description>
{description}
</database_description>

End with a final message that gives the answer first, then the assumptions you made.
"""


class BudgetReached(Exception):
    """Spend under the output directory reached the stop fraction of the budget (plan §7)."""


class ConditionsChanged(ValueError):
    """A finished run under the output directory was made under other conditions than this one."""


@dataclass(frozen=True)
class Scaffold:
    """The parts of DAB's `common_scaffold` the runner uses, imported from the clone."""

    query_tool: type[Any]
    list_tool: type[Any]
    exec_tool: type[Any]
    result_marker: str  # what `execute_python` code prints before its JSON result
    load_db_clients: Any


@dataclass(frozen=True)
class DabQuery:
    dataset: str
    query_id: str
    question: str
    description: str
    hints: bool
    db_config: Path
    databases: dict[str, str]  # logical name -> DAB db_type
    data_bytes: int


def _import_from(root: Path, name: str) -> ModuleType:
    """Import a DAB module from `root`, dropping any copy imported earlier from another clone."""
    loaded = sys.modules.get(name)
    if loaded is not None and root not in Path(loaded.__file__ or "/").resolve().parents:
        for key in [k for k in sys.modules if k.split(".")[0] == "common_scaffold"]:
            del sys.modules[key]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    return importlib.import_module(name)


def scaffold(dab_root: Path) -> Scaffold:
    root = dab_root.resolve()
    tools = "common_scaffold.tools"
    return Scaffold(
        query_tool=_import_from(root, f"{tools}.QueryDBTool").QueryDBTool,
        list_tool=_import_from(root, f"{tools}.ListDBTool").ListDBTool,
        exec_tool=_import_from(root, f"{tools}.ExecTool").ExecTool,
        result_marker=_import_from(root, f"{tools}.exec_utils.parse_result").RESULT_MARKER,
        load_db_clients=_import_from(root, f"{tools}.db_utils.db_config").load_db_clients,
    )


def _size(path: Path) -> int:
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return path.stat().st_size if path.is_file() else 0


def load_query(dab_root: Path, dataset: str, query_id: str, hints: bool) -> DabQuery:
    dataset_dir = dab_root / f"query_{dataset}"
    query_dir = dataset_dir / f"query{query_id}"
    if not query_id.isdigit() or not (query_dir / "query.json").is_file():
        raise ValueError(f"no DAB query at {query_dir}")
    question = json.loads((query_dir / "query.json").read_text())
    if isinstance(question, dict):
        question = question.get("query")
    if not isinstance(question, str):
        raise ValueError(f"unrecognized question in {query_dir / 'query.json'}")
    description = (dataset_dir / "db_description.txt").read_text().strip()
    if hints:
        hint = (dataset_dir / "db_description_withhint.txt").read_text().strip()
        description += "\n\n" + hint
    db_config = dataset_dir / "db_config.yaml"
    clients = scaffold(dab_root).load_db_clients(str(db_config))
    data_bytes = sum(
        _size(Path(client[key]))
        for client in clients.values()
        for key in ("db_path", "dump_folder", "sql_file")
        if key in client
    )
    return DabQuery(
        dataset=dataset,
        query_id=query_id,
        question=question.strip(),
        description=description,
        hints=hints,
        db_config=db_config,
        databases={name: client["db_type"] for name, client in clients.items()},
        data_bytes=data_bytes,
    )


def is_large(dab_query: DabQuery) -> bool:
    return dab_query.data_bytes >= LARGE_DB_BYTES


def max_turns(dab_query: DabQuery, config: RunConfig) -> int:
    return config.max_turns["large" if is_large(dab_query) else "query"]


def run_dir(out: Path, dataset: str, query_id: str, run: int) -> Path:
    return out / dataset / f"query{query_id}" / f"run{run}"


def server_args(dab_root: Path, db_config: Path, log: Path, workspace: Path) -> list[str]:
    return [
        *("-m", "evals.dab_server"),
        *("--dab-root", str(dab_root.resolve())),
        *("--db-config", str(db_config.resolve())),
        *("--log", str(log.resolve())),
        *("--workspace", str(workspace.resolve())),
    ]


def server_env() -> dict[str, str]:
    """The server starts in the agent's workspace; `evals` is found from the repo checkout."""
    return {"PYTHONPATH": str(REPO_ROOT)}


def skill_names(dab_query: DabQuery, config: RunConfig) -> list[str]:
    """sql-workflow plus the dialect Skill of each SQL database, as for any query task. Dialects
    without a Skill in this Skill version are left out; the version header lists what loaded."""
    dialects = sorted(
        {DIALECT_SKILLS[t] for t in dab_query.databases.values() if t in DIALECT_SKILLS}
    )
    names = [SQL_WORKFLOW, *dialects]
    return [name for name in names if (config.skills_dir / name / "SKILL.md").is_file()]


def system_prompt(dab_query: DabQuery, config: RunConfig) -> str:
    prompt = SYSTEM_PROMPT.format(server=MCP_SERVER)
    if config.skill:
        skills = load_skills(tuple(skill_names(dab_query, config)), config.skills_dir)
        prompt += SKILLS_PROMPT.format(skills=skills)
    return prompt


def task_prompt(dab_query: DabQuery, workspace: Path) -> str:
    databases = "\n".join(f"- {name} ({kind})" for name, kind in dab_query.databases.items())
    return TASK_PROMPT.format(
        server=MCP_SERVER,
        databases=databases,
        workspace=workspace.resolve(),
        question=dab_query.question,
        description=dab_query.description,
    )


def dab_commit(dab_root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(dab_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError:
        return "unknown"
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else "unknown"


async def run_once(
    dab_root: Path,
    dab_query: DabQuery,
    config: RunConfig,
    out: Path,
    run: int,
    env: dict[str, str],
) -> tuple[str, str]:
    """One agent session on the query. Returns its answer and how it ended.

    Mirrors `data_agent.agent.run` (same options, version header and events) with DAB's task
    prompt and DAB's tools in place of the DuckDB server.
    """
    settings = ModelSettings.from_env(env)
    directory = run_dir(out, dab_query.dataset, dab_query.query_id, run)
    directory.mkdir(parents=True)  # FileExistsError: a finished run's trace is never overwritten
    workspace = directory / "workspace"
    workspace.mkdir()
    turns = max_turns(dab_query, config)
    server: McpStdioServerConfig = {
        "type": "stdio",
        "command": sys.executable,
        "args": server_args(
            dab_root, dab_query.db_config, directory / "dab_tool_calls.jsonl", workspace
        ),
        "env": server_env(),
    }
    stderr: list[str] = []
    # build_options keeps the safety settings (tools, permissions, workspace guard, user-only
    # settings) identical to `data-agent run`; the task's DuckDB file is unused, DAB's server
    # replaces the DuckDB one.
    task = Task(text="", type="query", workspace=workspace, duckdb=dab_query.db_config)
    final: ResultMessage | None = None
    # The same price-table cap as `data-agent run`; the session's turns follow `max_turns` here.
    cap = spend_cap(settings.model, config.max_budget_usd)
    with EventLog(directory / "events.jsonl", secrets=secrets_of(env)) as log:
        options = replace(
            build_options(task, config, settings, stderr, log.emit, cap),
            system_prompt=system_prompt(dab_query, config),
            mcp_servers={MCP_SERVER: server},
            max_turns=turns,
        )
        log.emit(
            "version",
            git_commit=git_commit(),
            skill_version=skill_version(config.skills_dir) if config.skill else "none",
            config=config.name,
            layers=config.layers(),
            model=settings.model,
            sdk_version=claude_agent_sdk.__version__,
            cli_version=__cli_version__,
            dab={
                "commit": dab_commit(dab_root),
                "dataset": dab_query.dataset,
                "query": dab_query.query_id,
                "run": run,
                "hints": dab_query.hints,
                "large": is_large(dab_query),
                "max_turns": turns,
                "skills": skill_names(dab_query, config) if config.skill else [],
            },
        )
        tools: dict[str, str] = {}
        server_up = True
        try:
            stream = query(prompt=task_prompt(dab_query, workspace), options=options)
            async with aclosing(capped(stream, cap)) as messages:
                async for message in messages:
                    if isinstance(message, ResultMessage):
                        final = message
                        continue
                    record_message(log, message, tools)
                    if isinstance(message, SystemMessage) and message.subtype == "init":
                        # Without DAB's tools the agent can only say the data is unreachable;
                        # that is an outage, not an answer.
                        server_status = mcp_status(message.data.get("mcp_servers"))
                        if server_status != "connected":
                            server_up = False
                            log.emit(
                                "error", source="mcp", error=f"DAB tool server {server_status}"
                            )
        except Exception as exc:
            if final is None:
                log.emit("error", source="sdk", error=str(exc), stderr_tail=stderr[-20:])
        finally:  # also when the run is interrupted
            remove_exec_containers(workspace)
            mask_dab_logs(directory, secrets_of(env))
        stopped = cap.result_fields() if cap is not None and cap.stopped else None
        log.emit("result", **(stopped or ended_by_sdk(final, settings.model, cap)))
    if stopped is not None and server_up:
        return "", "unfinished"
    if (
        not server_up
        or final is None
        or final.api_error_status is not None
        or final.terminal_reason == "api_error"
    ):
        status = "infra_error"
    elif final.subtype == "success" and not final.is_error:
        status = "finished"
    else:
        status = "unfinished"
    return (final.result or "") if final else "", status


def secrets_of(env: dict[str, str]) -> list[str]:
    """What the trace, the submission and DAB's logs mask: the agent's own secrets plus the
    database settings DAB's tools read from the environment (a driver error may quote them),
    longest first so a URL is masked whole before its host. Loopback names are no server address
    and stay readable."""
    values = [env.get(name, "") for name in DAB_SECRET_ENV]
    if host := urlsplit(env.get("MONGO_URI", "")).hostname:
        values.append(host)
    values = [v for v in values if v and v not in LOOPBACK]
    return sorted(set(known_secrets(env)) | set(values), key=len, reverse=True)


def mask(text: str, secrets: list[str]) -> str:
    """Mask each secret as written and as escaped inside a JSON string (quotes, backslashes,
    non-ASCII letters), the form it takes in a JSON log."""
    for secret in secrets:
        escaped = (json.dumps(secret)[1:-1], json.dumps(secret, ensure_ascii=False)[1:-1])
        for form in dict.fromkeys((*escaped, secret)):
            text = text.replace(form, REDACTED)
    return text


def mask_dab_logs(directory: Path, secrets: list[str]) -> None:
    """DAB's tools log arguments and results unmasked; mask them once the session is over."""
    for path in directory.glob("*.jsonl"):
        if path.name != "events.jsonl":
            path.write_text(mask(path.read_text(), secrets))


def remove_exec_containers(workspace: Path, client: Any = None) -> list[str]:
    """Remove DAB's `execute_python` containers left on `workspace`; returns their names.

    The SDK kills the tool server (SIGINT, SIGTERM, then for good) within about half a second of
    the session's end, before DAB's clean-up can stop the container (a `docker stop` of up to
    10 s), so the runner removes it. Without the Docker SDK or daemon no container was started.
    """
    if client is None:
        try:
            docker = importlib.import_module("docker")  # in evals/dab-requirements.txt
        except ImportError:
            return []
        try:
            client = docker.from_env()
        except docker.errors.DockerException:
            return []
    removed = []
    try:  # a failed clean-up is reported, never a failed run
        for container in client.containers.list(all=True, filters={"ancestor": EXEC_IMAGE}):
            sources = {m.get("Source") for m in container.attrs.get("Mounts", [])}
            if str(workspace.resolve()) in sources:
                container.remove(force=True)
                removed.append(container.name)
    except Exception as exc:
        print(f"dab: could not remove the exec container on {workspace}: {exc}", file=sys.stderr)
    return removed


def mcp_status(servers: Any) -> str:
    """The DAB server's status in the SDK's init message (`connected` when it started)."""
    for server in servers or []:
        if isinstance(server, dict) and server.get("name") == MCP_SERVER:
            return str(server.get("status"))
    return "missing"


def conditions(header: dict[str, Any]) -> dict[str, Any]:
    """What a run's version header says about the experiment: runs that differ here must not share
    a submission file."""
    dab = header.get("dab") or {}
    return {
        "config": header.get("config"),
        "model": header.get("model"),
        "hints": dab.get("hints"),
        "DAB commit": dab.get("commit"),
    }


def check_finished_run(directory: Path, expected: dict[str, Any]) -> None:
    """Raise ConditionsChanged unless the run's trace starts with a header for `expected`."""
    trace = directory / "events.jsonl"
    try:
        with trace.open() as f:
            header = json.loads(f.readline())
    except (OSError, ValueError) as exc:
        raise ConditionsChanged(f"cannot check the finished run's trace {trace}: {exc}") from exc
    if not isinstance(header, dict) or header.get("type") != "version":
        raise ConditionsChanged(f"the finished run's trace {trace} has no version header")
    found = conditions(header)
    changed = [f"{k} {found[k]!r} there, {v!r} now" for k, v in expected.items() if found[k] != v]
    if changed:
        raise ConditionsChanged(
            f"{directory} was run under other conditions ({'; '.join(changed)}); "
            "use another --out for these"
        )


def spent(out: Path) -> Decimal:
    files, unchecked = walk_files(out)
    if unchecked:
        raise ValueError(f"cannot see every event file under {out}: {unchecked[0]}")
    return sum((spend.file_spend(p) for p in files if p.name == "events.jsonl"), Decimal(0))


def write_submission(path: Path, entries: list[dict[str, str]]) -> None:
    """Merge entries into the submission file; an entry replaces the old one for its run."""
    merged = {}
    if path.exists():
        merged = {(e["dataset"], e["query"], e["run"]): e for e in json.loads(path.read_text())}
    merged |= {(e["dataset"], e["query"], e["run"]): e for e in entries}
    ordered = sorted(merged.values(), key=lambda e: (e["dataset"], int(e["query"]), int(e["run"])))
    path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n")


async def run_query(
    dab_root: Path,
    dab_query: DabQuery,
    config: RunConfig,
    out: Path,
    runs: int,
    env: dict[str, str],
    budget: Decimal | None = None,
) -> int:
    """`runs` sessions on one query, one after another (DAB loads its Postgres and MongoDB
    databases per session under fixed names, so sessions on a dataset cannot overlap).

    A session that broke on infrastructure is not an answer: it stays out of the submission (its
    trace stays for a look) and is counted in the return value, so the caller can fail and the
    run be redone after removing its directory. Runs already in the submission are skipped, so a
    rerun only redoes what is missing, and only under the conditions (config, model, hints, DAB
    commit) of every run already in the submission; otherwise it raises ConditionsChanged before
    running anything.
    """
    out.mkdir(parents=True, exist_ok=True)
    submission = out / "submission.json"
    done = set()
    if submission.exists():
        done = {(e["dataset"], e["query"], e["run"]) for e in json.loads(submission.read_text())}
    if done:  # every run in the submission, of any query: the file is one experiment
        expected = {
            "config": config.name,
            "model": ModelSettings.from_env(env).model,
            "hints": dab_query.hints,
            "DAB commit": dab_commit(dab_root),
        }
        for dataset, query_id, run in sorted(done):
            check_finished_run(run_dir(out, dataset, query_id, int(run)), expected)
    secrets = secrets_of(env)
    infra_errors = 0
    for run in range(runs):
        if (dab_query.dataset, dab_query.query_id, str(run)) in done:
            continue
        if budget is not None and spent(out) >= budget * spend.STOP_FRACTION:
            raise BudgetReached(f"spend under {out} reached {spend.STOP_PERCENT} of ${budget}")
        answer, status = await run_once(dab_root, dab_query, config, out, run, env)
        print(f"{dab_query.dataset} query{dab_query.query_id} run{run}: {status}")
        if status == "infra_error":
            infra_errors += 1
            write_submission(submission, [])  # no entry, but the file exists for the batch
            continue
        # Masked like the trace, so the answer in the submission matches the one in the trace.
        answer = mask(answer, secrets)
        entry = {
            "dataset": dab_query.dataset,
            "query": dab_query.query_id,
            "run": str(run),
            "answer": answer,
        }
        write_submission(submission, [entry])
    return infra_errors


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser("dab", help="run the agent on DAB queries, write a submission")
    parser.add_argument("--dab-root", type=Path, required=True, help="local DataAgentBench clone")
    parser.add_argument("--dataset", required=True, help="as in query_<dataset>/")
    parser.add_argument("--query", action="append", required=True, help="query id; repeatable")
    parser.add_argument("--runs", type=int, default=RUNS_PER_QUERY)
    parser.add_argument("--config", required=True, choices=CONFIG_NAMES)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-hints", action="store_true", help="leave out the hint file")
    parser.add_argument("--budget", help="stop once spend under --out reaches 80%% of this (USD)")
    parser.set_defaults(run=run_cli)


def _error(message: str) -> int:
    print(f"dab: {message}", file=sys.stderr)
    return 2


def run_cli(args: argparse.Namespace) -> int:
    budget = None
    if args.budget is not None:
        try:
            budget = Decimal(args.budget)
        except InvalidOperation:
            budget = None
        if budget is None or not budget.is_finite() or budget <= 0:
            return _error(f"budget must be a positive number, got {args.budget!r}")
    if not (args.dab_root / "common_scaffold").is_dir():
        return _error(f"not a DataAgentBench clone: {args.dab_root}")
    try:
        config = load_config(args.config)
        queries = [
            load_query(args.dab_root, args.dataset, q, not args.no_hints) for q in args.query
        ]
    except ImportError as exc:
        return _error(f"{exc}; run under `uv run --with-requirements evals/dab-requirements.txt`")
    except (ValueError, OSError) as exc:
        return _error(str(exc))
    env = dict(os.environ)
    infra_errors = 0
    try:
        for dab_query in queries:
            infra_errors += asyncio.run(
                run_query(args.dab_root, dab_query, config, args.out, args.runs, env, budget)
            )
    except BudgetReached as exc:
        print(f"dab: {exc}; stop the batch and ask a human", file=sys.stderr)
        return 1
    except (ConfigError, FileExistsError, ValueError, spend.SpendUnknown) as exc:
        return _error(str(exc))
    if infra_errors:
        return _error(
            f"{infra_errors} run(s) broke on infrastructure; remove their run directories and rerun"
        )
    return 0
