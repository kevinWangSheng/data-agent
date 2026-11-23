"""External MCP end to end with a real model: MongoDB's official MCP server over stdio
(.scratch/mcp/spec.md "验收", MongoDB). `integration`: run by `make test-integration` (which loads
the DATA_AGENT_* variables), never by `make check`. Needs Docker (on macOS, `colima start`).

Written by ticket 115. Both tests are xfail(strict) until ticket 116 makes them pass and removes
the marker, changing nothing else. Run them the way seam A runs (`data-agent run` as a
subprocess, tests/acceptance/test_seam_a_run_cli.py).

Each test starts its own docker network and mongo container (names with a random suffix), loads
tests/fixtures/mcp/support_tickets.json into `support.tickets` (one document per support ticket,
`customer_id` as in tests/fixtures/shop.sql's `raw.customers`), and removes both afterwards. The
MCP server is the `mongodb/mongodb-mcp-server` image at a pinned tag, started by the config with
`docker run --network <that network>`; the connection string is built at run time and given as
`DATA_AGENT_MCP_MONGO_URL`, so the config file holds only the reference.

Checked against the image (ticket 115 Comments: an MCP client listed its tools, no model):
- Read-only (`MDB_MCP_READ_ONLY=true`) registers 18 tools, among them `find`, `aggregate`,
  `count`, `list-collections`, `collection-schema` and `list-connections`; without it 27, adding
  the write tools (`insert-many`, `update-many`, `delete-many`, `create-collection`, ...).
- In 3.3.0 the query tools take a `connectionId` ("preconfigured" for the configured string, as
  their schema says, or the id `list-connections` gives), so the allow list has
  `list-connections` too.
- The server sends telemetry unless `MDB_MCP_TELEMETRY=disabled`; the config turns it off.

1. Read-only, with the shop DuckDB: a question that needs both MongoDB (tickets per customer) and
   DuckDB (that customer's orders). The agent calls a `mcp__mongo__` tool and answers within 0.01.
2. Not read-only, only the query tools allowed: asked to insert a ticket, the agent has the
   server (the `version` event names it with its allow list, and at least one allowed query call
   succeeds), makes no successful write call (`dontAsk` refuses what is not allowed), and the
   collection is unchanged. This is the spec's "调用被拒, 演示库没有变化", automated (ticket 115
   "spec 里的解读" 4; the human may drop it).

The questions and answers are our own, not a benchmark's (ADR-0004, ADR-0005).
"""

import json
import os
import re
import secrets
import subprocess
import time
import warnings
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from mcp_support import SUPPORT_TICKETS, write_config
from seam_support import DATA_AGENT, build_shop_duckdb, duckdb_rows

pytestmark = pytest.mark.integration


MCP_IMAGE = "mongodb/mongodb-mcp-server:3.3.0"
MONGO_IMAGE = "mongo:8.0"
# Labels the MCP server containers a run starts, so teardown finds the ones a killed run left.
LABEL = "data-agent-test"
QUERY_TOOLS = ["find", "aggregate", "count", "list-collections", "collection-schema"]
ALLOWED = [*QUERY_TOOLS, "list-connections"]
START_TIMEOUT = 90
RUN_TIMEOUT = 1800

JOIN_TASK = (
    "The MongoDB database `support` has a collection `tickets` with one document per customer "
    "support ticket; its `customer_id` matches `customer_id` in the DuckDB table raw.customers. "
    "Which customer has opened the most support tickets, and what is the total `amount` of all of "
    "that customer's orders in raw.orders, whatever their status? raw.orders.customer_ref is "
    "messy: the same customer appears with different case, spaces and dashes. Answer with the "
    "customer id and the total."
)
INSERT_TASK = (
    "First tell me how many documents the `tickets` collection of the MongoDB database `support` "
    "holds. Then insert one new document into that collection: "
    'ticket_id "T-2001", customer_id "C0012", subject "Test ticket", status "open", '
    'opened_on "2025-10-01".'
)


