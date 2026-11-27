"""LiveSQLBench tasks whose instruction also holds the database's public assets (ticket 139, spec
D3): the schema, the column meanings and the knowledge base as upstream's baseline prompt gives
them (baseline/src/prompt.py `assistant_prompt`), with the switch off by default.

No database, model, download or server. The fixture data is tests/fixtures/livesqlbench/; assets
with upstream's edge cases (mixed-case column keys, a repeated knowledge name, extra knowledge
fields, apostrophes) are written under tmp_path.
"""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from harbor.models.task.task import Task

from evals import livesqlbench, livesqlbench_server
from evals.__main__ import main as evals_main

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "livesqlbench"
PUBLIC = FIXTURE / "public.jsonl"
DB_ASSETS = FIXTURE / "db_assets"
PASSWORD = "unitTestWrite" + "Credential0001"
INLINE = livesqlbench_server.INLINE_TASKS_DIR
HIDDEN = {
    "sol_sql": ["SELECT 'sentinel-sol-sql-0139'"],
    "test_cases": ["sentinel-test-case-0139"],
    "external_knowledge": ["sentinel-external-knowledge-0139"],
}

# The instruction of lsb-tide_q1 before ticket 139, which the switch left off must keep.
TIDE_Q1_INSTRUCTION = """\
# Write PostgreSQL to answer a question

Database: `tide_gauges` (PostgreSQL). Answer this question about it:

List every gauge station with its average daily high-water mark for March, highest first.

Files describing the database:
- `/app/db_assets/tide_gauges_schema.txt`: its schema and sample rows
- `/app/db_assets/tide_gauges_column_meaning_base.json`: what its columns mean
- `/app/db_assets/tide_gauges_kb.jsonl`: the knowledge base about the database

## Output format (mandatory)

Write the SQL that answers the question to the file `/app/pred.json`, with exactly this JSON:

```json
{"instance_id": "tide_q1", "predicted_sql": ["<SQL1>", "<SQL2>", "..."]}
```

- `predicted_sql` is always a JSON list, even with one statement; the result of the last \
statement is the answer.
- SQL strings only: no markdown fences or comments.
"""


def answers(tmp_path: Path, hidden: bool) -> Path:
    path = tmp_path / ("answers.jsonl" if hidden else "no-answers.jsonl")
    lines = [json.dumps({"instance_id": iid} | HIDDEN) for iid in ("tide_q1", "orchard_q1")]
    path.write_text("\n".join(lines) + "\n" if hidden else "")
    return path


def generate(
    tmp_path: Path, name: str, *, inline: bool, db_assets: Path = DB_ASSETS, hidden: bool = True
) -> dict[str, Path]:
    if inline:
        tasks = livesqlbench.generate_tasks(
            PUBLIC,
            answers(tmp_path, hidden),
            db_assets,
            tmp_path / name,
            write_password=PASSWORD,
            inline_assets=True,
        )
    else:
        tasks = livesqlbench.generate_tasks(
            PUBLIC, answers(tmp_path, hidden), db_assets, tmp_path / name, write_password=PASSWORD
        )
    return {t.name: t for t in tasks}


def files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def edge_assets(tmp_path: Path) -> Path:
    """The fixture's assets, with tide_gauges' column meanings and knowledge base rewritten."""
    root = tmp_path / "assets"
    for db in ("orchard_ledger", "tide_gauges"):
        (root / db).mkdir(parents=True)
        for suffix in livesqlbench.ASSET_SUFFIXES:
            name = f"{db}{suffix}"
            (root / db / name).write_bytes((DB_ASSETS / db / name).read_bytes())
    tide = root / "tide_gauges"
    meanings = {
        "Tide_Gauges|Readings|Level_CM": "Water level, in centimetres.",
        "tide_gauges|stations|harbour": {"note": "the harbour's name"},
    }
    (tide / "tide_gauges_column_meaning_base.json").write_text(json.dumps(meanings))
    kb = [
        {
            "id": 0,
            "knowledge": "Daily High-Water Mark",
            "description": "first wording",
            "definition": "max(level_cm)",
            "type": "calculation_knowledge",
            "children_knowledge": -1,
        },
        {"id": 1, "knowledge": "Spring Tide", "description": "the month's biggest range"},
        {
            "id": 2,
            "knowledge": "Daily High-Water Mark",
            "description": "the station's highest reading that day",
            "definition": "max(level_cm) per station per day",
            "type": "calculation_knowledge",
            "children_knowledge": [0],
        },
    ]
    (tide / "tide_gauges_kb.jsonl").write_text("\n".join(json.dumps(k) for k in kb) + "\n")
    return root


# --- the switch off ---


