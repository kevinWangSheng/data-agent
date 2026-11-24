"""The export's pieces in data_agent.tracing (ticket 112), below what the acceptance tests of
ticket 111 pin: the CLI's variables over an inherited environment, the parent span's exporter and
attributes, the trace URL, and `RunExport`'s clean-up on every path.

No network beyond 127.0.0.1: Langfuse is a small recording HTTP server.
"""

import base64
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from data_agent import trace_relay, tracing
from data_agent.trace_relay import ExportError, RelayReport
from data_agent.tracing import CliTrace, LangfuseSettings, Masks, RunExport, RunFacts

REPO_ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "http://127.0.0.1:40001/v1/traces"


@dataclass
class Recorder:
    """A Langfuse on 127.0.0.1: records every request; projects answer `projects` (None: 500),
    the OTLP ingestion `post_status`."""

    projects: Any = field(default_factory=lambda: {"data": [{"id": "proj-1"}]})
    post_status: int = 200
    seen: list[tuple[str, str, dict[str, str], bytes]] = field(default_factory=list)
    server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self.server is not None
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def settings(self) -> LangfuseSettings:
        return LangfuseSettings("pk-lf-1", "sk-lf-1", self.url)

    def start(self) -> "Recorder":
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self, status: int, body: bytes) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                recorder.seen.append(
                    (
                        self.command,
                        self.path,
                        {k.lower(): v for k, v in self.headers.items()},
                        self.rfile.read(length) if length else b"",
                    )
                )
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if recorder.projects is None:
                    self._answer(500, b"")
                else:
                    self._answer(200, json.dumps(recorder.projects).encode())

            def do_POST(self) -> None:
                self._answer(recorder.post_status, b"")

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        assert self.server is not None
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def langfuse() -> Iterator[Recorder]:
    recorder = Recorder().start()
    yield recorder
    recorder.stop()


# --- the switch and the CLI's variables ---------------------------------------------------------


def test_the_base_url_loses_its_trailing_slash() -> None:
    env: dict[str, str] = dict(
        zip(tracing.LANGFUSE_ENV, ("pk", "sk", "https://lf.example/"), strict=True)
    )
    settings = LangfuseSettings.from_env(env)
    assert settings is not None and settings.base_url == "https://lf.example"


def test_without_a_trace_the_cli_gets_no_export_variable_and_no_langfuse_key() -> None:
    env = tracing.cli_env(None, {"OTEL_EXPORTER_OTLP_HEADERS": "x=y"})
    assert env == dict.fromkeys(tracing.LANGFUSE_ENV, "")


