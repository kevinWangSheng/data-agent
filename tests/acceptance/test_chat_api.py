"""The chat backend's HTTP interface (.scratch/chat-ui/spec.md "Testing Decisions", no-model group
2), driven through FastAPI's test client. `data-agent run` is replaced by
tests/fixtures/chat/fake_chat_run.py, started with a stdin pipe: it writes events on a script,
turns stdin lines into steer-delivered events, and waits on a gate file per run before it finishes.

Written by ticket 88. Each test is xfail(strict) until ticket 93 (the chat backend) makes it pass
and removes the marker, changing nothing else.

The chat contract fixed here (ticket 88 Context items 1, 2, 4, 5, 6; the view model's types,
item 3, are in web/src/chat-view/model.ts). `create_app(runs_dir, data_sources, start_run)` is
unchanged; the routes below are added to it.

1. Conversation directory and index (spec 14, 16, 17). A conversation is the directory
   `<runs_dir>/conversations/<id>/` (`id` shaped like a run id), holding:
   - `conversation.json`, the index, replaced whole on every change:
     `{"id": str, "created_at": ISO-8601 str, "turns": [{"turn": int (from 1), "run_id": str,
     "message": str, "steers": [{"id": str, "text": str}, ...]}, ...]}`. `message` is the user
     message that started the turn (a clicked clarification option is such a message); `steers`
     are the messages sent while the turn was running, in the order sent.
   - `workspace/`: the conversation's working directory, empty at first, shared by every turn.
   - `claude-config/`: the conversation's `CLAUDE_CONFIG_DIR`, where the SDK keeps the session
     record (`projects/<cwd name>/<session_id>.jsonl`), never the user's `~/.claude`.
   Each turn is an ordinary run: one user message, one `data-agent run`, one directory
   `<runs_dir>/<run_id>/` with one event file, which `/api/runs`, `/api/runs/{id}/events`, SSE and
   the three-pane run page read as before.
2. Event fields (spec 8, 9, 17, 20); files written before them have none of these:
   - `task` of a conversation turn: `conversation` (the conversation id) and `turn` (int).
   - `status` (`state: "started"`) and `result`: `session_id`, the SDK session's id.
   - `clarification`: `tool_use_id`, `question` (str), `options` (2-5 str); written when the agent
     calls the clarification tool (`mcp__data__clarification`, input `{question, options}`, after
     nao's `clarification`), which ends the turn: the agent makes no further call, the turn writes
     no `answer` event, and its `result` is a success.
   - `steer_delivered`: `id` and `text` of a stdin message, written once it has been sent into
     the running session.
   - `sql`: `database`, the name of the data source the query ran on (also with one source).
   - The query and context tools take a `database` argument (a data source's name) only when the
     run has more than one source; with one, their definitions are what they are today.
4. Routes:
   - `GET /api/conversations` -> a list of conversation rows, newest first. A row:
     `{"id", "single_run": bool, "created_at", "running": bool, "turns": [{"turn", "run_id",
     "message", "steers", "status"}]}`, `status` as `/api/runs` gives it (`running`, `success`,
     `failure`, `cancelled`). A run not in any conversation (the command line's, the run form's,
     dbt included) is a row of its own: `id` = its run id, `single_run: true`, one turn whose
     `message` is its task (null when its file records none) and `steers` `[]`.
   - `GET /api/conversations/{id}` -> that row; 404 for an unknown id.
   - `POST /api/conversations` with `{}` (no task type, no data source) -> 201 `{"id"}`.
   - `POST /api/conversations/{id}/messages` with `{"text"}`: no turn running -> 201
     `{"kind": "turn", "turn": int, "run_id": str}`, a new turn started; this conversation's turn
     running -> 202 `{"kind": "steer", "run_id": <the running turn>, "id": <message id>}`, the
     message written to that run's stdin and added to its turn's `steers`, no new turn. 409 while a
     run of another conversation or of the run form is going (one run at a time, across both);
     4xx for a single run (no follow-up) and for an empty text. A conversation is `running` from
     the moment its turn starts until that turn's process has exited (a turn writes its
     `result` shortly before it exits); a message is a new turn exactly when it is not running.
   - `POST /api/conversations/{id}/cancel` -> 200 with the row, the running turn stopped as
     `POST /api/runs/{id}/cancel` stops a run (status `cancelled`); 4xx when nothing is running.
     The next message starts a new turn.
5. `data-agent run` arguments of a turn (spec 3, 14, 16, 19, 20), started through `start_run`
   with the conversation's `workspace/` as its working directory:
   `--task=<message> --type query --config <file> --events-dir <runs_dir>/<run_id>
   --workspace <conv>/workspace --chat --conversation-dir <conv> --turn <n>
   [--resume <session_id>]`.
   - `--config FILE` (ticket 141, product-config ticket 141 contract item 4; was `--config full
     --data-sources FILE`): the web server's effective config, written by the server for the run,
     with every configured source as its `[sources.<name>]` tables (the TOML `data-agent web
     --data-sources` took, data_agent.web.sources): name, kind, and the DuckDB path or the
     Postgres variable's name, never a connection string. The sources are exclusive with
     `--duckdb` / `--postgres-env`, which stay for single runs and evaluations.
   - `--chat`: conversation mode, the switch for the clarification tool and stdin steering; off by
     default, and evaluations and single runs never set it (spec 19).
   - `--conversation-dir DIR`: sets `CLAUDE_CONFIG_DIR` to `DIR/claude-config`; the conversation's
     id is DIR's name. `--turn N`: the turn's number, for the `task` event. Both need `--chat`.
   - `--resume SESSION_ID`: from turn 2 on, the session id of the latest earlier turn whose event
     file has one (its `status` event's `session_id`; a cancelled turn counts).
6. stdin of a `--chat` run: one JSON object per line, `{"id": str, "text": str}`; `id` is the
   backend's message id (a uuid), which the `steer_delivered` event repeats, as SignalPilot
   reconciles its stream-json user messages by uuid (claude_agent.py:355-408). The child turns each
   into a `query()` on its live `ClaudeSDKClient`.
"""

