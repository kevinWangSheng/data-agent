"""The local relay (.scratch/observability/spec.md "本地中转"; "Testing Decisions": 中转规则,
中转转发).

Written by ticket 111. Ticket 113 implements `data_agent.trace_relay` and removes `TICKET_113`; the
end-to-end case also waits for ticket 112 (`TICKET_112`). Nothing else here changes.

The input is tests/fixtures/observability/cli-request.json, a hand-made OTLP request shaped like
the tree the spike read back (docs/research/2026-11-23-otel-spike.md, "A"): an interaction whose
parent is a fixed span id (TRACEPARENT), three model requests (one of them a subagent's, under the
`Agent` tool span), and the tools `mcp__data__query_database` (this repo's MCP server),
`Read` (built in) and `mcp__weather__forecast` (an outside MCP server), each with its
`blocked_on_user` and `execution` spans.

Attribute names with a source:
- usage-mapping note §3 (`claude_code.llm_request`): `model`, `gen_ai.system`,
  `gen_ai.request.model`, `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_creation_tokens`, `duration_ms`, `ttft_ms`, `first_content_ms`, `stop_reason`,
  `gen_ai.response.finish_reasons`, `gen_ai.response.id`, `request_id`, `attempt`, `success`,
  `effort`, `agent_id`, `parent_agent_id`; `parent.source` (§3); `session.id` (§5).
- observability note §3: `tool_name`, `tool_use_id`, `duration_ms`.
- the spike's read-back: `gen_ai.conversation.id`, `user_prompt`, `user_prompt_length`.
- spec "本地中转": `gen_ai.tool.call.id`, `anthropic.custom_base_url`, `user.id`,
  `terminal.type`, `llm_request.context`, `span.type`, `gen_ai.tool.name`.
Placeholder names (what the CLI really calls them is unconfirmed; the dev tier is only checked to
keep them as they are, the held-out tier to let only the allowlist out, so a wrong guess does not
weaken a test): the content attributes `tool.input`, `tool.output`, `llm_request.output`; the MCP
server's name `mcp_server_name`; the scope `com.anthropic.claude_code.tracing` and
`scope.placeholder_attr`; the event attribute `event.placeholder_attr`; `decision`; the resource's
`host.name`, `host.arch`, `os.type`, `os.version`; `claude_code.future_field` (an attribute a later
CLI adds, on every layer); `claude_code.env_echo` and `api_key_hint`, carriers of a known secret and
a secret-like value. The secret-like value, the known secret and the model endpoint are put in at
run time (`Planted`).
"""

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import duckdb
import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage
from observability_support import (
    OTLP_PATH,
    FakeLangfuse,
    Planted,
    attributes,
    basic_auth,
    cli_request,
    cli_request_message,
    closed_port_url,
    layers,
    parse_request,
    post,
    read_events,
    span_count,
    spans,
)

from data_agent import agent, tracing
from data_agent.agent import Task
from data_agent.events import known_secrets
from data_agent.run_config import load_config
from data_agent.trace_relay import AnonIds, TraceRelay, rewrite
from data_agent.tracing import LangfuseSettings, TraceTier

TICKET_112 = pytest.mark.xfail(
    strict=True, reason="ticket 112: run starts the relay and writes its report"
)
TICKET_113 = pytest.mark.xfail(strict=True, reason="ticket 113: the local relay")

TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
USAGE_PREFIXES = ("gen_ai.usage.", "llm.token_count.")
# Attributes holding a secret or the endpoint: dropped or masked, either is fine.
SECRET_CARRIERS = ("anthropic.custom_base_url", "api_key_hint", "claude_code.env_echo")
ANONYMIZED = ("session.id", "gen_ai.conversation.id", "tool_use_id", "gen_ai.tool.call.id")
# The held-out tier's allowlist (spec "本地中转"), with the anonymized ids and the placeholder name
# of the MCP server's attribute; token counts go by prefix.
ALLOWLIST = frozenset(
    {
        "duration_ms",
        "ttft_ms",
        "first_content_ms",
        "stop_reason",
        "success",
        "attempt",
        "effort",
        "tool_name",
        "gen_ai.tool.name",
        "mcp_server_name",
        "model",
        "gen_ai.request.model",
        "span.type",
        *ANONYMIZED,
    }
)
ORIGINAL_IDS = (
    "sess-orig-7f3a",
    "conv-orig-5d1e",
    "toolu_orig_q1",
    "toolu_orig_r1",
    "toolu_orig_w1",
    "toolu_orig_a1",
)
CONTENT = (
    "How many customers are in each membership tier?",  # the prompt
    "SELECT tier, count(*) AS n FROM customers GROUP BY tier",  # the SQL
    "Bronze,4",  # the query's result
    "Lisbon",  # the outside MCP tool's input
    "Lisbon: sunny, 21 degrees",  # and its output
)
TIERS = pytest.mark.parametrize("tier", ["full", "structure"])


def leaked(planted: Planted, body: bytes) -> list[str]:
    values = (planted.secret_like, planted.known_secret, planted.endpoint, planted.endpoint_host)
    return [value for value in values if value.encode() in body]


def run_rewrite(planted: Planted, tier: TraceTier, ids: AnonIds | None = None) -> bytes:
    secrets = known_secrets(planted.model_env())
    return rewrite(cli_request(planted), tier=tier, ids=ids or AnonIds(), secrets=secrets)


def by_span(message: Any) -> dict[bytes, Any]:
    return {span.span_id: span for span in spans(message)}


# --- the rules (`rewrite`) ----------------------------------------------------------------------


@TICKET_113
@TIERS
def test_token_counts_get_the_names_langfuse_reads_and_the_endpoint_attribute_goes(
    tier: TraceTier,
) -> None:
    planted = Planted.new()
    before = by_span(cli_request_message(planted))
    after = by_span(parse_request(run_rewrite(planted, tier)))
    requests = [s for s in before.values() if s.name == "claude_code.llm_request"]
    assert len(requests) == 3
    for original in requests:
        old, new = attributes(original.attributes), attributes(after[original.span_id].attributes)
        assert new["gen_ai.usage.input_tokens"] == old["input_tokens"]
        assert new["gen_ai.usage.output_tokens"] == old["output_tokens"]
        # Langfuse's cache field names are checked when 113 is built: the counts sit under one
        # of the prefixes it reads, whatever the key.
        usage = {k: v for k, v in new.items() if k.startswith(USAGE_PREFIXES)}
        for cached in ("cache_read_tokens", "cache_creation_tokens"):
            if old[cached]:
                assert old[cached] in usage.values(), (cached, usage)
        assert not set(TOKEN_KEYS) & set(new)
        assert "anthropic.custom_base_url" not in new


@TICKET_113
def test_the_dev_tier_keeps_every_attribute_but_the_tokens_names_and_the_secrets() -> None:
    planted = Planted.new()
    body = run_rewrite(planted, "full")
    after = dict(layers(parse_request(body)))
    for where, old in layers(cli_request_message(planted)):
        kept = {k: v for k, v in old.items() if k not in (*TOKEN_KEYS, *SECRET_CARRIERS)}
        new = after[where]
        assert {k: new.get(k) for k in kept} == kept, where
    for content in CONTENT:
        assert content.encode() in body, content
    assert leaked(planted, body) == []


@TICKET_113
def test_the_held_out_tier_lets_only_the_allowlist_out() -> None:
    planted = Planted.new()
    body = run_rewrite(planted, "structure")
    message = parse_request(body)
    names = [s.name for s in spans(message)]
    assert names.count("claude_code.llm_request") == 3
    for where, attrs in layers(message):
        outside = {k for k in attrs if k not in ALLOWLIST and not k.startswith(USAGE_PREFIXES)}
        assert outside == set(), where
    for gone in ("claude_code.future_field", "user_prompt", "user.id", "terminal.type"):
        assert gone.encode() not in body, gone
    for host_info in ("host.name", "placeholder-host", "os.version"):
        assert host_info.encode() not in body, host_info
    for content in CONTENT:
        assert content.encode() not in body, content
    assert leaked(planted, body) == []