@pytest.mark.parametrize("tier", tracing.TRACE_TIERS)
def test_every_inherited_otel_variable_and_the_beta_tracing_are_blanked(tier: str) -> None:
    inherited = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example",
        "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Basic eA==",
        "OTEL_RESOURCE_ATTRIBUTES": "team=x",
        "OTEL_LOG_RAW_API_BODIES": "1",
        "BETA_TRACING_ENDPOINT": "https://beta.example",
        "PATH": "/usr/bin",
    }
    env = tracing.cli_env(CliTrace(ENDPOINT, tier), inherited)  # type: ignore[arg-type]
    for name in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_RESOURCE_ATTRIBUTES",
        "OTEL_LOG_RAW_API_BODIES",
        *tracing.CLI_BLANKED_ENV,
        *tracing.LANGFUSE_ENV,
    ):
        assert env[name] == "", name
    assert "PATH" not in env
    assert env["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == ENDPOINT
    on = {name for name, value in env.items() if value == "1" and name.startswith("OTEL_LOG_")}
    assert on == set(tracing.CONTENT_SWITCHES[tier])  # type: ignore[index]


# --- the parent span ----------------------------------------------------------------------------


FACTS = RunFacts(
    run_id="run-20261123T100000Z-abcd1234",
    session="run-20261123T100000Z-abcd1234",
    task_type="query",
    config="no-context",
    model="deepseek-flash",
    skill_version="skills-0001",
    git_commit="c0ffee",
    task="How many customers? key sk-planted-0001",
)
RESULT = {
    "uncached_input_tokens": 5,
    "cached_input_tokens": 5,
    "output_tokens": 7,
    "cost_usd": 0.25,
}


def test_the_dev_tier_parent_carries_the_task_and_answer_masked() -> None:
    masks = Masks(["sk-planted-0001"])
    start = tracing.parent_attributes("full", FACTS, masks)
    end = tracing.result_attributes("full", RESULT, "42, says sk-planted-0001", masks)
    assert start["langfuse.observation.input"] == "How many customers? key [REDACTED]"
    assert end["langfuse.observation.output"] == "42, says [REDACTED]"
    assert start["langfuse.session.id"] == FACTS.session
    assert json.loads(str(end["langfuse.observation.usage_details"])) == {
        "input": 5,
        "cache_read": 5,
        "output": 7,
    }
    assert json.loads(str(end["langfuse.observation.cost_details"])) == {"total": 0.25}


def test_the_held_out_tier_parent_holds_only_the_spec_list() -> None:
    start = tracing.parent_attributes("structure", FACTS, Masks())
    end = tracing.result_attributes("structure", RESULT, "42", Masks())
    assert set(start) == {
        "langfuse.trace.name",
        "langfuse.trace.tags",
        "trace_tier",
        "task_type",
        "config",
        "model",
    }
    assert set(end) == {"langfuse.observation.usage_details", "langfuse.observation.cost_details"}


def test_a_conversation_turn_names_its_conversation_on_the_dev_tier() -> None:
    turn = RunFacts(**{**FACTS.__dict__, "session": "conv-1", "conversation": "conv-1", "turn": 2})
    start = tracing.parent_attributes("full", turn, Masks())
    assert (start["langfuse.session.id"], start["conversation"], start["turn"]) == (
        "conv-1",
    ) * 2 + (2,)


def test_the_trace_url_is_the_projects_or_the_redirect_page() -> None:
    settings = LangfuseSettings("pk", "sk", "https://lf.example")
    assert tracing.trace_url(settings, "ab" * 16, "proj-1") == (
        f"https://lf.example/project/proj-1/traces/{'ab' * 16}"
    )
    assert tracing.trace_url(settings, "ab" * 16, None) == f"https://lf.example/trace/{'ab' * 16}"


def test_the_project_id_is_the_first_project_and_none_on_any_failure(langfuse: Recorder) -> None:
    assert tracing.project_id(langfuse.settings()) == "proj-1"
    [(method, path, headers, _)] = langfuse.seen
    assert (method, path) == ("GET", tracing.PROJECTS_PATH)
    assert headers["authorization"] == "Basic " + base64.b64encode(b"pk-lf-1:sk-lf-1").decode()
    for answer in (None, {"data": []}, {"nothing": 1}, [1]):
        langfuse.projects = answer
        assert tracing.project_id(langfuse.settings()) is None
    unreachable = LangfuseSettings("pk", "sk", "http://127.0.0.1:9")
    assert tracing.project_id(unreachable) is None


class StandInRelay:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.started = self.stopped = 0

    def start(self) -> str:
        self.started += 1
        return ENDPOINT

    def stop(self) -> RelayReport:
        self.stopped += 1
        return RelayReport(4, 1, (ExportError("parse_error", None),))


def test_the_parent_span_goes_to_langfuse_with_its_keys_whatever_the_shell_says(
    monkeypatch: pytest.MonkeyPatch, langfuse: Recorder
) -> None:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,  # pyright: ignore[reportAttributeAccessIssue]
    )

    monkeypatch.setattr(trace_relay, "TraceRelay", StandInRelay)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://127.0.0.1:9/v1/traces")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_off")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "host.name=shell-host")
    with RunExport(langfuse.settings(), "full", ["sk-planted-0001"]) as export:
        trace = export.start(FACTS)
        assert trace == CliTrace(ENDPOINT, "full")
        events = export.finish(RESULT, "42")
    [(_, posted, headers, body)] = [s for s in langfuse.seen if s[0] == "POST"]
    assert posted == tracing.OTLP_PATH
    assert headers["authorization"] == "Basic " + base64.b64encode(b"pk-lf-1:sk-lf-1").decode()
    assert headers["x-langfuse-ingestion-version"] == "4"
    request = ExportTraceServiceRequest()
    request.ParseFromString(body)
    [resource_spans] = request.resource_spans
    resource = {kv.key: kv.value.string_value for kv in resource_spans.resource.attributes}
    assert resource == {"service.name": "data-agent"}
    [span] = resource_spans.scope_spans[0].spans
    assert span.name == "data-agent.run"
    trace_id = span.trace_id.hex()
    assert events == [
        ("trace_export_error", {"error_type": "parse_error", "status": None}),
        (
            "trace",
            {
                "trace_id": trace_id,
                "url": f"{langfuse.url}/project/proj-1/traces/{trace_id}",
                "forwarded_spans": 4,
                "forwarded_llm_requests": 1,
            },
        ),
    ]
    assert export.relay.stopped == 1  # type: ignore[attr-defined]


