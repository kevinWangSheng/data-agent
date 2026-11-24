"""Exporting a run's trace to Langfuse (.scratch/observability/spec.md): the switch, the CLI's
export settings and the run's parent span `data-agent.run`.

The contract is ticket 111's (tests/acceptance/test_observability_*.py); ticket 112 implements it.
OpenTelemetry is imported only for type checking here and inside the functions that build the
export: with the export off, a run imports none of the SDK or its exporter.
"""

import base64
import http.client
import json
import time
import urllib.request
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from data_agent.events import mask

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan, Span, TracerProvider
    from opentelemetry.sdk.trace.export import SpanExporter

    from data_agent.trace_relay import ExportError, RelayReport, ToolInputs, TraceRelay

# The two tiers (spec "覆盖范围"): `full`, the dev tier, records everything but credentials and the
# model endpoint; `structure`, the held-out tier, lets only allowlisted structure through.
type TraceTier = Literal["full", "structure"]
TRACE_TIERS: tuple[TraceTier, ...] = ("full", "structure")

# The export is on only when all three are set and not empty (spec "开关与配置"). Prefixed like
# every other variable of ours: a bare LANGFUSE_* is read by other Langfuse tools on the machine.
LANGFUSE_ENV = (
    "DATA_AGENT_LANGFUSE_PUBLIC_KEY",
    "DATA_AGENT_LANGFUSE_SECRET_KEY",
    "DATA_AGENT_LANGFUSE_BASE_URL",
)

# Langfuse's OTLP ingestion (https://langfuse.com/integrations/native/opentelemetry, read
# 2026-11-23) and its projects endpoint, the one the trace URL needs.
OTLP_PATH = "/api/public/otel/v1/traces"
PROJECTS_PATH = "/api/public/projects"
INGESTION_VERSION = "4"
PARENT_TIMEOUT_S = 10.0
PROJECTS_TIMEOUT_S = 5.0

SPAN_NAME = "data-agent.run"
SERVICE_NAME = "data-agent"

# What `build_options` gives the CLI to export its spans (spec "传给 CLI 的环境变量"). Traces only;
# a short interval, as the CLI's docs advise for short tasks, whose flush on exit is brief.
CLI_EXPORT_ENV = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
    "OTEL_TRACES_EXPORTER": "otlp",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_LOGS_EXPORTER": "none",
    "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
    "OTEL_TRACES_EXPORT_INTERVAL": "1000",
}
CLI_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
# The CLI's content switches each tier turns on (https://code.claude.com/docs/en/monitoring-usage,
# read 2026-11-23): prompts, tool parameters, tool output. None of the documented switches puts the
# model's answer on a standard trace span: `OTEL_LOG_RAW_API_BODIES` emits log events, which the
# export leaves off (`OTEL_LOGS_EXPORTER=none`), and `response.model_output` is on the detailed
# beta tracing only. The dev tier's parent span carries the run's answer instead
# (`result_attributes`). Content is cut at the CLI's default 60 KB.
CONTENT_SWITCHES: dict[TraceTier, tuple[str, ...]] = {
    "full": ("OTEL_LOG_USER_PROMPTS", "OTEL_LOG_TOOL_DETAILS", "OTEL_LOG_TOOL_CONTENT"),
    "structure": (),
}
# Variables without the OTEL_ prefix that would send the CLI's spans elsewhere or with more in
# them (the detailed beta tracing, same docs): blank whenever the export is on.
CLI_BLANKED_ENV = ("ENABLE_BETA_TRACING_DETAILED", "BETA_TRACING_ENDPOINT")


@dataclass(frozen=True)
class LangfuseSettings:
    public_key: str
    secret_key: str
    base_url: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "LangfuseSettings | None":
        """The settings when all of LANGFUSE_ENV are set and not empty; None (the export is off)
        when any is missing, the address included: there is no default Langfuse."""
        values = [env.get(name, "") for name in LANGFUSE_ENV]
        if not all(values):
            return None
        public_key, secret_key, base_url = values
        return cls(public_key, secret_key, base_url.rstrip("/"))

    def headers(self) -> dict[str, str]:
        """Basic auth with the key pair, as Langfuse's OTLP endpoint and public API take it."""
        pair = base64.b64encode(f"{self.public_key}:{self.secret_key}".encode()).decode()
        return {"Authorization": f"Basic {pair}"}


