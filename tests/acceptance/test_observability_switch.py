"""The export's switch, the CLI's export variables and the run's parent span
(.scratch/observability/spec.md "开关与配置", "传给 CLI 的环境变量", "父 span 与 trace 组织";
"Testing Decisions": 开关, CLI 环境变量, 父 span).

Written by ticket 111. Ticket 112 implements them and removes `TICKET_112`; nothing else here
changes. The tests with the export off carry no mark: they pass now and guard that an export left
off does nothing at all.

No model and no network: `agent.query` is a scripted session (tests/test_agent_outcomes.py), the
relay is `FakeRelay` and the parent span goes to an `InMemorySpanExporter` (`run` looks both up on
their modules, `trace_relay.TraceRelay` and `tracing.parent_exporter`). Langfuse is
`FakeLangfuse`, in case the trace URL needs the project id (`GET /api/public/projects`).

Langfuse's attribute names (https://langfuse.com/integrations/native/opentelemetry, read
2026-11-23): `langfuse.trace.name` (string), `langfuse.trace.tags` (`string[]`),
`langfuse.session.id` (string), `langfuse.observation.usage_details` and
`langfuse.observation.cost_details` (JSON strings).
"""

import json
from collections.abc import AsyncIterator, Iterator
from itertools import combinations
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import ClaudeAgentOptions, CLIConnectionError
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from observability_support import (
    CONTENT_SWITCHES,
    LANGFUSE_ENV,
    PUBLIC_KEY,
    RELAY_ENDPOINT,
    REPORT_WITH_ERRORS,
    SECRET_KEY,
    ExportStandIns,
    FakeLangfuse,
    Forbidden,
    NoNetwork,
    cli_env,
    read_events,
    result_message,
    truthy_content_switches,
)
from opentelemetry import trace as otel_trace

from data_agent import agent, cli, trace_relay, tracing
from data_agent.agent import ConversationTurn, Outcome, Task
from data_agent.events import known_secrets
from data_agent.run_config import load_config
from data_agent.tracing import CliTrace, LangfuseSettings, TraceTier

TICKET_112 = pytest.mark.xfail(
    strict=True, reason="ticket 112: the switch, the CLI's variables, the parent span"
)

MODEL_KEY = "sk-test-model-key"
VALUES: dict[str, str] = dict(
    zip(LANGFUSE_ENV, (PUBLIC_KEY, SECRET_KEY, "https://langfuse.example"), strict=True)
)
# Every way of leaving at least one of the three out: none set, one, or two (the keys without the
# address among them).
INCOMPLETE = [
    subset for size in range(len(LANGFUSE_ENV)) for subset in combinations(LANGFUSE_ENV, size)
]
INCOMPLETE_IDS = [
    "+".join(n.removeprefix("DATA_AGENT_LANGFUSE_") for n in s) or "none" for s in INCOMPLETE
]
SHELL_EXPORT = {
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://collector.example/v1/traces",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Basic c2hlbGw6c2hlbGw=",
    "OTEL_LOG_USER_PROMPTS": "1",
    "OTEL_LOG_RAW_API_BODIES": "1",
}
EXPORT_SWITCHES = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
    "OTEL_TRACES_EXPORTER": "otlp",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_LOGS_EXPORTER": "none",
    "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
    "OTEL_TRACES_EXPORT_INTERVAL": "1000",
}


class Session:
    """A scripted session: records its options and the span active when it started."""

    def __init__(self) -> None:
        self.options: list[ClaudeAgentOptions] = []
        self.spans: list[otel_trace.SpanContext] = []

    async def __call__(
        self, *, prompt: str, options: ClaudeAgentOptions, **_: Any
    ) -> AsyncIterator[Any]:
        del prompt
        self.options.append(options)
        self.spans.append(otel_trace.get_current_span().get_span_context())
        yield result_message()


def query_task(tmp_path: Path, text: str = "How many customers are there?") -> Task:
    db = tmp_path / "x.duckdb"
    if not db.exists():
        duckdb.connect(str(db)).close()
    return Task(text=text, type="query", workspace=tmp_path, duckdb=db)


# --- the switch ---------------------------------------------------------------------------------


@TICKET_112
@pytest.mark.parametrize("empty", [False, True], ids=["missing", "empty"])
@pytest.mark.parametrize("present", INCOMPLETE, ids=INCOMPLETE_IDS)
def test_the_export_is_off_unless_all_three_variables_are_set(
    present: tuple[str, ...], empty: bool
) -> None:
    complete = tracing.LangfuseSettings.from_env(VALUES)
    assert complete == LangfuseSettings(PUBLIC_KEY, SECRET_KEY, "https://langfuse.example")
    env = {name: VALUES[name] for name in present}
    if empty:
        env |= {name: "" for name in LANGFUSE_ENV if name not in present}
    assert tracing.LangfuseSettings.from_env(env) is None


