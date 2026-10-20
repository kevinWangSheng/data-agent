"""The event file as an ATIF trajectory (ticket 18): `evals.atif.convert`."""

from typing import Any

from harbor.models.trajectories import Trajectory

from evals.atif import convert

VERSION = {
    "type": "version",
    "git_commit": "abc123",
    "skill_version": "def456",
    "config": "full",
    "layers": {"skill": True, "verifiers": True, "context": True},
    "model": "deepseek-flash",
    "sdk_version": "0.2.168",
    "cli_version": "2.1.301",
}
RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 3,
    "terminal_reason": "completed",
    "uncached_input_tokens": 1_000,
    "cached_input_tokens": 9_000,
    "output_tokens": 500,
    "cost_usd": 0.0123,
    "sdk_cost_usd": 0.5,
    "cost_basis": "price_table",
    "errors": [],
}


def stamped(*events: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"seq": seq, "ts": f"2026-10-20T10:00:{seq:02d}+00:00", **event}
        for seq, event in enumerate(events)
    ]


def tool(call_id: str, name: str, **arguments: Any) -> dict[str, Any]:
    return {"type": "tool_start", "tool": name, "tool_use_id": call_id, "input": arguments}


def done(call_id: str, name: str, content: Any, is_error: bool = False) -> dict[str, Any]:
    return {
        "type": "tool_end",
        "tool": name,
        "tool_use_id": call_id,
        "is_error": is_error,
        "content": content,
    }


RUN = stamped(
    VERSION,
    {"type": "status", "state": "started", "model": "deepseek-flash", "tools": ["Bash", "Agent"]},
    {"type": "thinking", "text": "Scan the project first."},
    {"type": "text", "text": "Scanning the project and the database."},
    tool("c1", "Bash", command="ls models"),
    tool("c2", "mcp__data__query_database", sql="SELECT 1"),
    {"type": "sql", "tool_use_id": "c2", "sql": "SELECT 1"},
    done("c1", "Bash", "stg_orders.sql"),
    done("c2", "mcp__data__query_database", '{"result":"1"}'),
    {"type": "thinking", "text": ""},
    tool("c3", "Agent", description="Verify", prompt="Check the model"),
    {"type": "verifier_start", "verifier": "verifier", "agent_id": "a1"},
    tool("v1", "Read", file_path="/app/models/x.sql"),
    done("v1", "Read", "select 1"),
    {
        "type": "verifier_end",
        "verifier": "verifier",
        "agent_id": "a1",
        "checks": [{"model": "x", "check": "CHECK 1", "status": "PASS"}],
        "report": "## Structure Report\n- CHECK 1: PASS",
    },
    done("c3", "Agent", [{"type": "text", "text": "All checks pass."}]),
    {"type": "text", "text": "Done: the model is fixed."},
    RESULT,
)


def test_the_instruction_is_the_first_step_and_the_agent_comes_from_the_version_header() -> None:
    trajectory = convert(RUN, instruction="fix the model", session_id="run-1")
    assert trajectory is not None
    first = trajectory.steps[0]
    assert (first.source, first.message) == ("user", "fix the model")
    assert trajectory.session_id == "run-1"
    assert trajectory.agent.name == "data-agent"
    assert trajectory.agent.version == "abc123"
    assert trajectory.agent.model_name == "deepseek-flash"
    assert trajectory.agent.extra is not None
    assert trajectory.agent.extra["config"] == "full"
    assert trajectory.agent.extra["skill_version"] == "def456"
    assert trajectory.agent.extra["tools"] == ["Bash", "Agent"]


def test_one_turn_holds_its_reasoning_text_tool_calls_and_their_results() -> None:
    trajectory = convert(RUN, instruction="fix the model", session_id="run-1")
    assert trajectory is not None
    turn = trajectory.steps[1]
    assert turn.source == "agent"
    assert turn.reasoning_content == "Scan the project first."
    assert turn.message == "Scanning the project and the database."
    assert turn.timestamp == "2026-10-20T10:00:02+00:00"
    assert turn.tool_calls is not None
    assert [(c.tool_call_id, c.function_name, c.arguments) for c in turn.tool_calls] == [
        ("c1", "Bash", {"command": "ls models"}),
        ("c2", "mcp__data__query_database", {"sql": "SELECT 1"}),
    ]
    assert turn.observation is not None
    assert [(r.source_call_id, r.content) for r in turn.observation.results] == [
        ("c1", "stg_orders.sql"),
        ("c2", '{"result":"1"}'),
    ]


