"""Exporting a run's trace to Langfuse (.scratch/observability/spec.md): the switch, the CLI's
export settings and the run's parent span `data-agent.run`.

The contract is ticket 111's (tests/acceptance/test_observability_*.py); ticket 112 implements it.
OpenTelemetry is imported only for type checking here: with the export off, a run imports none of
it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export import SpanExporter

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


@dataclass(frozen=True)
class LangfuseSettings:
    public_key: str
    secret_key: str
    base_url: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "LangfuseSettings | None":
        """The settings when all of LANGFUSE_ENV are set and not empty; None (the export is off)
        when any is missing, the address included: there is no default Langfuse."""
        raise NotImplementedError("ticket 112")


@dataclass(frozen=True)
class CliTrace:
    """What `build_options` needs to have the Claude Code CLI export its spans: the local relay's
    endpoint (`TraceRelay.start`) and the tier, which sets the CLI's content switches. A run
    without one (None) sets no export variable."""

    endpoint: str
    tier: TraceTier


def parent_exporter(settings: LangfuseSettings) -> "SpanExporter":
    """The exporter of the run's parent span, sending to `settings.base_url`. `run` looks it up on
    this module on every call, so a test puts an in-memory exporter in its place."""
    raise NotImplementedError("ticket 112")