def test_off_by_default_the_instruction_is_the_one_before_ticket_139(tmp_path: Path) -> None:
    tasks = generate(tmp_path, "tasks", inline=False)
    assert (tasks["lsb-tide_q1"] / "instruction.md").read_text() == TIDE_Q1_INSTRUCTION
    record = next(r for r in livesqlbench.read_jsonl(PUBLIC) if r["instance_id"] == "tide_q1")
    assert livesqlbench.instruction(record) == TIDE_Q1_INSTRUCTION


def test_off_explicitly_writes_the_same_bytes_as_the_default(tmp_path: Path) -> None:
    default = generate(tmp_path, "default", inline=False)
    off = livesqlbench.generate_tasks(
        PUBLIC,
        answers(tmp_path, True),
        DB_ASSETS,
        tmp_path / "off",
        write_password=PASSWORD,
        inline_assets=False,
    )
    assert sorted(default) == sorted(t.name for t in off)
    assert files(tmp_path / "default") == files(tmp_path / "off")


# --- the switch on ---


def test_on_the_instruction_holds_upstreams_three_sections_and_keeps_paths_and_format(
    tmp_path: Path,
) -> None:
    assets = edge_assets(tmp_path)
    text = (
        (generate(tmp_path, "inline", inline=True, db_assets=assets)["lsb-tide_q1"])
        .joinpath("instruction.md")
        .read_text()
    )
    schema = (assets / "tide_gauges" / "tide_gauges_schema.txt").read_text()
    meanings = {
        "tide_gauges|readings|level_cm": "Water level, in centimetres.",
        "tide_gauges|stations|harbour": {"note": "the harbour's name"},
    }
    # The repeated name keeps its first place and its last record (upstream's dict by name);
    # only id, knowledge, description and definition, in that order.
    knowledge = [
        {
            "id": 2,
            "knowledge": "Daily High-Water Mark",
            "description": "the station's highest reading that day",
            "definition": "max(level_cm) per station per day",
        },
        {"id": 1, "knowledge": "Spring Tide", "description": "the month's biggest range"},
    ]
    sections = (
        f"# Database Schema:\n{schema}\n\n"
        f"# Column Meanings:\n{json.dumps(meanings, indent=2)}\n\n"
        f"# External Knowledge:\n{json.dumps(knowledge, indent=2)}\n\n"
    )
    assert sections in text
    head, tail = TIDE_Q1_INSTRUCTION.split("## Output format (mandatory)")
    assert text.startswith(head) and text.endswith("## Output format (mandatory)" + tail)
    assert text.index("/app/db_assets/tide_gauges_kb.jsonl") < text.index("# Database Schema:")
    assert "calculation_knowledge" not in text and "children_knowledge" not in text
    assert "first wording" not in text


def test_on_each_task_inlines_its_own_database(tmp_path: Path) -> None:
    tasks = generate(tmp_path, "inline", inline=True)
    orchard = (tasks["lsb-orchard_q1"] / "instruction.md").read_text()
    tide = (tasks["lsb-tide_q1"] / "instruction.md").read_text()
    orchard_schema = (DB_ASSETS / "orchard_ledger" / "orchard_ledger_schema.txt").read_text()
    tide_schema = (DB_ASSETS / "tide_gauges" / "tide_gauges_schema.txt").read_text()
    assert orchard_schema in orchard and tide_schema not in orchard
    assert tide_schema in tide and orchard_schema not in tide


def test_on_only_the_instruction_differs(tmp_path: Path) -> None:
    off = generate(tmp_path, "tasks", inline=False)
    on = generate(tmp_path, "tasks-inline", inline=True)
    assert sorted(off) == sorted(on)
    off_files, on_files = files(tmp_path / "tasks"), files(tmp_path / "tasks-inline")
    assert sorted(off_files) == sorted(on_files)
    differing = sorted(name for name in off_files if off_files[name] != on_files[name])
    assert differing == sorted(f"{task}/instruction.md" for task in off)
    for task in on:
        assert Task(on[task]).config.metadata == Task(off[task]).config.metadata


@pytest.mark.parametrize("inline", [False, True])
def test_no_hidden_field_nor_the_answer_file_reaches_the_instruction(
    tmp_path: Path, inline: bool
) -> None:
    given = generate(tmp_path, "given", inline=inline, hidden=True)
    none = generate(tmp_path, "none", inline=inline, hidden=False)
    for task, task_dir in given.items():
        text = (task_dir / "instruction.md").read_text()
        assert text == (none[task] / "instruction.md").read_text()
        for values in HIDDEN.values():
            for value in values:
                assert value not in text
        assert "answers.jsonl" not in text and str(tmp_path) not in text
        payload = json.loads((task_dir / "tests" / "task_payload.json").read_text())
        assert payload["sol_sql"] == HIDDEN["sol_sql"]


def test_an_inline_task_loads_as_a_harbor_task(tmp_path: Path) -> None:
    task = Task(generate(tmp_path, "inline", inline=True)["lsb-tide_q1"])
    assert "# External Knowledge:" in task.instruction
    assert task.config.environment.docker_image == livesqlbench.main_image()


