"""A stand-in for `data-agent run` in the web backend's acceptance tests (ticket 73).

`python fake_run.py --gate FILE <data-agent run arguments>` writes a run's event file the way
`data-agent run` does (`<events-dir>/run-<stamp>.jsonl`, one JSON event per line, `seq` from 0):
the header events and one query call, then waits until FILE exists, then writes the answer and the
result event and exits 0. Without the gate a test can watch a run that is still going, or cancel it.
"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GATE_TIMEOUT_SECONDS = 120


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--task")
    parser.add_argument("--task-file", type=Path)
    parser.add_argument("--type", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--events-dir", type=Path, required=True)
    parser.add_argument("--duckdb", type=Path)
    parser.add_argument("--postgres-env")
    args, _ = parser.parse_known_args()

    task = args.task if args.task is not None else args.task_file.read_text()
    source = args.postgres_env or (args.duckdb.name if args.duckdb else None)
    args.events_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    with (args.events_dir / f"run-{stamp}.jsonl").open("x", encoding="utf-8") as file:
        seq = 0

        def emit(type_: str, **fields: Any) -> None:
            nonlocal seq
            event = {"seq": seq, "type": type_, "ts": datetime.now(UTC).isoformat(), **fields}
            file.write(json.dumps(event) + "\n")
            file.flush()
            seq += 1

        emit("version", config=args.config, model="fake-model", git_commit="0" * 40)
        emit("task", task=task, task_type=args.type, data_source=source)
        emit("status", state="started", model="fake-model")
        emit("tool_start", tool="mcp__data__query_database", tool_use_id="q1", input={})
        emit("tool_end", tool="mcp__data__query_database", tool_use_id="q1", content="n\n-\n3")

        deadline = time.monotonic() + GATE_TIMEOUT_SECONDS
        while not args.gate.exists():
            if time.monotonic() > deadline:
                raise SystemExit("fake_run: gate never opened")
            time.sleep(0.05)

        emit("text", text="There are 3 orders.")
        emit("result", subtype="success", is_error=False, num_turns=2, cost_usd=0.01)


if __name__ == "__main__":
    main()
