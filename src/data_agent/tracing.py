"""Exporting a run's trace to Langfuse (.scratch/observability/spec.md): the switch, the CLI's
export settings and the run's parent span `data-agent.run`.

The contract is ticket 111's (tests/acceptance/test_observability_*.py); ticket 112 implements it.
OpenTelemetry is imported only for type checking here and inside the functions that build the
export: with the export off, a run imports none of the SDK or its exporter.
"""

import base64
import json
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal, cast

from data_agent.events import mask

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import Span, TracerProvider
    from opentelemetry.sdk.trace.export import SpanExporter

    from data_agent.trace_relay import RelayReport, TraceRelay

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
    so a shell's own destination, headers or content switch never applies, and so are the Langfuse
    keys, which the CLI never gets (the relay adds them). Without a trace, nothing."""
    if trace is None:
        return {}
    blanked = [name for name in inherited if name.startswith("OTEL_")]
    return (
        dict.fromkeys([*blanked, *CLI_BLANKED_ENV, *LANGFUSE_ENV], "")
        | CLI_EXPORT_ENV
        | {CLI_ENDPOINT_ENV: trace.endpoint}
        | dict.fromkeys(CONTENT_SWITCHES[trace.tier], "1")
    )


def parent_exporter(settings: LangfuseSettings) -> "SpanExporter":
    """The exporter of the run's parent span, sending to `settings.base_url`. `run` looks it up on
    this module on every call, so a test puts an in-memory exporter in its place."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(
        endpoint=settings.base_url + OTLP_PATH,
        headers=settings.headers() | {"x-langfuse-ingestion-version": INGESTION_VERSION},
        timeout=PARENT_TIMEOUT_S,
    )


def project_id(settings: LangfuseSettings) -> str | None:
    """The key pair's project (`GET /api/public/projects`, its first entry, as Langfuse's Python
    SDK reads it in `Langfuse._get_project_id`); None when Langfuse cannot say."""
    request = urllib.request.Request(settings.base_url + PROJECTS_PATH, headers=settings.headers())
    try:
        with urllib.request.urlopen(request, timeout=PROJECTS_TIMEOUT_S) as response:
            data = json.loads(response.read())["data"]
        found = data[0]["id"]
    except (OSError, ValueError, LookupError, TypeError):
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


def parent_attributes(tier: TraceTier, facts: RunFacts, secrets: Sequence[str]) -> Attributes:
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
        "langfuse.observation.input": mask(facts.task, secrets),
    }
    if facts.conversation is not None and facts.turn is not None:
        attributes |= {"conversation": facts.conversation, "turn": facts.turn}
    return attributes


def result_attributes(
    tier: TraceTier, result: Mapping[str, Any], answer: str | None, secrets: Sequence[str]
) -> Attributes:
    """The parent span's attributes at the end: the `result` event's tokens and `cost_usd`, the
    report's own figures. The dev tier also gets the run's answer, the one place the model's
    answer reaches Langfuse (`CONTENT_SWITCHES`)."""
    usage = {
        "input": int(result.get("uncached_input_tokens") or 0),
        "cache_read": int(result.get("cached_input_tokens") or 0),
        "output": int(result.get("output_tokens") or 0),
    }
    attributes: Attributes = {
        "langfuse.observation.usage_details": json.dumps(usage),
        "langfuse.observation.cost_details": json.dumps(
            {"total": float(result.get("cost_usd") or 0.0)}
        ),
    }
    if tier == "full" and answer:
        attributes["langfuse.observation.output"] = mask(answer, secrets)
    return attributes


class RunExport:
    """One run's export: the local relay the CLI sends its spans to, and the parent span, which
    goes to Langfuse on its own TracerProvider (the process's global one is left alone). Use it
    as a context manager: `start` before the session, `active()` around it, `finish` once the CLI
    has exited; leaving the block stops whatever is still running, on any path.

    The relay and the exporter are looked up on their modules here, so a test's stand-ins
    apply."""

    def __init__(self, settings: LangfuseSettings, tier: TraceTier, secrets: Sequence[str]):
        from data_agent import trace_relay

        self.settings = settings
        self.tier: TraceTier = tier
        self.secrets = tuple(secrets)
        self.relay: TraceRelay = trace_relay.TraceRelay(settings, tier, self.secrets)
        self._started = False
        self._report: RelayReport | None = None
        self._provider: TracerProvider | None = None
        self._span: Span | None = None
        self._project: str | None = None

    def __enter__(self) -> "RunExport":
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._close()

    def start(self, facts: RunFacts) -> CliTrace:
        """Opens the relay and starts the parent span; the CLI is to export to the trace this
        returns. Blocks for Langfuse's project id (`PROJECTS_TIMEOUT_S` at most)."""
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON

        endpoint = self.relay.start()
        self._started = True
        # Given outright, so no OTEL_* variable of the process changes the resource or sampling.
        provider = TracerProvider(
            resource=Resource({"service.name": SERVICE_NAME}), sampler=ALWAYS_ON
        )
        provider.add_span_processor(SimpleSpanProcessor(parent_exporter(self.settings)))
        self._provider = provider
        attributes = parent_attributes(self.tier, facts, self.secrets)
        self._span = cast(
            "Span", provider.get_tracer("data_agent").start_span(SPAN_NAME, attributes=attributes)
        )
        self._project = project_id(self.settings)
        return CliTrace(endpoint=endpoint, tier=self.tier)

    def active(self) -> Any:
        """A context manager making the parent span the current one: the SDK hands it to the CLI
        it starts inside (`TRACEPARENT`), so the CLI's spans are its children."""
        from opentelemetry import trace

        assert self._span is not None, "RunExport.start comes first"
        return trace.use_span(self._span, end_on_exit=False)

    def finish(
        self, result: Mapping[str, Any], answer: str | None
    ) -> list[tuple[str, dict[str, Any]]]:
        """Stops the relay (the CLI has exited), ends and sends the parent span; returns the
        events to write: one `trace_export_error` per batch the relay dropped, then `trace`."""
        assert self._span is not None, "RunExport.start comes first"
        report = self._stop_relay()
        self._span.set_attributes(result_attributes(self.tier, result, answer, self.secrets))
        trace_id = format(self._span.get_span_context().trace_id, "032x")
        self._close()
        events: list[tuple[str, dict[str, Any]]] = [
            ("trace_export_error", {"error_type": error.error_type, "status": error.status})
            for error in report.errors
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

    def _stop_relay(self) -> "RelayReport":
        from data_agent.trace_relay import RelayReport

        if self._report is None:
            self._report = self.relay.stop() if self._started else RelayReport(0, 0, ())
        return self._report

    def _close(self) -> None:
        try:
            self._stop_relay()
        finally:
            if self._span is not None and self._span.is_recording():
                self._span.end()
            if self._provider is not None:
                self._provider.shutdown()
                self._provider = None