def docker(*args: str, stdin: str | None = None, timeout: float = 120) -> str:
    proc = subprocess.run(
        ["docker", *args], input=stdin, capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker {args[0]} failed: {proc.stderr.strip()}")
    return proc.stdout


@dataclass
class Mongo:
    network: str
    container: str

    @property
    def url(self) -> str:
        # Only reachable on the test's own network, by the container's name.
        return f"mongodb://{self.container}:27017/support"

    def eval(self, script: str) -> str:
        return docker("exec", self.container, "mongosh", "--quiet", "support", "--eval", script)

    def collections(self) -> list[str]:
        return sorted(json.loads(self.eval("EJSON.stringify(db.getCollectionNames())")))

    def tickets(self) -> list[dict[str, Any]]:
        out = self.eval(
            "EJSON.stringify(db.tickets.find({}, {_id: 0}).sort({ticket_id: 1}).toArray())"
        )
        return json.loads(out)


@pytest.fixture
def mongo() -> Iterator[Mongo]:
    suffix = secrets.token_hex(4)
    demo = Mongo(network=f"da-mcp-{suffix}", container=f"da-mongo-{suffix}")
    docker("network", "create", demo.network)
    try:
        docker(
            *("run", "-d", "--rm", "--name", demo.container, "--network", demo.network),
            MONGO_IMAGE,
            timeout=600,
        )
        deadline = time.monotonic() + START_TIMEOUT
        while True:
            try:
                if demo.eval("db.runCommand({ping: 1}).ok").strip() == "1":
                    break
            except RuntimeError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"mongo did not answer within {START_TIMEOUT} s")
            time.sleep(1)
        documents = json.loads(SUPPORT_TICKETS.read_text())
        demo.eval(f"db.tickets.insertMany({json.dumps(documents)})")
        if len(demo.tickets()) != len(documents):
            raise RuntimeError("the support tickets did not load")
        # The MCP image is there before the run, so its pull is not part of the session.
        docker("pull", MCP_IMAGE, timeout=600)
        yield demo
    finally:
        # The mongo container, and any MCP server container a killed or timed-out run left
        # behind (the config labels them), selected by this test's own label only.
        listed = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label={LABEL}={demo.network}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        leftovers = listed.stdout.split()
        subprocess.run(
            ["docker", "rm", "-f", demo.container, *leftovers], capture_output=True, timeout=60
        )
        removed = subprocess.run(
            ["docker", "network", "rm", demo.network], capture_output=True, text=True, timeout=60
        )
        if removed.returncode != 0:
            warnings.warn(
                f"docker network {demo.network} not removed: {removed.stderr.strip()}",
                stacklevel=1,
            )


def mongo_config(path: Path, demo: Mongo, read_only: bool) -> Path:
    return write_config(
        path,
        {
            "mongo": {
                "type": "stdio",
                "command": "docker",
                "args": [
                    *("run", "--rm", "-i", "--network", demo.network),
                    *("--label", f"{LABEL}={demo.network}"),
                    *("-e", "MDB_MCP_CONNECTION_STRING", "-e", "MDB_MCP_READ_ONLY"),
                    *("-e", "MDB_MCP_TELEMETRY", MCP_IMAGE),
                ],
                "env": {
                    "MDB_MCP_CONNECTION_STRING": "${DATA_AGENT_MCP_MONGO_URL}",
                    "MDB_MCP_READ_ONLY": "true" if read_only else "false",
                    "MDB_MCP_TELEMETRY": "disabled",
                },
                "allowedTools": ALLOWED,
            }
        },
    )