@pytest.mark.parametrize("present", INCOMPLETE, ids=INCOMPLETE_IDS)
async def test_with_the_export_off_a_run_builds_nothing_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, present: tuple[str, ...]
) -> None:
    """A guard: passes now and must keep passing."""
    session = Session()
    monkeypatch.setattr(agent, "query", session)
    exporter, relay = Forbidden("parent_exporter"), Forbidden("TraceRelay")
    monkeypatch.setattr(tracing, "parent_exporter", exporter)
    monkeypatch.setattr(trace_relay, "TraceRelay", relay)
    for name in LANGFUSE_ENV:
        if name in present:
            monkeypatch.setenv(name, VALUES[name])
        else:
            monkeypatch.delenv(name, raising=False)
    env = {"DATA_AGENT_API_KEY": MODEL_KEY} | {name: VALUES[name] for name in present}
    task = query_task(tmp_path)
    network = NoNetwork().install(monkeypatch)
    outcome = await agent.run(task, load_config("full"), tmp_path / "events", env)
    monkeypatch.undo()
    [options] = session.options
    assert [k for k in options.env if k.startswith("OTEL_")] == []
    assert "CLAUDE_CODE_ENABLE_TELEMETRY" not in options.env
    assert (exporter.calls, relay.calls) == ([], [])
    assert network.attempts == []
    events = read_events(outcome.events_path)
    assert [e for e in events if e["type"] in ("trace", "trace_export_error")] == []
    assert outcome.finished


def test_the_no_network_guard_refuses_and_records_a_loopback_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    network = NoNetwork().install(monkeypatch)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(PermissionError):
            sock.connect(("127.0.0.1", 9))
        with pytest.raises(PermissionError):
            sock.bind(("127.0.0.1", 0))
        with pytest.raises(PermissionError):
            sock.connect_ex(("127.0.0.1", 9))
    left, right = socket.socketpair()
    left.close()
    right.close()
    assert [name for name, _ in network.attempts] == ["connect", "bind", "connect_ex"]


# --- the CLI's variables ------------------------------------------------------------------------


SETTINGS = agent.ModelSettings.from_env({"DATA_AGENT_API_KEY": MODEL_KEY})


@pytest.fixture
def shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """A shell that exported its own OTel destination, a content switch and the Langfuse keys."""
    for name, value in (SHELL_EXPORT | VALUES).items():
        monkeypatch.setenv(name, value)


def traced_options(tmp_path: Path, tier: TraceTier) -> ClaudeAgentOptions:
    return agent.build_options(
        query_task(tmp_path),
        load_config("full"),
        SETTINGS,
        [],
        lambda *_, **__: None,
        trace=CliTrace(endpoint=RELAY_ENDPOINT, tier=tier),
    )


def assert_exports_to_the_relay(env: dict[str, str]) -> None:
    endpoint = env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
    assert endpoint.startswith("http://127.0.0.1:") and endpoint.endswith("/v1/traces")
    assert endpoint == RELAY_ENDPOINT  # the shell's is overridden
    assert {name: env.get(name) for name in EXPORT_SWITCHES} == EXPORT_SWITCHES
    for name, value in env.items():
        assert PUBLIC_KEY not in value and SECRET_KEY not in value, name
        # Only the variables of ours and of OTel: an unrelated one of the machine may say anything.
        if name.startswith(("OTEL_", "DATA_AGENT_", "CLAUDE_CODE_")):
            assert "Authorization" not in value, name


@TICKET_112
@pytest.mark.usefixtures("shell")
def test_the_dev_tier_exports_to_the_relay_with_the_content_switches_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = traced_options(tmp_path, "full")
    for env in (built.env, cli_env(built, monkeypatch)):
        assert_exports_to_the_relay(env)
        assert {name: env.get(name) for name in CONTENT_SWITCHES} == dict.fromkeys(
            CONTENT_SWITCHES, "1"
        )


@TICKET_112
@pytest.mark.usefixtures("shell")
def test_the_held_out_tier_exports_to_the_relay_with_no_content_switch_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = traced_options(tmp_path, "structure")
    for env in (built.env, cli_env(built, monkeypatch)):
        assert_exports_to_the_relay(env)
        assert truthy_content_switches(env) == {}


