"""`evals.process` reads `verifier_end.checks` in both shapes the agent has written: the old list
(one entry per check, each with a `status`) and the v2 mapping (model -> {check number -> status},
ticket 46; JSON turns the numbers into string keys). Its timing metrics (ticket 51) come from
`tool_timing` events."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from evals.harbor import Trial
from evals.process import (
    job_metrics,
    percentiles,
    timing_markdown,
    timing_summary,
    trial_metrics,
    verifier_checks,
)


def trial_with(tmp_path: Path, *events: dict[str, Any]) -> Trial:
    events_dir = tmp_path / "t1__a" / "agent" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "run-1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return Trial(
        task="t1",
        name="t1__a",
        reward=1.0,
        exception_type=None,
        uncached_input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        dir=tmp_path / "t1__a",
    )


def test_checks_as_a_list_count_each_entry_status(tmp_path: Path) -> None:
    trial = trial_with(
        tmp_path,
        {"type": "verifier_start", "verifier": "verifier", "agent_id": "a1"},
        {
            "type": "verifier_end",
            "verifier": "verifier",
            "agent_id": "a1",
            "checks": [
                {"model": "x", "check": "CHECK 1", "status": "PASS"},
                {"model": "x", "check": "CHECK 2", "status": "FAIL"},
                {"model": "y", "check": "CHECK 1", "status": "PASS"},
            ],
        },
    )
    assert trial_metrics(trial)["verifier_checks"] == {"PASS": 2, "FAIL": 1}


def test_checks_as_a_mapping_count_each_status_under_every_model(tmp_path: Path) -> None:
    trial = trial_with(
        tmp_path,
        {"type": "verifier_start", "verifier": "verifier", "agent_id": "a1"},
        {
            "type": "verifier_end",
            "verifier": "verifier",
            "agent_id": "a1",
            "checks": {"x": {"1": "PASS", "2": "FAIL"}, "y": {"1": "PASS", "3": "NEEDS_INFO"}},
        },
    )
    assert trial_metrics(trial)["verifier_checks"] == {"PASS": 2, "FAIL": 1, "NEEDS_INFO": 1}


def test_verifier_checks_flattens_a_mapping_into_rows() -> None:
    assert verifier_checks({"checks": {"x": {"2": "FAIL", "1": "PASS"}}}) == [
        {"model": "x", "check": "CHECK 1", "status": "PASS"},
        {"model": "x", "check": "CHECK 2", "status": "FAIL"},
    ]
    rows = [{"model": "x", "check": "CHECK 1", "status": "PASS"}]
    assert verifier_checks({"checks": rows}) == rows
    assert verifier_checks({}) == []


# Ticket 51: timing comes from `tool_timing.duration_ms`, never from `tool_end - tool_start`.
T0 = datetime.fromisoformat("2026-11-05T00:00:00+00:00")


def at(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def call(
    tool: str, call_id: str, start: float, end: float, ms: int | None, **extra: Any
) -> list[dict[str, Any]]:
    """A tool call as the agent writes it: start and end from the message stream, and (unless
    `ms` is None, an old event file) the hook's `tool_timing`, written when the call finished."""
    events: list[dict[str, Any]] = [
        {"type": "tool_start", "ts": at(start), "tool": tool, "tool_use_id": call_id},
        {"type": "tool_end", "ts": at(end), "tool": tool, "tool_use_id": call_id},
    ]
    if ms is not None:
        timing: dict[str, Any] = {"type": "tool_timing", "ts": at(start + ms / 1000), "tool": tool}
        timing |= {"tool_use_id": call_id, "duration_ms": ms, "failed": False, "timed_out": False}
        events.insert(1, timing | extra)
    return events


