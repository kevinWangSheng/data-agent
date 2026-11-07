"""Readiness findings of one smoke trial and the Docker probe (eval-readiness ticket 02).

The CLI-level contract (blockers stop before the first job, warnings reach the table) is tested at
the `evals ablate` seam in test_eval_ablate.py; here, fake trial files for the cache and timeout
evidence the fake Harbor there does not vary.
"""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from lifecycle_events import dispatches

from evals import readiness
from evals.harbor import Trial, load_trials


def smoke_trial(
    tmp_path: Path,
    events: list[dict[str, Any]],
    input_tokens: int = 100_000,
    cached: int = 95_000,
) -> Trial:
    """One smoke trial on dbt-alpha: a version header, `events`, a result event."""
    trial = tmp_path / "job" / "dbt-alpha__x"
    (trial / "agent" / "events").mkdir(parents=True)
    lines = [
        {"type": "version", "git_commit": "c1", "skill_version": "s1", "config": "full"},
        *events,
        {"type": "result", "subtype": "success", "num_turns": 9, "cost_usd": 0.1},
    ]
    (trial / "agent" / "events" / "run.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in lines)
    )
    result = {
        "task_name": "org/dbt-alpha",
        "trial_name": trial.name,
        "verifier_result": {"rewards": {"reward": 1.0}},
        "agent_result": {"n_input_tokens": input_tokens, "n_cache_tokens": cached},
    }
    (trial / "result.json").write_text(json.dumps(result))
    (found,) = load_trials(tmp_path / "job")
    return found


def checks(found: list[dict[str, str]]) -> list[str]:
    return [w["check"] for w in found]


def test_a_smoke_trial_like_the_dev_runs_gives_no_warning(tmp_path: Path) -> None:
    trial = smoke_trial(tmp_path, dispatches(21, 6, 5))
    assert readiness.smoke_warnings("full", "dbt-alpha", trial) == []


def test_a_tool_call_that_hit_the_statement_timeout_is_a_warning(tmp_path: Path) -> None:
    timing = [
        {
            "type": "tool_timing",
            "tool": "mcp__db__query",
            "duration_ms": 150_000,
            "timed_out": True,
        },
        {"type": "tool_timing", "tool": "Bash", "duration_ms": 2_000},
    ]
    (found,) = readiness.smoke_warnings("full", "dbt-alpha", smoke_trial(tmp_path, timing))
    assert found["check"] == "tool_timeout"
    assert "mcp__db__query" in found["reason"] and "full" in found["reason"]


def test_input_mostly_missing_the_prompt_cache_is_a_warning(tmp_path: Path) -> None:
    """Every dev run so far had 94-99% of its input tokens from the cache; 30% means most of each
    turn's input was sent again uncached."""
    trial = smoke_trial(tmp_path, [], input_tokens=100_000, cached=30_000)
    (found,) = readiness.smoke_warnings("baseline", "dbt-alpha", trial)
    assert found["check"] == "prompt_cache"
    assert "30%" in found["reason"] and "uncached" in found["reason"]


def test_a_trial_without_token_counts_gives_no_cache_warning(tmp_path: Path) -> None:
    trial = smoke_trial(tmp_path, [], input_tokens=0, cached=0)
    assert readiness.smoke_warnings("full", "dbt-alpha", trial) == []


def test_findings_of_one_trial_are_listed_together(tmp_path: Path) -> None:
    trial = smoke_trial(tmp_path, dispatches(21, 21), input_tokens=100, cached=10)
    found = readiness.smoke_warnings("full", "dbt-alpha", trial)
    assert checks(found) == ["full_redispatch", "prompt_cache"]


def completed(code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["docker"], code, stdout, stderr)


def test_docker_info_gives_its_memory_and_data_root(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return completed(0, "8221065216 /var/lib/docker\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert readiness.docker_info() == (8221065216, "/var/lib/docker")
    assert seen == [["docker", "info", "--format", "{{.MemTotal}} {{.DockerRootDir}}"]]


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (completed(1, stderr="Cannot connect to the Docker daemon"), "Cannot connect"),
        (completed(0, "<no value>\n"), "no memory size"),
        (FileNotFoundError("docker"), "docker info failed"),
        (subprocess.TimeoutExpired(["docker"], 30), "docker info failed"),
    ],
)
def test_a_docker_that_does_not_answer_is_not_ready(
    monkeypatch: pytest.MonkeyPatch, reply: Any, message: str
) -> None:
    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(readiness.NotReady, match=message):
        readiness.docker_info()


def test_the_agents_default_trial_limit_is_read_from_the_agent() -> None:
    assert readiness.agent_timeout([]) == 4000.0
    kwargs = ["agent_timeout_sec=1500", "note=x", "agent_timeout_sec=1800"]
    assert readiness.agent_timeout(kwargs) == 1800.0
    with pytest.raises(readiness.NotReady, match="not a number"):
        readiness.agent_timeout(["agent_timeout_sec=soon"])
