"""The chat, end to end with a real model (.scratch/chat-ui/spec.md "Testing Decisions", main
acceptance): a real `data-agent web` on the loopback address, a real agent, a real model, driven
over HTTP only, with the contract test_chat_api.py's docstring fixes (the Playwright main flow in
web/e2e/ uses the same one).

These tests spend money, so they are `integration` (`make test-integration` loads the DATA_AGENT_*
variables). Each scenario runs 3 times and passes only when all 3 do (spec "判定"); a failure is
read from its trajectory before it is put down to the model.

The backend has two data sources and is never told which one to use: `orders` (sales.orders,
tests/fixtures/chat/orders.sql) and `customers` (crm.customers, customers.sql), built fresh by
tests/fixtures/chat/build_sources.py. The questions and the expected answers are written for these
fixtures (ADR-0005); each expected value notes the SQL that gives it.

Written by ticket 88. Scenarios a-d and g are xfail(strict) until ticket 93 (the chat backend,
merged after 89-92) makes them pass and removes the marker, changing nothing else. Scenario f
runs today's `data-agent run` and has no marker: it guards the evaluation path.
"""

import contextlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from seam_support import DATA_AGENT, FIXTURES

pytestmark = pytest.mark.integration

# Each scenario three times; all three must pass.
THREE_TIMES = pytest.mark.parametrize("attempt", [1, 2, 3])

sys.path.insert(0, str(FIXTURES / "chat"))
from build_sources import build as build_sources  # noqa: E402  # pyright: ignore[reportMissingImports]

# How long one turn of a real agent may take, and a server to come up.
TURN_TIMEOUT = 900
START_TIMEOUT = 60
# Talk to the local server directly, whatever proxy the environment names.
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# --- Questions and their answers -----------------------------------------------------------

# a: a count, then a question that only makes sense after it.
REFUNDED_QUESTION = "How many orders have the status 'refunded'?"
REFUNDED = 11  # SELECT count(*) FROM sales.orders WHERE status = 'refunded'
FOLLOW_UP_QUESTION = "How many of those were placed in 2024?"
# SELECT count(*) FROM sales.orders WHERE status = 'refunded' AND year(order_date) = 2024
REFUNDED_2024 = 6

# b and f: revenue changes with whether refunded orders count.
REVENUE_QUESTION = "What was the total revenue from orders placed in 2024?"
# SELECT sum(amount) FROM sales.orders WHERE year(order_date) = 2024
GROSS_2024 = Decimal("2121.75")
# SELECT sum(amount) FROM sales.orders WHERE year(order_date) = 2024 AND status <> 'refunded'
NET_2024 = Decimal("1474.50")
# f: the answer says how it treated refunded orders, by naming refunds, order statuses ("all
# order statuses included") or orders by the other status value ("only completed orders", "orders
# marked completed"), not "completed" alone ("the analysis is completed"). Markdown marks (* _ `)
# are taken out of the answer first, so "only **completed** orders" counts too.
STATED_REFUND_TREATMENT = re.compile(
    r"refund|\bstatus(?:es)?\b|\bcompleted\s+orders?\b|\borders?\b[^.\n]{0,30}\bcompleted\b", re.I
)
MARKDOWN_MARKS = re.compile(r"[*_`]")
# b: nothing in it changes with a reading of the data.
PLAIN_QUESTION = "What is the order_id of the order with the largest amount?"
LARGEST_ORDER = 1034  # SELECT order_id FROM sales.orders ORDER BY amount DESC LIMIT 1

# c: a ranked list, cut to the top 3 by a message sent while the agent works.
RANKING_QUESTION = (
    "List every customer_id in the orders data with its number of orders, from most to fewest."
)
STEER = "Only show the top 3 customers, please."
# SELECT customer_id, count(*) AS n FROM sales.orders GROUP BY 1 ORDER BY n DESC
# -> C01 9, C02 7, C03 6, C04 5, C05 4, C06 3, C07 2, C08 1; names from crm.customers.
TOP_3 = {"C01": "Avery Lane", "C02": "Blake Moss", "C03": "Casey Reed"}
REST = {
    "C04": "Devon Hale",
    "C05": "Emery Cole",
    "C06": "Finley Shaw",
    "C07": "Gray Holt",
    "C08": "Harper Voss",
}

# d: a turn that is cancelled, then one that must still be answered.
LONG_QUESTION = (
    "For every customer, list the number of orders, the total amount, and the date of the first "
    "and the last order."
)
CUSTOMERS_QUESTION = "How many customers are in the crm.customers table?"
CUSTOMERS = 14  # SELECT count(*) FROM crm.customers

