"""LiveSQLBench answers stay hidden (.scratch/livesqlbench/spec.md "Testing Decisions": 答案不外泄;
Q1, U7).

Written by ticket 127. Every test is `TICKET_128` until ticket 128 implements the task generation,
the adapter's LiveSQLBench run and the evaluation script's entry (`evals.livesqlbench`), which
then drops the marks and changes nothing else. Each test first checks the thing it inspects was
produced (a task, a query run, a reward), then that no answer is in it.

"The answer's path" means its path and its content (spec U7): the answer file's path, and the
stand-in sentinels of `sol_sql`, `test_cases` and `external_knowledge`
(tests/acceptance/livesqlbench_support.py), plus, for the evaluation script, a gold result value.

- Workspace and task files: the instruction and everything under `environment/` (the image the
  agent works in, the files mounted into it, `db_assets` among them).
- The agent's event file, argv and environment: one adapter trial on a generated task, with the
  stub `data-agent` writing its argv and environment into its event file.
- The evaluation script's output (what Harbor keeps in the trial's `verifier/`): `evaluate` with a
  stand-in executor, scored right, wrong, and with an execution error on either side. The
  stand-in's errors quote the failing SQL, as Postgres's do (`LINE 1: ...`), so printing an error
  as is would leak a gold SQL. Its files, stdout, stderr and logging are all checked; upstream logs
  every query (evaluation/src/db_utils.py:302), the behavior ruled out here.
- The pulled `runs/`: a dev-set ablate trial whose `verifier/` holds that output, pulled whole by
  `da-remote pull` (its fake rsync copies nothing, so the server run dir a whole pull copies is
  what is checked).
"""

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from harbor.models.task.task import Task
from livesqlbench_support import (
    Env,
    ablate_run,
    agent_container_env,
    agent_visible,
    all_sentinels,
    answer,
    files_under,
    generate,
    leaks,
    lsb_server_manifest,
    new_agent,
    pulled_whole,
    query_records,
    read,
    run_trial,
    sentinels,
    task_of,
    without_host,
)

from evals import livesqlbench
from evals.livesqlbench import ExecutionError, Result

TICKET_128 = pytest.mark.xfail(
    strict=True,
    raises=(NotImplementedError, AssertionError),
    reason="ticket 128: LiveSQLBench tasks, adapter run and evaluation script are not implemented",
)

GOLD_VALUE = "GOLDVAL-" + "p44" + "-tide"
GOLD = Result(columns=("station_id", "avg_high"), rows=((GOLD_VALUE, 212.4),))
WRONG = Result(columns=("station_id", "avg_high"), rows=(("TG-9", 198.0),))
PREDICTED = {
    "right": ["SELECT station_id, avg_high FROM pred_right"],
    "wrong": ["SELECT station_id, avg_high FROM pred_wrong"],
    "predicted error": ["SELECT station_id FORM pred_broken"],
    "gold error": ["SELECT station_id, avg_high FROM pred_right"],
}
EXPECTED = {"right": 1, "wrong": 0, "predicted error": 0, "gold error": 0}


def payload() -> dict[str, Any]:
    record = next(r for r in query_records() if r["instance_id"] == "tide_q1")
    return record | answer("tide_q1")


def stand_in(case: str):
    """The evaluation script's executor without a database: the gold SQL returns GOLD; the
    predicted SQL returns GOLD's rows under other column names (right) or other rows (wrong)."""

    def execute(sqls: Sequence[str]) -> Result:
        text = "\n".join(sqls)
        if not text.strip():
            return Result(columns=(), rows=())
        if "SOLSQL-" in text:
            if case == "gold error":
                raise ExecutionError(
                    f"canceling statement due to statement timeout\nLINE 1: {text}"
                )
            return GOLD
        if "pred_broken" in text:
            raise ExecutionError(f'syntax error at or near "FORM"\nLINE 1: {text}')
        if "pred_right" in text:
            return Result(columns=("station", "value"), rows=GOLD.rows)
        if "pred_wrong" in text:
            return WRONG
        raise AssertionError(f"the stand-in executor got unexpected SQL: {text!r}")

    return execute


def answer_needles(instance_id: str) -> list[str]:
    return [*sentinels(instance_id), *answer(instance_id)["sol_sql"]]


@TICKET_128
def test_no_answer_or_answer_path_is_in_the_workspace_or_the_task_files(tmp_path: Path) -> None:
    generated = generate(tmp_path)

    assert len(generated.tasks) == len(query_records())
    needles = [*all_sentinels(), str(generated.answers), str(generated.answers.parent)]
    for task_dir in generated.tasks:
        visible = agent_visible(task_dir)
        assert any(p.startswith("environment/") for p in visible), "the task has no environment"
        assert leaks(visible, needles) == []


@TICKET_128
def test_no_answer_is_in_the_agents_event_file_argv_or_environment(tmp_path: Path) -> None:
    generated = generate(tmp_path)
    task_dir = task_of(generated, "tide_q1")
    agent = new_agent(tmp_path, benchmark=livesqlbench.BENCHMARK)
    trial = run_trial(tmp_path, agent, Task(task_dir).instruction, agent_container_env(task_dir))

    assert trial.value("--type") == "query"
    events = trial.events()
    assert events, "the trial left no event file"
    seen = {
        **events,
        "argv": json.dumps(trial.args),
        "environment": json.dumps(without_host(trial.environ)),
        "exec env": json.dumps(trial.env.exec_envs),
        "commands": "\n".join(trial.env.commands),
    }
    needles = [*all_sentinels(), str(generated.answers), str(generated.answers.parent)]
    assert leaks(seen, needles) == []


def run_evaluation(case: str, out: Path) -> int:
    return livesqlbench.evaluate(payload(), PREDICTED[case], stand_in(case), out)


@TICKET_128
@pytest.mark.parametrize("case", EXPECTED)
def test_the_evaluation_output_is_the_reward_and_no_answer(
    tmp_path: Path,
    case: str,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    out = tmp_path / "verifier"
    out.mkdir()
    reward = run_evaluation(case, out)

    assert reward == EXPECTED[case]
    assert (out / "reward.txt").exists()
    assert float(read(out / "reward.txt").strip()) == EXPECTED[case]
    printed = capsys.readouterr()
    seen = {
        **{str(p): read(p) for p in files_under(out)},
        "stdout": printed.out,
        "stderr": printed.err,
        "logging": "\n".join(r.getMessage() for r in caplog.records),
    }
    assert leaks(seen, [*answer_needles("tide_q1"), GOLD_VALUE]) == []


@TICKET_128
def test_a_pulled_dev_run_holds_no_answer(tmp_path: Path) -> None:
    (tmp_path / "remote").mkdir()
    env = Env(tmp_path / "remote")
    try:
        task = "lsb-tide_q1"
        run = ablate_run(
            env, lsb_server_manifest([task], ["lsb-orchard_q1"]), {"no-verifiers": [task]}
        )
        verifier = run / "jobs" / "no-verifiers" / f"{task}__abc" / "verifier"
        verifier.mkdir()
        assert run_evaluation("right", verifier) == 1
        assert (verifier / "reward.txt").exists()

        assert pulled_whole(env)
        pulled = {str(p): read(p) for p in files_under(run)}
        assert leaks(pulled, [*answer_needles("tide_q1"), GOLD_VALUE]) == []
    finally:
        (env.server / "release").write_text("0")