def test_a_parent_span_langfuse_rejects_is_a_trace_export_error(
    monkeypatch: pytest.MonkeyPatch, langfuse: Recorder
) -> None:
    """The returned 112 (PR #125): the parent span's failed export was swallowed and `trace`
    written as if it had arrived."""
    monkeypatch.setattr(trace_relay, "TraceRelay", StandInRelay)
    langfuse.post_status = 500
    with RunExport(langfuse.settings(), "full", []) as export:
        assert export.start(FACTS) is not None
        events = export.finish(RESULT, "42")
    assert [s[0] for s in langfuse.seen].count("POST") == 1  # 500 is not retried
    assert events[:2] == [
        ("trace_export_error", {"error_type": "parse_error", "status": None}),
        ("trace_export_error", {"error_type": "http_error", "status": 500}),
    ]
    assert [name for name, _ in events] == ["trace_export_error"] * 2 + ["trace"]
    assert export.failure is None


def test_a_parent_span_langfuse_redirects_is_an_http_error(
    monkeypatch: pytest.MonkeyPatch, langfuse: Recorder
) -> None:
    """The exporter takes any 2xx-3xx as success and follows no redirect: a 302 (an http:// base
    URL bounced to https, a proxy's login page) never delivered the span."""
    monkeypatch.setattr(trace_relay, "TraceRelay", StandInRelay)
    langfuse.post_status = 302
    with RunExport(langfuse.settings(), "full", []) as export:
        assert export.start(FACTS) is not None
        events = export.finish(RESULT, "42")
    assert ("trace_export_error", {"error_type": "http_error", "status": 302}) in events
    assert events[-1][0] == "trace"


def test_a_parent_span_langfuse_never_answers_is_a_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(trace_relay, "TraceRelay", StandInRelay)
    monkeypatch.setattr(tracing, "PARENT_TIMEOUT_S", 0.5)
    with RunExport(LangfuseSettings("pk", "sk", "http://127.0.0.1:9"), "full", []) as export:
        assert export.start(FACTS) is not None
        events = export.finish(RESULT, "42")
    assert ("trace_export_error", {"error_type": "connection_error", "status": None}) in events
    assert events[-1][0] == "trace"


def test_a_run_that_breaks_still_stops_the_relay_and_ends_the_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    monkeypatch.setattr(trace_relay, "TraceRelay", StandInRelay)
    monkeypatch.setattr(tracing, "parent_exporter", lambda _: exporter)
    monkeypatch.setattr(tracing, "project_id", lambda _: None)
    export = RunExport(LangfuseSettings("pk", "sk", "http://127.0.0.1:9"), "structure", [])
    with pytest.raises(RuntimeError), export:
        export.start(FACTS)
        raise RuntimeError("the session broke")
    assert export.relay.stopped == 1  # type: ignore[attr-defined]
    assert [s.name for s in exporter.get_finished_spans()] == ["data-agent.run"]


def test_a_relay_never_started_is_never_built(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trace_relay, "TraceRelay", StandInRelay)
    with RunExport(LangfuseSettings("pk", "sk", "http://127.0.0.1:9"), "full", []) as export:
        pass
    assert export.relay is None and export.failure is None


# --- the export off -----------------------------------------------------------------------------


