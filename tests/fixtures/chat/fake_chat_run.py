"""A stand-in for `data-agent run` in the chat backend's acceptance tests (ticket 88,
tests/acceptance/test_chat_api.py). It does not replace tests/fixtures/web/fake_run.py, which the
run-form tests keep using.

`python fake_chat_run.py --gate-dir DIR <data-agent run arguments>` writes one event file the way
`data-agent run` does (`<events-dir>/run-<stamp>.jsonl`, `seq` from 0) and follows the chat
contract (test_chat_api.py's docstring):

- the `task` event names the conversation (its directory's name) and the turn, when
  `--conversation-dir` and `--turn` are given;
- the `status` event carries the session id: the `--resume` id when given (a resumed session keeps
  its id), else a new one;
- one query call, with a `sql` event naming the first data source of `--data-sources` (or the
  `--duckdb` file's name) as its `database`;
- with `--chat`, every line read from stdin (`{"id": ..., "text": ...}`) becomes a
  `steer_delivered` event with the same id and text; a line that is not such an object is skipped;
- it appends `turn <n>` to `fake-turns.txt` in its working directory, so a test can see that the
  turns of one conversation share a working directory.

Then it waits until `<gate-dir>/<events-dir name>` exists (the test's go-ahead for this run), and
writes the answer and the `result` event and exits 0. Until then a test can steer or cancel it.
"""

import argparse
import json
import secrets
import sys
import threading
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GATE_TIMEOUT_SECONDS = 120


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-dir", type=Path, required=True)
    parser.add_argument("--task")
    parser.add_argument("--task-file", type=Path)
    parser.add_argument("--type", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--events-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--duckdb", type=Path)
    parser.add_argument("--postgres-env")
    parser.add_argument("--data-sources", type=Path)
    parser.add_argument("--chat", action="store_true")
    parser.add_argument("--conversation-dir", type=Path)
    parser.add_argument("--turn", type=int)
    parser.add_argument("--resume")
    args, _ = parser.parse_known_args()

    task = args.task if args.task is not None else args.task_file.read_text()
    if args.data_sources is not None:
        database = next(iter(tomllib.loads(args.data_sources.read_text())["sources"]))
    else:
        database = args.postgres_env or (args.duckdb.name if args.duckdb else None)
    session_id = args.resume or f"fake-session-{secrets.token_hex(4)}"
    conversation = args.conversation_dir.name if args.conversation_dir is not None else None

    args.events_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    with (args.events_dir / f"run-{stamp}.jsonl").open("x", encoding="utf-8") as file:
        lock = threading.Lock()
        seq = 0

        def emit(type_: str, **fields: Any) -> None:
            nonlocal seq
            with lock:
                event = {"seq": seq, "type": type_, "ts": datetime.now(UTC).isoformat(), **fields}
                file.write(json.dumps(event) + "\n")
                file.flush()
                seq += 1

        emit("version", config=args.config, model="fake-model", git_commit="0" * 40)
        conversation_fields = (
            {"conversation": conversation, "turn": args.turn} if conversation is not None else {}
        )
        emit("task", task=task, task_type=args.type, data_source=None, **conversation_fields)
        emit("status", state="started", model="fake-model", session_id=session_id)
        with (Path.cwd() / "fake-turns.txt").open("a") as turns:
            turns.write(f"turn {args.turn}\n")

        if args.chat:

            def read_stdin() -> None:
                for line in sys.stdin:
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(message, dict) and {"id", "text"} <= message.keys():
                        emit("steer_delivered", id=message["id"], text=message["text"])

            threading.Thread(target=read_stdin, daemon=True).start()

        sql = "SELECT count(*) AS n FROM sales.orders"
        query = "mcp__data__query_database"
        emit("tool_start", tool=query, tool_use_id="q1", parent=None, input={"sql": sql})
        emit("sql", tool_use_id="q1", sql=sql, database=database)
        emit(
            "tool_end", tool=query, tool_use_id="q1", parent=None, is_error=False, content="n\n-\n3"
        )

        gate = args.gate_dir / args.events_dir.name
        deadline = time.monotonic() + GATE_TIMEOUT_SECONDS
        while not gate.exists():
            if time.monotonic() > deadline:
                raise SystemExit("fake_chat_run: gate never opened")
            time.sleep(0.05)

        emit("text", text="There are 3 orders.")
        emit("answer", text="There are 3 orders.", sql_tool_use_id="q1")
        emit(
            "result",
            subtype="success",
            is_error=False,
            num_turns=2,
            cost_usd=0.01,
            session_id=session_id,
        )


if __name__ == "__main__":
    main()
