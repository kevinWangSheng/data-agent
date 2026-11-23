"""The local relay between the Claude Code CLI and Langfuse (.scratch/observability/spec.md
"本地中转"): it takes the CLI's OTLP requests on 127.0.0.1, rewrites them by tier and forwards them
with the Langfuse keys, which the CLI never gets.

The contract is ticket 111's (tests/acceptance/test_observability_relay.py); ticket 113 implements
it. `run` and `evals.dab.run_once` look up `TraceRelay` on this module, so a test puts a recording
stand-in in its place.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from data_agent.tracing import LangfuseSettings, TraceTier


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
        raise NotImplementedError("ticket 113")


def rewrite(body: bytes, *, tier: TraceTier, ids: AnonIds, secrets: Sequence[str]) -> bytes:
    """A serialized `ExportTraceServiceRequest` rewritten by `tier`'s rules. `secrets` are the
    run's known secret values (`events.known_secrets`), which never leave in the output."""
    raise NotImplementedError("ticket 113")


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
        raise NotImplementedError("ticket 113")

    def start(self) -> str:
        """Opens the port; returns the endpoint the CLI exports to,
        `http://127.0.0.1:<port>/v1/traces`."""
        raise NotImplementedError("ticket 113")

    def stop(self) -> RelayReport:
        """Waits for the requests already taken to be forwarded (at most `drain_timeout_s`),
        then closes the port."""
        raise NotImplementedError("ticket 113")
