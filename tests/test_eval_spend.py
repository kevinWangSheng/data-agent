"""Edge cases for `python -m evals spend` beyond the seam C acceptance test (ticket 11)."""

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from evals.spend import FileSpend, file_spend

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


def test_spend_refuses_negative_costs(tmp_path: Path) -> None:
    # A negative cost would hide real spend from the 80% guard.
    write_events(tmp_path / "events.jsonl", result(5.0), result(-5.0))
    proc = spend(tmp_path, "1")
    assert proc.returncode == 2
    assert "events.jsonl" in proc.stderr


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


# Ticket 34: billed spend. DeepSeek bills off-peak runs at half of every price; peak is Monday to
# Friday UTC 01:00-04:00 and 06:00-10:00. A run is priced by the `ts` of its first event.

MONDAY = "2026-11-02"
SATURDAY = "2026-10-31"
SUNDAY = "2026-11-01"


def run_events(
    ts: str | None, cost: float, model: str = "deepseek-flash", basis: str = "price_table"
) -> list[dict[str, object]]:
    version: dict[str, object] = {"seq": 0, "type": "version", "model": model}
    if ts is not None:
        version["ts"] = ts
    end = {"seq": 1, "type": "result", "cost_usd": cost, "cost_basis": basis}
    return [version, end]


def costs(tmp_path: Path, *events: dict[str, object]) -> FileSpend:
    path = tmp_path / "events.jsonl"
    write_events(path, *events)
    return file_spend(path)


@pytest.mark.parametrize(
    ("hour", "peak"),
    [
        ("00:59", False),
        ("01:00", True),
        ("03:59", True),
        ("04:00", False),
        ("05:59", False),
        ("06:00", True),
        ("09:59", True),
        ("10:00", False),
    ],
)
def test_a_weekday_run_is_billed_by_the_hour_it_started(
    tmp_path: Path, hour: str, peak: bool
) -> None:
    found = costs(tmp_path, *run_events(f"{MONDAY}T{hour}:30.5+00:00", 0.4))
    assert found.listed == Decimal("0.4")
    assert found.billed == (Decimal("0.4") if peak else Decimal("0.2"))
    assert found.period == ("peak" if peak else "off-peak")


@pytest.mark.parametrize("day", [SATURDAY, SUNDAY])
@pytest.mark.parametrize("hour", ["01:00", "08:00", "23:00"])
def test_weekends_are_off_peak_all_day(tmp_path: Path, day: str, hour: str) -> None:
    found = costs(tmp_path, *run_events(f"{day}T{hour}:00+00:00", 0.4))
    assert (found.billed, found.period) == (Decimal("0.2"), "off-peak")


def test_the_start_is_read_in_utc(tmp_path: Path) -> None:
    # 09:30 in Beijing is 01:30 UTC on a Monday: peak.
    found = costs(tmp_path, *run_events(f"{MONDAY}T09:30:00+08:00", 0.4))
    assert (found.billed, found.period) == (Decimal("0.4"), "peak")


@pytest.mark.parametrize(
    "events",
    [
        run_events(f"{SATURDAY}T12:00:00+00:00", 0.4, basis="sdk"),
        run_events(f"{SATURDAY}T12:00:00+00:00", 0.4, model="claude-sonnet-4-5"),
        run_events(None, 0.4),
        run_events("not a time", 0.4),
        run_events(f"{SATURDAY}T12:00:00", 0.4),  # no zone: the UTC hour is unknown
        [{"seq": 1, "type": "result", "cost_usd": 0.4, "cost_basis": "price_table"}],
    ],
    ids=["sdk", "other-model", "no-ts", "bad-ts", "naive-ts", "no-version"],
)
def test_no_discount_unless_an_off_peak_deepseek_price_table_run(
    tmp_path: Path, events: list[dict[str, object]]
) -> None:
    found = costs(tmp_path, *events)
    assert found.billed == found.listed == Decimal("0.4")


def test_the_spend_guard_stops_on_billed_spend_and_prints_both_totals(tmp_path: Path) -> None:
    write_events(tmp_path / "a" / "events.jsonl", *run_events(f"{SATURDAY}T12:00:00+00:00", 0.6))
    write_events(tmp_path / "b" / "events.jsonl", *run_events(f"{MONDAY}T02:00:00+00:00", 0.2))
    write_events(tmp_path / "c" / "events.jsonl", *run_events(None, 0.1))
    # Billed: 0.3 + 0.2 + 0.1 = 0.6; cost_usd 0.9. A $1 budget stops at $0.80.
    under = spend(tmp_path, "1")
    assert under.returncode == 0, under.stderr
    assert "billed $0.6000" in under.stdout
    assert "(cost_usd $0.9000)" in under.stdout
    assert "1 off-peak, 1 peak, 1 unknown" in under.stdout
    assert spend(tmp_path, "0.75").returncode == 1  # $0.60 is exactly 80% of $0.75
    assert spend(tmp_path, "0.76").returncode == 0  # though cost_usd $0.90 is over $0.608
