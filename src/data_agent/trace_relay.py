"""The local relay between the Claude Code CLI and Langfuse (.scratch/observability/spec.md
"本地中转"): it takes the CLI's OTLP requests on 127.0.0.1, rewrites them by tier and forwards them
with the Langfuse keys, which the CLI never gets.

The contract is ticket 111's (tests/acceptance/test_observability_relay.py); ticket 113 implements
it. `run` and `evals.dab.run_once` look up `TraceRelay` on this module, so a test puts a recording
stand-in in its place.

OpenTelemetry's protobuf classes are imported only when a request is rewritten: with the export
off, a run imports none of them. The relay writes no events: its handlers run on their own threads
and `EventLog.emit` takes no lock, so failures go into the `RelayReport` and `run` writes them.
"""

import base64
import gzip
import re
import secrets as random_secrets
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from data_agent.events import REDACTED, mask
from data_agent.tracing import LangfuseSettings, TraceTier

# `ExportError.error_type`'s three values (ticket 111).
HTTP_ERROR = "http_error"
CONNECTION_ERROR = "connection_error"
PARSE_ERROR = "parse_error"

# Both tiers: the CLI's token counts under the names Langfuse reads as usage. Langfuse takes every
# `gen_ai.usage.*` attribute as a usage key without the prefix. A key it knows as a cache alias
# (`cache_read_tokens`, `cache_read.input_tokens`, `input_cache_creation`, ...) is also subtracted
# from `input`, which assumes OpenTelemetry's input that includes the cache; the CLI's
# `input_tokens` does not (captured: 371 input, 10496 cache read), so `input` would drop to 0.
# The cache counts therefore go under keys outside those aliases, kept as they are:
# `input_cache_read`, a key Langfuse's Claude prices use, and the CLI's own
# `cache_creation_tokens` (every priced cache-write key is an alias or names a cache TTL the CLI
# does not report). Source: langfuse/langfuse
# packages/shared/src/server/otel/OtelIngestionProcessor.ts `extractGenericGenAiUsageDetails` and
# worker/src/constants/default-model-prices.json, main at 7c5b39cc, read 2026-11-23.
TOKEN_RENAMES = {
    "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
    "cache_read_tokens": "gen_ai.usage.input_cache_read",
    "cache_creation_tokens": "gen_ai.usage.cache_creation_tokens",
}
USAGE_PREFIXES = ("gen_ai.usage.", "llm.token_count.")
# Both tiers: attributes dropped by name. `anthropic.custom_base_url` is the model endpoint.
DROPPED = frozenset({"anthropic.custom_base_url"})
# Both tiers: values shaped like a credential, masked wherever they appear in a string. The same
# shapes as `evals.scrub.KEY_SHAPE`: sk- keys, bearer tokens, JWTs, GitHub tokens, AWS key ids.
# Not after a name character, except one ending a JSON escape: tool output is often JSON in a
# string, where a key on a new line follows the `n` of `\n`.
NAME_START = r"(?:(?<![A-Za-z0-9_-])|(?<=\\[bfnrt])|(?<=\\u[0-9A-Fa-f]{4}))"
SECRET_SHAPE = re.compile(
    "|".join(
        [
            rf"{NAME_START}sk-[A-Za-z0-9_-]{{20,}}",
            r"\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{20,}",
            rf"{NAME_START}eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
            rf"{NAME_START}gh[pousr]_[A-Za-z0-9]{{36,}}",
            rf"{NAME_START}AKIA[0-9A-Z]{{16}}(?![0-9A-Z])",
        ]
    )
)

# The held-out tier: the ids replaced by a run's anonymous ones, and the only attributes let out
# on any layer (resource, scope, span, span event, link), besides these ids and the usage
# prefixes (spec "本地中转"). The tool's MCP server is `anthropic.mcp.server.name` in CLI 2.1.330
# (tests/fixtures/observability/captured/); `mcp_server_name` is the name ticket 111's fixture
# gives it. Add a name here when a later CLI renames a field the spec keeps.
ANONYMIZED_IDS = frozenset(
    {"session.id", "gen_ai.conversation.id", "tool_use_id", "gen_ai.tool.call.id"}
)
HELD_OUT_ALLOWLIST = frozenset(
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
        "anthropic.mcp.server.name",
        "mcp_server_name",
        "model",
        "gen_ai.request.model",
        "span.type",
    }
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is Langfuse's answer, an `http_error`: following it would resend the keys and
    turn the POST into a GET without the spans."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)