def langfuse_keys(env: Mapping[str, str]) -> list[str]:
    """The Langfuse key values `env` holds, set or not as a pair: what the event file masks."""
    return [env[name] for name in LANGFUSE_ENV[:2] if env.get(name)]


@dataclass(frozen=True)
class CliTrace:
    """What `build_options` needs to have the Claude Code CLI export its spans: the local relay's
    endpoint (`TraceRelay.start`) and the tier, which sets the CLI's content switches. A run
    without one (None) sets no export variable."""

    endpoint: str
    tier: TraceTier


def cli_env(trace: CliTrace | None, inherited: Mapping[str, str]) -> dict[str, str]:
    """The CLI's export variables for `trace`, over the SDK's merge of `inherited` (the process
    environment) and the options' env: every inherited `OTEL_*` and `CLI_BLANKED_ENV` is blanked,
    so a shell's own destination, headers or content switch never applies. The Langfuse keys are
    blanked with or without a trace: the CLI and its tool servers never get them (the relay adds
    them). Without a trace, no export variable is set."""
    keys: dict[str, str] = dict.fromkeys(LANGFUSE_ENV, "")
    if trace is None:
        return keys
    blanked = [name for name in inherited if name.startswith("OTEL_")]
    return (
        dict.fromkeys([*blanked, *CLI_BLANKED_ENV], "")
        | keys
        | CLI_EXPORT_ENV
        | {CLI_ENDPOINT_ENV: trace.endpoint}
        | dict.fromkeys(CONTENT_SWITCHES[trace.tier], "1")
    )


class ParentExporter:
    """The exporter of the run's parent span: Langfuse's OTLP endpoint at `settings.base_url`.
    `send` says what went wrong, which the OTLP exporter's own result does not (only success or
    failure), by keeping the HTTP status of each attempt, retries included."""

    def __init__(self, settings: LangfuseSettings):
        # requests comes with the exporter: its public `session` parameter is a requests.Session.
        import requests
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        self.last_status: int | None = None
        exporter = self

        class Session(requests.Session):
            """Each attempt starts with no status, so one that got no answer leaves none."""

            def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
                exporter.last_status = None
                response = super().send(request, **kwargs)
                exporter.last_status = response.status_code
                return response

        self._otlp = OTLPSpanExporter(
            endpoint=settings.base_url + OTLP_PATH,
            headers=settings.headers() | {"x-langfuse-ingestion-version": INGESTION_VERSION},
            timeout=PARENT_TIMEOUT_S,
            session=Session(),
        )

    def send(self, spans: Sequence["ReadableSpan"]) -> "ExportError | None":
        """Exports `spans`; what went wrong, as the relay reports a batch: `http_error` when the
        last attempt's answer was not 2xx (a 3xx too: the exporter counts it as success but
        follows no redirect, so the span never arrived), `connection_error` when it got no
        answer. Every other failure without an answer (the exporter refusing to encode or
        send) is reported as `connection_error` as well."""
        from opentelemetry.sdk.trace.export import SpanExportResult

        from data_agent.trace_relay import ExportError

        self.last_status = None
        sent = self._otlp.export(spans) is SpanExportResult.SUCCESS
        status = self.last_status
        if status is not None and not 200 <= status < 300:
            return ExportError("http_error", status)
        return None if sent else ExportError("connection_error", None)

    def shutdown(self) -> None:
        self._otlp.shutdown()


def parent_exporter(settings: LangfuseSettings) -> "SpanExporter | ParentExporter":
    """The exporter of the run's parent span. `run` looks it up on this module on every call, so
    a test puts an in-memory exporter in its place (hence `SpanExporter`)."""
    return ParentExporter(settings)