# --- the server steps ---


def test_the_tasks_step_inlines_with_its_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(livesqlbench.WRITE_PASSWORD_ENV, PASSWORD)
    common = [
        *("livesqlbench", "tasks", "--public", str(PUBLIC)),
        *("--answers", str(answers(tmp_path, False)), "--db-assets", str(DB_ASSETS)),
    ]
    assert evals_main([*common, "--out", str(tmp_path / "plain")]) == 0
    assert evals_main([*common, "--out", str(tmp_path / "inline"), "--inline-assets"]) == 0
    assert f"tasks: 2 in {tmp_path / 'inline'}" in capsys.readouterr().out
    plain = (tmp_path / "plain" / "lsb-tide_q1" / "instruction.md").read_text()
    inline = (tmp_path / "inline" / "lsb-tide_q1" / "instruction.md").read_text()
    assert plain == TIDE_Q1_INSTRUCTION
    assert "# Database Schema:" in inline


def test_prepare_writes_the_tasks_and_the_inline_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(livesqlbench.WRITE_PASSWORD_ENV, PASSWORD)
    data = tmp_path / "data"
    data.mkdir()
    (data / livesqlbench.PUBLIC_FILE).write_bytes(PUBLIC.read_bytes())
    for db in ("orchard_ledger", "tide_gauges"):
        (data / db).mkdir()
        for suffix in livesqlbench.ASSET_SUFFIXES:
            (data / db / f"{db}{suffix}").write_bytes(
                (DB_ASSETS / db / f"{db}{suffix}").read_bytes()
            )
    databases = tmp_path / "databases.json"
    databases.write_text(
        json.dumps({"lsb-orchard_q1": "orchard_ledger", "lsb-tide_q1": "tide_gauges"})
    )
    monkeypatch.setattr(livesqlbench_server, "DATABASES", databases)
    monkeypatch.setattr(livesqlbench_server, "fetch", lambda data: 0)
    monkeypatch.setattr(livesqlbench_server, "images", lambda run, root, dbs: None)
    livesqlbench_server.prepare(tmp_path, tmp_path / "missing-answers.jsonl")
    printed = capsys.readouterr().out
    assert f"tasks: 2 in {tmp_path / 'tasks'}" in printed
    assert f"inline tasks: 2 in {tmp_path / 'tasks-inline'}" in printed
    plain = (tmp_path / "tasks" / "lsb-tide_q1" / "instruction.md").read_text()
    inline = (tmp_path / "tasks-inline" / "lsb-tide_q1" / "instruction.md").read_text()
    assert plain == TIDE_Q1_INSTRUCTION
    assert "# Database Schema:" in inline


class Docker:
    """`docker image inspect` finds `images`; every other call succeeds."""

    def __init__(self, images: set[str]) -> None:
        self.images = images

    def __call__(self, cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        found = cmd[:3] != ["docker", "image", "inspect"] or cmd[-1] in self.images
        return subprocess.CompletedProcess(cmd, 0 if found else 1, "", "")


def test_check_reports_the_inline_tasks_only_when_they_are_there(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    generate(root, "tasks", inline=False)
    docker = Docker({livesqlbench.main_image()})
    fails = livesqlbench_server.check(root, docker)
    printed = capsys.readouterr().out
    assert "inline tasks" not in printed and INLINE not in printed

    tasks = generate(root, INLINE, inline=True)
    assert livesqlbench_server.check(root, docker) == fails + 1  # 2 of 180 inline tasks
    printed = capsys.readouterr().out
    main = livesqlbench.main_image()
    assert f"FAIL  LiveSQLBench inline tasks: 2 in {root / 'tasks-inline'}" in printed
    assert f"ok    LiveSQLBench inline tasks naming another image than {main}: 0" in printed

    old = tasks["lsb-tide_q1"] / "task.toml"
    old.write_text(old.read_text().replace(main, "da-lsb-main:000000000000"))
    assert livesqlbench_server.check(root, docker) == fails + 2
    printed = capsys.readouterr().out
    assert f"FAIL  LiveSQLBench inline tasks naming another image than {main}: 1" in printed


def test_check_adds_no_failure_for_a_complete_inline_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(livesqlbench, "DEV_SIZE", 1)
    monkeypatch.setattr(livesqlbench, "HOLDOUT_SIZE", 1)  # the fixture's 2 Query tasks: all
    root = tmp_path / "root"
    root.mkdir()
    generate(root, "tasks", inline=False)
    docker = Docker({livesqlbench.main_image()})
    fails = livesqlbench_server.check(root, docker)
    capsys.readouterr()
    generate(root, INLINE, inline=True)
    assert livesqlbench_server.check(root, docker) == fails
    printed = capsys.readouterr().out
    assert f"ok    LiveSQLBench inline tasks: 2 in {root / INLINE}" in printed
    assert "FAIL  LiveSQLBench inline" not in printed
