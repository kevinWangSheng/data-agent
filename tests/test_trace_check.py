"""`data-agent trace-check` below the acceptance tests (ticket 112): finding the event file by run
id, paging through the observations, and failing without printing a key or a value."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from data_agent import trace_check
from data_agent.trace_check import CheckFailed, find_events
from data_agent.tracing import LANGFUSE_ENV

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
SECRET = "sk-lf-unit-secret-0001"

# Two pages: the second is asked for by the first's cursor.
PAGES: dict[str, dict[str, Any]] = {
    "": {
        "data": [
            {"id": "a", "parentObservationId": None, "type": "SPAN", "name": "data-agent.run"},
            {
                "id": "b",
                "parentObservationId": "a",
                "type": "GENERATION",
                "name": "claude_code.llm_request",
                "startTime": "2",
                "usageDetails": {"input": 3, "output": 4},
                "costDetails": {"total": 0.1},
                "metadata": {"attributes": {"stop_reason": "end_turn-value"}},
            },
        ],
        "meta": {"cursor": "page-2"},
    },
    "page-2": {
        "data": [
            {
                "id": "c",
                "parentObservationId": "a",
                "type": "TOOL",
                "name": "claude_code.tool",
                "startTime": "3",
                "input": '{"sql": "SELECT secret_column_value"}',
                "output": "",
                "metadata": {"attributes": {"tool_name": "mcp__data__query_database"}},
            },
            # A parent outside the trace: printed at the top.
            {"id": "d", "parentObservationId": "elsewhere", "type": "EVENT", "name": "orphan"},
        ],
        "meta": {},
    },
}


class Langfuse:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.queries: list[dict[str, list[str]]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                query = parse_qs(urlsplit(self.path).query)
                fake.queries.append(query)
                page = PAGES[(query.get("cursor") or [""])[0]]
                body = json.dumps(page).encode() if fake.status == 200 else SECRET.encode()
                self.send_response(fake.status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def env(self) -> dict[str, str]:
        url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return dict(zip(LANGFUSE_ENV, ("pk-lf-unit", SECRET, url), strict=True))


@pytest.fixture
def langfuse() -> Iterator[Langfuse]:
    fake = Langfuse()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


def events(path: Path, trace: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"seq": 0, "type": "version"}]
    if trace:
        lines.append({"seq": 1, "type": "trace", "trace_id": TRACE_ID, "url": "u"})
    lines.append({"seq": 2, "type": "result"})
    path.write_text("".join(json.dumps(e) + "\n" for e in lines))
    return path


def test_a_run_id_is_found_under_the_runs_directory(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    found = events(runs / "web" / "r1" / "run-20261123T100000Z-aaaa1111.jsonl")
    assert find_events("run-20261123T100000Z-aaaa1111", runs) == found
    assert find_events(str(found), tmp_path / "elsewhere") == found
    with pytest.raises(CheckFailed, match="no event file"):
        find_events("run-missing", runs)
    events(runs / "other" / "run-20261123T100000Z-aaaa1111.jsonl")
    with pytest.raises(CheckFailed, match="2 event files"):
        find_events("run-20261123T100000Z-aaaa1111", runs)


def test_every_page_is_read_and_only_the_structure_printed(
    tmp_path: Path, langfuse: Langfuse
) -> None:
    lines = trace_check.check(str(events(tmp_path / "run.jsonl")), langfuse.env())
    assert [q.get("cursor") for q in langfuse.queries] == [None, ["page-2"]]
    # `io` so the check can say whether an input or output is there (ticket 121), never what.
    assert langfuse.queries[0]["fields"] == ["core,basic,io,metadata,usage"]
    text = "\n".join(lines)
    assert lines[0] == f"trace {TRACE_ID}: 4 observations"
    assert "  SPAN data-agent.run  usage: none  cost: no  input: no  output: no" in lines
    assert (
        "    GENERATION claude_code.llm_request  usage: input, output  cost: yes"
        "  input: no  output: no"
    ) in lines
    assert "    TOOL claude_code.tool  usage: none  cost: no  input: yes  output: no" in lines
    assert "      metadata.attributes: tool_name" in lines
    assert "  EVENT orphan  usage: none  cost: no  input: no  output: no" in lines
    assert "  1 TOOL claude_code.tool" in lines
    for value in ("end_turn-value", "mcp__data__query_database", SECRET, "secret_column_value"):
        assert value not in text


def test_langfuse_errors_and_missing_settings_fail_without_the_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(events(tmp_path / "run.jsonl"))
    failing = Langfuse(status=401)
    try:
        assert trace_check.main(path, failing.env()) == 2
    finally:
        failing.server.shutdown()
        failing.server.server_close()
    err = capsys.readouterr().err
    assert "HTTP 401" in err and SECRET not in err
    assert trace_check.main(path, {}) == 2
    assert "DATA_AGENT_LANGFUSE_BASE_URL" in capsys.readouterr().err


def test_a_run_without_a_trace_event_fails_before_asking_langfuse(
    tmp_path: Path, langfuse: Langfuse
) -> None:
    with pytest.raises(CheckFailed, match="no trace event"):
        trace_check.check(str(events(tmp_path / "run.jsonl", trace=False)), langfuse.env())
    assert langfuse.queries == []


def test_a_trace_without_observations_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No observations (not ingested yet, or another project's keys) is no pass."""
    monkeypatch.setattr(trace_check, "observations", lambda *_: iter(()))
    env: dict[str, str] = dict(zip(LANGFUSE_ENV, ("pk", "sk", "http://127.0.0.1:9"), strict=True))
    with pytest.raises(CheckFailed, match="no observations"):
        trace_check.check(str(events(tmp_path / "run.jsonl")), env)
