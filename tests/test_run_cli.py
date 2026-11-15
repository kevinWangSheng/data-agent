"""`data-agent run` failures that stop before any model call: exit 2, a readable message, no event
file. The model-backed behavior is covered by seam A (tests/acceptance/test_seam_a_run_cli.py)."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest

from data_agent import cli
from data_agent.agent import Outcome, Task

DATA_AGENT = Path(sys.executable).with_name("data-agent")


def make_db(path: Path) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE items AS SELECT range AS id FROM range(3)")
    finally:
        con.close()
    return path


def run(
    tmp_path: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("DATA_AGENT_")}
    base["DATA_AGENT_API_KEY"] = "sk-test-not-used"
    return subprocess.run(
        [str(DATA_AGENT), "run", *args],
        env=base | (env or {}),
        capture_output=True,
        text=True,
        timeout=60,
    )


def run_args(tmp_path: Path, **overrides: str) -> list[str]:
    args = {
        "--task": "How many items are there?",
        "--type": "query",
        "--workspace": str(tmp_path),
        "--duckdb": str(tmp_path / "x.duckdb"),
        "--config": "full",
        "--events-dir": str(tmp_path / "events"),
    } | overrides
    return [part for pair in args.items() for part in pair]


def assert_config_error(proc: subprocess.CompletedProcess[str], tmp_path: Path, text: str) -> None:
    assert proc.returncode == 2, proc.stderr
    assert text in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not list((tmp_path / "events").glob("*.jsonl"))


@pytest.mark.parametrize("config", ["nope", "FULL"])
def test_unknown_config_is_rejected(tmp_path: Path, config: str) -> None:
    make_db(tmp_path / "x.duckdb")
    proc = run(tmp_path, *run_args(tmp_path, **{"--config": config}))
    assert_config_error(proc, tmp_path, config)


def test_missing_duckdb_file_is_rejected(tmp_path: Path) -> None:
    proc = run(tmp_path, *run_args(tmp_path))
    assert_config_error(proc, tmp_path, str(tmp_path / "x.duckdb"))


def test_missing_workspace_is_rejected(tmp_path: Path) -> None:
    make_db(tmp_path / "x.duckdb")
    missing = tmp_path / "no-such-dir"
    proc = run(tmp_path, *run_args(tmp_path, **{"--workspace": str(missing)}))
    assert_config_error(proc, tmp_path, str(missing))


def test_missing_api_key_is_rejected(tmp_path: Path) -> None:
    make_db(tmp_path / "x.duckdb")
    proc = run(tmp_path, *run_args(tmp_path), env={"DATA_AGENT_API_KEY": ""})
    assert_config_error(proc, tmp_path, "DATA_AGENT_API_KEY")


def test_missing_task_file_is_rejected(tmp_path: Path) -> None:
    make_db(tmp_path / "x.duckdb")
    args = run_args(tmp_path)
    task_at = args.index("--task")
    del args[task_at : task_at + 2]
    missing = tmp_path / "task.md"
    proc = run(tmp_path, *args, "--task-file", str(missing))
    assert_config_error(proc, tmp_path, str(missing))


def test_dbt_task_without_dbt_on_path_is_rejected(tmp_path: Path) -> None:
    """dbt tasks use the dbt the environment provides (Harbor: the image's), so none is an error."""
    make_db(tmp_path / "x.duckdb")
    proc = run(tmp_path, *run_args(tmp_path, **{"--type": "dbt"}), env={"PATH": "/usr/bin:/bin"})
    assert_config_error(proc, tmp_path, "dbt is not on PATH")


@pytest.mark.parametrize(
    ("extra", "name"), [((), "x.duckdb"), (("--data-source-name", "shop"), "shop")]
)
def test_data_source_name_is_the_file_name_or_the_name_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: tuple[str, ...], name: str
) -> None:
    """Ticket 74: the name the `task` event records; the caller may give it directly."""
    make_db(tmp_path / "x.duckdb")
    seen: list[Task] = []

    async def fake_run(task: Task, *_: Any) -> Outcome:
        seen.append(task)
        return Outcome("", True, False, [], tmp_path / "events.jsonl")

    monkeypatch.setattr(cli, "run", fake_run)
    with pytest.raises(SystemExit):
        cli.main(["run", *run_args(tmp_path), *extra])
    assert seen[0].data_source_name == name