@pytest.fixture
def langfuse() -> Iterator[FakeLangfuse]:
    fake = FakeLangfuse().start()
    yield fake
    fake.stop()


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[ClaudeAgentOptions]:
    """The options of every session a run starts, stopped at the transport's `connect`
    (tests/test_effort_option.py)."""
    seen: list[ClaudeAgentOptions] = []

    async def connect(self: SubprocessCLITransport) -> None:
        seen.append(self._options)  # pyright: ignore[reportPrivateUsage]
        raise CLIConnectionError("stopped by test_observability_switch before the CLI starts")

    monkeypatch.setattr(SubprocessCLITransport, "connect", connect)
    monkeypatch.setenv("DATA_AGENT_API_KEY", MODEL_KEY)
    return seen


def run_argv(tmp_path: Path, *flags: str) -> list[str]:
    task = query_task(tmp_path)
    assert task.duckdb is not None
    return [
        "run",
        *("--type", "query", "--workspace", str(tmp_path), "--duckdb", str(task.duckdb)),
        *("--config", "no-context", "--events-dir", str(tmp_path / "events"), "--sandbox", "local"),
        *("--task", "How many customers are there?", *flags),
    ]


@TICKET_112
@pytest.mark.parametrize(
    ("flags", "tier"), [((), "full"), (("--trace-tier", "structure"), "structure")]
)
def test_the_run_commands_trace_tier_picks_the_sessions_tier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sessions: list[ClaudeAgentOptions],
    langfuse: FakeLangfuse,
    capsys: pytest.CaptureFixture[str],
    flags: tuple[str, ...],
    tier: TraceTier,
) -> None:
    traced = ExportStandIns(monkeypatch)
    for name, value in langfuse.env().items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit):
        cli.main(run_argv(tmp_path, *flags))
    assert "unrecognized arguments" not in capsys.readouterr().err
    [built] = sessions
    [relay] = traced.relays
    assert relay.tier == tier
    assert built.env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") == RELAY_ENDPOINT
    on = {name: built.env.get(name) for name in CONTENT_SWITCHES}
    if tier == "full":
        assert on == dict.fromkeys(CONTENT_SWITCHES, "1")
    else:
        assert truthy_content_switches(built.env) == {}


# --- the parent span ----------------------------------------------------------------------------


async def traced_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    langfuse: FakeLangfuse,
    traced: ExportStandIns,
    *,
    tier: TraceTier = "full",
    turn: ConversationTurn | None = None,
    events_dir: Path | None = None,
    text: str = "How many customers are there?",
    config: str = "full",
) -> tuple[Outcome, list[dict[str, Any]], Session, dict[str, str]]:
    session = Session()
    monkeypatch.setattr(agent, "query", session)
    env = {"DATA_AGENT_API_KEY": MODEL_KEY} | langfuse.env()
    outcome = await agent.run(
        query_task(tmp_path, text),
        load_config(config),
        events_dir or tmp_path / "events",
        env,
        turn=turn,
        trace_tier=tier,
    )
    return outcome, read_events(outcome.events_path), session, env


def root_span(traced: ExportStandIns, before: int = 0) -> Any:
    """The one `data-agent.run` span exported since `before` spans had been, with no parent."""
    runs = [s for s in traced.finished()[before:] if s.name == "data-agent.run"]
    assert len(runs) == 1, [s.name for s in traced.finished()]
    [run] = runs
    assert run.parent is None
    return run


def flat_values(attributes: Any) -> list[str]:
    values: list[str] = []
    for value in attributes.values():
        values += [str(v) for v in value] if isinstance(value, tuple) else [str(value)]
    return values


def one(events: list[dict[str, Any]], type: str) -> dict[str, Any]:
    [event] = [e for e in events if e["type"] == type]
    return event


def assert_usage_and_cost(attributes: Any, result: dict[str, Any]) -> None:
    usage = json.loads(attributes["langfuse.observation.usage_details"])
    assert usage["output"] == result["output_tokens"]
    total = (
        result["uncached_input_tokens"] + result["cached_input_tokens"] + result["output_tokens"]
    )
    assert sum(usage.values()) == total
    cost = json.loads(attributes["langfuse.observation.cost_details"])
    assert cost["total"] == pytest.approx(result["cost_usd"])


@TICKET_112
async def test_the_run_has_one_parent_span_named_tagged_and_priced_from_its_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, langfuse: FakeLangfuse
) -> None:
    traced = ExportStandIns(monkeypatch)
    # Not `full`, which is also the tier's name: the tag must be the config's.
    _, events, _, _ = await traced_run(monkeypatch, tmp_path, langfuse, traced, config="no-context")
    attributes = root_span(traced).attributes
    assert attributes["langfuse.trace.name"] == "data-agent.run"
    tags = attributes["langfuse.trace.tags"]
    assert isinstance(tags, tuple | list) and all(isinstance(tag, str) for tag in tags)
    version = one(events, "version")
    assert {"query", "no-context", version["model"]} <= set(tags)
    values = flat_values(attributes)
    assert version["skill_version"] in values
    assert version["git_commit"] in values
    assert_usage_and_cost(attributes, one(events, "result"))
    assert traced.settings == [langfuse.settings()]


