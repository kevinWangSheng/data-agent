"""Stand-ins and fixture readers shared by the observability acceptance tests (ticket 111;
.scratch/observability/spec.md "Testing Decisions").

- `FakeLangfuse`: a Langfuse on 127.0.0.1 that records every request.
- `NoNetwork`: refuses (and records) every AF_INET / AF_INET6 `connect` and `bind`.
- `cli_request`: the hand-made OTLP request of tests/fixtures/observability/cli-request.json, with
  the secret-like value, a known secret and the model endpoint put in at run time (so neither
  pre-commit's secret scan nor `evals scrub` finds them in the file).
- `FakeRelay`: records what `run` asks of `trace_relay.TraceRelay`, opens no port.
- `cli_env`: the environment the SDK starts the CLI with (tests/test_context_window.py).

Langfuse's API, as its official definitions give it (read 2026-11-23):
- OTLP ingestion: `POST /api/public/otel/v1/traces`, Basic auth (public key : secret key),
  `x-langfuse-ingestion-version: 4` (https://langfuse.com/integrations/native/opentelemetry).
- `GET /api/public/v2/observations` (query `traceId`, `fields`, ...) answers
  `{"data": [ObservationV2, ...], "meta": {"cursor"?}}`; the observation fields are `id`,
  `traceId`, `parentObservationId`, `type`, `name`, `startTime`, `endTime`, `input`, `output`,
  `metadata`, `usageDetails`, `costDetails`, ... (fern definitions
  https://raw.githubusercontent.com/langfuse/langfuse/main/fern/apis/server/definition/observations.yml
  and .../commons.yml `ObservationV2`).
- `GET /api/public/projects` answers `{"data": [{"id", "name", "organization", "metadata"}]}`
  (.../definition/projects.yml).
"""

import base64
import json
import secrets as random_secrets
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlsplit

from claude_agent_sdk import ClaudeAgentOptions, CLIConnectionError

from data_agent import trace_relay, tracing
from data_agent.trace_relay import ExportError, RelayReport
from data_agent.tracing import LangfuseSettings, TraceTier

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "observability"
CLI_REQUEST = FIXTURES / "cli-request.json"
OBSERVATIONS = FIXTURES / "observations.json"
PROJECTS = FIXTURES / "projects.json"
EVAL_MANIFEST = REPO_ROOT / "tests" / "fixtures" / "eval_manifest.json"

LANGFUSE_ENV: tuple[str, ...] = (
    "DATA_AGENT_LANGFUSE_PUBLIC_KEY",
    "DATA_AGENT_LANGFUSE_SECRET_KEY",
    "DATA_AGENT_LANGFUSE_BASE_URL",
)
PUBLIC_KEY = "pk-lf-test-public-0001"
SECRET_KEY = "sk-lf-test-secret-0001"
OTLP_PATH = "/api/public/otel/v1/traces"
OBSERVATIONS_PATH = "/api/public/v2/observations"
PROJECTS_PATH = "/api/public/projects"


# --- run-time secrets ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Planted:
    """Values put into fixtures at run time: a secret-like key (`sk-` and 40 characters), a secret
    only the run knows (`events.known_secrets`), and the model endpoint."""

    secret_like: str
    known_secret: str
    endpoint: str

    @classmethod
    def new(cls) -> "Planted":
        return cls(
            secret_like="sk-" + random_secrets.token_hex(20),
            known_secret="known-" + random_secrets.token_hex(8),
            endpoint="https://" + "llm.example" + "/anthropic",
        )

    @property
    def endpoint_host(self) -> str:
        return urlsplit(self.endpoint).hostname or ""

    def model_env(self) -> dict[str, str]:
        """The model variables whose values `known_secrets` gives the relay."""
        return {"DATA_AGENT_API_KEY": self.known_secret, "DATA_AGENT_BASE_URL": self.endpoint}

    def fill(self, text: str) -> str:
        return (
            text.replace("@@SECRET_LIKE@@", self.secret_like)
            .replace("@@KNOWN_SECRET@@", self.known_secret)
            .replace("@@MODEL_ENDPOINT@@", self.endpoint)
        )


# --- the CLI's OTLP request ---------------------------------------------------------------------


def cli_request_message(planted: Planted) -> Any:
    """The fixture as an `ExportTraceServiceRequest`."""
    from google.protobuf.json_format import Parse
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,  # pyright: ignore[reportAttributeAccessIssue]
    )

    return Parse(planted.fill(CLI_REQUEST.read_text()), ExportTraceServiceRequest())


def cli_request(planted: Planted) -> bytes:
    """The fixture serialized, as the CLI POSTs it (http/protobuf)."""
    return cli_request_message(planted).SerializeToString()


def parse_request(body: bytes) -> Any:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,  # pyright: ignore[reportAttributeAccessIssue]
    )

    message = ExportTraceServiceRequest()
    message.ParseFromString(body)
    return message