@TICKET_113
@TIERS
def test_both_tiers_keep_the_spans_their_ids_tree_and_times(tier: TraceTier) -> None:
    planted = Planted.new()

    def shape(message: Any) -> list[tuple[Any, ...]]:
        return [
            (
                s.name,
                s.trace_id,
                s.span_id,
                s.parent_span_id,
                s.start_time_unix_nano,
                s.end_time_unix_nano,
            )
            for s in spans(message)
        ]

    before = shape(cli_request_message(planted))
    assert len(before) == 14
    assert shape(parse_request(run_rewrite(planted, tier))) == before


@TICKET_113
def test_the_held_out_tier_replaces_the_ids_consistently_within_a_run() -> None:
    planted = Planted.new()
    ids = AnonIds()
    first = run_rewrite(planted, "structure", ids)
    for original in ORIGINAL_IDS:
        assert original.encode() not in first, original
    message = parse_request(first)
    attrs = {s.span_id: (s.name, attributes(s.attributes)) for s in spans(message)}
    sessions = {a["session.id"] for _, a in attrs.values()}
    assert len(sessions) == 1 and "" not in sessions
    [conversation] = {
        a["gen_ai.conversation.id"] for _, a in attrs.values() if "gen_ai.conversation.id" in a
    }
    assert conversation
    # Each tool span pairs with its blocked_on_user and execution spans by the new id, and its
    # gen_ai.tool.call.id (the same original id) becomes the same new id.
    tools = [(sid, a) for sid, (name, a) in attrs.items() if name == "claude_code.tool"]
    assert len(tools) == 4
    for span_id, tool in tools:
        children = [
            a for sid, (_, a) in attrs.items() if by_span(message)[sid].parent_span_id == span_id
        ]
        new_id = tool["tool_use_id"]
        assert tool["gen_ai.tool.call.id"] == new_id
        for child in children:
            if "tool_use_id" in child:
                assert child["tool_use_id"] == new_id
    assert len({tool["tool_use_id"] for _, tool in tools}) == 4
    # The same AnonIds on a second request of the run gives the same ids; another run's differs.
    assert run_rewrite(planted, "structure", ids) == first
    other = parse_request(run_rewrite(planted, "structure", AnonIds()))
    other_sessions = {attributes(s.attributes)["session.id"] for s in spans(other)}
    assert other_sessions.isdisjoint(sessions)


# --- forwarding (`TraceRelay`) ------------------------------------------------------------------


@pytest.fixture
def langfuse() -> Iterator[FakeLangfuse]:
    fake = FakeLangfuse().start()
    yield fake
    fake.stop()


def relay_for(
    settings: LangfuseSettings, tier: TraceTier = "full", drain_timeout_s: float = 10.0
) -> TraceRelay:
    return TraceRelay(settings, tier, secrets=(), drain_timeout_s=drain_timeout_s)


@TICKET_113
@TIERS
def test_the_relay_forwards_the_rewritten_request_with_langfuses_headers(
    langfuse: FakeLangfuse, tier: TraceTier
) -> None:
    relay = relay_for(langfuse.settings(), tier)
    endpoint = relay.start()
    url = urlsplit(endpoint)
    assert (url.scheme, url.hostname, url.path) == ("http", "127.0.0.1", "/v1/traces")
    body = cli_request(langfuse.planted)
    post(endpoint, body)
    report = relay.stop()
    [seen] = langfuse.ingested()
    assert seen.headers["authorization"] == basic_auth()
    assert seen.headers["x-langfuse-ingestion-version"] == "4"
    forwarded = parse_request(seen.body)
    assert len(spans(forwarded)) == 14
    all_names = {k for _, attrs in layers(forwarded) for k in attrs}
    assert "gen_ai.usage.input_tokens" in all_names
    assert "anthropic.custom_base_url" not in all_names
    assert ("claude_code.future_field" in all_names) == (tier == "full")
    assert (report.forwarded_spans, report.forwarded_llm_requests, report.errors) == (14, 3, ())


