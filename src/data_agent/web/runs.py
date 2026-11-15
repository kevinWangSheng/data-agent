"""The runs directory as the web backend reads it (.scratch/web-ui/spec.md "后端接口").

One subdirectory per run, named by run id, holding the run's append-only JSONL event file. There
is no index: every listing scans the directory (spec "运行索引"). A reader may catch the writer
mid-line, so only complete lines (ending in a newline) count as events.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# The header events (`version`, `task`, `status`, ...) come first; a summary reads this many lines.
HEAD_LINES = 8


@dataclass(frozen=True)
class Chunk:
    """Events read from an event file, and the byte offset just past the last complete line."""

    events: list[dict[str, Any]]
    offset: int


class RunsDir:
    def __init__(self, path: Path) -> None:
        self.path = path

    def run_dir(self, run_id: str) -> Path | None:
        """The run's directory, or None when there is no run by that id. An id is a plain directory
        name: anything that would point outside the runs directory is no run."""
        if run_id in ("", ".", "..") or "/" in run_id or "\\" in run_id or run_id.startswith("."):
            return None
        path = self.path / run_id
        return path if path.is_dir() else None

    def event_file(self, run_id: str) -> Path | None:
        """The run's event file; None while the run has not written it yet."""
        run_dir = self.run_dir(run_id)
        if run_dir is None:
            return None
        files = sorted(run_dir.glob("*.jsonl"))
        return files[0] if files else None

    def summaries(self) -> list[dict[str, Any]]:
        """Every run with an event file, newest start first."""
        runs = [
            summary(path.name, event_file)
            for path in self.path.iterdir()
            if (event_file := self.event_file(path.name)) is not None
        ]
        return sorted(runs, key=lambda run: run["started_at"] or "", reverse=True)


def read_from(path: Path, offset: int = 0) -> Chunk:
    """The complete event lines from byte `offset` on."""
    with path.open("rb") as file:
        file.seek(offset)
        data = file.read()
    end = data.rfind(b"\n") + 1
    events = [json.loads(line) for line in data[:end].splitlines() if line.strip()]
    return Chunk(events, offset + end)


def summary(run_id: str, path: Path) -> dict[str, Any]:
    """A run's row in the list, from its header events and its `result` event. A run without a
    `result` event is still going (ticket 78 adds `cancelled` for the runs this server stops)."""
    head: list[dict[str, Any]] = []
    last_line: bytes | None = None
    with path.open("rb") as file:
        for line in file:
            if not line.endswith(b"\n") or not line.strip():
                continue
            if len(head) < HEAD_LINES:
                head.append(json.loads(line))
            last_line = line
    # Only the last complete line can be the `result` event: it is the final event a run writes.
    last = json.loads(last_line) if last_line is not None else None
    first = {event["type"]: event for event in reversed(head)}
    version = first.get("version", {})
    task = first.get("task", {})
    result = last if last is not None and last.get("type") == "result" else None
    started_at: str | None = head[0].get("ts") if head else None
    if result is None:
        status, duration_ms, cost_usd = "running", None, None
    else:
        ok = result.get("subtype") == "success" and not result.get("is_error")
        status = "success" if ok else "failure"
        duration_ms = elapsed_ms(started_at, result.get("ts"))
        cost_usd = result.get("cost_usd")
    return {
        "id": run_id,
        "task": task.get("task"),
        "task_type": task.get("task_type"),
        "config": version.get("config"),
        "status": status,
        "started_at": started_at,
        "duration_ms": duration_ms,
        "cost_usd": cost_usd,
    }


def elapsed_ms(start: str | None, end: str | None) -> int | None:
    if start is None or end is None:
        return None
    return round(
        (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000
    )