import json
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from product_config_support import flag_value, key_of
from seam_support import FIXTURES

from data_agent.web.app import DataSource, create_app

TICKET_143 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 143: the config loader is not built yet"
)
# The run form without a `config` field (contract item 5) is ticket 144's.
TICKET_144 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 144: the config is not wired to the run yet"
)

CHAT_FIXTURES = FIXTURES / "chat"
FAKE_CHAT_RUN = CHAT_FIXTURES / "fake_chat_run.py"
QUERY_SHOP = FIXTURES / "web" / "real" / "query-shop" / "events.jsonl"
NEW_FIELDS = FIXTURES / "web" / "new-fields" / "events.jsonl"
# A run from the run form.
# No `config`: the form picks no ablation (product-config ticket 141 contract item 5).
FORM = {"task": "Count orders", "task_type": "query", "data_source": "orders"}

PG_ENV = "DA_TEST_WAREHOUSE_URL"
# A connection string the server holds in its environment; no byte of it may leave the server.
PG_PASSWORD = "hunter2-" + "k4Rz"
PG_HOST = "warehouse-db.internal.example"
PG_URL = f"postgresql://agent:{PG_PASSWORD}@{PG_HOST}:5432/warehouse"

TIMEOUT = 60


@dataclass
class Started:
    args: list[str]
    cwd: Path
    process: "subprocess.Popen[bytes]"


@dataclass
class Chat:
    client: TestClient
    runs_dir: Path
    gates: Path
    sources: list[DataSource]
    started: list[Started] = field(default_factory=list)

    def release(self, run_id: str) -> None:
        """Lets the fake run `run_id` write its answer and finish."""
        (self.gates / run_id).touch()