@TICKET_113
def test_the_relay_takes_only_post_v1_traces(langfuse: FakeLangfuse) -> None:
    relay = relay_for(langfuse.settings())
    endpoint = relay.start()
    body = cli_request(langfuse.planted)
    origin = endpoint.removesuffix("/v1/traces")
    post(endpoint, body, method="PUT")
    post(origin + "/v1/metrics", body)
    post(origin + "/v1/logs", body)
    post(endpoint, body)
    report = relay.stop()
    assert len(langfuse.ingested()) == 1
    assert [s.path for s in langfuse.seen] == [OTLP_PATH]
    assert report.forwarded_spans == 14


@TICKET_113
@pytest.mark.parametrize(
    ("case", "error"),
    [("status-500", ("http_error", 500)), ("refused", ("connection_error", None))],
)
def test_a_failed_forward_is_reported_not_raised(
    langfuse: FakeLangfuse, case: str, error: tuple[str, int | None]
) -> None:
    settings = langfuse.settings()
    if case == "status-500":
        langfuse.status = 500
    else:
        settings = LangfuseSettings(settings.public_key, settings.secret_key, closed_port_url())
    relay = relay_for(settings)
    post(relay.start(), cli_request(langfuse.planted))
    report = relay.stop()
    assert [(e.error_type, e.status) for e in report.errors] == [error]
    assert (report.forwarded_spans, report.forwarded_llm_requests) == (0, 0)


@TICKET_113
def test_an_unparsable_request_is_reported_and_nothing_is_sent(langfuse: FakeLangfuse) -> None:
    relay = relay_for(langfuse.settings())
    post(relay.start(), b"\xff\x00 this is no ExportTraceServiceRequest \xff")
    report = relay.stop()
    assert [(e.error_type, e.status) for e in report.errors] == [("parse_error", None)]
    assert langfuse.ingested() == []


@TICKET_113
def test_stop_forwards_every_request_already_taken(langfuse: FakeLangfuse) -> None:
    langfuse.delay_s = 0.3
    relay = relay_for(langfuse.settings())
    endpoint = relay.start()
    bodies = [cli_request(langfuse.planted) for _ in range(3)]
    posts = [threading.Thread(target=post, args=(endpoint, body)) for body in bodies]
    for thread in posts:
        thread.start()
    for thread in posts:
        thread.join(5)
    report = relay.stop()
    received = langfuse.ingested()
    assert len(received) == 3
    counts = [span_count(seen.body) for seen in received]
    assert report.forwarded_spans == sum(n for n, _ in counts) == 42
    assert report.forwarded_llm_requests == sum(n for _, n in counts) == 9
    assert report.errors == ()


@TICKET_113
def test_stop_gives_up_on_a_langfuse_that_never_answers(langfuse: FakeLangfuse) -> None:
    langfuse.hang = True
    relay = relay_for(langfuse.settings(), drain_timeout_s=1)
    endpoint = relay.start()
    sender = threading.Thread(
        target=post, args=(endpoint, cli_request(langfuse.planted), "POST", 3)
    )
    sender.start()
    deadline = time.monotonic() + 3
    while not langfuse.ingested() and time.monotonic() < deadline:
        time.sleep(0.05)
    started = time.monotonic()
    relay.stop()
    assert time.monotonic() - started < 4
    langfuse.release()
    sender.join(5)


# --- end to end: `run` with the real relay ------------------------------------------------------


def posting_query(body: bytes, seen: list[ClaudeAgentOptions]) -> Any:
    """A session that, like the CLI, POSTs its spans to the endpoint `build_options` gave it."""

    async def fake_query(
        *, prompt: str, options: ClaudeAgentOptions, **_: Any
    ) -> AsyncIterator[Any]:
        seen.append(options)
        endpoint = options.env["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]
        await asyncio.to_thread(post, endpoint, body)
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            usage={"input_tokens": 3, "cache_read_input_tokens": 5, "output_tokens": 7},
            total_cost_usd=0.01,
            result="42",
        )

    return fake_query