def project_id(settings: LangfuseSettings) -> str | None:
    """The key pair's project (`GET /api/public/projects`, its first entry, as Langfuse's Python
    SDK reads it in `Langfuse._get_project_id`); None when Langfuse cannot say."""
    request = urllib.request.Request(settings.base_url + PROJECTS_PATH, headers=settings.headers())
    try:
        with urllib.request.urlopen(request, timeout=PROJECTS_TIMEOUT_S) as response:
            data = json.loads(response.read())["data"]
        found = data[0]["id"]
    except (OSError, ValueError, LookupError, TypeError, http.client.HTTPException):
        return None
    return found if isinstance(found, str) and found else None


def trace_url(settings: LangfuseSettings, trace_id: str, project: str | None) -> str:
    """Where Langfuse shows the trace: `<base>/project/<project>/traces/<trace id>`, as Langfuse's
    Python SDK builds it (`Langfuse.get_trace_url`, langfuse-python 2cea520, read 2026-11-23).
    Without the project, the web app's own `/trace/<trace id>` page, which redirects to it
    (langfuse web/src/pages/trace/[traceId].tsx, 67b035c)."""
    if project is None:
        return f"{settings.base_url}/trace/{trace_id}"
    return f"{settings.base_url}/project/{project}/traces/{trace_id}"


type Attributes = dict[str, str | int | float | Sequence[str]]

# The run's token total on the parent span: metadata, not `langfuse.observation.usage_details`,
# which Langfuse would add to the CLI's GENERATIONs' own and count every token twice (human,
# 2026-11-23, ticket 121). The keys are the `result` event's.
USAGE_TOTAL = "langfuse.observation.metadata.usage_total"
USAGE_TOTAL_KEYS = ("uncached_input_tokens", "cached_input_tokens", "output_tokens")
# The dev tier's observations from the run's own events, under the parent span (ticket 121):
# each of the model's texts, and a tool call's input no CLI span took (`RunExport._children`).
TEXT_SPAN = "assistant.message"
INPUT_SPAN = "tool.input"


class Emitter(Protocol):
    """What takes a run's events: `events.EventLog`, or one `RunExport.observing` wraps."""

    def emit(self, type: str, **fields: Any) -> None: ...


class ObservedLog:
    """`log`, whose every event `export` also observes, after it is written."""

    def __init__(self, log: Emitter, export: "RunExport") -> None:
        self._log = log
        self._export = export

    def emit(self, type: str, **fields: Any) -> None:
        self._log.emit(type, **fields)
        self._export.observe(type, fields)


@dataclass(frozen=True)
class RunFacts:
    """What the parent span says of a run (spec "父 span 与 trace 组织"). `session` is the
    conversation's id for a turn, else the run's own id."""

    run_id: str
    session: str
    task_type: str
    config: str
    model: str
    skill_version: str
    git_commit: str
    task: str
    conversation: str | None = None
    turn: int | None = None


@dataclass(frozen=True)
class Masks:
    """What the parent span's content is masked with, as the event file is (`events.mask`):
    secrets anywhere, names (a database's single-label hosts) as whole words only."""

    secrets: Sequence[str] = ()
    names: Sequence[str] = ()

    def __call__(self, text: str) -> str:
        return mask(text, self.secrets, self.names)


def parent_attributes(tier: TraceTier, facts: RunFacts, masks: Masks) -> Attributes:
    """The parent span's attributes at the start. The held-out tier names no run, task or
    conversation: only the config, the model, the task type and the tier."""
    attributes: Attributes = {
        "langfuse.trace.name": SPAN_NAME,
        "langfuse.trace.tags": [facts.task_type, facts.config, facts.model],
        "trace_tier": tier,
        "task_type": facts.task_type,
        "config": facts.config,
        "model": facts.model,
    }
    if tier == "structure":
        return attributes
    attributes |= {
        "langfuse.session.id": facts.session,
        "run_id": facts.run_id,
        "skill_version": facts.skill_version,
        "git_commit": facts.git_commit,
        "langfuse.observation.input": masks(facts.task),
    }
    if facts.conversation is not None and facts.turn is not None:
        attributes |= {"conversation": facts.conversation, "turn": facts.turn}
    return attributes