def parallel_batch(timed: bool) -> list[dict[str, Any]]:
    """A Glob and a Bash dispatched together: both results come back with the Bash, 60 s in."""
    query = "mcp__data__query_database"
    return [
        {"type": "version", "ts": at(0)},
        *call("Glob", "g1", 1, 61, 400 if timed else None),
        *call("Bash", "b1", 1, 61, 60000 if timed else None),
        *call("Glob", "g2", 62, 62.5, 300 if timed else None),
        *call(query, "q1", 63, 213, 150000 if timed else None, timed_out=True, failed=True),
        {"type": "result", "ts": at(220), "num_turns": 3, "cost_usd": 0.1},
    ]


def test_tool_durations_come_from_duration_ms_not_the_batch(tmp_path: Path) -> None:
    row = trial_metrics(trial_with(tmp_path, *parallel_batch(timed=True)))
    assert row["durations"] == {
        "Glob": [0.4, 0.3],
        "Bash": [60.0],
        "mcp__data__query_database": [150.0],
    }
    assert row["timeouts"] == {"mcp__data__query_database": 1}
    assert row["tool_calls"] == 4
    assert row["wall_s"] == 220.0


def test_an_old_event_file_has_no_durations_but_still_a_wall_and_calls(tmp_path: Path) -> None:
    row = trial_metrics(trial_with(tmp_path, *parallel_batch(timed=False)))
    assert row["durations"] is None and row["timeouts"] is None
    assert row["tool_calls"] == 4 and row["wall_s"] == 220.0


def verifier(agent_id: str, start: float, end: float | None) -> list[dict[str, Any]]:
    fields = {"verifier": "verifier", "agent_id": agent_id}
    events = [{"type": "verifier_start", "ts": at(start), **fields}]
    if end is not None:
        events.append({"type": "verifier_end", "ts": at(end), **fields})
    return events


def test_verifier_wall_is_the_union_of_verifier_windows(tmp_path: Path) -> None:
    # Two verifiers overlapping over 100-250 s and one alone over 300-350 s: 200 s.
    events = [
        {"type": "version", "ts": at(0)},
        *verifier("a1", 100, 200),
        *verifier("a2", 150, 250),
        *verifier("a3", 300, 350),
        {"type": "result", "ts": at(400)},
    ]
    row = trial_metrics(trial_with(tmp_path, *events))
    assert row["verifier_wall_s"] == 200.0 and row["verifier_unclosed"] == 0


def test_a_verifier_cut_off_ends_at_its_agent_call_or_the_trial_end(tmp_path: Path) -> None:
    events = [
        {"type": "version", "ts": at(0)},
        {"type": "tool_start", "ts": at(99), "tool": "Agent", "tool_use_id": "v1"},
        *verifier("a1", 100, None),
        {"type": "tool_end", "ts": at(180), "tool": "Agent", "tool_use_id": "v1"},
        {"type": "tool_start", "ts": at(199), "tool": "Agent", "tool_use_id": "v2"},
        *verifier("a2", 200, None),
        {"type": "status", "ts": at(500)},  # killed: no result, no tool_end for v2
    ]
    row = trial_metrics(trial_with(tmp_path, *events))
    assert row["verifier_wall_s"] == 80.0 + 300.0
    assert row["verifier_unclosed"] == 2


def test_parallel_verifiers_cut_off_end_at_their_own_agent_calls(tmp_path: Path) -> None:
    """Two Agent calls dispatched together; their verifiers start in the same order. a1 is cut
    off, a2 ends at 100 s. Paired in order, a1 takes v1 and ends when v1 returned (150 s);
    paired with the latest call before it, it would take v2 and run to 400 s."""
    events = [
        {"type": "version", "ts": at(0)},
        {"type": "tool_start", "ts": at(10), "tool": "Agent", "tool_use_id": "v1"},
        {"type": "tool_start", "ts": at(10.5), "tool": "Agent", "tool_use_id": "v2"},
        *verifier("a1", 11, None),
        *verifier("a2", 12, 100),  # closed: still takes its own call, v2
        {"type": "tool_end", "ts": at(150), "tool": "Agent", "tool_use_id": "v1"},
        {"type": "tool_end", "ts": at(400), "tool": "Agent", "tool_use_id": "v2"},
        {"type": "result", "ts": at(900)},
    ]
    row = trial_metrics(trial_with(tmp_path, *events))
    # a1 takes v1 (ends 150); taking v2 (400) or the trial end would give more.
    assert row["verifier_wall_s"] == 150.0 - 11.0
    assert row["verifier_unclosed"] == 1


