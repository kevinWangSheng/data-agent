"""Edge cases for `python -m evals spend` beyond the seam C acceptance test (ticket 11)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def spend(runs: Path, budget: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "evals", "spend", str(runs), "--budget", budget],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def write_events(path: Path, *events: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


def result(cost: float) -> dict[str, object]:
    return {"seq": 1, "type": "result", "output_tokens": 10, "cost_usd": cost}


def test_spend_sums_result_events_in_nested_files_only(tmp_path: Path) -> None:
    write_events(tmp_path / "a" / "events.jsonl", {"seq": 0, "type": "version"}, result(0.1))
    write_events(tmp_path / "b" / "c" / "run.jsonl", result(0.2))
    (tmp_path / "notes.json").write_text(json.dumps(result(100.0)))

    under = spend(tmp_path, "1")
    assert under.returncode == 0, under.stderr
    assert "0.30" in under.stdout
    assert spend(tmp_path, "0.375").returncode == 1  # 0.30 is exactly 80% of 0.375


def test_spend_is_zero_for_runs_without_results(tmp_path: Path) -> None:
    write_events(tmp_path / "crashed.jsonl", {"seq": 0, "type": "version"})
    assert spend(tmp_path, "1").returncode == 0


def test_spend_refuses_unreadable_event_files(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    write_events(path, result(0.1))
    with path.open("a") as f:
        f.write('{"seq": 2, "type": "res')
    proc = spend(tmp_path, "100")
    assert proc.returncode == 2
    assert "events.jsonl" in proc.stderr


def test_spend_reads_lines_with_unicode_line_separators(tmp_path: Path) -> None:
    # scrub writes non-ASCII back literally, so a tool output can hold U+2028, U+2029 or U+0085.
    path = tmp_path / "events.jsonl"
    output = {"seq": 0, "type": "tool_end", "output": "a\u2028b\u2029c\u0085d"}
    path.write_text(json.dumps(output, ensure_ascii=False) + "\n" + json.dumps(result(0.1)) + "\n")
    proc = spend(tmp_path, "1")
    assert proc.returncode == 0, proc.stderr
    assert "0.10" in proc.stdout


def test_spend_refuses_result_events_without_a_cost(tmp_path: Path) -> None:
    write_events(tmp_path / "events.jsonl", {"seq": 1, "type": "result", "cost_usd": None})
    assert spend(tmp_path, "100").returncode == 2


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any directory")
def test_spend_refuses_when_a_subdirectory_cannot_be_read(tmp_path: Path) -> None:
    locked = tmp_path / "trial"
    write_events(locked / "events.jsonl", result(5.0))
    locked.chmod(0)
    try:
        proc = spend(tmp_path, "100")
    finally:
        locked.chmod(0o755)
    assert proc.returncode == 2
    assert "trial" in proc.stderr


def test_spend_refuses_missing_directory_and_bad_budget(tmp_path: Path) -> None:
    assert spend(tmp_path / "nope", "1").returncode == 2
    assert spend(tmp_path, "0").returncode == 2
    assert spend(tmp_path, "-1").returncode == 2
