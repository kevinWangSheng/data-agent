"""`evals.process` reads `verifier_end.checks` in both shapes the agent has written: the old list
(one entry per check, each with a `status`) and the v2 mapping (model -> {check number -> status},
ticket 46; JSON turns the numbers into string keys)."""

import json
from pathlib import Path
from typing import Any

from evals.harbor import Trial
from evals.process import trial_metrics, verifier_checks


def trial_with(tmp_path: Path, *events: dict[str, Any]) -> Trial:
    events_dir = tmp_path / "t1__a" / "agent" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "run-1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return Trial(
        task="t1",
        name="t1__a",
        reward=1.0,
        exception_type=None,
        uncached_input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        dir=tmp_path / "t1__a",
    )


def test_checks_as_a_list_count_each_entry_status(tmp_path: Path) -> None:
    trial = trial_with(
        tmp_path,
        {"type": "verifier_start", "verifier": "verifier", "agent_id": "a1"},
        {
            "type": "verifier_end",
            "verifier": "verifier",
            "agent_id": "a1",
            "checks": [
                {"model": "x", "check": "CHECK 1", "status": "PASS"},
                {"model": "x", "check": "CHECK 2", "status": "FAIL"},
                {"model": "y", "check": "CHECK 1", "status": "PASS"},
            ],
        },
    )
    assert trial_metrics(trial)["verifier_checks"] == {"PASS": 2, "FAIL": 1}


def test_checks_as_a_mapping_count_each_status_under_every_model(tmp_path: Path) -> None:
    trial = trial_with(
        tmp_path,
        {"type": "verifier_start", "verifier": "verifier", "agent_id": "a1"},
        {
            "type": "verifier_end",
            "verifier": "verifier",
            "agent_id": "a1",
            "checks": {"x": {"1": "PASS", "2": "FAIL"}, "y": {"1": "PASS", "3": "NEEDS_INFO"}},
        },
    )
    assert trial_metrics(trial)["verifier_checks"] == {"PASS": 2, "FAIL": 1, "NEEDS_INFO": 1}


def test_verifier_checks_flattens_a_mapping_into_rows() -> None:
    assert verifier_checks({"checks": {"x": {"2": "FAIL", "1": "PASS"}}}) == [
        {"model": "x", "check": "CHECK 1", "status": "PASS"},
        {"model": "x", "check": "CHECK 2", "status": "FAIL"},
    ]
    rows = [{"model": "x", "check": "CHECK 1", "status": "PASS"}]
    assert verifier_checks({"checks": rows}) == rows
    assert verifier_checks({}) == []
