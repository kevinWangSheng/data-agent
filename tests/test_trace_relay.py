"""The relay's rules on real CLI requests (ticket 113): tests/fixtures/observability/captured/ holds
the five OTLP requests of one query run by CLI 2.1.330 with every content switch on (`full-*`) and
of one with none (`structure-*`), with the machine's details replaced (its README). The acceptance
tests (tests/acceptance/test_observability_relay.py) run on ticket 111's hand-made request."""

import gzip
import threading
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from data_agent.trace_relay import (
    ANONYMIZED_IDS,
    CONNECTION_ERROR,
    HELD_OUT_ALLOWLIST,
    HTTP_ERROR,
    TOKEN_RENAMES,
    USAGE_PREFIXES,
    AnonIds,
    ExportError,
    TraceRelay,
    rewrite,
)
from data_agent.tracing import LangfuseSettings, TraceTier

CAPTURED = Path(__file__).parent / "fixtures" / "observability" / "captured"
SPAN_NAMES = {
    "claude_code.interaction": 1,
    "claude_code.llm_request": 5,
    "claude_code.tool": 6,
    "claude_code.tool.execution": 6,
    "claude_code.tool.blocked_on_user": 6,
}


def message_of(path: Path) -> Any:
    from google.protobuf.json_format import Parse
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,  # pyright: ignore[reportAttributeAccessIssue]
    )

    return Parse(path.read_text(), ExportTraceServiceRequest())


def parse(body: bytes) -> Any:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,  # pyright: ignore[reportAttributeAccessIssue]
    )

    message = ExportTraceServiceRequest()
    message.ParseFromString(body)
    return message


def requests(prefix: str) -> list[Any]:
    paths = sorted(CAPTURED.glob(f"{prefix}-req*.json"))
    assert len(paths) == 5
    return [message_of(path) for path in paths]


def rewritten(prefix: str, tier: TraceTier) -> list[Any]:
    """One run's requests rewritten with one `AnonIds`, as one relay does."""
    ids = AnonIds()
    return [
        parse(rewrite(m.SerializeToString(), tier=tier, ids=ids, secrets=()))
        for m in requests(prefix)
    ]


def value(v: Any) -> Any:
    kind = v.WhichOneof("value")
    if kind == "array_value":
        return tuple(value(item) for item in v.array_value.values)
    if kind == "kvlist_value":
        return {kv.key: value(kv.value) for kv in v.kvlist_value.values}
    return None if kind is None else getattr(v, kind)


def attrs(kvs: Any) -> dict[str, Any]:
    return {kv.key: value(kv.value) for kv in kvs}


def spans(messages: list[Any]) -> list[Any]:
    return [
        span
        for message in messages
        for resource_spans in message.resource_spans
        for scope_spans in resource_spans.scope_spans
        for span in scope_spans.spans
    ]


