"""Process metrics of trials, read from their event files (`data_agent.agent` writes them; ticket
28): turns, a run stopped by the spend cap or the turn limit (the SDK result's subtype), verifier
calls and their check statuses, edits to dbt model files, spend, and timing (ticket 51: trial and
verifier walls, tool calls, each tool's p50/p90/p95/p99 and timeouts); per task, and the mean
spend of the successful trials. For dev trials only: callers keep holdout trials out (ADR-0004).

Model edits count Claude Code's file-writing tools on a `.sql` file under a `models/` directory;
a model rewritten through Bash (`sed -i`, a heredoc) is not counted.
"""

import json
from collections import Counter
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from data_agent.verifiers import AGENT_TOOL
from evals.harbor import Trial, last_event_file
from evals.metrics import is_success

EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})
# The SDK ResultMessage subtypes of a run cut off by a limit (data_agent.agent's `Spend` writes the
# budget one too when it stops the run itself).
STOPS = {"error_max_budget_usd": "budget", "error_max_turns": "turns"}


def verifier_checks(event: dict[str, Any]) -> list[dict[str, Any]]:
    """A `verifier_end` event's checks as rows `{model, check, status}`, whichever shape the agent
    wrote: the old list of such rows, or the v2 mapping model -> {check number -> status}
    (verifier_v2.hooks; the numbers are string keys once the event is JSON). Rows of a mapping
    come out in model order, checks numbered in order."""
    checks = event.get("checks") or []
    if isinstance(checks, list):
        return checks
    rows: list[dict[str, Any]] = []
    for model, statuses in checks.items():
        for number, status in sorted(statuses.items(), key=lambda item: int(item[0])):
            rows.append({"model": model, "check": f"CHECK {number}", "status": status})
    return rows


def is_model_edit(event: dict[str, Any]) -> bool:
    if event.get("type") != "tool_start" or event.get("tool") not in EDIT_TOOLS:
        return False
    path = PurePosixPath(str((event.get("input") or {}).get("file_path") or ""))
    return path.suffix == ".sql" and "models" in path.parts[:-1]


def event_time(event: dict[str, Any]) -> float | None:
    try:
        return datetime.fromisoformat(str(event["ts"])).timestamp()
    except (KeyError, ValueError):
        return None


def union_length(windows: list[tuple[float, float]]) -> float:
    """Seconds covered by the windows, overlaps counted once."""
    total, reach = 0.0, float("-inf")
    for start, end in sorted(windows):
        if end > reach:
            total += end - max(start, reach)
            reach = end
    return total


def verifier_wall(
    stamped: list[tuple[float, dict[str, Any]]], trial_end: float
) -> tuple[float | None, int]:
    """Seconds during which some verifier ran, and how many verifiers had no `verifier_end` (cut
    off by a timeout), from `(time, event)` pairs in file order. `verifier_start` and
    `verifier_end` pair by `agent_id`. Verifiers and Agent calls pair in order: each verifier, by
    start, takes the earliest Agent call started at or before it that no earlier verifier took.
    An unclosed verifier ends when its Agent call returned (unless that came first: a background
    dispatch), else at the trial's last event. None when no verifier started."""
    starts: list[tuple[float, str]] = []
    ends: dict[str, float] = {}
    call_starts: list[tuple[float, str]] = []
    call_ends: dict[str, float] = {}
    for when, event in stamped:
        kind = event.get("type")
        if kind == "verifier_start":
            starts.append((when, str(event.get("agent_id"))))
        elif kind == "verifier_end":
            ends[str(event.get("agent_id"))] = when
        elif kind == "tool_start" and event.get("tool") == AGENT_TOOL:
            call_starts.append((when, str(event.get("tool_use_id"))))
        elif kind == "tool_end" and event.get("tool") == AGENT_TOOL:
            call_ends[str(event.get("tool_use_id"))] = when
    if not starts:
        return None, 0
    unclaimed = sorted(call_starts)
    windows: list[tuple[float, float]] = []
    unclosed = 0
    for start, agent_id in sorted(starts):
        call = next((c for c in unclaimed if c[0] <= start), None)
        if call is not None:
            unclaimed.remove(call)
        if agent_id in ends:
            windows.append((start, ends[agent_id]))
            continue
        unclosed += 1
        returned = call_ends.get(call[1]) if call is not None else None
        end = returned if returned is not None and returned > start else trial_end
        windows.append((start, max(start, end)))
    return union_length(windows), unclosed