def test_a_trial_without_verifiers_has_no_verifier_wall(tmp_path: Path) -> None:
    row = trial_metrics(trial_with(tmp_path, *parallel_batch(timed=True)))
    assert row["verifier_wall_s"] is None


def test_percentiles_interpolate_between_ranks() -> None:
    assert percentiles([]) is None
    assert percentiles([5.0]) == {"p50": 5.0, "p90": 5.0, "p95": 5.0, "p99": 5.0}
    values = [float(v) for v in range(1, 101)]  # 1..100
    assert percentiles(values) == {
        "p50": pytest.approx(50.5),
        "p90": pytest.approx(90.1),
        "p95": pytest.approx(95.05),
        "p99": pytest.approx(99.01),
    }


def test_timing_summary_pools_the_trials_durations(tmp_path: Path) -> None:
    rows = [trial_metrics(trial_with(tmp_path, *parallel_batch(timed=True)))]
    summary = timing_summary(rows)
    assert summary["tools"]["Glob"]["calls"] == 2
    assert summary["tools"]["Glob"]["p50"] == pytest.approx(0.35)
    assert summary["tools"]["Glob"]["p99"] < 1.0  # the batch's 60 s never leaks in
    assert summary["tools"]["mcp__data__query_database"]["timeouts"] == 1
    assert summary["timeouts"] == 1 and summary["tool_calls"] == 4
    assert summary["wall_s"]["p50"] == 220.0
    assert summary["verifier_wall_s"] is None


def test_timing_summary_of_old_files_marks_durations_missing(tmp_path: Path) -> None:
    rows = [trial_metrics(trial_with(tmp_path, *parallel_batch(timed=False)))]
    summary = timing_summary(rows)
    assert summary["tools"] is None and summary["timeouts"] is None
    assert summary["tool_calls"] == 4


def test_calls_without_a_duration_are_counted_not_dropped(tmp_path: Path) -> None:
    events = [
        {"type": "version", "ts": at(0)},
        *call("Read", "r1", 1, 2, 500),
        {"type": "tool_timing", "ts": at(3), "tool": "Read", "tool_use_id": "r2"},
        {"type": "tool_timing", "ts": at(4), "tool": "Grep", "tool_use_id": "g1"},
    ]
    old = trial_with(tmp_path / "old", {"type": "version", "ts": at(0)})
    rows = [trial_metrics(trial_with(tmp_path / "new", *events)), trial_metrics(old)]
    assert rows[0]["untimed"] == {"Read": 1, "Grep": 1}
    summary = timing_summary(rows)
    assert (summary["trials_timed"], summary["trials"], summary["untimed_calls"]) == (1, 2, 2)
    assert (summary["tools"]["Read"]["calls"], summary["tools"]["Read"]["untimed"]) == (2, 1)
    assert summary["tools"]["Read"]["p50"] == 0.5
    assert summary["tools"]["Grep"]["p50"] is None and summary["tools"]["Grep"]["calls"] == 1
    text = "\n".join(timing_markdown([("new", summary)]))
    assert "| 1/2 | 0 | 2 |" in text
    assert "| new | Grep | 1 | missing | missing | missing | missing | 0 | 1 |" in text


def test_job_and_task_metrics_carry_the_timing_summary(tmp_path: Path) -> None:
    job = job_metrics([trial_with(tmp_path, *parallel_batch(timed=True))])
    assert job["timing"]["tools"]["Bash"]["calls"] == 1
    assert job["per_task"]["t1"]["timing"]["tools"]["Bash"]["p50"] == 60.0