def result_attributes(
    tier: TraceTier, result: Mapping[str, Any], answer: str | None, masks: Masks
) -> Attributes:
    """The parent span's attributes at the end: the `result` event's `cost_usd`, the report's own
    figure (no cost when the result has none), and its token counts as metadata (`USAGE_TOTAL`).
    The dev tier also gets the run's answer, the final one of the model's messages (each is an
    `assistant.message` observation, `RunExport._children`)."""
    usage = {key: int(result.get(key) or 0) for key in USAGE_TOTAL_KEYS}
    attributes: Attributes = {USAGE_TOTAL: json.dumps(usage)}
    cost = result.get("cost_usd")
    if cost is not None:
        attributes["langfuse.observation.cost_details"] = json.dumps({"total": float(cost)})
    if tier == "full" and answer:
        attributes["langfuse.observation.output"] = masks(answer)
    return attributes


class RunExport:
    """One run's export: the local relay the CLI sends its spans to, and the parent span, which
    goes to Langfuse on its own TracerProvider (the process's global one is left alone). Use it
    as a context manager: `start` before the session, `active()` around it, `finish` once the CLI
    has exited; leaving the block stops whatever is still running, on any path.

    A failed export never fails the run (spec "出错不影响运行"): `start` returns None and
    `finish` an `error` event instead, the failure in `failure`. Langfuse refusing the parent
    span, or not answering, is one more `trace_export_error` from `finish`, as for the relay's
    batches. The relay gets the run's secrets; the parent span is masked with them and with
    `names` (`Masks`).

    The relay and the exporter are looked up on their modules here, so a test's stand-ins
    apply."""

    def __init__(
        self,
        settings: LangfuseSettings,
        tier: TraceTier,
        secrets: Sequence[str],
        names: Sequence[str] = (),
    ):
        self.settings = settings
        self.tier: TraceTier = tier
        self.secrets = tuple(secrets)
        self.masks = Masks(self.secrets, tuple(names))
        self.relay: TraceRelay | None = None
        self.failure: str | None = None
        self._report: RelayReport | None = None
        self._provider: TracerProvider | None = None
        self._exporter: SpanExporter | ParentExporter | None = None
        self._span: Span | None = None
        self._project: str | None = None
        # The dev tier's content from the run's own events (`observe`): the tool calls' inputs,
        # which the relay puts on the CLI's tool spans, and the model's texts, `(time_ns,
        # sequence, text, parent)`, which `finish` sends under the parent span.
        self._inputs: ToolInputs | None = None
        self._texts: list[tuple[int, int, str, str | None]] = []
        self._sequence = 0

    def __enter__(self) -> "RunExport":
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._close()

    def start(self, facts: RunFacts) -> CliTrace | None:
        """Opens the relay and starts the parent span; the CLI is to export to the trace this
        returns, and to take `context_env()`. Blocks for Langfuse's project id
        (`PROJECTS_TIMEOUT_S` at most). None when any of it failed (`failure` says what): the
        run goes on without the export."""
        try:
            return self._start(facts)
        except Exception as exc:
            self._failed(exc)
            self._close()
            return None

    def _start(self, facts: RunFacts) -> CliTrace:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON

        from data_agent import trace_relay

        self.relay = trace_relay.TraceRelay(self.settings, self.tier, self.secrets)
        if self.tier == "full":
            self._inputs = trace_relay.ToolInputs()
            self.relay.tool_inputs = self._inputs
        endpoint = self.relay.start()
        # Given outright, so no OTEL_* variable of the process changes the resource or sampling.
        # No span processor: `_end_span` exports the parent span itself, to see the result (a
        # span processor drops it).
        provider = TracerProvider(
            resource=Resource({"service.name": SERVICE_NAME}), sampler=ALWAYS_ON
        )
        self._provider = provider
        self._exporter = parent_exporter(self.settings)
        attributes = parent_attributes(self.tier, facts, self.masks)
        self._span = cast(
            "Span", provider.get_tracer("data_agent").start_span(SPAN_NAME, attributes=attributes)
        )
        if not self._span.get_span_context().is_valid:
            # OTEL_SDK_DISABLED in the process: the provider hands out a no-op tracer.
            raise RuntimeError("OpenTelemetry is disabled in this process (OTEL_SDK_DISABLED)")
        self._project = project_id(self.settings)
        return CliTrace(endpoint=endpoint, tier=self.tier)

    def context_env(self) -> dict[str, str]:
        """The parent span as the CLI's `TRACEPARENT`, for the options' env, which wins over what
        the SDK would inject: the SDK injects through the process's propagators, which an
        inherited `OTEL_PROPAGATORS` may have turned off, and then keeps an inherited
        `TRACEPARENT`. A blank `TRACESTATE`, so no inherited one pairs with it."""
        assert self._span is not None, "RunExport.start comes first"
        context = self._span.get_span_context()
        traceparent = f"00-{context.trace_id:032x}-{context.span_id:016x}-01"
        return {"TRACEPARENT": traceparent, "TRACESTATE": ""}

    def active(self) -> AbstractContextManager[object]:
        """A context manager making the parent span the current one while the SDK starts the
        CLI. An exception passing through is not recorded on the span: its message and stack
        would reach Langfuse past the held-out tier's allowlist."""
        from opentelemetry import trace

        assert self._span is not None, "RunExport.start comes first"
        return trace.use_span(
            self._span, end_on_exit=False, record_exception=False, set_status_on_exception=False
        )

    def observing(self, log: "Emitter") -> "Emitter":
        """`log`, with each event also given to `observe` on the dev tier; `log` itself on the
        held-out tier, which sends none of the content."""
        return log if self.tier == "structure" else ObservedLog(log, self)

    def observe(self, kind: str, fields: Mapping[str, Any]) -> None:
        """One of the run's events, on the dev tier (ticket 121): a `tool_start`'s input goes to
        the relay for the call's `claude_code.tool` span, a `text` to `finish`'s
        `assistant.message` observations; both masked here (`Masks` and the relay's secret
        shapes), the input as JSON. `thinking` is left out. Never raises: a failure goes to
        `failure` and leaves the run alone."""
        if self.tier != "full" or self._span is None or not self._span.is_recording():
            return
        try:
            self._observe(kind, fields)
        except Exception as exc:
            self._failed(exc)

    def _observe(self, kind: str, fields: Mapping[str, Any]) -> None:
        now = time.time_ns()
        if kind == "tool_start" and self._inputs is not None:
            call = fields.get("tool_use_id")
            if isinstance(call, str):
                text = json.dumps(self._clean(fields.get("input")), ensure_ascii=False)
                self._inputs.put(call, text, now)
        elif kind == "text":
            parent = fields.get("parent")
            self._sequence += 1
            text = self._clean(str(fields.get("text") or ""))
            self._texts.append((now, self._sequence, text, parent if parent else None))

    def _clean(self, value: Any) -> Any:
        """`value` with every string masked, leaf by leaf, before any JSON escapes a secret."""
        from data_agent.trace_relay import mask_shapes

        if isinstance(value, str):
            return mask_shapes(self.masks(value))
        if isinstance(value, Mapping):
            return {str(k): self._clean(v) for k, v in cast(Mapping[Any, Any], value).items()}
        if isinstance(value, list | tuple):
            return [self._clean(item) for item in cast(Sequence[Any], value)]
        if value is None or isinstance(value, bool | int | float):
            return value
        return self._clean(str(value))

    def finish(
        self, result: Mapping[str, Any], answer: str | None
    ) -> list[tuple[str, dict[str, Any]]]:
        """Stops the relay (the CLI has exited), ends and sends the parent span; returns the
        events to write: one `trace_export_error` per batch the relay dropped and one when the
        parent span did not arrive, then `trace`.
        When that fails, one `error` event (`source: trace`) instead."""
        try:
            return self._finish(result, answer)
        except Exception as exc:
            self._failed(exc)
            self._close()
            return [("error", {"source": "trace", "error": self.failure})]

    def _finish(
        self, result: Mapping[str, Any], answer: str | None
    ) -> list[tuple[str, dict[str, Any]]]:
        assert self._span is not None, "RunExport.start comes first"
        report = self._stop_relay()
        try:
            children = self._children()
        except Exception as exc:
            # The run's own span still goes: losing the texts is no reason to lose the trace.
            self._failed(exc)
            children = []
        self._span.set_attributes(result_attributes(self.tier, result, answer, self.masks))
        trace_id = format(self._span.get_span_context().trace_id, "032x")
        parent_error = self._end_span(children)
        self._shutdown()
        errors = [*report.errors, *([parent_error] if parent_error else [])]
        events: list[tuple[str, dict[str, Any]]] = [
            ("trace_export_error", {"error_type": error.error_type, "status": error.status})
            for error in errors
        ]
        events.append(
            (
                "trace",
                {
                    "trace_id": trace_id,
                    "url": trace_url(self.settings, trace_id, self._project),
                    "forwarded_spans": report.forwarded_spans,
                    "forwarded_llm_requests": report.forwarded_llm_requests,
                },
            )
        )
        return events

    def _children(self) -> list["ReadableSpan"]:
        """The dev tier's observations under the parent span, ended at their events' times: one
        `assistant.message` per text (a verifier's names its `parent`), and one `tool.input` per
        input no CLI span took (the span reached the relay before the event did, or never), with
        the call's `tool_use_id`. Each has a `sequence`, the events' order, should two share a
        time. The relay has stopped, so nothing claims an input any more."""
        from opentelemetry import trace

        assert self._span is not None and self._provider is not None
        tracer = self._provider.get_tracer("data_agent")
        context = trace.set_span_in_context(self._span)
        unclaimed = [] if self._inputs is None else self._inputs.unclaimed()
        planned: list[tuple[int, str, Attributes]] = []
        for at, sequence, text, parent in self._texts:
            attributes: Attributes = {"langfuse.observation.output": text, "sequence": sequence}
            if parent is not None:
                attributes["parent"] = parent
            planned.append((at, TEXT_SPAN, attributes))
        for call, text, at in unclaimed:
            self._sequence += 1
            attributes = {
                "tool_use_id": call,
                "langfuse.observation.input": text,
                "sequence": self._sequence,
            }
            planned.append((at, INPUT_SPAN, attributes))
        children: list[ReadableSpan] = []
        for at, name, attributes in planned:
            span = tracer.start_span(name, context=context, attributes=attributes, start_time=at)
            span.end(end_time=at)
            children.append(cast("ReadableSpan", span))
        return children

    def _failed(self, exc: Exception) -> None:
        if self.failure is None:
            self.failure = f"the trace export failed ({type(exc).__name__}: {exc})"

    def _stop_relay(self) -> "RelayReport":
        from data_agent.trace_relay import RelayReport

        if self._report is None:
            self._report = RelayReport(0, 0, ())
            if self.relay is not None:
                self._report = self.relay.stop()
        return self._report

    def _close(self) -> None:
        """Stops whatever still runs; never raises (a failure goes to `failure`)."""
        for step in (self._stop_relay, self._end_span, self._shutdown):
            try:
                step()
            except Exception as exc:
                self._failed(exc)

    def _end_span(self, children: Sequence["ReadableSpan"] = ()) -> "ExportError | None":
        """Ends the parent span and sends it with `children`, once; what went wrong
        (`ParentExporter.send`). A test's stand-in exporter says only success or failure."""
        from opentelemetry.sdk.trace.export import SpanExportResult

        from data_agent.trace_relay import ExportError

        if self._span is None or not self._span.is_recording():
            return None
        self._span.end()
        exporter = self._exporter
        assert exporter is not None, "the exporter comes with the span"
        spans = [*children, cast("ReadableSpan", self._span)]
        if isinstance(exporter, ParentExporter):
            return exporter.send(spans)
        if exporter.export(spans) is SpanExportResult.SUCCESS:
            return None
        return ExportError("connection_error", None)

    def _shutdown(self) -> None:
        if self._exporter is not None:
            exporter, self._exporter = self._exporter, None
            exporter.shutdown()
        if self._provider is not None:
            provider, self._provider = self._provider, None
            provider.shutdown()
