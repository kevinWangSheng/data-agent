"""The run's end in `agent.run`'s event file (ticket 122): `result.ended_ts` is taken before the
trace export waits for its relay to drain, so it precedes the export's events and `result` itself.
Without the export, see tests/test_agent_outcomes.py."""

import time
from datetime import datetime
from pathlib import Path

import pytest
from test_tracing import StandInRelay, conversation, traced_run

from data_agent.trace_relay import RelayReport


class SlowRelay(StandInRelay):
    """A relay that takes a while to drain, as the real one waits up to its timeout."""

    def stop(self) -> RelayReport:
        time.sleep(0.05)
        return super().stop()


async def test_the_result_records_the_runs_end_before_the_export_waits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, events, _, _ = await traced_run(monkeypatch, tmp_path, SlowRelay, conversation())
    result = events[-1]
    assert result["type"] == "result"
    ended = datetime.fromisoformat(result["ended_ts"])
    exported = [e for e in events if e["type"] in ("trace", "trace_export_error")]
    assert [e["type"] for e in exported] == ["trace_export_error", "trace"]
    answer = next(e for e in events if e["type"] == "answer")
    assert datetime.fromisoformat(answer["ts"]) <= ended
    assert all(ended < datetime.fromisoformat(e["ts"]) for e in [*exported, result])
