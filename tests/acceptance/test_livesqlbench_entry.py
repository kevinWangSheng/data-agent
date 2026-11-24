"""The LiveSQLBench evaluation entry (.scratch/livesqlbench/spec.md "Testing Decisions": 评测入口;
U5).

Written by ticket 127. The `TICKET_128` test passes once ticket 128 makes the Harbor adapter run
LiveSQLBench tasks, which then drops its mark and changes nothing else; the guard (no mark)
passes now and must keep passing.

The adapter's real command runs locally against a stub `data-agent`, built with
tests/test_harbor_agent.py `make_agent` (tests/acceptance/livesqlbench_support.py). The trial
runs one of the fixture's generated tasks: its instruction as Harbor's `Task` reads it, and its
agent container's variables (`[environment] env` and the compose `main` service's) as the
container's environment.

Contract (ticket 127): with `benchmark=livesqlbench` (Harbor's `--ak`) the trial is a query run,
`data-agent run --type query`, on the task's Postgres named by `--postgres-env <VAR>`, a variable
the agent's container sets; no `--duckdb`, and no `--network`, `--mcp-servers` or `--charts`
(spec "适配器" :40: no `--network`, no external MCP, no charts). Without the kwarg the trial
is the data-eng-bench dbt run it is today.
"""

import re
from pathlib import Path

import pytest
from harbor.models.task.task import Task
from livesqlbench_support import agent_container_env, generate, new_agent, run_trial, task_of

from evals.livesqlbench import BENCHMARK

TICKET_128 = pytest.mark.xfail(
    strict=True,
    raises=(NotImplementedError, AssertionError),
    reason="ticket 128: the Harbor adapter does not run LiveSQLBench tasks yet",
)

DBT_CONTAINER = {"DB_TYPE": "duckdb", "DUCKDB_PATH": "/app/database/retail.duckdb"}
OFF = ("--network", "--mcp-servers", "--charts")


def test_without_the_benchmark_kwarg_a_trial_is_the_data_eng_bench_dbt_run(
    tmp_path: Path,
) -> None:
    trial = run_trial(tmp_path, new_agent(tmp_path), "Fix the model", DBT_CONTAINER)

    assert trial.value("--type") == "dbt"
    assert trial.value("--duckdb") == "/app/database/retail.duckdb"
    for flag in OFF:
        assert flag not in trial.args


@TICKET_128
def test_a_livesqlbench_trial_is_a_query_run_on_postgres_without_network_mcp_or_charts(
    tmp_path: Path,
) -> None:
    task_dir = task_of(generate(tmp_path), "tide_q1")
    task = Task(task_dir)
    agent = new_agent(tmp_path, benchmark=BENCHMARK)
    trial = run_trial(tmp_path, agent, task.instruction, agent_container_env(task_dir))

    assert trial.value("--type") == "query"
    variable = trial.value("--postgres-env")
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable), variable
    assert trial.environ.get(variable), f"the container does not set {variable}"
    assert "--duckdb" not in trial.args
    for flag in OFF:
        assert flag not in trial.args