OFF_RUN = """
import asyncio, sys
from pathlib import Path
import duckdb
from claude_agent_sdk import ResultMessage
from data_agent import agent
from data_agent.agent import Task
from data_agent.run_config import load_config

async def session(*, prompt, options, **_):
    yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
        num_turns=1, session_id="s", usage={"input_tokens": 1, "output_tokens": 1},
        total_cost_usd=0.0, result="42")

agent.query = session
work = Path(sys.argv[1])
duckdb.connect(str(work / "x.duckdb")).close()
task = Task(text="q", type="query", workspace=work, duckdb=work / "x.duckdb")
env = {"DATA_AGENT_API_KEY": "sk-test", "DATA_AGENT_LANGFUSE_PUBLIC_KEY": "pk"}
outcome = asyncio.run(agent.run(task, load_config("no-context"), work / "events", env))
assert outcome.finished, outcome
ours = ("opentelemetry.sdk", "opentelemetry.exporter", "data_agent.trace_relay")
loaded = sorted(m for m in sys.modules if m.startswith(ours))
print(",".join(loaded))
"""


def test_a_run_with_the_export_off_loads_no_opentelemetry_sdk(tmp_path: Path) -> None:
    """In a process of its own: the tests around it have long imported the SDK. The Agent SDK may
    import `opentelemetry` (the API) itself, to hand a current span to the CLI; that is not ours."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("DATA_AGENT_LANGFUSE_")}
    found = subprocess.run(
        [sys.executable, "-c", OFF_RUN, str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=120,
    )
    assert found.returncode == 0, found.stderr
    assert found.stdout.strip() == ""


# --- review round 1: the export never fails the run, and the process's OTEL_* cannot steer it ---


def test_a_database_host_name_is_masked_as_a_word_on_the_parent_span() -> None:
    """A single-label host (`url_names`) is masked as the event file masks it: whole words only,
    so a Postgres host `db` leaves "dbt" alone."""
    facts = RunFacts(**{**FACTS.__dict__, "task": "Fix the dbt model on db"})
    start = tracing.parent_attributes("full", facts, Masks((), ["db"]))
    assert start["langfuse.observation.input"] == "Fix the dbt model on [REDACTED]"


def test_a_result_without_a_cost_sends_none() -> None:
    end = tracing.result_attributes("full", {"output_tokens": 1, "cost_usd": None}, None, Masks())
    assert "langfuse.observation.cost_details" not in end


def test_a_garbled_projects_answer_is_no_project() -> None:
    """An answer that is not HTTP at all (`http.client.HTTPException`, no OSError)."""
    import socket

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def answer() -> None:
        conn, _ = server.accept()
        conn.recv(4096)
        conn.sendall(b"garbage\r\n\r\n")
        conn.close()

    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.getsockname()[1]}"
    try:
        assert tracing.project_id(LangfuseSettings("pk", "sk", url)) is None
    finally:
        thread.join(5)
        server.close()


@dataclass
class Scripted:
    """A session that records its options and ends with a result, or raises `error`."""

    options: list[Any] = field(default_factory=list)
    error: BaseException | None = None

    async def __call__(self, *, prompt: str, options: Any, **_: Any) -> Any:
        from claude_agent_sdk import ResultMessage

        del prompt
        self.options.append(options)
        if self.error is not None:
            raise self.error
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            usage={"input_tokens": 3, "output_tokens": 7},
            total_cost_usd=0.01,
            result="42",
        )


async def traced_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, relay: Any = None
) -> tuple[Any, list[dict[str, Any]], Scripted, Any]:
    """`agent.run` with the export on: the relay a stand-in, the parent span in memory."""
    import duckdb
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from data_agent import agent
    from data_agent.agent import Task
    from data_agent.run_config import load_config

    exporter = InMemorySpanExporter()
    session = Scripted()
    monkeypatch.setattr(agent, "query", session)
    monkeypatch.setattr(trace_relay, "TraceRelay", relay or StandInRelay)
    monkeypatch.setattr(tracing, "parent_exporter", lambda _: exporter)
    monkeypatch.setattr(tracing, "project_id", lambda _: None)
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    task = Task(text="How many?", type="query", workspace=tmp_path, duckdb=db)
    env = {"DATA_AGENT_API_KEY": "sk-test"} | dict(
        zip(tracing.LANGFUSE_ENV, ("pk-lf-1", "sk-lf-1", "http://127.0.0.1:9"), strict=True)
    )
    outcome = await agent.run(task, load_config("no-context"), tmp_path / "events", env)
    events = [json.loads(line) for line in outcome.events_path.read_text().splitlines()]
    return outcome, events, session, exporter


async def test_the_cli_gets_the_parent_span_whatever_the_processs_propagators(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`OTEL_PROPAGATORS=none` turns the SDK's own injection off, and an inherited TRACEPARENT
    would then stay: the options' env names the parent span itself."""
    monkeypatch.setenv("OTEL_PROPAGATORS", "none")
    monkeypatch.setenv("TRACEPARENT", "00-" + "1" * 32 + "-" + "2" * 16 + "-01")
    monkeypatch.setenv("TRACESTATE", "vendor=shell")
    _, events, session, exporter = await traced_run(monkeypatch, tmp_path)
    [span] = exporter.get_finished_spans()
    context = span.get_span_context()
    [options] = session.options
    assert options.env["TRACEPARENT"] == f"00-{context.trace_id:032x}-{context.span_id:016x}-01"
    assert options.env["TRACESTATE"] == ""
    [trace] = [e for e in events if e["type"] == "trace"]
    assert trace["trace_id"] == f"{context.trace_id:032x}"


