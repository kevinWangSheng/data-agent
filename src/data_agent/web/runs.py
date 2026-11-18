"""The runs directory as the web backend reads it (.scratch/web-ui/spec.md "后端接口").

One subdirectory per run, named by run id, holding the run's append-only JSONL event file. There
is no index: every listing scans the directory (spec "运行索引"). A reader may catch the writer
mid-line, so only complete lines (ending in a newline) count as events.

A run this server started also has `web-run.json` (data_agent.web.launcher): its row as submitted,
with `status` "running" until the process exits, then "cancelled" or "failure" when it exits
without a `result` event. It lists the run before the event file exists and ends a run that never
writes its `result`.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# The header events (`version`, `task`, `status`, ...) come first; a summary reads this many lines.
HEAD_LINES = 8
# The tail is read back in blocks of this size until it holds the last complete line.
TAIL_BLOCK = 64 * 1024
RECORD = "web-run.json"
# The conversations' directory (data_agent.web.conversations), not a run.
CONVERSATIONS = "conversations"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Chunk:
    """Events read from an event file, and the byte offset just past the last complete line."""

    events: list[dict[str, Any]]
    offset: int


class RunsDir:
    def __init__(self, path: Path) -> None:
        self.path = path

    def run_dir(self, run_id: str) -> Path | None:
        """The run's directory, or None when there is no run by that id. An id from a URL is a plain
        directory name: `..`, a path or a hidden name is no run. Entries the user placed in the
        runs directory, symlinks included, are runs like any other."""
        if not is_plain_name(run_id) or run_id == CONVERSATIONS:
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

    def record(self, run_id: str) -> dict[str, Any] | None:
        """The server's record of a run it started; None for any other run."""
        run_dir = self.run_dir(run_id)
        path = run_dir / RECORD if run_dir is not None else None
        return json.loads(path.read_text()) if path is not None and path.is_file() else None

    def write_record(self, run_id: str, record: dict[str, Any]) -> None:
        """Replaces the record whole, so a reader never sees half of it."""
        path = self.path / run_id / RECORD
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record))
        os.replace(tmp, path)

    def ended(self, run_id: str) -> bool:
        """Whether the server saw its run exit without a `result` event."""
        record = self.record(run_id)
        return record is not None and record["status"] != "running"

    def has_result(self, run_id: str) -> bool:
        """Whether the run has written its `result` event, its last event."""
        path = self.event_file(run_id)
        last = last_line(path) if path is not None else None
        return last is not None and is_result(json.loads(last))

    def session_id(self, run_id: str) -> str | None:
        """The SDK session's id the run's `status` event (`state: "started"`) gives; None when it
        has written none (yet)."""
        path = self.event_file(run_id)
        if path is None:
            return None
        with path.open("rb") as file:
            for line in file:
                if not line.endswith(b"\n"):
                    break
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("type") == "status" and event.get("state") == "started":
                    session = event.get("session_id")
                    return session if isinstance(session, str) and session else None
        return None

    def read(self, run_id: str, offset: int = 0) -> Chunk | None:
        """The run's complete events from byte `offset` on; None before its event file exists."""
        path = self.event_file(run_id)
        return read_from(path, offset) if path is not None else None

    def summaries(self) -> list[dict[str, Any]]:
        """Every run, newest start first. A run whose files do not parse is left out (and the log
        says so), not the whole list."""
        runs: list[dict[str, Any]] = []
        for entry in self.path.iterdir():
            try:
                summary = self.summary(entry.name)
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                log.warning("run %s: unreadable run files: %s", entry.name, exc)
                continue
            if summary is not None:
                runs.append(summary)
        return sorted(runs, key=lambda run: run["started_at"] or "", reverse=True)

    def summary(self, run_id: str) -> dict[str, Any] | None:
        """The run's row: from its event file when there is one, else from the server's record;
        None when there is neither (not a run)."""
        event_file = self.event_file(run_id)
        record = self.record(run_id)
        if event_file is None:
            return record
        summary = summarize_run(run_id, event_file)
        if summary["status"] == "running" and record is not None:
            summary["status"] = record["status"]
        return summary


def is_plain_name(name: str) -> bool:
    """Whether an id from a URL is a plain directory name: not `..`, a path or a hidden name."""
    return name not in ("", ".", "..") and "/" not in name and "\\" not in name and name[0] != "."


def read_from(path: Path, offset: int = 0) -> Chunk:
    """The complete event lines from byte `offset` on."""
    with path.open("rb") as file:
        file.seek(offset)
        data = file.read()
    end = data.rfind(b"\n") + 1
    events = [json.loads(line) for line in data[:end].splitlines() if line.strip()]
    return Chunk(events, offset + end)


def summarize_run(run_id: str, path: Path) -> dict[str, Any]:
    """A run's row in the list, from its header events and its `result` event. A run without a
    `result` event is still going (as far as its event file says)."""
    head: list[dict[str, Any]] = []
    with path.open("rb") as file:
        for line in file:
            if len(head) == HEAD_LINES or not line.endswith(b"\n"):
                break
            if line.strip():
                head.append(json.loads(line))
    last = last_line(path)
    # The `result` event is the final event a run writes, so only the last line can be it.
    last_event = json.loads(last) if last is not None else None
    first_by_type = {event["type"]: event for event in reversed(head)}
    version = first_by_type.get("version", {})
    task = first_by_type.get("task", {})
    result = last_event if last_event is not None and is_result(last_event) else None
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


def is_result(event: dict[str, Any]) -> bool:
    return event.get("type") == "result"


def last_line(path: Path) -> bytes | None:
    """The file's last complete (newline-ended) non-blank line, read back from the end."""
    with path.open("rb") as file:
        end = file.seek(0, 2)
        data = b""
        while True:
            start = max(0, end - len(data) - TAIL_BLOCK)
            file.seek(start)
            data = file.read(end - start)
            complete = data[: data.rfind(b"\n") + 1]
            lines = [line for line in complete.splitlines() if line.strip()]
            # The first line of a block may be cut off at the block's start; only one that begins
            # after a newline (or at the file's start) is whole.
            if len(lines) > 1 or (lines and start == 0):
                return lines[-1]
            if start == 0:
                return None


def elapsed_ms(start: str | None, end: str | None) -> int | None:
    if start is None or end is None:
        return None
    return round(
        (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000
    )
