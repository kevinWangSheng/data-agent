"""`data-agent trace-check` (.scratch/observability/spec.md "读回 trace"; "Testing Decisions":
trace-check).

Written by ticket 111. Ticket 112 implements it and removes `TICKET_112`; nothing else here changes.

The command runs in-process against `FakeLangfuse`, which answers `GET /api/public/v2/observations`
from tests/fixtures/observability/observations.json (the response shape of Langfuse's fern
definition `ObservationsV2Response`: `data`, a list of `ObservationV2`, and `meta`; read
2026-11-23, see observability_support). The fixture holds content (a prompt, SQL, a query's
result, a model answer) in `input`, `output` and `metadata` values, and a secret-like value put in
at run time; none of it, nor any attribute value or the secret key, may be printed. The `run_id`
form of the argument is not tested: the spec does not say where to look a run id up.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from observability_support import (
    OBSERVATIONS,
    OBSERVATIONS_PATH,
    SECRET_KEY,
    FakeLangfuse,
    basic_auth,
)

from data_agent import cli

TICKET_112 = pytest.mark.xfail(strict=True, reason="ticket 112: data-agent trace-check")

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


@pytest.fixture
def langfuse(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeLangfuse]:
    fake = FakeLangfuse().start()
    for name, value in fake.env().items():
        monkeypatch.setenv(name, value)
    yield fake
    fake.stop()


def events_file(path: Path, *, trace: bool) -> Path:
    events: list[dict[str, Any]] = [
        {"seq": 0, "type": "version", "ts": "2026-11-23T10:00:00+00:00", "config": "full"},
        {"seq": 1, "type": "task", "ts": "2026-11-23T10:00:00+00:00", "task": "t"},
        {"seq": 2, "type": "result", "ts": "2026-11-23T10:00:20+00:00", "cost_usd": 0.0123},
    ]
    if trace:
        events.append(
            {
                "seq": 3,
                "type": "trace",
                "ts": "2026-11-23T10:00:21+00:00",
                "trace_id": TRACE_ID,
                "url": f"https://langfuse.example/trace/{TRACE_ID}",
                "forwarded_spans": 3,
                "forwarded_llm_requests": 1,
            }
        )
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


def trace_check(path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    try:
        cli.main(["trace-check", str(path)])
        code = 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    out = capsys.readouterr()
    return code, out.out + out.err


def observations() -> list[dict[str, Any]]:
    return json.loads(OBSERVATIONS.read_text())["data"]


def strings_in(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():  # pyright: ignore[reportUnknownVariableType]
            yield from strings_in(item)
    elif isinstance(value, list):
        for item in value:  # pyright: ignore[reportUnknownVariableType]
            yield from strings_in(item)


@TICKET_112
def test_trace_check_prints_the_traces_structure_and_nothing_of_its_content(
    tmp_path: Path, langfuse: FakeLangfuse, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = trace_check(events_file(tmp_path / "run.jsonl", trace=True), capsys)
    assert code == 0, output
    [request] = [s for s in langfuse.seen if s.path == OBSERVATIONS_PATH]
    assert (request.method, request.query.get("traceId")) == ("GET", [TRACE_ID])
    assert request.headers["authorization"] == basic_auth()
    for observation in observations():
        assert observation["name"] in output
        assert observation["type"] in output
        # Langfuse keeps the OTel attributes it does not map under `metadata.attributes`
        # (docs/research/2026-11-23-langfuse-usage-mapping.md §1); `resourceAttributes` is the
        # fixture's guess for the resource's, unconfirmed.
        for group in observation["metadata"].values():
            for attribute in group:
                assert attribute in output, attribute
    # Content, attribute values, the secret-like value and the secret key stay out.
    hidden = {SECRET_KEY, langfuse.planted.secret_like}
    for observation in observations():
        for part in ("input", "output", "metadata"):
            # Short values ("data", "400") would match words of the structure itself.
            hidden |= {
                s for s in strings_in(observation.get(part)) if len(s) >= 6 and "@@" not in s
            }
    for value in sorted(hidden):
        assert value not in output, value


@TICKET_112
@pytest.mark.usefixtures("langfuse")
def test_trace_check_fails_on_a_run_without_a_trace_event(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = trace_check(events_file(tmp_path / "traced.jsonl", trace=True), capsys)
    assert code == 0, output
    code, _ = trace_check(events_file(tmp_path / "untraced.jsonl", trace=False), capsys)
    assert code != 0