@TICKET_112
async def test_the_trace_event_names_the_parent_spans_trace_and_the_relays_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, langfuse: FakeLangfuse
) -> None:
    traced = ExportStandIns(monkeypatch)
    outcome, events, session, env = await traced_run(monkeypatch, tmp_path, langfuse, traced)
    trace_id = format(root_span(traced).context.trace_id, "032x")
    trace = one(events, "trace")
    assert trace["trace_id"] == trace_id
    assert trace["url"].startswith(langfuse.base_url) and trace_id in trace["url"]
    assert (trace["forwarded_spans"], trace["forwarded_llm_requests"]) == (9, 3)
    # The span was active when the session started: the SDK hands it to the CLI (TRACEPARENT).
    [active] = session.spans
    assert active.is_valid and format(active.trace_id, "032x") == trace_id
    [relay] = traced.relays
    assert (relay.langfuse, relay.tier, relay.started, relay.stopped) == (
        langfuse.settings(),
        "full",
        1,
        1,
    )
    assert set(known_secrets(env)) <= set(relay.secrets)
    [options] = session.options
    assert options.env["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == RELAY_ENDPOINT
    assert outcome.finished


@TICKET_112
async def test_each_relay_error_is_one_trace_export_error_event_and_the_run_goes_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, langfuse: FakeLangfuse
) -> None:
    traced = ExportStandIns(monkeypatch, REPORT_WITH_ERRORS)
    outcome, events, _, _ = await traced_run(monkeypatch, tmp_path, langfuse, traced)
    errors = [e for e in events if e["type"] == "trace_export_error"]
    assert [(e["error_type"], e["status"]) for e in errors] == [
        ("http_error", 500),
        ("connection_error", None),
    ]
    for error in errors:
        assert set(error) == {"seq", "type", "ts", "error_type", "status"}
    trace = one(events, "trace")
    assert (trace["forwarded_spans"], trace["forwarded_llm_requests"]) == (5, 2)
    assert outcome.finished


@TICKET_112
async def test_a_conversations_turns_share_its_session_in_two_traces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, langfuse: FakeLangfuse
) -> None:
    traced = ExportStandIns(monkeypatch)
    conversation = tmp_path / "conv-2f9b61"
    conversation.mkdir()
    trace_ids = []
    for number in (1, 2):
        before = len(traced.finished())
        turn = ConversationTurn(conversation, number)
        await traced_run(monkeypatch, tmp_path, langfuse, traced, turn=turn)
        run = root_span(traced, before)
        assert run.attributes["langfuse.session.id"] == "conv-2f9b61"
        trace_ids.append(run.context.trace_id)
    assert trace_ids[0] != trace_ids[1]


@TICKET_112
async def test_each_single_run_is_a_session_of_its_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, langfuse: FakeLangfuse
) -> None:
    traced = ExportStandIns(monkeypatch)
    ids = []
    for _ in range(2):
        before = len(traced.finished())
        await traced_run(monkeypatch, tmp_path, langfuse, traced)
        ids.append(root_span(traced, before).attributes["langfuse.session.id"])
    assert all(ids) and ids[0] != ids[1]


@TICKET_112
async def test_the_held_out_tiers_parent_span_names_no_run_task_or_conversation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, langfuse: FakeLangfuse
) -> None:
    traced = ExportStandIns(monkeypatch)
    conversation = tmp_path / "conv-holdout-7c1d"
    conversation.mkdir()
    text = "Which customers churned after the March price change?"
    events_dir = tmp_path / "events-holdout-3e9a"
    outcome, events, _, _ = await traced_run(
        monkeypatch,
        tmp_path,
        langfuse,
        traced,
        tier="structure",
        turn=ConversationTurn(conversation, 1),
        events_dir=events_dir,
        text=text,
    )
    attributes = root_span(traced).attributes
    assert attributes["trace_tier"] == "structure"
    assert_usage_and_cost(attributes, one(events, "result"))
    assert "langfuse.session.id" not in attributes
    values = flat_values(attributes)
    for named in (outcome.events_path.stem, events_dir.name, conversation.name, text):
        assert not [v for v in values if named in v], named
    [relay] = traced.relays
    assert relay.tier == "structure"