# g: orders and regions live in different databases.
REGION_ORDERS_QUESTION = "How many orders were placed by customers of each region?"
# SELECT region, count(*) FROM sales.orders JOIN crm.customers USING (customer_id) GROUP BY 1
# (each database queried on its own; the agent combines the results)
REGION_ORDERS = {"North": 13, "South": 10, "East": 8, "West": 6}
REGION_CUSTOMERS_QUESTION = "How many customers does each region have?"
# SELECT region, count(*) FROM crm.customers GROUP BY 1
REGION_CUSTOMERS = {"North": 3, "South": 2, "East": 4, "West": 5}


# --- The server ----------------------------------------------------------------------------


@dataclass
class Server:
    url: str
    runs_dir: Path
    sources_dir: Path
    process: "subprocess.Popen[bytes]"

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.url + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with NO_PROXY.open(request, timeout=30) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            text = exc.read().decode(errors="replace")
            with contextlib.suppress(ValueError):
                return exc.code, json.loads(text)
            return exc.code, text


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(
    check: Callable[[], bool], timeout: float, what: str, error: type[Exception] = AssertionError
) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise error(f"timed out waiting for {what}")
        time.sleep(0.5)


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    """`data-agent web` with both sources, on a port and a runs directory of its own."""
    root = tmp_path_factory.mktemp("chat-e2e")
    sources_file = build_sources(root / "sources")
    runs_dir = root / "runs"
    runs_dir.mkdir()
    port = free_port()
    process = subprocess.Popen(
        [
            *(str(DATA_AGENT), "web", "--runs-dir", str(runs_dir), "--port", str(port)),
            *("--data-sources", str(sources_file)),
        ],
        env=dict(os.environ),
    )
    up = Server(f"http://127.0.0.1:{port}", runs_dir, sources_file.parent, process)
    try:

        def answers() -> bool:
            if process.poll() is not None:
                raise RuntimeError("data-agent web exited")
            with contextlib.suppress(OSError):
                return up.call("GET", "/api/runs")[0] == 200
            return False

        # Alive and answering: whatever fails later is the chat's, not the server's start.
        wait_until(answers, START_TIMEOUT, "data-agent web to answer", RuntimeError)
        yield up
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=START_TIMEOUT)


# --- Talking to it -------------------------------------------------------------------------


def new_conversation(server: Server) -> str:
    status, body = server.call("POST", "/api/conversations", {})
    assert status == 201, body
    return body["id"]


def send(server: Server, conversation: str, text: str) -> tuple[int, Any]:
    return server.call("POST", f"/api/conversations/{conversation}/messages", {"text": text})


def ask(server: Server, conversation: str, text: str) -> str:
    """Starts a turn; returns its run id."""
    status, body = send(server, conversation, text)
    assert status == 201 and body["kind"] == "turn", body
    return body["run_id"]


def row(server: Server, conversation: str) -> dict[str, Any]:
    status, body = server.call("GET", f"/api/conversations/{conversation}")
    assert status == 200, body
    return body


def turn_status(server: Server, conversation: str, run_id: str) -> str:
    [turn] = [t for t in row(server, conversation)["turns"] if t["run_id"] == run_id]
    return turn["status"]


def wait_turn(server: Server, conversation: str, run_id: str) -> str:
    """Waits for the turn to end; returns how it ended."""
    wait_until(
        # Ended once its process has exited: the conversation is no longer running, so the next
        # message starts a new turn.
        lambda: (
            turn_status(server, conversation, run_id) != "running"
            and not row(server, conversation)["running"]
        ),
        TURN_TIMEOUT,
        f"turn {run_id} to end",
    )
    return turn_status(server, conversation, run_id)


def answered(server: Server, conversation: str, text: str) -> list[dict[str, Any]]:
    """Asks and waits for a successful turn; returns its events."""
    run_id = ask(server, conversation, text)
    assert wait_turn(server, conversation, run_id) == "success", events(server, run_id)[-1]
    return events(server, run_id)


def events(server: Server, run_id: str) -> list[dict[str, Any]]:
    status, body = server.call("GET", f"/api/runs/{run_id}/events")
    assert status == 200, body
    return body["events"]


def of_type(run: list[dict[str, Any]], type_: str) -> list[dict[str, Any]]:
    return [event for event in run if event["type"] == type_]


def answer_of(run: list[dict[str, Any]]) -> str:
    answers = of_type(run, "answer")
    assert answers, f"no answer event; the run ended with {run[-1]}"
    return str(answers[-1]["text"])


