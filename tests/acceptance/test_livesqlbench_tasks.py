"""LiveSQLBench task generation (.scratch/livesqlbench/spec.md "Testing Decisions": 任务生成; U8).

Written by ticket 127. Every test is `TICKET_128` until ticket 128 implements
`evals.livesqlbench.generate_tasks`, which then drops the marks and changes nothing else.

The fixture's tasks (tests/acceptance/livesqlbench_support.py) are read back with Harbor 0.24's
own `Task(task_dir)`. What the agent can see of a task is its instruction and everything under
`environment/`; the answer stand-ins (`sol_sql`, `test_cases`, `external_knowledge`) must be in
neither. Upstream's adapter writes `external_knowledge` into the instruction
(LiveSQLBench-CLI/adapters/livesqlbench/adapter.py:254,270); that is the behavior ruled out here.
When Harbor uploads `tests/` (spec :20) is not pinned.
"""

from pathlib import Path

from harbor.models.task.task import Task
from livesqlbench_support import (
    DB_ASSETS,
    agent_visible,
    all_sentinels,
    files_under,
    generate,
    leaks,
    management_records,
    query_records,
    read,
    task_of,
)

from evals import livesqlbench


def test_each_query_record_becomes_a_task_harbor_reads(tmp_path: Path) -> None:
    generated = generate(tmp_path)

    expected = {f"{livesqlbench.TASK_PREFIX}{r['instance_id']}" for r in query_records()}
    assert {t.name for t in generated.tasks} == expected
    for record in query_records():
        task = Task(task_of(generated, record["instance_id"]))
        assert task.name == f"lsb-{record['instance_id']}"
        assert record["query"] in task.instruction
        assert record["selected_database"] in task.instruction
        assert task.config.metadata.get("task_type") == "query"


def test_the_agent_sees_its_databases_public_assets(tmp_path: Path) -> None:
    """Spec U8: the official tasks give the agent `db_assets` (knowledge base, column meanings,
    schema); here each fixture file of the task's database is under `environment/` unchanged."""
    generated = generate(tmp_path)

    for record in query_records():
        visible = agent_visible(task_of(generated, record["instance_id"])).values()
        assets = sorted((DB_ASSETS / record["selected_database"]).iterdir())
        assert len(assets) == 3
        for asset in assets:
            assert read(asset) in visible, f"{asset.name} is not in the task's environment"


def test_a_management_record_gets_no_task(tmp_path: Path) -> None:
    generated = generate(tmp_path)

    assert len(generated.tasks) == len(query_records())
    out = tmp_path / "tasks"
    texts = {str(p): read(p) for p in files_under(out)}
    assert texts, "no task files were written"
    for record in management_records():
        assert not (out / f"lsb-{record['instance_id']}").exists()
        assert leaks(texts, [record["query"]]) == []


def test_no_answer_is_in_the_instruction_or_the_environment(tmp_path: Path) -> None:
    generated = generate(tmp_path)

    assert len(generated.tasks) == len(query_records())
    for task_dir in generated.tasks:
        visible = agent_visible(task_dir)
        assert "instruction.md" in visible
        assert leaks(visible, all_sentinels()) == []