CLI_PATH = "/v1/traces"
LANGFUSE_OTLP_PATH = "/api/public/otel/v1/traces"
# How long one forward waits for Langfuse; `stop` gives up on it sooner (`drain_timeout_s`).
FORWARD_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class ExportError:
    """A batch the relay dropped: what went wrong and Langfuse's HTTP status. `error_type` is
    `http_error` (Langfuse answered with an error status), `connection_error` (no response: the
    connection was refused, dropped or timed out; `status` None) or `parse_error` (the CLI's
    request could not be parsed, so nothing was sent; `status` None). `run` writes each as a
    `trace_export_error` event with these two fields only."""

    error_type: str
    status: int | None


@dataclass(frozen=True)
class RelayReport:
    """What the relay forwarded, once stopped: the spans, the `claude_code.llm_request` spans among
    them, and the batches it dropped."""

    forwarded_spans: int
    forwarded_llm_requests: int
    errors: tuple[ExportError, ...]


class AnonIds:
    """One run's anonymous ids (the held-out tier): the same original id always becomes the same
    anonymous id within one object, so a tool span still pairs with its execution span."""

    def __init__(self) -> None:
        self._ids: dict[str, str] = {}
        self._lock = threading.Lock()

    def __call__(self, original: str) -> str:
        with self._lock:
            if original not in self._ids:
                self._ids[original] = "anon-" + random_secrets.token_hex(8)
            return self._ids[original]


def rewrite(body: bytes, *, tier: TraceTier, ids: AnonIds, secrets: Sequence[str]) -> bytes:
    """A serialized `ExportTraceServiceRequest` rewritten by `tier`'s rules. `secrets` are the
    run's known secret values (`events.known_secrets`), which never leave in the output."""
    message = _parse(body)
    _rewrite_message(message, tier, ids, secrets)
    return message.SerializeToString()


def _parse(body: bytes) -> Any:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,  # pyright: ignore[reportAttributeAccessIssue]
    )

    message = ExportTraceServiceRequest()
    message.ParseFromString(body)
    return message


def _rewrite_message(message: Any, tier: TraceTier, ids: AnonIds, secrets: Sequence[str]) -> None:
    def clean(text: str) -> str:
        return SECRET_SHAPE.sub(REDACTED, mask(text, secrets))

    def attrs(kvs: Any) -> None:
        _rewrite_attributes(kvs, tier, ids, clean)

    for resource_spans in message.resource_spans:
        attrs(resource_spans.resource.attributes)
        for scope_spans in resource_spans.scope_spans:
            attrs(scope_spans.scope.attributes)
            for span in scope_spans.spans:
                attrs(span.attributes)
                for event in span.events:
                    attrs(event.attributes)
                for link in span.links:
                    attrs(link.attributes)
                if tier == "structure":
                    span.status.message = ""
                else:
                    span.status.message = clean(span.status.message)


def _rewrite_attributes(
    kvs: Any, tier: TraceTier, ids: AnonIds, clean: Callable[[str], str]
) -> None:
    """One layer's attributes, in place: renamed, dropped, masked and (held out) allowlisted."""
    dropped: list[int] = []
    for index, kv in enumerate(kvs):
        original: str = kv.key
        key = TOKEN_RENAMES.get(original, original)
        kv.key = key
        if key in DROPPED:
            dropped.append(index)
            continue
        if tier == "structure":
            if key in ANONYMIZED_IDS and kv.value.WhichOneof("value") == "string_value":
                kv.value.string_value = ids(kv.value.string_value)
            elif key not in HELD_OUT_ALLOWLIST and not key.startswith(USAGE_PREFIXES):
                dropped.append(index)
                continue
        _clean_value(kv.value, clean)
    for index in reversed(dropped):
        del kvs[index]


def _clean_value(value: Any, clean: Callable[[str], str]) -> None:
    kind = value.WhichOneof("value")
    if kind == "string_value":
        value.string_value = clean(value.string_value)
    elif kind == "array_value":
        for item in value.array_value.values:
            _clean_value(item, clean)
    elif kind == "kvlist_value":
        for kv in value.kvlist_value.values:
            _clean_value(kv.value, clean)


def _span_counts(message: Any) -> tuple[int, int]:
    found = [
        span.name
        for resource_spans in message.resource_spans
        for scope_spans in resource_spans.scope_spans
        for span in scope_spans.spans
    ]
    return len(found), found.count("claude_code.llm_request")