def session_of(run: list[dict[str, Any]]) -> str:
    [status] = [e for e in of_type(run, "status") if e.get("state") == "started"]
    return status["session_id"]


def wait_for_tool_call(server: Server, run_id: str) -> None:
    """Until the main agent has started a tool call: the turn is under way."""
    wait_until(
        lambda: any(e.get("parent") is None for e in of_type(events(server, run_id), "tool_start")),
        TURN_TIMEOUT,
        f"a tool call in {run_id}",
    )


# --- Reading answers -----------------------------------------------------------------------

NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[Decimal]:
    return [Decimal(n.replace(",", "")) for n in NUMBER.findall(text)]


def mentions(text: str, value: Decimal | int) -> bool:
    """Whether the text gives this number (to the cent)."""
    return any(abs(n - Decimal(value)) <= Decimal("0.01") for n in numbers_in(text))


def mentions_per_region(text: str, expected: dict[str, int]) -> bool:
    """Each region's count is the first number after the region's name, before the line or the
    next region's name ends it ("North: 13", "| North | 13 |", "North 13, South 10"), at some
    mention of the region."""
    regions = "|".join(expected)
    mention = re.compile(rf"\b({regions})\b([^\n]*?)(?=\b(?:{regions})\b|\n|$)", re.I)
    found: dict[str, set[Decimal]] = {}
    for match in mention.finditer(text):
        after = numbers_in(match.group(2))
        if after:
            found.setdefault(match.group(1).title(), set()).add(after[0])
    return all(Decimal(n) in found.get(region, set()) for region, n in expected.items())


NET_WORDS = re.compile(r"\bnet\b|exclud|without|minus|subtract|deduct|less refund", re.I)
GROSS_WORDS = re.compile(r"\bgross\b|includ|all orders|regardless", re.I)


def choose(options: list[str]) -> tuple[str, Decimal]:
    """An option that clearly reads as net or gross revenue, net first, and the revenue it means."""
    for words, value in ((NET_WORDS, NET_2024), (GROSS_WORDS, GROSS_2024)):
        other = GROSS_WORDS if words is NET_WORDS else NET_WORDS
        for option in options:
            if words.search(option) and not other.search(option):
                return option, value
    raise AssertionError(f"no option reads as net or gross revenue: {options}")


# --- a: follow-up --------------------------------------------------------------------------


@THREE_TIMES
def test_a_follow_up_resumes_the_session(server: Server, attempt: int) -> None:
    conversation = new_conversation(server)
    first = answered(server, conversation, REFUNDED_QUESTION)
    assert mentions(answer_of(first), REFUNDED), answer_of(first)
    second = answered(server, conversation, FOLLOW_UP_QUESTION)
    assert mentions(answer_of(second), REFUNDED_2024), answer_of(second)

    session = session_of(first)
    assert session_of(second) == session
    for run, turn in ((first, 1), (second, 2)):
        [task] = of_type(run, "task")
        assert (task["conversation"], task["turn"]) == (conversation, turn)
    # The session record is the conversation's own, not the user's.
    config = server.runs_dir / "conversations" / conversation / "claude-config"
    assert list(config.glob(f"projects/*/{session}.jsonl")), f"no session record under {config}"
    assert not list((Path.home() / ".claude").glob(f"projects/*/{session}.jsonl"))


# --- b: clarification ----------------------------------------------------------------------


@THREE_TIMES
def test_b_an_ambiguous_question_is_clarified_and_answered_as_chosen(
    server: Server, attempt: int
) -> None:
    conversation = new_conversation(server)
    first = answered(server, conversation, REVENUE_QUESTION)
    clarifications = of_type(first, "clarification")
    assert len(clarifications) == 1, [e["type"] for e in first]
    [clarification] = clarifications
    assert isinstance(clarification["question"], str) and clarification["question"].strip()
    options = clarification["options"]
    assert 2 <= len(options) <= 5, options
    # The question ended the turn: the agent did nothing after it.
    after = [e for e in first if e["seq"] > clarification["seq"]]
    assert not of_type(after, "tool_start") and not of_type(first, "answer"), after

    option, expected = choose(options)
    second = answered(server, conversation, option)
    assert not of_type(second, "clarification")
    assert mentions(answer_of(second), expected), (option, answer_of(second))
    [turn_1, turn_2] = row(server, conversation)["turns"]
    assert turn_2["message"] == option and turn_1["message"] == REVENUE_QUESTION


@THREE_TIMES
def test_b_a_plain_question_is_answered_without_clarification(server: Server, attempt: int) -> None:
    conversation = new_conversation(server)
    run = answered(server, conversation, PLAIN_QUESTION)
    assert not of_type(run, "clarification")
    assert mentions(answer_of(run), LARGEST_ORDER), answer_of(run)


