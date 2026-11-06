"""Ticket 64: a disagreement between the verifier and the main agent may end the run, and the
verifier may not query the models it was dispatched to verify.

- A dispute: the same CHECK of the same model FAILs a second time, the model's fingerprint is
  the one it had at the first FAIL, and the dispatch in between carried counter-evidence (the
  `UNVERIFIED_HEADING` section, non-empty). Stop no longer blocks on it and records
  `ended_disputed` when the run ends; any other FAIL still blocks.
- The verifier's `query_database` that reads a dispatched model (by name or alias, any schema)
  is denied; the main agent's is not.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import HookEvent

from data_agent.run_config import load_config
from data_agent.verifier_v2 import run_hooks
from evals.harbor import Trial
from evals.process import job_metrics, trial_metrics

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "verifier_v2"
TRANSCRIPT = FIXTURES / "verifier_transcript.jsonl"
QUERY_TOOL = "mcp__data__query_database"
CHECK3_FAIL = "### alpha\nCHECK 1: PASS — ok\nCHECK 3: FAIL — 167 NULL-key rows\n"
EVIDENCE = "CHECK 3: `SELECT count(*) FROM raw WHERE k IS NULL` returns 167; the source has them."


def tiny_project(root: Path, **models: str) -> None:
    (root / "models").mkdir(parents=True)
    (root / "dbt_project.yml").write_text(
        'name: tiny\nversion: "1.0.0"\nconfig-version: 2\nprofile: tiny\nmodel-paths: ["models"]\n'
    )
    for name, sql in models.items():
        (root / "models" / f"{name}.sql").write_text(sql)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """`alpha` (built as `alpha_v2`) and `beta` are new since the snapshot; `same` is not."""
    snapshot = tmp_path / "run" / "snapshot"
    tiny_project(snapshot / "project", same="SELECT 9\n")
    tiny_project(
        tmp_path / "ws" / "project",
        alpha="{{ config(alias='alpha_v2') }}\nSELECT 1 AS x\n",
        beta="SELECT 2 AS y\n",
        same="SELECT 9\n",
    )
    return tmp_path / "ws"


class Run:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.events: list[dict[str, Any]] = []
        self.hooks: dict[HookEvent, list[HookMatcher]] = run_hooks(
            load_config("full"),
            workspace=workspace,
            snapshot=workspace.parent / "run" / "snapshot",
            task_file=workspace.parent / "task.md",
            emit=lambda type, **fields: self.events.append({"type": type, **fields}),
        )

    async def call(self, event: HookEvent, **fields: Any) -> dict[str, Any]:
        hook_input: dict[str, Any] = {"hook_event_name": event, "session_id": "s", **fields}
        merged: dict[str, Any] = {}
        for matcher in self.hooks.get(event, []):
            for hook in matcher.hooks:
                merged |= dict(await hook(hook_input, None, {"signal": None}))  # type: ignore[arg-type]
        return merged

    async def verify(self, report: str, prompt: str = "verify", agent_id: str = "v1") -> None:
        """One verifier call: dispatch, then report."""
        tool_input = {"subagent_type": "verifier", "prompt": prompt}
        await self.call("PreToolUse", tool_name="Agent", tool_input=tool_input)
        await self.call(
            "SubagentStop",
            agent_id=agent_id,
            agent_type="verifier",
            agent_transcript_path=str(TRANSCRIPT),
            last_assistant_message=report,
            stop_hook_active=False,
        )

    async def stop(self) -> dict[str, Any]:
        return await self.call("Stop", stop_hook_active=False)

    def ended(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


BETA_PASS = "### beta\nCHECK 1: PASS — ok\n"


async def test_a_second_fail_after_counter_evidence_on_an_unchanged_model_ends_disputed(
    workspace: Path,
) -> None:
    run = Run(workspace)
    await run.verify(CHECK3_FAIL + BETA_PASS)
    blocked = await run.stop()
    assert blocked["decision"] == "block" and "alpha: rule 3" in blocked["reason"]
    await run.verify(CHECK3_FAIL, prompt=EVIDENCE)
    assert await run.stop() == {}
    [ended] = run.ended("ended_disputed")
    assert ended["disputed"] == [{"model": "alpha", "check": 3, "dispatches": [1, 2]}]
    assert "alpha" in ended["reason"] and "CHECK 3" in ended["reason"]
    assert run.ended("ended_with_unresolved") == []


async def test_a_second_fail_on_a_changed_model_still_blocks(workspace: Path) -> None:
    run = Run(workspace)
    await run.verify(CHECK3_FAIL + BETA_PASS)
    await run.stop()
    model = workspace / "project" / "models" / "alpha.sql"
    model.write_text(model.read_text() + "WHERE x IS NOT NULL\n")
    await run.verify(CHECK3_FAIL, prompt=EVIDENCE)
    blocked = await run.stop()
    assert blocked["decision"] == "block" and "alpha: rule 3" in blocked["reason"]
    assert run.ended("ended_disputed") == []


@pytest.mark.parametrize("prompt", ["", "   \n"])
async def test_a_second_fail_without_counter_evidence_still_blocks(
    workspace: Path, prompt: str
) -> None:
    run = Run(workspace)
    await run.verify(CHECK3_FAIL + BETA_PASS)
    await run.stop()
    await run.verify(CHECK3_FAIL, prompt=prompt)
    blocked = await run.stop()
    assert blocked["decision"] == "block" and "alpha: rule 3" in blocked["reason"]
    assert run.ended("ended_disputed") == []


async def test_a_disputed_check_next_to_another_fail_still_blocks_on_the_other(
    workspace: Path,
) -> None:
    """alpha CHECK 3 is disputed; alpha CHECK 5 and beta CHECK 2 fail for the first time."""
    run = Run(workspace)
    await run.verify(CHECK3_FAIL + BETA_PASS)
    await run.stop()
    second = CHECK3_FAIL + "CHECK 5: FAIL — new\n### beta\nCHECK 2: FAIL — bad\n"
    await run.verify(second, prompt=EVIDENCE)
    blocked = await run.stop()
    assert blocked["decision"] == "block"
    assert "alpha: rule 3, the last report that named it has a FAIL (CHECK 5)" in blocked["reason"]
    assert "beta: rule 3" in blocked["reason"] and "CHECK 3" not in blocked["reason"]
    assert run.ended("ended_disputed") == []


async def test_a_dispute_stands_while_the_check_keeps_failing_on_the_same_model(
    workspace: Path,
) -> None:
    """A later dispatch (for beta's new FAIL) without counter-evidence on alpha re-reports alpha
    CHECK 3 FAIL: the dispute stands, with its original dispatches, and blocks nothing."""
    run = Run(workspace)
    await run.verify(CHECK3_FAIL + BETA_PASS)
    await run.stop()
    await run.verify(CHECK3_FAIL + "### beta\nCHECK 2: FAIL — bad\n", prompt=EVIDENCE)
    await run.stop()
    await run.verify(CHECK3_FAIL + BETA_PASS, prompt="")
    assert await run.stop() == {}
    [ended] = run.ended("ended_disputed")
    assert ended["disputed"] == [{"model": "alpha", "check": 3, "dispatches": [1, 2]}]


async def test_two_reports_of_one_outstanding_dispatch_are_no_dispute(workspace: Path) -> None:
    """d2 (with counter-evidence) and d3 are outstanding together, so both reports take d2's
    number and evidence; the first records a new FAIL and the second repeats it. No dispatch
    with counter-evidence came after that first FAIL, so it still blocks."""
    run = Run(workspace)
    await run.verify("### alpha\nCHECK 3: PASS — ok\n" + BETA_PASS)
    tool_input = {"subagent_type": "verifier", "prompt": EVIDENCE}
    await run.call("PreToolUse", tool_name="Agent", tool_input=tool_input)
    await run.call("PreToolUse", tool_name="Agent", tool_input=tool_input | {"prompt": ""})
    for agent_id in ("v2", "v3"):
        await run.call(
            "SubagentStop",
            agent_id=agent_id,
            agent_type="verifier",
            agent_transcript_path=str(TRANSCRIPT),
            last_assistant_message=CHECK3_FAIL,
            stop_hook_active=False,
        )
    blocked = await run.stop()
    assert blocked["decision"] == "block" and "alpha: rule 3" in blocked["reason"]
    assert run.ended("ended_disputed") == []


async def test_a_fail_counts_as_disputed_only_if_every_outstanding_dispatch_has_evidence(
    workspace: Path,
) -> None:
    """After d1's FAIL, d2 (with counter-evidence) and d3 (without) are outstanding together.
    The report that repeats the FAIL may be d3's, which never saw counter-evidence, so it
    still blocks."""
    run = Run(workspace)
    await run.verify(CHECK3_FAIL + BETA_PASS)
    await run.stop()
    tool_input = {"subagent_type": "verifier", "prompt": EVIDENCE}
    await run.call("PreToolUse", tool_name="Agent", tool_input=tool_input)
    await run.call("PreToolUse", tool_name="Agent", tool_input=tool_input | {"prompt": ""})
    for agent_id, report in (("v3", CHECK3_FAIL), ("v2", BETA_PASS)):
        await run.call(
            "SubagentStop",
            agent_id=agent_id,
            agent_type="verifier",
            agent_transcript_path=str(TRANSCRIPT),
            last_assistant_message=report,
            stop_hook_active=False,
        )
    blocked = await run.stop()
    assert blocked["decision"] == "block" and "alpha: rule 3" in blocked["reason"]
    assert run.ended("ended_disputed") == []


async def test_disputes_are_listed_when_the_run_ends_unresolved(workspace: Path) -> None:
    run = Run(workspace)
    await run.verify(CHECK3_FAIL + "### beta\nCHECK 2: FAIL — bad\n")
    await run.stop()
    # beta fails another check this time, which is no dispute.
    await run.verify(CHECK3_FAIL + "### beta\nCHECK 4: FAIL — bad\n", prompt=EVIDENCE)
    await run.stop()
    assert await run.stop() == {}
    [unresolved] = run.ended("ended_with_unresolved")
    assert [r["model"] for r in unresolved["reasons"]] == ["beta"]
    [disputed] = run.ended("ended_disputed")
    assert disputed["disputed"] == [{"model": "alpha", "check": 3, "dispatches": [1, 2]}]


async def query(
    run: Run, sql: str, agent_id: str | None = "v1", agent_type: str = "verifier"
) -> dict[str, Any]:
    """A query_database call as the PreToolUse input gives it: from inside a subagent with its
    agent_id and agent_type, from the main agent with neither."""
    fields: dict[str, Any] = {"tool_name": QUERY_TOOL, "tool_input": {"sql": sql}}
    if agent_id is not None:
        fields |= {"agent_id": agent_id, "agent_type": agent_type}
    return await run.call("PreToolUse", **fields)


async def dispatched(workspace: Path) -> Run:
    """A run whose verifier is running on the first dispatch (alpha and beta)."""
    run = Run(workspace)
    tool_input = {"subagent_type": "verifier", "prompt": "verify"}
    await run.call("PreToolUse", tool_name="Agent", tool_input=tool_input)
    return run


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM alpha",
        "SELECT count(*) FROM main.ALPHA",
        'SELECT * FROM "analytics"."alpha_v2"',
        "SELECT a.x FROM raw_orders r JOIN beta a ON r.id = a.y",
    ],
)
async def test_the_verifier_may_not_query_a_dispatched_model(workspace: Path, sql: str) -> None:
    run = await dispatched(workspace)
    output = (await query(run, sql))["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "grain_facts" in output["permissionDecisionReason"]
    assert "compare_by_key" in output["permissionDecisionReason"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM raw_orders WHERE id IS NULL",
        "SELECT * FROM same",
        "WITH alpha AS (SELECT 1 AS x) SELECT * FROM alpha",
        "SELEC * FRM ((alpha",
        # Too deep for the parser (RecursionError, not a parse error): unparsed, so it passes.
        pytest.param("SELECT " + "(" * 3000 + "1" + ")" * 3000 + " FROM alpha", id="too-deep"),
    ],
)
async def test_the_verifier_may_query_sources_other_tables_and_unparsed_sql(
    workspace: Path, sql: str
) -> None:
    run = await dispatched(workspace)
    assert await query(run, sql) == {}


async def test_the_main_agent_and_other_subagents_may_query_the_models(workspace: Path) -> None:
    run = await dispatched(workspace)
    assert await query(run, "SELECT * FROM alpha", agent_id=None) == {}
    assert await query(run, "SELECT * FROM alpha", agent_id="e1", agent_type="Explore") == {}


async def test_the_guard_follows_the_dispatch_the_verifier_runs_on(workspace: Path) -> None:
    """A later dispatch lists only the models to verify again (here alpha, after its FAIL);
    a verifier on it may read beta, which it was not dispatched for."""
    run = Run(workspace)
    await run.verify(CHECK3_FAIL + BETA_PASS)
    tool_input = {"subagent_type": "verifier", "prompt": EVIDENCE}
    await run.call("PreToolUse", tool_name="Agent", tool_input=tool_input)
    assert await query(run, "SELECT * FROM beta", agent_id="v2") == {}
    denied = await query(run, "SELECT * FROM alpha", agent_id="v2")
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_eval_process_reads_a_trial_that_ended_disputed(tmp_path: Path) -> None:
    events_dir = tmp_path / "t1__a" / "agent" / "events"
    events_dir.mkdir(parents=True)
    events = [
        {"type": "verifier_start", "verifier": "verifier", "agent_id": "v1"},
        {
            "type": "ended_disputed",
            "reason": "alpha: CHECK 3 (dispatches 1, 2)",
            "disputed": [{"model": "alpha", "check": 3, "dispatches": [1, 2]}],
        },
        {"type": "result", "num_turns": 3, "cost_usd": 0.2, "subtype": "success"},
    ]
    (events_dir / "run-1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    trial = Trial(
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
    row = trial_metrics(trial)
    assert (row["verifier_calls"], row["turns"], row["ended_unverified"]) == (1, 3, False)
    assert job_metrics([trial])["per_task"]["t1"]["trials"] == 1