class TraceRelay:
    """Listens on a random 127.0.0.1 port for `POST /v1/traces` and forwards each request,
    rewritten, to `<base_url>/api/public/otel/v1/traces`. It never raises on a failed batch: the
    failure goes into the report."""

    def __init__(
        self,
        langfuse: LangfuseSettings,
        tier: TraceTier,
        secrets: Sequence[str] = (),
        drain_timeout_s: float = 10.0,
    ) -> None:
        self._url = langfuse.base_url.rstrip("/") + LANGFUSE_OTLP_PATH
        token = base64.b64encode(f"{langfuse.public_key}:{langfuse.secret_key}".encode()).decode()
        self._headers = {
            "Content-Type": "application/x-protobuf",
            "Authorization": "Basic " + token,
            "x-langfuse-ingestion-version": "4",
        }
        self._tier: TraceTier = tier
        self._secrets = tuple(secrets)
        self._drain_timeout_s = drain_timeout_s
        self._ids = AnonIds()
        self._server: ThreadingHTTPServer | None = None
        self._done = threading.Condition()
        self._in_flight = 0
        self._spans = 0
        self._llm_requests = 0
        self._errors: list[ExportError] = []

    def start(self) -> str:
        """Opens the port; returns the endpoint the CLI exports to,
        `http://127.0.0.1:<port>/v1/traces`."""
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.path.split("?")[0] != CLI_PATH:
                    self.reply(404)
                    return
                relay._take(self.headers.get, self.rfile.read, self.reply)

            def do_GET(self) -> None:
                self.reply(405)

            do_PUT = do_DELETE = do_PATCH = do_HEAD = do_GET

            def reply(self, status: int) -> None:
                """Answers the CLI; one that has gone away is no reason to drop its spans."""
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/x-protobuf")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    self.wfile.flush()
                except OSError:
                    pass

            def log_message(self, format: str, *args: Any) -> None:
                pass

        class Server(ThreadingHTTPServer):
            """Counts a request as taken once accepted, before `shutdown` can return, and as done
            when its handler's thread ends."""

            daemon_threads = True

            def process_request(self, request: Any, client_address: Any) -> None:
                with relay._done:
                    relay._in_flight += 1
                super().process_request(request, client_address)

            def process_request_thread(self, request: Any, client_address: Any) -> None:
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    with relay._done:
                        relay._in_flight -= 1
                        relay._done.notify_all()

        self._server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}{CLI_PATH}"

    def stop(self) -> RelayReport:
        """Waits for the requests already taken to be forwarded (at most `drain_timeout_s`),
        then closes the port."""
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
        with self._done:
            self._done.wait_for(lambda: self._in_flight == 0, timeout=self._drain_timeout_s)
            # A forward still waiting on Langfuse when time is up got no response: dropped.
            abandoned = [ExportError(CONNECTION_ERROR, None)] * self._in_flight
            report = RelayReport(self._spans, self._llm_requests, (*self._errors, *abandoned))
        if server is not None:
            server.server_close()
        return report

    def _take(
        self,
        header: Callable[[str], str | None],
        read: Callable[[int], bytes],
        reply: Callable[[int], None],
    ) -> None:
        """One CLI request: answered at once (the CLI does not wait on Langfuse), then forwarded,
        rewritten, on the handler's thread."""
        try:
            body = read(int(header("Content-Length") or 0))
            if (header("Content-Encoding") or "").lower() == "gzip":
                body = gzip.decompress(body)
            message = _parse(body)
            _rewrite_message(message, self._tier, self._ids, self._secrets)
        except Exception:
            reply(400)
            self._record(ExportError(PARSE_ERROR, None))
            return
        reply(200)
        self._record(self._forward(message), _span_counts(message))

    def _forward(self, message: Any) -> ExportError | None:
        try:
            request = urllib.request.Request(
                self._url, data=message.SerializeToString(), method="POST", headers=self._headers
            )
            with _OPENER.open(request, timeout=FORWARD_TIMEOUT_S) as response:
                status: int = response.status
        except urllib.error.HTTPError as exc:
            return ExportError(HTTP_ERROR, exc.code)
        except Exception:
            # No usable response: refused, dropped, timed out, a malformed answer or address.
            return ExportError(CONNECTION_ERROR, None)
        return None if 200 <= status < 300 else ExportError(HTTP_ERROR, status)

    def _record(self, error: ExportError | None, counts: tuple[int, int] = (0, 0)) -> None:
        with self._done:
            if error is not None:
                self._errors.append(error)
            else:
                self._spans += counts[0]
                self._llm_requests += counts[1]
