"""A run's duration in the history list (ticket 122): `result` comes after the trace export's
wait, so the run's end is its `ended_ts`; older files without it end at `result`'s own `ts`."""

from pathlib import Path

import pytest
from test_web_read import client, event


@pytest.mark.parametrize(
    ("ended", "duration_ms"),
    [({"ended_ts": "2026-11-15T09:00:15+00:00"}, 15000), ({}, 25000)],
    ids=["the run's end", "an older file: the result"],
)
def test_the_duration_ends_where_the_run_ended_not_after_the_trace_export(
    tmp_path: Path, ended: dict[str, str], duration_ms: int
) -> None:
    (tmp_path / "traced").mkdir()
    (tmp_path / "traced" / "run.jsonl").write_text(
        event(0, "version", "2026-11-15T09:00:00+00:00", config="full")
        + event(1, "answer", "2026-11-15T09:00:15+00:00", text="42")
        + event(2, "trace", "2026-11-15T09:00:25+00:00", trace_id="abc", url="https://x/abc")
        + event(3, "result", "2026-11-15T09:00:25+00:00", subtype="success", **ended)
    )
    [listed] = client(tmp_path).get("/api/runs").json()
    assert listed["duration_ms"] == duration_ms