def any_value(value: Any) -> Any:
    kind = value.WhichOneof("value")
    if kind == "array_value":
        return tuple(any_value(v) for v in value.array_value.values)
    if kind == "kvlist_value":
        return {kv.key: any_value(kv.value) for kv in value.kvlist_value.values}
    return None if kind is None else getattr(value, kind)


def attributes(kvs: Any) -> dict[str, Any]:
    return {kv.key: any_value(kv.value) for kv in kvs}


def spans(message: Any) -> list[Any]:
    return [
        span
        for resource_spans in message.resource_spans
        for scope_spans in resource_spans.scope_spans
        for span in scope_spans.spans
    ]


def layers(message: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every attribute set of the request, named by where it sits: the resource's, the scope's,
    each span's and each span event's."""
    for r, resource_spans in enumerate(message.resource_spans):
        yield f"resource[{r}]", attributes(resource_spans.resource.attributes)
        for s, scope_spans in enumerate(resource_spans.scope_spans):
            yield f"scope[{r}.{s}]", attributes(scope_spans.scope.attributes)
            for span in scope_spans.spans:
                yield f"span {span.span_id.hex()} {span.name}", attributes(span.attributes)
                for e, event in enumerate(span.events):
                    yield f"event {span.span_id.hex()}[{e}]", attributes(event.attributes)


def span_count(body: bytes) -> tuple[int, int]:
    """The spans of a serialized request, and the `claude_code.llm_request` ones among them."""
    found = spans(parse_request(body))
    return len(found), sum(span.name == "claude_code.llm_request" for span in found)


# --- the fake Langfuse --------------------------------------------------------------------------


@dataclass(frozen=True)
class Seen:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes


@dataclass
class FakeLangfuse:
    """A Langfuse on 127.0.0.1: the OTLP ingestion answers `status` after `delay_s` (or never,
    with `hang`, until `release`); projects and v2 observations answer from the fixtures (the
    observations' `@@TRACE_ID@@` becomes the requested trace id, `@@SECRET_LIKE@@` `planted`'s
    secret-like value). Every request is recorded in `seen`."""

    planted: Planted = field(default_factory=Planted.new)
    status: int = 200
    delay_s: float = 0.0
    hang: bool = False
    seen: list[Seen] = field(default_factory=list)
    _released: threading.Event = field(default_factory=threading.Event)
    _server: ThreadingHTTPServer | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None, "the fake Langfuse is not started"
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def settings(self) -> LangfuseSettings:
        return LangfuseSettings(PUBLIC_KEY, SECRET_KEY, self.base_url)

    def env(self) -> dict[str, str]:
        return dict(zip(LANGFUSE_ENV, (PUBLIC_KEY, SECRET_KEY, self.base_url), strict=True))

    def ingested(self) -> list[Seen]:
        return [s for s in self.seen if s.method == "POST" and s.path == OTLP_PATH]

    def release(self) -> None:
        self._released.set()

    def start(self) -> "FakeLangfuse":
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self) -> Seen:
                url = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                seen = Seen(
                    method=self.command,
                    path=url.path,
                    query=parse_qs(url.query),
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=self.rfile.read(length) if length else b"",
                )
                fake.seen.append(seen)
                return seen

            def _reply(self, status: int, body: bytes, kind: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                seen = self._record()
                if seen.path != OTLP_PATH:
                    self._reply(404, b"{}", "application/json")
                    return
                if fake.hang:
                    fake._released.wait(30)
                    return
                if fake.delay_s:
                    fake._released.wait(fake.delay_s)
                self._reply(fake.status, b"", "application/x-protobuf")

            def do_GET(self) -> None:
                seen = self._record()
                if seen.path == PROJECTS_PATH:
                    self._reply(200, PROJECTS.read_bytes(), "application/json")
                elif seen.path == OBSERVATIONS_PATH:
                    trace_id = (seen.query.get("traceId") or [""])[0]
                    text = fake.planted.fill(OBSERVATIONS.read_text())
                    body = text.replace("@@TRACE_ID@@", trace_id).encode()
                    self._reply(200, body, "application/json")
                else:
                    self._reply(404, b"{}", "application/json")

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self.release()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


def basic_auth(public_key: str = PUBLIC_KEY, secret_key: str = SECRET_KEY) -> str:
    return "Basic " + base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()


def closed_port_url() -> str:
    """A 127.0.0.1 address nothing listens on: a connection to it is refused."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def post(url: str, body: bytes, method: str = "POST", timeout: float = 10) -> int | None:
    """POST (or `method`) the body as the CLI's exporter does; the status, None without one."""
    request = urllib.request.Request(
        url, data=body, method=method, headers={"Content-Type": "application/x-protobuf"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return None


# --- no network ---------------------------------------------------------------------------------


@dataclass
class NoNetwork:
    """Every AF_INET / AF_INET6 `connect`, `connect_ex` and `bind` of this process is refused
    and recorded; Unix sockets (asyncio's self-pipe is a `socketpair`) are left alone."""

    attempts: list[tuple[str, Any]] = field(default_factory=list)

    INET: ClassVar[frozenset[int]] = frozenset({socket.AF_INET, socket.AF_INET6})

    def install(self, monkeypatch: Any) -> "NoNetwork":
        for name in ("connect", "connect_ex", "bind"):
            original: Callable[..., Any] = getattr(socket.socket, name)
            monkeypatch.setattr(socket.socket, name, self._guard(name, original))
        return self

    def _guard(self, name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        guard = self

        def guarded(sock: socket.socket, address: Any, *args: Any) -> Any:
            if sock.family in guard.INET:
                guard.attempts.append((name, address))
                raise PermissionError(f"no network in this test: {name} {address!r}")
            return original(sock, address, *args)

        return guarded


# --- stand-ins for the run's exporter and relay -------------------------------------------------


class Forbidden:
    """Stands in for `tracing.parent_exporter` or `trace_relay.TraceRelay` where neither may be
    built: every call is recorded (`run` may swallow the error), then refused."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        raise AssertionError(f"{self.name} was built with the export off")


RELAY_ENDPOINT = "http://127.0.0.1:47231/v1/traces"


@dataclass
class FakeRelay:
    """One `TraceRelay` as `run` uses it, opening no port: records how it was built, started and
    stopped, and reports `report`. `FakeRelay.factory(report)` stands in for the class."""

    langfuse: LangfuseSettings
    tier: TraceTier
    secrets: Sequence[str] = ()
    drain_timeout_s: float = 10.0
    report: RelayReport = field(default_factory=lambda: RelayReport(9, 3, ()))
    started: int = 0
    stopped: int = 0

    def start(self) -> str:
        self.started += 1
        return RELAY_ENDPOINT

    def stop(self) -> RelayReport:
        self.stopped += 1
        return self.report

    @staticmethod
    def factory(
        report: RelayReport | None = None,
    ) -> tuple[Callable[..., "FakeRelay"], list["FakeRelay"]]:
        built: list[FakeRelay] = []

        def make(
            langfuse: LangfuseSettings,
            tier: TraceTier,
            secrets: Sequence[str] = (),
            drain_timeout_s: float = 10.0,
        ) -> FakeRelay:
            relay = FakeRelay(langfuse, tier, tuple(secrets), drain_timeout_s)
            if report is not None:
                relay.report = report
            built.append(relay)
            return relay

        return make, built


class Traced:
    """The stand-ins of a run with the export on: the relays built and the parent exporters."""

    def __init__(self, monkeypatch: Any, report: RelayReport | None = None) -> None:
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        factory, self.relays = FakeRelay.factory(report)
        self.exporters: list[InMemorySpanExporter] = []
        self.settings: list[LangfuseSettings] = []

        def parent_exporter(settings: LangfuseSettings) -> InMemorySpanExporter:
            self.settings.append(settings)
            self.exporters.append(InMemorySpanExporter())
            return self.exporters[-1]

        monkeypatch.setattr(trace_relay, "TraceRelay", factory)
        monkeypatch.setattr(tracing, "parent_exporter", parent_exporter)

    def finished(self) -> list[Any]:
        return [span for e in self.exporters for span in e.get_finished_spans()]


REPORT_WITH_ERRORS = RelayReport(
    forwarded_spans=5,
    forwarded_llm_requests=2,
    errors=(ExportError("http_error", 500), ExportError("connection_error", None)),
)


# --- the CLI's environment ----------------------------------------------------------------------


class Spawned(Exception):
    """Raised in place of starting the CLI, once its environment is recorded."""


def cli_env(built: ClaudeAgentOptions, monkeypatch: Any) -> dict[str, str]:
    """The environment the SDK starts the CLI with: what it inherits, under the options' env
    (as tests/test_context_window.py `cli_env`)."""
    import anyio
    import pytest
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    seen: dict[str, str] = {}

    async def open_process(*_: Any, env: dict[str, str], **__: Any) -> None:
        seen.update(env)
        raise Spawned

    monkeypatch.setattr(anyio, "open_process", open_process)
    monkeypatch.setenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")
    transport = SubprocessCLITransport(prompt="p", options=built)
    transport._cli_path = "claude"  # pyright: ignore[reportPrivateUsage]

    async def connect() -> None:
        with pytest.raises(CLIConnectionError):
            await transport.connect()

    anyio.run(connect)
    assert seen, "the SDK never reached the CLI's spawn"
    return seen


# The CLI reads a switch as true for "1", "true", "yes" or "on" (agent.py, `sdk_env`'s note).
TRUTHY = frozenset({"1", "true", "yes", "on"})
CONTENT_SWITCHES = ("OTEL_LOG_USER_PROMPTS", "OTEL_LOG_TOOL_DETAILS", "OTEL_LOG_TOOL_CONTENT")


def truthy_content_switches(env: Mapping[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in env.items()
        if name.startswith("OTEL_LOG_") and value.strip().lower() in TRUTHY
    }


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]