def trial_metrics(trial: Trial) -> dict[str, Any]:
    """One trial's process metrics, from the event file it is scored by (`last_event_file`).

    Timing (ticket 51): `wall_s` from the first event to the last; `tool_calls` counts
    `tool_start` events (main agent and subagents); `durations` (seconds per call, by tool) and
    `timeouts` (calls by tool that hit the gateway's statement timeout) come from `tool_timing`
    events alone, never from `tool_end - tool_start` (a parallel batch's results come back
    together), and are None for an event file written before them. `untimed` counts by tool the
    `tool_timing` events without a `duration_ms` (the SDK leaves it optional), so a gap shows
    rather than shrinking the sample."""
    events = last_event_file(trial)
    turns, edits, calls, cost, tool_calls = 0, 0, 0, 0.0, 0
    checks: Counter[str] = Counter()
    stopped: str | None = None
    durations: dict[str, list[float]] | None = None
    timeouts: Counter[str] = Counter()
    untimed: Counter[str] = Counter()
    stamped: list[tuple[float, dict[str, Any]]] = []  # (time, event) of events with a timestamp
    if events is not None:
        with events.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a line cut short by a killed run (as harbor_agent.parse_events)
                if not isinstance(event, dict):
                    continue
                when = event_time(event)
                if when is not None:
                    stamped.append((when, event))
                kind = event.get("type")
                if kind == "result":
                    turns += int(event.get("num_turns") or 0)
                    cost += float(event.get("cost_usd") or 0.0)
                    stopped = STOPS.get(str(event.get("subtype")), stopped)
                elif kind == "verifier_start":
                    calls += 1
                elif kind == "verifier_end":
                    checks.update(str(c.get("status")) for c in verifier_checks(event))
                elif kind == "tool_start":
                    tool_calls += 1
                    if is_model_edit(event):
                        edits += 1
                elif kind == "tool_timing":
                    durations = {} if durations is None else durations
                    tool = str(event.get("tool"))
                    if event.get("duration_ms") is not None:
                        durations.setdefault(tool, []).append(float(event["duration_ms"]) / 1000)
                    else:
                        untimed[tool] += 1
                    if event.get("timed_out"):
                        timeouts[tool] += 1
    times = [when for when, _ in stamped]
    trial_end = max(times) if times else None
    verifier_s, unclosed = verifier_wall(stamped, trial_end) if trial_end is not None else (None, 0)
    return {
        "task": trial.task,
        "trial": trial.name,
        "success": is_success(trial.reward),
        "turns": turns,
        "stopped": stopped,
        "verifier_calls": calls,
        "verifier_checks": dict(checks),
        "model_edits": edits,
        "cost_usd": cost,
        "wall_s": trial_end - min(times) if trial_end is not None else None,
        "tool_calls": tool_calls,
        "verifier_wall_s": verifier_s,
        "verifier_unclosed": unclosed,
        "durations": durations,
        "timeouts": dict(timeouts) if durations is not None else None,
        "untimed": dict(untimed) if durations is not None else None,
        "events": str(events) if events else None,
    }


QUANTILES = (50, 90, 95, 99)
QUANTILE_KEYS = tuple(f"p{q}" for q in QUANTILES)


def percentiles(values: list[float]) -> dict[str, float] | None:
    """p50/p90/p95/p99, interpolating linearly between ranks (numpy's default); None for none."""
    if not values:
        return None
    ordered = sorted(values)
    out: dict[str, float] = {}
    for q in QUANTILES:
        rank = (len(ordered) - 1) * q / 100
        low = int(rank)
        high = min(low + 1, len(ordered) - 1)
        out[f"p{q}"] = ordered[low] + (ordered[high] - ordered[low]) * (rank - low)
    return out