def test_a_tool_result_made_of_content_blocks_becomes_its_text() -> None:
    trajectory = convert(RUN, instruction="fix the model", session_id="run-1")
    assert trajectory is not None
    dispatch = trajectory.steps[2]
    assert dispatch.tool_calls is not None
    assert dispatch.tool_calls[0].function_name == "Agent"
    assert dispatch.observation is not None
    assert dispatch.observation.results[0].content == "All checks pass."


def test_a_failed_tool_call_is_marked_on_its_result() -> None:
    events = stamped(VERSION, tool("c1", "Bash", command="dbt run"), done("c1", "Bash", "no", True))
    trajectory = convert(events, instruction="t", session_id="s")
    assert trajectory is not None
    assert trajectory.steps[1].observation is not None
    assert trajectory.steps[1].observation.results[0].extra == {"is_error": True}


def test_verifier_steps_are_told_apart_from_the_main_agents() -> None:
    trajectory = convert(RUN, instruction="fix the model", session_id="run-1")
    assert trajectory is not None
    inside = trajectory.steps[3]
    assert inside.tool_calls is not None
    assert inside.tool_calls[0].tool_call_id == "v1"
    assert inside.extra == {"verifiers": ["verifier"]}
    report = trajectory.steps[4]
    assert report.source == "agent"
    assert report.message == "## Structure Report\n- CHECK 1: PASS"
    assert report.extra == {
        "verifier": "verifier",
        "agent_id": "a1",
        "checks": [{"model": "x", "check": "CHECK 1", "status": "PASS"}],
    }
    final = trajectory.steps[5]
    assert final.message == "Done: the model is fixed."
    assert final.extra is None
    main = [s for s in trajectory.steps if s.source == "agent" and s.extra is None]
    assert len(main) == 3


def test_parallel_verifiers_name_every_verifier_running() -> None:
    events = stamped(
        VERSION,
        {"type": "verifier_start", "verifier": "verifier", "agent_id": "a1"},
        {"type": "verifier_start", "verifier": "value-verifier", "agent_id": "a2"},
        tool("v1", "Read", file_path="/app/x.sql"),
        done("v1", "Read", "select 1"),
        {"type": "verifier_end", "verifier": "verifier", "agent_id": "a1", "report": "ok"},
        tool("v2", "mcp__data__query_database", sql="SELECT 2"),
    )
    trajectory = convert(events, instruction="t", session_id="s")
    assert trajectory is not None
    assert trajectory.steps[1].extra == {"verifiers": ["value-verifier", "verifier"]}
    assert trajectory.steps[3].extra == {"verifiers": ["value-verifier"]}


def test_final_metrics_come_from_the_result_event() -> None:
    trajectory = convert(RUN, instruction="fix the model", session_id="run-1")
    assert trajectory is not None
    metrics = trajectory.final_metrics
    assert metrics is not None
    # ATIF's prompt tokens include the cached ones, as Harbor's n_input_tokens does.
    assert metrics.total_prompt_tokens == 10_000
    assert metrics.total_cached_tokens == 9_000
    assert metrics.total_completion_tokens == 500
    assert metrics.total_cost_usd == 0.0123
    assert metrics.total_steps == len(trajectory.steps)
    assert metrics.extra is not None
    assert metrics.extra["cost_basis"] == "price_table"
    assert metrics.extra["terminal_reason"] == "completed"


def test_errors_become_system_steps() -> None:
    events = stamped(VERSION, {"type": "error", "source": "sdk", "error": "initialize timed out"})
    trajectory = convert(events, instruction="t", session_id="s")
    assert trajectory is not None
    step = trajectory.steps[1]
    assert (step.source, step.message) == ("system", "error (sdk): initialize timed out")
    assert trajectory.final_metrics is None


def test_the_trajectory_round_trips_through_harbors_model() -> None:
    trajectory = convert(RUN, instruction="fix the model", session_id="run-1")
    assert trajectory is not None
    assert Trajectory.model_validate(trajectory.to_json_dict()) == trajectory
    assert [s.step_id for s in trajectory.steps] == list(range(1, len(trajectory.steps) + 1))


def test_a_run_with_no_events_has_no_trajectory() -> None:
    assert convert([], instruction="t", session_id="s") is None


def test_without_an_instruction_the_trajectory_starts_at_the_agent() -> None:
    trajectory = convert(RUN, instruction=None, session_id="run-1")
    assert trajectory is not None
    assert trajectory.steps[0].source == "agent"