def run_agent(
    tmp_path: Path, demo: Mongo, task: str, read_only: bool
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]], Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    events_dir = tmp_path / "events"
    db = build_shop_duckdb(tmp_path / "shop.duckdb")
    config = mongo_config(tmp_path / "config" / "mcp.json", demo, read_only)
    proc = subprocess.run(
        [
            str(DATA_AGENT),
            "run",
            *("--task", task, "--type", "query", "--workspace", str(workspace)),
            *("--duckdb", str(db), "--config", "full", "--events-dir", str(events_dir)),
            *("--mcp-servers", str(config)),
        ],
        env={**os.environ, "DATA_AGENT_MCP_MONGO_URL": demo.url},
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT,
    )
    # A run that never reached the model is the environment's failure, not the feature's: it
    # must not hide as the expected failure.
    files = list(events_dir.glob("*.jsonl"))
    if len(files) != 1:
        raise RuntimeError(f"the run wrote {len(files)} event files: {proc.stderr}")
    events = [json.loads(line) for line in files[0].read_text().splitlines() if line.strip()]
    results = [e for e in events if e["type"] == "result"]
    if not results or not results[-1].get("output_tokens"):
        raise RuntimeError(f"the model never answered: {proc.stderr}")
    return proc, events, db


def calls(events: list[dict[str, Any]], prefix: str = "mcp__mongo__") -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == "tool_start" and str(e["tool"]).startswith(prefix)]


def succeeded(events: list[dict[str, Any]], prefix: str = "mcp__mongo__") -> list[str]:
    """The tools of the calls that returned without an error."""
    return [
        str(e["tool"])
        for e in events
        if e["type"] == "tool_end" and str(e["tool"]).startswith(prefix) and not e.get("is_error")
    ]


def expected_join_answer(db: Path) -> tuple[str, Decimal]:
    """The customer with the most tickets in the fixture (C0004, 5 tickets), and the sum of
    amount over all of that customer's orders, with the key cleaned as the task says:

        SELECT sum(amount) FROM raw.orders
        WHERE upper(replace(trim(customer_ref), '-', '')) = 'C0004'   -- 202.50
    """
    counts = Counter(doc["customer_id"] for doc in json.loads(SUPPORT_TICKETS.read_text()))
    [(top, most), (_, second)] = counts.most_common(2)
    assert most > second, "the fixture must have one customer with the most tickets"
    total = sum(
        (
            amount
            for ref, amount in duckdb_rows(db, "SELECT customer_ref, amount FROM raw.orders")
            if ref.strip().replace("-", "").upper() == top
        ),
        Decimal(0),
    )
    return top, total


def test_a_question_across_mongodb_and_duckdb_is_answered_with_the_external_tools(
    tmp_path: Path, mongo: Mongo
) -> None:
    proc, events, db = run_agent(tmp_path, mongo, JOIN_TASK, read_only=True)
    assert calls(events), "the agent called no mcp__mongo__ tool"
    assert proc.returncode == 0, proc.stderr
    customer, total = expected_join_answer(db)
    assert (customer, total) == ("C0004", Decimal("202.50"))
    numbers = [Decimal(n.replace(",", "")) for n in re.findall(r"-?\d[\d,]*\.\d+|\d+", proc.stdout)]
    assert any(abs(n - total) <= Decimal("0.01") for n in numbers), (
        f"expected {total} in the answer: {proc.stdout!r}"
    )
    assert customer in proc.stdout


def test_a_write_the_allow_list_leaves_out_is_refused_and_the_collection_is_unchanged(
    tmp_path: Path, mongo: Mongo
) -> None:
    before, collections = mongo.tickets(), mongo.collections()
    proc, events, _ = run_agent(tmp_path, mongo, INSERT_TASK, read_only=False)

    # The server was there: refusing the write is not a run without it.
    [version] = [e for e in events if e["type"] == "version"]
    assert "external_mcp_servers" in version, "the version event names no external server"
    recorded = [(s.get("name"), s.get("allowed_tools")) for s in version["external_mcp_servers"]]
    assert recorded == [("mongo", ALLOWED)]
    allowed = {f"mcp__mongo__{tool}" for tool in ALLOWED}
    assert set(succeeded(events)) & allowed, "no allowed mongo tool call succeeded"

    assert proc.returncode in (0, 1), proc.stderr
    assert "Traceback" not in proc.stderr
    writes = [tool for tool in succeeded(events) if tool not in allowed]
    assert writes == [], f"a tool outside the allow list succeeded: {writes}"
    assert mongo.tickets() == before
    # Nor written anywhere else (an allowed `aggregate` with `$out` would be a write).
    assert mongo.collections() == collections
