"""`evals.process` reads `verifier_end.checks` in both shapes the agent has written: the old list
(one entry per check, each with a `status`) and the v2 mapping (model -> {check number -> status},
ticket 46; JSON turns the numbers into string keys). Its timing metrics (ticket 51) come from
`tool_timing` events."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from lifecycle_events import dispatches

from evals.harbor import Trial
from evals.process import (
    job_metrics,
    percentiles,
    timing_markdown,
    timing_summary,
    trial_metrics,
    verifier_checks,
)


def named_trial(tmp_path: Path, task: str, name: str, *events: dict[str, Any]) -> Trial:
    events_dir = tmp_path / name / "agent" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "run-1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return Trial(
        task=task,
        name=name,
        reward=1.0,
        exception_type=None,
        uncached_input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        dir=tmp_path / name,
    )


def trial_with(tmp_path: Path, *events: dict[str, Any]) -> Trial:
    return named_trial(tmp_path, "t1", "t1__a", *events)


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


def test_a_trial_stopped_at_its_deadline_is_counted_not_an_infrastructure_error(
    tmp_path: Path,
) -> None:
    """Ticket 54: the agent stops itself at the trial deadline (`terminal_reason: deadline`) and
    exits as an unfinished agent, so Harbor records no exception; the Stop hook let it end
    unverified first (`ended_unverified`)."""
    from evals.report import is_infra_error

    trial = trial_with(
        tmp_path,
        {"type": "ended_unverified", "reason": "alpha: rule 1", "remaining_s": 900.0},
        {
            "type": "result",
            "subtype": "error_deadline",
            "terminal_reason": "deadline",
            "num_turns": 12,
            "cost_usd": 0.4,
        },
    )
    assert not is_infra_error(trial)
    row = trial_metrics(trial)
    assert (row["stopped"], row["ended_unverified"], row["cost_usd"]) == ("deadline", True, 0.4)
    task = job_metrics([trial])["per_task"]["t1"]
    assert (task["trials"], task["stopped_by_deadline"], task["ended_unverified"]) == (1, 1, 1)


def test_a_trial_that_ended_verified_has_no_ended_unverified(tmp_path: Path) -> None:
    row = trial_metrics(trial_with(tmp_path, {"type": "result", "subtype": "success"}))
    assert (row["stopped"], row["ended_unverified"]) == (None, False)


DENIED = (
    "PreToolUse:Bash hook error: Bash may only run one dbt command in the foreground. "
    "Denied: a second command."
)
OVERSIZE_RESULT = (
    "Error: result (665,331 characters) exceeds maximum allowed tokens. Output has been saved "
    "to /tmp/out.txt"
)


def tool_result(content: str, *, is_error: bool = True, tool: str = "Bash") -> dict[str, Any]:
    return {"type": "tool_end", "tool": tool, "content": content, "is_error": is_error}


def test_a_trial_counts_the_gate_events(tmp_path: Path) -> None:
    """Ticket 67: the stage-1 gate's mechanical metrics, from the events tickets 59 and 64 add
    and the `tool_end` contents Claude Code writes for a PreToolUse denial and for an output too
    long to return."""
    trial = trial_with(
        tmp_path,
        {"type": "verifier_dispatch", "n": 1, "models": ["a", "b", "c"], "source": "first"},
        {"type": "verifier_start", "verifier": "verifier", "agent_id": "v1"},
        tool_result(DENIED),
        tool_result(DENIED.replace("Bash", "Grep", 1), tool="Grep"),
        tool_result(OVERSIZE_RESULT, is_error=False, tool="mcp__data__query_database"),
        {"type": "verifier_dispatch", "n": 2, "models": ["b"], "source": "again"},
        {"type": "verifier_start", "verifier": "verifier", "agent_id": "v2"},
        {"type": "ended_with_unresolved", "reason": "b: rule 2", "blocks": 3},
        {"type": "ended_disputed", "reason": "b: CHECK 1", "disputed": {"b": [1]}},
        {"type": "result", "subtype": "success", "num_turns": 30},
    )
    row = trial_metrics(trial)
    assert row["ended_with_unresolved"] is True
    assert row["ended_disputed"] is True
    assert row["guard_denials"] == 2
    assert row["oversize_outputs"] == 1
    assert row["verifier_dispatches"] == 2
    assert row["dispatch_models"] == [3, 1]


def test_an_event_file_before_dispatch_events_has_no_dispatch_or_dispute_counts(
    tmp_path: Path,
) -> None:
    """An event file written before tickets 59 and 64 (accept-2's): a verifier ran but no
    `verifier_dispatch` was recorded, so dispatches and disputes are unknown, not zero; the
    rest is counted as usual."""
    trial = trial_with(
        tmp_path,
        {"type": "verifier_start", "verifier": "verifier", "agent_id": "v1"},
        tool_result(DENIED),
        tool_result(OVERSIZE_RESULT, is_error=False, tool="mcp__data__query_database"),
        {"type": "ended_with_unresolved", "reason": "a: rule 2", "blocks": 3},
        {"type": "result", "subtype": "success", "num_turns": 30},
    )
    row = trial_metrics(trial)
    assert (row["verifier_dispatches"], row["dispatch_models"], row["ended_disputed"]) == (
        None,
        None,
        None,
    )
    assert (row["guard_denials"], row["oversize_outputs"]) == (1, 1)
    assert (row["ended_with_unresolved"], row["verifier_calls"]) == (True, 1)


def test_a_trial_without_a_verifier_has_no_dispatch_and_no_dispute(tmp_path: Path) -> None:
    """No verifier ran (a baseline trial, or one that never verified): zero dispatches and no
    disputed FAIL are known in any event-file version."""
    row = trial_metrics(trial_with(tmp_path, {"type": "result", "subtype": "success"}))
    assert (row["verifier_dispatches"], row["dispatch_models"], row["ended_disputed"]) == (
        0,
        [],
        False,
    )


def test_a_result_that_says_truncated_is_no_oversize_output(tmp_path: Path) -> None:
    """Only Claude Code's own "exceeds maximum allowed tokens" error counts; a tool's result
    that mentions truncation (a gateway row cap, a log line) does not."""
    trial = trial_with(
        tmp_path,
        tool_result("rows: 1000 (truncated: true)", is_error=False, tool="mcp__data__query"),
        tool_result("Error: output truncated at 30000 characters"),
        tool_result("note: Error: result (5 characters) exceeds maximum allowed tokens"),
    )
    assert trial_metrics(trial)["oversize_outputs"] == 0


def test_a_result_given_as_content_blocks_is_read_as_text(tmp_path: Path) -> None:
    trial = trial_with(
        tmp_path,
        {
            "type": "tool_end",
            "tool": "Read",
            "is_error": True,
            "content": [
                {"type": "text", "text": DENIED},
            ],
        },
    )
    assert trial_metrics(trial)["guard_denials"] == 1


def gate_trials(tmp_path: Path) -> list[Trial]:
    """Three trials: a new one with two dispatches (3 and 1 models) that ended unresolved and
    disputed, a new one with no verifier, and an old one (a verifier, no dispatch event)."""
    dispatch = {"type": "verifier_dispatch", "source": "first"}
    start = {"type": "verifier_start", "verifier": "verifier", "agent_id": "v"}
    return [
        named_trial(
            tmp_path,
            "t1",
            "t1__a",
            {**dispatch, "n": 1, "models": ["a", "b", "c"]},
            start,
            {**dispatch, "n": 2, "models": ["b"]},
            start,
            tool_result(DENIED),
            tool_result(DENIED),
            tool_result(OVERSIZE_RESULT, is_error=False),
            {"type": "ended_with_unresolved"},
            {"type": "ended_disputed"},
        ),
        named_trial(tmp_path, "t1", "t1__b", tool_result(DENIED), {"type": "ended_unverified"}),
        named_trial(
            tmp_path,
            "t2",
            "t2__a",
            start,
            tool_result(DENIED),
            tool_result(OVERSIZE_RESULT, is_error=False),
        ),
    ]


def test_task_metrics_add_up_the_gate_counts_of_known_trials(tmp_path: Path) -> None:
    per_task = job_metrics(gate_trials(tmp_path))["per_task"]
    t1, t2 = per_task["t1"], per_task["t2"]
    assert (t1["ended_with_unresolved"], t1["ended_disputed"]) == (1, 1)
    assert (t1["guard_denials"], t1["oversize_outputs"]) == (3, 1)
    assert (t1["verifier_dispatches"], t1["dispatch_models"]) == (2, [3, 1])
    assert (t2["guard_denials"], t2["oversize_outputs"]) == (1, 1)
    assert (t2["ended_disputed"], t2["verifier_dispatches"], t2["dispatch_models"]) == (
        None,
        None,
        None,
    )


def test_job_metrics_average_the_gate_counts_per_trial_leaving_unknowns_out(
    tmp_path: Path,
) -> None:
    """Means per trial over the three trials; dispatches and disputes over the two whose event
    files record them; models per dispatch over the two dispatches."""
    gate = job_metrics(gate_trials(tmp_path))["gate"]
    assert gate["trials"] == 3
    assert gate["ended_with_unresolved"] == pytest.approx(1 / 3)
    assert gate["ended_unverified"] == pytest.approx(1 / 3)
    assert gate["guard_denials"] == pytest.approx(4 / 3)
    assert gate["oversize_outputs"] == pytest.approx(2 / 3)
    assert gate["trials_with_dispatches_recorded"] == 2
    assert gate["ended_disputed"] == pytest.approx(1 / 2)
    assert gate["verifier_dispatches"] == pytest.approx(2 / 2)
    assert gate["models_per_dispatch"] == pytest.approx(4 / 2)


def test_job_gate_means_are_none_when_no_trial_records_dispatches(tmp_path: Path) -> None:
    gate = job_metrics(gate_trials(tmp_path)[2:])["gate"]
    assert (gate["trials_with_dispatches_recorded"], gate["ended_disputed"]) == (0, None)
    assert (gate["verifier_dispatches"], gate["models_per_dispatch"]) == (None, None)
    assert gate["guard_denials"] == 1.0


def version(commit: str) -> dict[str, Any]:
    return {"type": "version", "git_commit": commit}


def test_trials_of_a_version_that_predates_dispatch_events_all_leave_them_unknown(
    tmp_path: Path,
) -> None:
    """A job run on code before ticket 59 (accept-2): one trial ran a verifier with no
    `verifier_dispatch`, so no trial of that commit records dispatches, not even one that ran no
    verifier; a trial of another commit that records them keeps its counts."""
    start = {"type": "verifier_start", "verifier": "verifier", "agent_id": "v"}
    dispatch = {"type": "verifier_dispatch", "n": 1, "models": ["a"], "source": "first"}
    job = job_metrics(
        [
            named_trial(tmp_path, "t1", "t1__a", version("old"), start),
            named_trial(tmp_path, "t1", "t1__b", version("old"), tool_result(DENIED)),
            named_trial(tmp_path, "t2", "t2__a", version("new"), dispatch, start),
            named_trial(tmp_path, "t2", "t2__b", version("new")),
        ]
    )
    old = [r for r in job["trials"] if r["task"] == "t1"]
    assert [(r["verifier_dispatches"], r["ended_disputed"]) for r in old] == [(None, None)] * 2
    assert job["per_task"]["t1"]["verifier_dispatches"] is None
    gate = job["gate"]
    assert (gate["trials_with_dispatches_recorded"], gate["verifier_dispatches"]) == (2, 0.5)


@pytest.mark.xfail(strict=True, reason="ticket 04: full-model redispatches per trial")
@pytest.mark.parametrize(
    ("counts", "full"),
    [((21,), 0), ((21, 6, 5), 0), ((21, 17, 3, 1, 21), 1), ((21, 21, 21), 2)],
)
def test_a_trial_counts_its_dispatches_that_resend_every_model(
    tmp_path: Path, counts: tuple[int, ...], full: int
) -> None:
    """Stage 1's efficiency line: a dispatch after the first that lists every model of the first
    dispatch (stage 1's pos-operations trials dispatched [21, 6, 5], [21, 17, 3, 1, 21] and
    [21, 21, 21] models)."""
    trial = trial_with(tmp_path, *dispatches(*counts), {"type": "result", "subtype": "success"})
    assert trial_metrics(trial)["full_redispatches"] == full


@pytest.mark.xfail(strict=True, reason="ticket 04: full-model redispatches per trial")
def test_full_redispatches_are_unknown_in_an_event_file_before_dispatch_events(
    tmp_path: Path,
) -> None:
    """As the other dispatch counts: a verifier ran and no dispatch was recorded (accept-2)."""
    trial = trial_with(
        tmp_path,
        {"type": "verifier_start", "verifier": "verifier", "agent_id": "v1"},
        {"type": "result", "subtype": "success"},
    )
    assert trial_metrics(trial)["full_redispatches"] is None