async def test_opentelemetry_disabled_in_the_process_leaves_the_run_untraced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    built: list[StandInRelay] = []

    def relay(*args: Any) -> StandInRelay:
        built.append(StandInRelay(*args))
        return built[-1]

    outcome, events, session, _ = await traced_run(monkeypatch, tmp_path, relay)
    assert outcome.finished
    assert not [e for e in events if e["type"] in ("trace", "trace_export_error")]
    [error] = [e for e in events if e["type"] == "error"]
    assert error["source"] == "trace" and "OTEL_SDK_DISABLED" in error["error"]
    [options] = session.options
    assert not [k for k, v in options.env.items() if k.startswith("OTEL_EXPORTER") and v]
    assert [(r.started, r.stopped) for r in built] == [(1, 1)]
    assert events[-1]["type"] == "result"


class RefusingRelay(StandInRelay):
    def start(self) -> str:
        raise OSError("no port")


class BrokenStopRelay(StandInRelay):
    def stop(self) -> RelayReport:
        raise RuntimeError("the relay broke")


@pytest.mark.parametrize("relay", [RefusingRelay, BrokenStopRelay], ids=["start", "stop"])
async def test_a_relay_that_breaks_leaves_the_run_to_its_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, relay: type[StandInRelay]
) -> None:
    outcome, events, _, _ = await traced_run(monkeypatch, tmp_path, relay)
    assert outcome.finished
    [error] = [e for e in events if e["type"] == "error"]
    assert error["source"] == "trace"
    assert not [e for e in events if e["type"] == "trace"]
    assert [e["type"] for e in events].count("result") == 1 and events[-1]["type"] == "result"


def test_an_exception_through_the_active_span_is_not_recorded_on_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    monkeypatch.setattr(trace_relay, "TraceRelay", StandInRelay)
    monkeypatch.setattr(tracing, "parent_exporter", lambda _: exporter)
    monkeypatch.setattr(tracing, "project_id", lambda _: None)
    with RunExport(LangfuseSettings("pk", "sk", "http://127.0.0.1:9"), "structure", []) as export:
        assert export.start(FACTS) is not None
        with pytest.raises(TimeoutError), export.active():
            raise TimeoutError("/Users/someone/secret/path")
    [span] = exporter.get_finished_spans()
    assert list(span.events) == []
    assert span.status.description is None


def test_with_the_export_off_the_shells_langfuse_keys_never_reach_the_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import duckdb

    from data_agent import agent
    from data_agent.agent import Task
    from data_agent.run_config import load_config

    for name, value in zip(tracing.LANGFUSE_ENV[:2], ("pk-lf-shell", "sk-lf-shell"), strict=True):
        monkeypatch.setenv(name, value)
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    options = agent.build_options(
        Task(text="q", type="query", workspace=tmp_path, duckdb=db),
        load_config("full"),
        agent.ModelSettings.from_env({"DATA_AGENT_API_KEY": "sk-test"}),
        [],
        lambda *_, **__: None,
    )
    merged = {**os.environ, **options.env}  # the SDK's merge (subprocess_cli.py)
    assert {name: merged[name] for name in tracing.LANGFUSE_ENV} == dict.fromkeys(
        tracing.LANGFUSE_ENV, ""
    )
    assert tracing.langfuse_keys(dict(os.environ)) == ["pk-lf-shell", "sk-lf-shell"]