@pytest.fixture
def chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Chat]:
    monkeypatch.setenv(PG_ENV, PG_URL)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    gates = tmp_path / "gates"
    gates.mkdir()
    databases = tmp_path / "sources"
    databases.mkdir()
    for name in ("orders", "customers"):
        (databases / f"{name}.duckdb").write_bytes(b"")
    sources = [
        DataSource(name="orders", duckdb=databases / "orders.duckdb"),
        DataSource(name="customers", duckdb=databases / "customers.duckdb"),
        DataSource(name="warehouse", postgres_env=PG_ENV),
    ]
    started: list[Started] = []

    def start_run(args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
        process = subprocess.Popen(
            [sys.executable, str(FAKE_CHAT_RUN), "--gate-dir", str(gates), *args],
            cwd=cwd,
            stdin=subprocess.PIPE,
        )
        started.append(Started(list(args), cwd, process))
        return process

    try:
        with TestClient(create_app(runs_dir, sources, start_run)) as client:
            # The app is up and its run API answers: whatever fails below is the chat API's.
            if client.get("/api/runs").status_code != 200:
                raise RuntimeError("the app does not answer GET /api/runs")
            yield Chat(client, runs_dir, gates, sources, started)
    finally:
        # Only the fake runs this test started, by their own process handles.
        for run in started:
            if run.process.poll() is None:
                run.process.terminate()
            run.process.wait(timeout=TIMEOUT)


# --- Helpers -------------------------------------------------------------------------------


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_run(runs_dir: Path, run_id: str, events: list[dict[str, Any]]) -> None:
    """A run as `data-agent run --events-dir <runs_dir>/<run_id>` leaves it."""
    path = runs_dir / run_id / "run-20261117T080000Z-cli.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


def wait_until(check: Callable[[], bool], timeout: float = TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting")
        time.sleep(0.05)


def flag(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def new_conversation(chat: Chat) -> str:
    response = chat.client.post("/api/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def send(chat: Chat, conversation: str, text: str) -> Any:
    return chat.client.post(f"/api/conversations/{conversation}/messages", json={"text": text})


def start_turn(chat: Chat, conversation: str, text: str) -> str:
    """Sends a message to an idle conversation; returns the new turn's run id."""
    response = send(chat, conversation, text)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "turn"
    return body["run_id"]


def row(chat: Chat, conversation: str) -> dict[str, Any]:
    response = chat.client.get(f"/api/conversations/{conversation}")
    assert response.status_code == 200, response.text
    return response.json()


def rows(chat: Chat) -> dict[str, dict[str, Any]]:
    response = chat.client.get("/api/conversations")
    assert response.status_code == 200, response.text
    return {item["id"]: item for item in response.json()}


def events_of(chat: Chat, run_id: str) -> list[dict[str, Any]]:
    files = list((chat.runs_dir / run_id).glob("*.jsonl"))
    return read_events(files[0]) if files else []


def has_event(chat: Chat, run_id: str, type_: str) -> bool:
    return any(event["type"] == type_ for event in events_of(chat, run_id))


def session_of(chat: Chat, run_id: str) -> str:
    [status] = [e for e in events_of(chat, run_id) if e["type"] == "status"]
    return status["session_id"]


def turn_status(chat: Chat, conversation: str, turn: int) -> str:
    [entry] = [t for t in row(chat, conversation)["turns"] if t["turn"] == turn]
    return entry["status"]


def finish(chat: Chat, conversation: str, run_id: str, turn: int) -> None:
    """Lets the turn's fake run finish and waits until the backend has it ended: its process has
    exited, so the conversation is no longer running (a message now starts a new turn)."""
    wait_until(lambda: has_event(chat, run_id, "status"))
    chat.release(run_id)
    wait_until(
        lambda: (
            turn_status(chat, conversation, turn) == "success"
            and not row(chat, conversation)["running"]
        )
    )


def conversation_dir(chat: Chat, conversation: str) -> Path:
    return chat.runs_dir / "conversations" / conversation


def assert_no_connection_string(text: str) -> None:
    for secret in (PG_URL, PG_PASSWORD, PG_HOST):
        assert secret not in text


# --- Creating a conversation ---------------------------------------------------------------


def test_a_new_conversation_takes_no_task_type_or_data_source(chat: Chat) -> None:
    conversation = new_conversation(chat)
    listed = rows(chat)[conversation]
    assert (listed["single_run"], listed["running"], listed["turns"]) == (False, False, [])

    index = json.loads((conversation_dir(chat, conversation) / "conversation.json").read_text())
    assert (index["id"], index["turns"]) == (conversation, [])
    assert isinstance(index["created_at"], str)
    assert (conversation_dir(chat, conversation) / "workspace").is_dir()
    assert chat.started == []

    run_id = start_turn(chat, conversation, "How many orders are there?")
    [run] = chat.started
    assert flag(run.args, "--type") == "query"
    assert "--data-source-name" not in run.args
    assert Path(flag(run.args, "--events-dir")) == chat.runs_dir / run_id


# --- What a turn's run gets ----------------------------------------------------------------


@TICKET_143
def test_a_turn_gets_every_data_source_by_name_and_kind(chat: Chat) -> None:
    conversation = new_conversation(chat)
    start_turn(chat, conversation, "How many orders are there?")
    [run] = chat.started
    assert "--chat" in run.args
    assert "--duckdb" not in run.args and "--postgres-env" not in run.args
    # Ticket 141: the sources are the `[sources.<name>]` tables of the run's config file.
    sources_file = Path(flag_value(run.args, "--config"))
    assert sources_file.is_file(), f"the turn's --config is not a file: {sources_file}"
    given = key_of(tomllib.loads(sources_file.read_text()), "sources")
    assert list(given) == ["orders", "customers", "warehouse"]
    for source in chat.sources:
        entry = given[source.name]
        assert entry.get("env") == source.postgres_env
        assert ("path" not in entry) == (source.duckdb is None)
        if source.duckdb is not None:
            assert (sources_file.parent / entry["path"]).resolve() == source.duckdb.resolve()
    assert_no_connection_string(sources_file.read_text())
    assert_no_connection_string(json.dumps(run.args))


def test_from_the_second_turn_the_run_resumes_the_session_in_the_same_conversation(
    chat: Chat,
) -> None:
    conversation = new_conversation(chat)
    directory = conversation_dir(chat, conversation)
    first = start_turn(chat, conversation, "How many orders have the status refunded?")
    finish(chat, conversation, first, 1)
    second = start_turn(chat, conversation, "How many of those were placed in 2024?")
    wait_until(lambda: has_event(chat, second, "status"))

    one, two = chat.started
    assert "--resume" not in one.args
    assert flag(two.args, "--resume") == session_of(chat, first)
    for run, turn in ((one, 1), (two, 2)):
        assert Path(flag(run.args, "--conversation-dir")) == directory
        assert Path(flag(run.args, "--workspace")) == directory / "workspace"
        assert run.cwd == directory / "workspace"
        assert flag(run.args, "--turn") == str(turn)
    # Both turns' runs worked in the one working directory.
    assert (directory / "workspace" / "fake-turns.txt").read_text() == "turn 1\nturn 2\n"

    index = json.loads((directory / "conversation.json").read_text())
    assert [(t["turn"], t["run_id"], t["message"]) for t in index["turns"]] == [
        (1, first, "How many orders have the status refunded?"),
        (2, second, "How many of those were placed in 2024?"),
    ]
    listed = row(chat, conversation)
    assert [t["run_id"] for t in listed["turns"]] == [first, second]
    assert listed["running"] is True


# --- Steering a running turn ---------------------------------------------------------------


def test_a_message_while_a_turn_runs_goes_into_that_run(chat: Chat) -> None:
    conversation = new_conversation(chat)
    run_id = start_turn(chat, conversation, "List every customer by number of orders.")
    wait_until(lambda: has_event(chat, run_id, "status"))

    response = send(chat, conversation, "Only the top 3, please.")
    assert response.status_code == 202, response.text
    body = response.json()
    assert (body["kind"], body["run_id"]) == ("steer", run_id)

    def delivered() -> list[dict[str, Any]]:
        return [e for e in events_of(chat, run_id) if e["type"] == "steer_delivered"]

    wait_until(lambda: bool(delivered()))
    assert [(e["id"], e["text"]) for e in delivered()] == [(body["id"], "Only the top 3, please.")]
    assert len(chat.started) == 1
    [turn] = row(chat, conversation)["turns"]
    assert (turn["run_id"], turn["status"]) == (run_id, "running")
    assert turn["steers"] == [{"id": body["id"], "text": "Only the top 3, please."}]

    chat.release(run_id)
    wait_until(lambda: turn_status(chat, conversation, 1) == "success")
    assert len(row(chat, conversation)["turns"]) == 1


# --- Cancelling ----------------------------------------------------------------------------


def test_after_a_cancel_the_next_message_starts_a_new_turn(chat: Chat) -> None:
    conversation = new_conversation(chat)
    first = start_turn(chat, conversation, "List every customer by number of orders.")
    wait_until(lambda: has_event(chat, first, "status"))

    response = chat.client.post(f"/api/conversations/{conversation}/cancel")
    assert response.status_code == 200, response.text
    assert chat.started[0].process.wait(timeout=TIMEOUT) != 0
    assert turn_status(chat, conversation, 1) == "cancelled"
    assert row(chat, conversation)["running"] is False
    # Nothing is left to cancel.
    assert 400 <= chat.client.post(f"/api/conversations/{conversation}/cancel").status_code < 500

    second = start_turn(chat, conversation, "How many customers are there?")
    wait_until(lambda: has_event(chat, second, "status"))
    assert flag(chat.started[1].args, "--resume") == session_of(chat, first)
    assert flag(chat.started[1].args, "--turn") == "2"
    finish(chat, conversation, second, 2)


# --- One run at a time ---------------------------------------------------------------------


@TICKET_144
def test_one_run_at_a_time_across_conversations_and_the_run_form(chat: Chat) -> None:
    a, b = new_conversation(chat), new_conversation(chat)
    run_a = start_turn(chat, a, "How many orders are there?")
    wait_until(lambda: has_event(chat, run_a, "status"))

    assert send(chat, b, "How many customers are there?").status_code == 409
    assert chat.client.post("/api/runs", json=FORM).status_code == 409
    assert len(chat.started) == 1
    assert row(chat, b)["turns"] == []

    finish(chat, a, run_a, 1)
    response = chat.client.post("/api/runs", json=FORM)
    assert response.status_code == 201, response.text
    form_run = response.json()["id"]
    wait_until(lambda: has_event(chat, form_run, "status"))
    assert send(chat, b, "How many customers are there?").status_code == 409

    chat.release(form_run)
    chat.started[1].process.wait(timeout=TIMEOUT)
    wait_until(lambda: rows(chat)[form_run]["turns"][0]["status"] == "success")
    start_turn(chat, b, "How many customers are there?")
    assert len(chat.started) == 3


# --- Single runs ---------------------------------------------------------------------------


@TICKET_144
def test_single_runs_list_as_one_turn_conversations_without_follow_up(chat: Chat) -> None:
    write_run(chat.runs_dir, "20261115T143153Z-old", read_events(QUERY_SHOP))
    write_run(chat.runs_dir, "20261115T090000Z-dbt", read_events(NEW_FIELDS))
    response = chat.client.post("/api/runs", json=FORM)
    assert response.status_code == 201, response.text
    form_run = response.json()["id"]
    wait_until(lambda: has_event(chat, form_run, "status"))
    chat.release(form_run)
    chat.started[0].process.wait(timeout=TIMEOUT)

    conversation = new_conversation(chat)
    turn = start_turn(chat, conversation, "How many orders are there?")
    finish(chat, conversation, turn, 1)

    listed = rows(chat)
    # The conversation's turn is a run too, but it lists inside its conversation only.
    assert set(listed) == {"20261115T143153Z-old", "20261115T090000Z-dbt", form_run, conversation}
    old = listed["20261115T143153Z-old"]
    assert (old["single_run"], old["running"]) == (True, False)
    assert old["turns"] == [
        {
            "turn": 1,
            "run_id": "20261115T143153Z-old",
            "message": None,
            "steers": [],
            "status": "success",
        }
    ]
    dbt = listed["20261115T090000Z-dbt"]
    assert (dbt["single_run"], [t["message"] for t in dbt["turns"]]) == (
        True,
        ["Build the orders mart from raw.orders."],
    )
    assert listed[form_run]["single_run"] is True
    assert [(t["run_id"], t["message"]) for t in listed[form_run]["turns"]] == [
        (form_run, "Count orders")
    ]
    assert row(chat, form_run) == listed[form_run]

    for single in ("20261115T143153Z-old", "20261115T090000Z-dbt", form_run):
        assert 400 <= send(chat, single, "And in 2024?").status_code < 500
    assert len(chat.started) == 2
    assert chat.client.get("/api/conversations/no-such-conversation").status_code == 404


# --- No connection strings -----------------------------------------------------------------


def test_chat_output_holds_no_connection_string(chat: Chat) -> None:
    conversation = new_conversation(chat)
    response = send(chat, conversation, "How many rows does the warehouse have?")
    assert response.status_code == 201, response.text
    run_id = response.json()["run_id"]
    assert_no_connection_string(response.text)
    wait_until(lambda: has_event(chat, run_id, "status"))
    steer = send(chat, conversation, "Use the warehouse source.")
    assert steer.status_code == 202, steer.text
    assert_no_connection_string(steer.text)
    finish(chat, conversation, run_id, 1)

    for url in ("/api/conversations", f"/api/conversations/{conversation}"):
        assert_no_connection_string(chat.client.get(url).text)
    directory = conversation_dir(chat, conversation)
    for path in (p for p in directory.rglob("*") if p.is_file()):
        assert_no_connection_string(path.read_text(errors="replace"))
    assert_no_connection_string(json.dumps([run.args for run in chat.started]))