def timing_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Timing over trial rows: percentiles of the trial wall and of the verifier wall (trials
    with a verifier), total tool calls, and per tool its calls, percentiles, timeouts and calls
    without a duration, pooled from the trials that have `tool_timing` events (`trials_timed` of
    `trials`: the rest are old event files). `tools`, `timeouts` and `untimed_calls` are None
    when no trial has any: missing, not zero."""
    timed = [r for r in rows if r.get("durations") is not None]
    tools: dict[str, dict[str, Any]] | None = None
    if timed:
        pooled: dict[str, list[float]] = {}
        timeouts: Counter[str] = Counter()
        untimed: Counter[str] = Counter()
        for row in timed:
            for tool, values in row["durations"].items():
                pooled.setdefault(tool, []).extend(values)
            timeouts.update(row["timeouts"])
            untimed.update(row["untimed"])
        names = pooled.keys() | timeouts.keys() | untimed.keys()
        tools = {
            tool: {
                "calls": len(pooled.get(tool, [])) + untimed[tool],
                **(percentiles(pooled.get(tool, [])) or dict.fromkeys(QUANTILE_KEYS)),
                "timeouts": timeouts[tool],
                "untimed": untimed[tool],
            }
            for tool in sorted(names, key=lambda t: (-len(pooled.get(t, [])) - untimed[t], t))
        }
    walls = [r["wall_s"] for r in rows if r.get("wall_s") is not None]
    verifiers = [r["verifier_wall_s"] for r in rows if r.get("verifier_wall_s") is not None]
    return {
        "trials": len(rows),
        "trials_timed": len(timed),
        "wall_s": percentiles(walls),
        "verifier_wall_s": percentiles(verifiers),
        "verifier_unclosed": sum(r.get("verifier_unclosed") or 0 for r in rows),
        "tool_calls": sum(r.get("tool_calls") or 0 for r in rows),
        "timeouts": sum(t["timeouts"] for t in tools.values()) if tools is not None else None,
        "untimed_calls": sum(t["untimed"] for t in tools.values()) if tools is not None else None,
        "tools": tools,
    }


MISSING = "missing"


def seconds(value: float | None) -> str:
    return MISSING if value is None else f"{value:.1f}"


def quantile_cells(stats: dict[str, Any] | None) -> str:
    """`p50 / p90 / p95 / p99` in seconds, or `missing`."""
    if stats is None:
        return MISSING
    return " / ".join(seconds(stats.get(key)) for key in QUANTILE_KEYS)


def timing_markdown(summaries: list[tuple[str, dict[str, Any] | None]]) -> list[str]:
    """Markdown tables of named timing summaries (a config, an old/new job): the trial and
    verifier walls, calls and timeouts, then each tool's percentiles. A summary that is None
    (no process metrics, e.g. a holdout report) or lacks tool timings shows `missing`; `timed
    trials` says how many trials the tool times pool, `no duration` the calls left out."""
    lines = [
        "Timing, seconds as p50 / p90 / p95 / p99. Tool times are each call's own execution "
        "time (`tool_timing.duration_ms`); `missing` means the event files predate it.",
        "",
        "| run | trial wall | verifier wall | verifiers cut off | tool calls | timed trials "
        "| timeouts | no duration |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for name, s in summaries:
        if s is None:
            lines.append(f"| {name} |" + f" {MISSING} |" * 7)
            continue
        timeouts, untimed = (
            MISSING if s.get(key) is None else str(s[key]) for key in ("timeouts", "untimed_calls")
        )
        lines.append(
            f"| {name} | {quantile_cells(s['wall_s'])} | "
            f"{quantile_cells(s['verifier_wall_s'])} | {s['verifier_unclosed']} | "
            f"{s['tool_calls']} | {s['trials_timed']}/{s['trials']} | "
            f"{timeouts} | {untimed} |"
        )
    lines += [
        "",
        "| run | tool | calls | p50 | p90 | p95 | p99 | timeouts | no duration |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, s in summaries:
        tools = s.get("tools") if s is not None else None
        if tools is None:
            lines.append(f"| {name} | {MISSING} | | | | | | | |")
            continue
        for tool, t in tools.items():
            cells = " | ".join(seconds(t.get(key)) for key in QUANTILE_KEYS)
            lines.append(
                f"| {name} | {tool} | {t['calls']} | {cells} | {t['timeouts']} | {t['untimed']} |"
            )
    return lines


def task_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    checks: Counter[str] = Counter()
    for row in rows:
        checks.update(row["verifier_checks"])
    return {
        "successes": sum(1 for r in rows if r["success"]),
        "trials": len(rows),
        "mean_turns": sum(r["turns"] for r in rows) / len(rows),
        "stopped_by_budget": sum(1 for r in rows if r["stopped"] == "budget"),
        "stopped_by_turns": sum(1 for r in rows if r["stopped"] == "turns"),
        "verifier_calls": sum(r["verifier_calls"] for r in rows),
        "verifier_checks": dict(checks),
        "model_edits": sum(r["model_edits"] for r in rows),
        "cost_usd": sum(r["cost_usd"] for r in rows),
        "timing": timing_summary(rows),
    }


def job_metrics(trials: list[Trial]) -> dict[str, Any]:
    """Process metrics of dev trials: per task, per trial, and the mean spend of a success."""
    rows: dict[str, list[dict[str, Any]]] = {}
    for trial in trials:
        rows.setdefault(trial.task, []).append(trial_metrics(trial))
    succeeded = [r["cost_usd"] for rs in rows.values() for r in rs if r["success"]]
    return {
        "mean_cost_usd_succeeded": sum(succeeded) / len(succeeded) if succeeded else None,
        "per_task": {task: task_metrics(rs) for task, rs in sorted(rows.items())},
        "timing": timing_summary([r for rs in rows.values() for r in rs]),
        "trials": [r for _, rs in sorted(rows.items()) for r in rs],
    }