def layers(messages: list[Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for message in messages:
        for resource_spans in message.resource_spans:
            yield "resource", attrs(resource_spans.resource.attributes)
            for scope_spans in resource_spans.scope_spans:
                yield "scope", attrs(scope_spans.scope.attributes)
                for span in scope_spans.spans:
                    yield span.name, attrs(span.attributes)
                    for event in span.events:
                        yield f"event {event.name}", attrs(event.attributes)


def shape(messages: list[Any]) -> list[tuple[Any, ...]]:
    return [
        (
            s.name,
            s.trace_id,
            s.span_id,
            s.parent_span_id,
            s.start_time_unix_nano,
            s.end_time_unix_nano,
        )
        for s in spans(messages)
    ]


@pytest.mark.parametrize("prefix", ["full", "structure"])
def test_the_captured_run_has_the_spans_the_readme_lists(prefix: str) -> None:
    names = [s.name for s in spans(requests(prefix))]
    assert {name: names.count(name) for name in set(names)} == SPAN_NAMES


@pytest.mark.parametrize("prefix", ["full", "structure"])
@pytest.mark.parametrize("tier", ["full", "structure"])
def test_both_tiers_keep_the_captured_spans_and_rename_the_tokens(
    prefix: str, tier: TraceTier
) -> None:
    before = requests(prefix)
    after = rewritten(prefix, tier)
    assert shape(after) == shape(before)
    for old, new in zip(spans(before), spans(after), strict=True):
        old_attrs, new_attrs = attrs(old.attributes), attrs(new.attributes)
        assert not set(TOKEN_RENAMES) & set(new_attrs)
        assert "anthropic.custom_base_url" not in new_attrs
        for key, renamed in TOKEN_RENAMES.items():
            if key in old_attrs:
                assert new_attrs[renamed] == old_attrs[key], (old.name, key)
    requests_after = [s for s in spans(after) if s.name == "claude_code.llm_request"]
    assert all("gen_ai.usage.input_tokens" in attrs(s.attributes) for s in requests_after)


def test_the_dev_tier_keeps_the_captured_content() -> None:
    before, after = requests("full"), rewritten("full", "full")
    dropped = {*TOKEN_RENAMES, "anthropic.custom_base_url"}
    for (where, old), (_, new) in zip(layers(before), layers(after), strict=True):
        assert {k: v for k, v in old.items() if k not in dropped} == {
            k: v for k, v in new.items() if not k.startswith(USAGE_PREFIXES)
        }, where
    found = dict(layers(after))
    assert found["claude_code.interaction"]["user_prompt"].startswith("Task type: query")
    outputs = [a["output"] for where, a in layers(after) if where == "event tool.output"]
    assert len(outputs) == 6 and all(outputs)


@pytest.mark.parametrize("prefix", ["full", "structure"])
def test_the_held_out_tier_lets_only_the_allowlist_out_of_the_captured_run(prefix: str) -> None:
    after = rewritten(prefix, "structure")
    for where, found in layers(after):
        outside = {
            k
            for k in found
            if k not in HELD_OUT_ALLOWLIST | ANONYMIZED_IDS and not k.startswith(USAGE_PREFIXES)
        }
        assert outside == set(), where
    body = b"".join(m.SerializeToString() for m in after)
    for gone in (b"user_prompt", b"Task type: query", b"service.name", b"os.version", b"Orca"):
        assert gone not in body, gone
    tools = [attrs(s.attributes) for s in spans(after) if s.name == "claude_code.tool"]
    assert {t["anthropic.mcp.server.name"] for t in tools} == {"data"}
    assert all(t["tool_name"] and t["gen_ai.tool.name"] for t in tools)
    llm = [attrs(s.attributes) for s in spans(after) if s.name == "claude_code.llm_request"]
    assert all(a["model"] == "deepseek-flash" and "duration_ms" in a for a in llm)


def test_the_held_out_tier_pairs_the_captured_tool_spans_by_their_new_ids() -> None:
    before, after = requests("structure"), rewritten("structure", "structure")
    originals = {v for _, a in layers(before) for k, v in a.items() if k in ANONYMIZED_IDS}
    body = b"".join(m.SerializeToString() for m in after)
    assert originals and not [o for o in originals if o.encode() in body]
    # Across the five requests the same original id has the same new one.
    pairs = {
        (old_v, new_v)
        for old, new in zip(spans(before), spans(after), strict=True)
        for key in ANONYMIZED_IDS
        if (old_v := attrs(old.attributes).get(key)) is not None
        and (new_v := attrs(new.attributes).get(key)) is not None
    }
    assert len({o for o, _ in pairs}) == len(pairs) == len({n for _, n in pairs})
    # In CLI 2.1.330 the execution span repeats its tool's id (blocked_on_user carries none).
    tools = {s.span_id: attrs(s.attributes) for s in spans(after) if s.name == "claude_code.tool"}
    executions = [s for s in spans(after) if s.name == "claude_code.tool.execution"]
    assert len(tools) == len(executions) == 6
    for execution in executions:
        tool = tools[execution.parent_span_id]
        assert attrs(execution.attributes)["tool_use_id"] == tool["tool_use_id"]
        assert tool["gen_ai.tool.call.id"] == tool["tool_use_id"]
    assert len({t["tool_use_id"] for t in tools.values()}) == 6


def test_secret_shaped_values_and_known_secrets_are_masked_in_nested_values() -> None:
    [message, *_] = requests("full")
    span = spans([message])[0]
    kv = span.attributes.add()
    kv.key = "claude_code.nested"
    item = kv.value.array_value.values.add()
    item.string_value = "token sk-" + "a1" * 15 + " and known-value-1 here"
    span.status.message = "failed at known-value-1"
    out = rewrite(
        message.SerializeToString(), tier="full", ids=AnonIds(), secrets=("known-value-1",)
    )
    assert b"sk-a1a1" not in out and b"known-value-1" not in out
    assert b"token [REDACTED] and [REDACTED] here" in out


def test_a_key_on_a_new_line_of_json_tool_output_is_masked() -> None:
    [message, *_] = requests("full")
    outputs = [
        kv
        for span in spans([message])
        for event in span.events
        for kv in event.attributes
        if kv.key == "output"
    ]
    key = "sk-" + "b2" * 15
    # JSON in a string: the key follows the two characters `\` and `n`.
    outputs[0].value.string_value = '{"rows": "a\\n' + key + '"}'
    out = rewrite(message.SerializeToString(), tier="full", ids=AnonIds(), secrets=())
    assert key.encode() not in out
    assert b'{"rows": "a\\n[REDACTED]"}' in out


# --- the relay's server -----------------------------------------------------------------------


class Langfuse:
    """A Langfuse that records the bodies it got (a GET as `GET <path>`) and answers 200, a
    redirect with `redirect`, or never with `hang`."""

    def __init__(self, hang: bool = False, redirect: bool = False) -> None:
        self.bodies: list[bytes] = []
        self.release = threading.Event()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                fake.bodies.append(self.rfile.read(int(self.headers["Content-Length"])))
                if hang:
                    fake.release.wait(10)
                    return
                self.send_response(302 if redirect else 200)
                if redirect:
                    self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:
                fake.bodies.append(b"GET " + self.path.encode())
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.settings = LangfuseSettings("pk", "sk", f"http://127.0.0.1:{self.server.server_port}")

    def close(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


def send(endpoint: str, body: bytes, headers: dict[str, str]) -> int:
    request = urllib.request.Request(endpoint, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status


def test_a_gzipped_request_is_unpacked_and_forwarded() -> None:
    langfuse = Langfuse()
    relay = TraceRelay(langfuse.settings, "structure")
    try:
        body = requests("full")[0].SerializeToString()
        headers = {"Content-Type": "application/x-protobuf", "Content-Encoding": "gzip"}
        assert send(relay.start(), gzip.compress(body), headers) == 200
        report = relay.stop()
    finally:
        langfuse.close()
    [forwarded] = langfuse.bodies
    assert len(spans([parse(forwarded)])) == len(spans([parse(body)]))
    assert report.forwarded_spans == len(spans([parse(body)])) and report.errors == ()


def test_a_forward_stop_gives_up_on_counts_as_a_connection_error() -> None:
    langfuse = Langfuse(hang=True)
    relay = TraceRelay(langfuse.settings, "full", drain_timeout_s=0.5)
    try:
        endpoint = relay.start()
        body = requests("full")[0].SerializeToString()
        assert send(endpoint, body, {"Content-Type": "application/x-protobuf"}) == 200
        report = relay.stop()
    finally:
        langfuse.close()
    assert langfuse.bodies
    assert report.errors == (ExportError(CONNECTION_ERROR, None),)
    assert (report.forwarded_spans, report.forwarded_llm_requests) == (0, 0)


@pytest.mark.parametrize(
    ("case", "error"),
    [
        ("redirect", ExportError(HTTP_ERROR, 302)),
        ("no-scheme", ExportError(CONNECTION_ERROR, None)),
    ],
)
def test_a_redirect_or_an_unusable_address_is_reported_not_followed(
    case: str, error: ExportError
) -> None:
    langfuse = Langfuse(redirect=True)
    settings = langfuse.settings
    if case == "no-scheme":
        settings = LangfuseSettings("pk", "sk", settings.base_url.removeprefix("http://"))
    relay = TraceRelay(settings, "full")
    try:
        body = requests("full")[0].SerializeToString()
        assert send(relay.start(), body, {"Content-Type": "application/x-protobuf"}) == 200
        report = relay.stop()
    finally:
        langfuse.close()
    assert report.errors == (error,)
    assert (report.forwarded_spans, report.forwarded_llm_requests) == (0, 0)
    assert not [b for b in langfuse.bodies if b.startswith(b"GET ")]
