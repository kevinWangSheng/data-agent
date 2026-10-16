"""`data-agent run` failures that stop before any model call: exit 2, a readable message, no event
file. The model-backed behavior is covered by seam A (tests/acceptance/test_seam_a_run_cli.py)."""

import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

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