async def run_with_relay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, langfuse: FakeLangfuse
) -> tuple[agent.Outcome, list[dict[str, Any]]]:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    seen: list[ClaudeAgentOptions] = []
    monkeypatch.setattr(agent, "query", posting_query(cli_request(langfuse.planted), seen))
    monkeypatch.setattr(tracing, "parent_exporter", lambda _settings: InMemorySpanExporter())
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    task = Task(text="How many customers?", type="query", workspace=tmp_path, duckdb=db)
    env = {"DATA_AGENT_API_KEY": "sk-test-secret"} | langfuse.env()
    outcome = await agent.run(task, load_config("full"), tmp_path / "events", env)
    assert seen, "the session never started"
    return outcome, read_events(outcome.events_path)


@TICKET_112
@TICKET_113
async def test_the_trace_event_counts_what_langfuse_received(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, langfuse: FakeLangfuse
) -> None:
    outcome, events = await run_with_relay(monkeypatch, tmp_path, langfuse)
    [trace] = [e for e in events if e["type"] == "trace"]
    counts = [span_count(seen.body) for seen in langfuse.ingested()]
    assert counts, "Langfuse received nothing"
    assert trace["forwarded_spans"] == sum(n for n, _ in counts)
    assert trace["forwarded_llm_requests"] == sum(n for _, n in counts)
    assert outcome.finished
    assert not [e for e in events if e["type"] == "trace_export_error"]


@TICKET_112
@TICKET_113
async def test_a_langfuse_error_leaves_the_run_to_finish_with_an_error_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, langfuse: FakeLangfuse
) -> None:
    langfuse.status = 500
    outcome, events = await run_with_relay(monkeypatch, tmp_path, langfuse)
    errors = [e for e in events if e["type"] == "trace_export_error"]
    assert errors
    assert {(e["error_type"], e["status"]) for e in errors} == {("http_error", 500)}
    assert outcome.finished
    assert [e["type"] for e in events].count("result") == 1


# --- self-checks: the fixture parses and the fake Langfuse answers (pass now) -------------------


def test_the_fixture_parses_into_the_spikes_tree() -> None:
    planted = Planted.new()
    message = cli_request_message(planted)
    found = spans(message)
    assert len(found) == 14
    names = [s.name for s in found]
    assert names.count("claude_code.llm_request") == 3
    assert names.count("claude_code.tool") == 4
    [interaction] = [s for s in found if s.name == "claude_code.interaction"]
    assert interaction.parent_span_id.hex() == "0a0a0a0a0a0a0a0a"
    agent_tool = next(s for s in found if attributes(s.attributes).get("tool_name") == "Agent")
    assert any(
        s.name == "claude_code.llm_request" and s.parent_span_id == agent_tool.span_id
        for s in found
    )
    body = cli_request(planted)
    assert planted.secret_like.encode() in body and planted.endpoint.encode() in body
    for name, attrs in layers(message):
        assert "claude_code.future_field" in attrs or name.startswith(("span", "event")), name
    assert parse_request(body) == message


def test_the_fixture_file_holds_no_secret_or_endpoint() -> None:
    text = (
        Path(__file__).parents[1] / "fixtures" / "observability" / "cli-request.json"
    ).read_text()
    assert "sk-" not in text and "llm.example" not in text
    json.loads(text)


def test_the_fake_langfuse_records_and_answers(langfuse: FakeLangfuse) -> None:
    body = cli_request(langfuse.planted)
    assert post(langfuse.base_url + OTLP_PATH, body) == 200
    langfuse.status = 500
    assert post(langfuse.base_url + OTLP_PATH, body) == 500
    [first, _] = langfuse.ingested()
    assert first.body == body
    assert urlsplit(langfuse.base_url).hostname == "127.0.0.1"
    assert post(closed_port_url() + OTLP_PATH, body, timeout=2) is None
