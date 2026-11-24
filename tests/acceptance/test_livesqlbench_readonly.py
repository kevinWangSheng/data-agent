"""LiveSQLBench stays read-only for the agent (.scratch/livesqlbench/spec.md "Testing Decisions":
只读; U6, ADR-0010, AGENTS.md "沙箱和 agent 拿不到写库凭据").

Written by ticket 127. Both tests are `TICKET_128` until ticket 128 implements the task
generation and the adapter's LiveSQLBench run, which then drops the marks and changes nothing
else. Each first checks it has a LiveSQLBench task or query run, then that no write credential
reaches the agent.

Upstream's template gives its SUPERUSER `root/123123` to the agent's `main` service as well as to
the database (LiveSQLBench-CLI/adapters/livesqlbench/template/environment/docker-compose.yaml:13-14,
:24-25; bootstrap-db-image.sh:36), because its evaluator, run in the same container after the
agent, creates and drops `<db>_process_1` (template/tests/evaluator.py:46, :91, :109, :119-127).
Here the write credential is a stand-in given to `generate_tasks`:

- The generated task gives it to the verification phase (`[verifier] env`, which Harbor 0.24 merges
  only when verifying: harbor/verifier/verifier.py:190), as the value or as a `${VAR}` template of
  `WRITE_PASSWORD_ENV`, resolved from the host (harbor/utils/env.py `resolve_env_vars`), and nowhere
  the agent sees, neither the value nor that variable's name: not
  `[environment] env`, not the compose `main` service's environment, not any file under
  `environment/` (Dockerfiles, compose, mounted documents such as upstream's `db_env.sh`), not
  the instruction. Neither is upstream's `123123`.
- The Harbor adapter, given the stand-in under the usual variable names in its `--ae` (the
  agent's `extra_env`), passes neither it nor `123123` to `exec_as_agent` (its env or command), and
  names the database by `--postgres-env <VAR>`, so no connection string is on the command line.

Not tested here, as `make check` has no Postgres (tests/test_postgres.py marks those tests
`integration`): whether the role the agent connects with is really read-only, and whether the
sandbox can reach the database with another credential. Ticket 128 checks both on its smoke run
and records them in its Comments. Harbor itself also puts a job's `--ae` into the container
(base.py `_exec`'s docstring), so a job config must not pass a write credential there; that is
the job config's, not the adapter's.
"""

import json
from pathlib import Path

import pytest
from harbor.models.task.task import Task
from harbor.utils.env import resolve_env_vars
from livesqlbench_support import (
    MODEL_ENV,
    UPSTREAM_PASSWORD,
    WRITE_PASSWORD,
    WRITE_PASSWORD_VARS,
    agent_container_env,
    agent_visible,
    generate,
    leaks,
    new_agent,
    query_records,
    run_trial,
    task_of,
    without_host,
)

from evals.livesqlbench import BENCHMARK, WRITE_PASSWORD_ENV

TICKET_128 = pytest.mark.xfail(
    strict=True,
    raises=(NotImplementedError, AssertionError),
    reason="ticket 128: LiveSQLBench tasks and the adapter's query run are not implemented",
)

CREDENTIALS = [WRITE_PASSWORD, UPSTREAM_PASSWORD]


@TICKET_128
def test_the_write_credential_reaches_the_verifier_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(WRITE_PASSWORD_ENV, WRITE_PASSWORD)
    generated = generate(tmp_path)

    assert len(generated.tasks) == len(query_records())
    for task_dir in generated.tasks:
        config = Task(task_dir).config
        verifier_env = resolve_env_vars(config.verifier.env)
        assert WRITE_PASSWORD in verifier_env.values(), "the verifier has no credential"
        # Nor the template's variable, which compose would interpolate into the agent's service.
        assert leaks(agent_visible(task_dir), [*CREDENTIALS, WRITE_PASSWORD_ENV]) == []


@TICKET_128
def test_the_adapter_gives_the_agent_no_write_credential(tmp_path: Path) -> None:
    task_dir = task_of(generate(tmp_path), "orchard_q1")
    planted = MODEL_ENV | dict.fromkeys(WRITE_PASSWORD_VARS, WRITE_PASSWORD)
    agent = new_agent(tmp_path, agent_env=planted, benchmark=BENCHMARK)
    trial = run_trial(tmp_path, agent, Task(task_dir).instruction, agent_container_env(task_dir))

    assert trial.value("--type") == "query"
    assert trial.value("--postgres-env")
    command_line = {"commands": "\n".join(trial.env.commands), "argv": json.dumps(trial.args)}
    seen = {
        **command_line,
        "exec env": json.dumps(trial.env.exec_envs),
        "environment": json.dumps(without_host(trial.environ)),
    }
    assert leaks(seen, CREDENTIALS) == []
    # The (read-only) connection string may sit in the variable; never on the command line.
    assert leaks(command_line, ["postgres://", "postgresql://"]) == []
