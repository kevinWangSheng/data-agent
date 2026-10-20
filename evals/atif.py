"""A run's event file as an ATIF trajectory, the format the leaderboard reads (ticket 18).

Built with Harbor's own pydantic models, so a trajectory that converts is one Harbor accepts. The
steps, in event order:

- the task instruction, as the user step (the event file does not hold the prompt);
- one agent step per turn: consecutive `thinking`, `text` and `tool_start` events, with each
  `tool_end` attached to the step that made the call (Harbor's Claude Code converter bundles one
  model message the same way, `harbor/agents/installed/claude_code.py:1286`);
- a verifier's steps carry `extra.verifiers`, the verifiers running then: the SDK streams their
  messages without saying which subagent sent them, so parallel verifiers share the label. Each
  `verifier_end` adds an agent step with the verifier's report and checks;
- each `error` event as a system step.

Totals come from the `result` event, which counts the verifiers too.
"""

import json
import math
from dataclasses import dataclass, field
from typing import Any

from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)

AGENT_NAME = "data-agent"
TURN_EVENTS = frozenset({"thinking", "text", "tool_start"})
VERSION_FIELDS = ("skill_version", "config", "layers", "sdk_version", "cli_version")
RESULT_FIELDS = ("subtype", "num_turns", "terminal_reason", "cost_basis", "sdk_cost_usd", "errors")


@dataclass
class Turn:
    timestamp: str | None
    verifiers: list[str]
    reasoning: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    results: list[ObservationResult] = field(default_factory=list)

    def step(self, step_id: int) -> Step:
        return Step(
            step_id=step_id,
            timestamp=self.timestamp,
            source="agent",
            message="\n\n".join(self.text),
            reasoning_content="\n\n".join(self.reasoning) or None,
            tool_calls=self.calls or None,
            observation=Observation(results=self.results) if self.results else None,
            extra={"verifiers": self.verifiers} if self.verifiers else None,
        )


def result_text(content: Any) -> str:
    """A tool result's text: content blocks give their text, anything else its JSON."""
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(
        isinstance(b, dict) and b.get("type") == "text" for b in content
    ):
        return "\n".join(str(b.get("text", "")) for b in content)
    return json.dumps(content, ensure_ascii=False)


def convert(
    events: list[dict[str, Any]], instruction: str | None, session_id: str
) -> Trajectory | None:
    """The trajectory of a run's events; None when there are none."""
    if not events:
        return None
    version: dict[str, Any] = {}
    status: dict[str, Any] = {}
    result: dict[str, Any] | None = None
    # Steps in order: a Turn until the end, when it becomes a Step. Steps made in the loop carry
    # step_id 1 until they are numbered at the end.
    entries: list[Turn | Step] = []
    if instruction is not None:
        entries.append(Step(step_id=1, source="user", message=instruction))
    turn: Turn | None = None
    by_call: dict[str, Turn] = {}
    running: dict[str, str] = {}  # agent_id -> verifier name

    for event in events:
        kind = event.get("type")
        ts = event.get("ts")
        if kind in TURN_EVENTS:
            if kind == "thinking" and not event.get("text"):
                continue
            if turn is None:
                turn = Turn(timestamp=ts, verifiers=sorted(set(running.values())))
                entries.append(turn)
            if kind == "thinking":
                turn.reasoning.append(str(event["text"]))
            elif kind == "text":
                turn.text.append(str(event.get("text") or ""))
            else:
                call_id = str(event.get("tool_use_id"))
                arguments = event.get("input")
                turn.calls.append(
                    ToolCall(
                        tool_call_id=call_id,
                        function_name=str(event.get("tool") or ""),
                        arguments=arguments
                        if isinstance(arguments, dict)
                        else {"input": arguments},
                    )
                )
                by_call[call_id] = turn
            continue
        if kind == "sql":
            # The query_database call's SQL again, already in its arguments.
            continue
        turn = None
        if kind == "version":
            version = event
        elif kind == "status":
            status = event
        elif kind == "result":
            result = event
        elif kind == "tool_end":
            owner = by_call.get(str(event.get("tool_use_id")))
            if owner is not None:
                owner.results.append(
                    ObservationResult(
                        source_call_id=str(event.get("tool_use_id")),
                        content=result_text(event.get("content")),
                        extra={"is_error": True} if event.get("is_error") else None,
                    )
                )
        elif kind == "verifier_start":
            running[str(event.get("agent_id"))] = str(event.get("verifier"))
        elif kind == "verifier_end":
            running.pop(str(event.get("agent_id")), None)
            entries.append(
                Step(
                    step_id=1,
                    timestamp=ts,
                    source="agent",
                    message=str(event.get("report") or ""),
                    extra={
                        "verifier": event.get("verifier"),
                        "agent_id": event.get("agent_id"),
                        "checks": event.get("checks") or [],
                    },
                )
            )
        elif kind == "error":
            entries.append(
                Step(
                    step_id=1,
                    timestamp=ts,
                    source="system",
                    message=f"error ({event.get('source')}): {event.get('error')}",
                )
            )

    steps = [
        entry.step(n) if isinstance(entry, Turn) else entry.model_copy(update={"step_id": n})
        for n, entry in enumerate(entries, start=1)
    ]
    if not steps:
        return None
    agent_extra = {key: version[key] for key in VERSION_FIELDS if key in version}
    if status.get("tools"):
        agent_extra["tools"] = status["tools"]
    return Trajectory(
        session_id=session_id,
        agent=Agent(
            name=AGENT_NAME,
            version=str(version.get("git_commit") or "unknown"),
            model_name=version.get("model"),
            extra=agent_extra or None,
        ),
        steps=steps,
        final_metrics=final_metrics(result, len(steps)),
    )


@dataclass(frozen=True)
class Usage:
    """A run's tokens and cost from its `result` event. Input counts the cached tokens too, as
    Harbor's n_input_tokens and ATIF's prompt tokens do."""

    input_tokens: int
    cached_tokens: int
    output_tokens: int
    cost_usd: float


def usage(result: dict[str, Any]) -> Usage:
    """Raises ValueError, TypeError or OverflowError on a corrupt field."""
    cached = int(result.get("cached_input_tokens") or 0)
    cost = float(result.get("cost_usd") or 0.0)
    # float() takes inf and nan, which json.dumps writes as non-JSON tokens.
    if not math.isfinite(cost) or cost < 0:
        raise ValueError(f"cost_usd is not a cost: {cost}")
    return Usage(
        input_tokens=int(result.get("uncached_input_tokens") or 0) + cached,
        cached_tokens=cached,
        output_tokens=int(result.get("output_tokens") or 0),
        cost_usd=cost,
    )


def final_metrics(result: dict[str, Any] | None, n_steps: int) -> FinalMetrics | None:
    if result is None:
        return None
    totals = usage(result)
    return FinalMetrics(
        total_prompt_tokens=totals.input_tokens,
        total_completion_tokens=totals.output_tokens,
        total_cached_tokens=totals.cached_tokens,
        total_cost_usd=totals.cost_usd,
        total_steps=n_steps,
        extra={key: result[key] for key in RESULT_FIELDS if key in result} or None,
    )