# --- c: steering ---------------------------------------------------------------------------


@THREE_TIMES
def test_c_a_message_sent_while_the_agent_works_is_followed(server: Server, attempt: int) -> None:
    conversation = new_conversation(server)
    run_id = ask(server, conversation, RANKING_QUESTION)
    wait_for_tool_call(server, run_id)
    status, steer = send(server, conversation, STEER)
    assert status == 202 and steer["kind"] == "steer" and steer["run_id"] == run_id, steer
    assert wait_turn(server, conversation, run_id) == "success"

    run = events(server, run_id)
    delivered = of_type(run, "steer_delivered")
    assert [(e["id"], e["text"]) for e in delivered] == [(steer["id"], STEER)]
    assert len(row(server, conversation)["turns"]) == 1
    answer = answer_of(run)
    # Only the listed result, which comes before the assumptions, counts: the assumptions may name
    # the first customer left out, to show there is no tie at the cut (human decision 2026-11-17,
    # ticket 93).
    listed = re.split(r"(?im)^[\s*_#>]*assumptions?\b", answer, maxsplit=1)[0]
    for customer, name in TOP_3.items():
        assert customer in listed or name in listed, (customer, answer)
    for customer, name in REST.items():
        assert customer not in listed and name not in listed, (customer, answer)


# --- d: cancel, then go on -----------------------------------------------------------------


@THREE_TIMES
def test_d_after_a_cancelled_turn_the_next_one_is_answered(server: Server, attempt: int) -> None:
    conversation = new_conversation(server)
    cancelled = ask(server, conversation, LONG_QUESTION)
    wait_for_tool_call(server, cancelled)
    status, body = server.call("POST", f"/api/conversations/{conversation}/cancel")
    assert status == 200, body
    assert turn_status(server, conversation, cancelled) == "cancelled"

    run = answered(server, conversation, CUSTOMERS_QUESTION)
    assert mentions(answer_of(run), CUSTOMERS), answer_of(run)
    assert [t["status"] for t in row(server, conversation)["turns"]] == ["cancelled", "success"]


# --- f: the evaluation path ----------------------------------------------------------------


@THREE_TIMES
def test_f_a_single_run_answers_an_ambiguous_question_with_its_assumption(
    tmp_path: Path, attempt: int
) -> None:
    """Today's `data-agent run`, as evaluations call it: no clarification, an answer that says
    how it read the question."""
    sources = build_sources(tmp_path / "sources").parent
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    events_dir = tmp_path / "events"
    proc = subprocess.run(
        [
            *(str(DATA_AGENT), "run", "--task", REVENUE_QUESTION, "--type", "query"),
            *("--workspace", str(workspace), "--duckdb", str(sources / "orders.duckdb")),
            *("--config", "full", "--events-dir", str(events_dir)),
        ],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=TURN_TIMEOUT,
    )
    assert proc.returncode == 0, proc.stderr
    [path] = events_dir.glob("*.jsonl")
    run = [json.loads(line) for line in path.read_text().splitlines()]
    assert not of_type(run, "clarification")
    assert not [e for e in of_type(run, "tool_start") if "clarification" in str(e["tool"])]
    answer = proc.stdout
    assert mentions(answer, GROSS_2024) or mentions(answer, NET_2024), answer
    # The assumption: how refunded orders were treated.
    assert STATED_REFUND_TREATMENT.search(MARKDOWN_MARKS.sub("", answer)), answer


# --- g: the agent picks the database, and uses both when it must ----------------------------


@THREE_TIMES
def test_g_a_question_across_both_databases_queries_each(server: Server, attempt: int) -> None:
    conversation = new_conversation(server)
    run = answered(server, conversation, REGION_ORDERS_QUESTION)
    assert mentions_per_region(answer_of(run), REGION_ORDERS), answer_of(run)
    queried = [e.get("database") for e in of_type(run, "sql")]
    assert {"orders", "customers"} <= set(queried), queried
    assert set(queried) <= {"orders", "customers"}, queried


@THREE_TIMES
def test_g_a_question_on_one_database_queries_only_it(server: Server, attempt: int) -> None:
    conversation = new_conversation(server)
    run = answered(server, conversation, REGION_CUSTOMERS_QUESTION)
    assert mentions_per_region(answer_of(run), REGION_CUSTOMERS), answer_of(run)
    queried = [e.get("database") for e in of_type(run, "sql")]
    assert queried and set(queried) == {"customers"}, queried
