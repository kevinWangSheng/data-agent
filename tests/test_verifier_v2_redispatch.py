"""Ticket 57: a verifier dispatch after the first lists only the models that need verifying
again (never reported, changed since their report, FAIL or NEEDS_INFO in it) and their `ref()`
downstream among the changed models; with none, it lists every changed model again."""

from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import HookEvent

from data_agent.run_config import load_config
from data_agent.verifier_v2 import run_hooks
from data_agent.verifier_v2.project import referenced_models

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "verifier_v2"
TRANSCRIPT = FIXTURES / "verifier_transcript.jsonl"
VERIFIER = {"tool_name": "Agent", "tool_input": {"subagent_type": "verifier", "prompt": "again"}}
MODELS_LINE = "Models model_diff lists as new or changed: "

# `b` refs `a`, `d` refs `b` (so `a`'s downstream is b and d); `c` and `e` stand alone; `raw`
# is untouched since the snapshot, so it is never listed even though `e` refs it.
WORKSPACE_MODELS = {
    "a": "SELECT 1 AS x\n",
    "b": "SELECT x FROM {{ ref('a') }}\n",
    "c": "SELECT 3 AS z\n",
    "d": 'SELECT x FROM {{ ref( "b" ) }}\n',
    "e": "SELECT * FROM {{ ref('raw') }}\n",
    "raw": "SELECT 0 AS r\n",
}


def tiny_project(root: Path, models: dict[str, str]) -> None:
    (root / "models").mkdir(parents=True)
    (root / "dbt_project.yml").write_text(
        'name: tiny\nversion: "1.0.0"\nconfig-version: 2\nprofile: tiny\nmodel-paths: ["models"]\n'
    )
    for name, sql in models.items():
        (root / "models" / f"{name}.sql").write_text(sql)


@pytest.fixture
def run(tmp_path: Path) -> tuple[Path, Path, dict[HookEvent, list[HookMatcher]]]:
    """(workspace, snapshot, hooks): a, b, c, d and e are new since the snapshot."""
    workspace, snapshot = tmp_path / "ws", tmp_path / "run" / "snapshot"
    tiny_project(snapshot / "project", {"raw": WORKSPACE_MODELS["raw"]})
    tiny_project(workspace / "project", WORKSPACE_MODELS)
    hooks = run_hooks(
        load_config("full"),
        workspace=workspace,
        snapshot=snapshot,
        task_file=tmp_path / "task.md",
        emit=lambda type, **fields: None,
    )
    return workspace, snapshot, hooks


async def call(
    hooks: dict[HookEvent, list[HookMatcher]], event: HookEvent, **fields: Any
) -> dict[str, Any]:
    hook_input: dict[str, Any] = {"hook_event_name": event, "session_id": "s", **fields}
    merged: dict[str, Any] = {}
    for matcher in hooks.get(event, []):
        for hook in matcher.hooks:
            merged |= dict(await hook(hook_input, None, {"signal": None}))  # type: ignore[arg-type]
    return merged


async def dispatch(hooks: dict[HookEvent, list[HookMatcher]]) -> list[str]:
    """The models the rewritten dispatch prompt lists."""
    output = await call(hooks, "PreToolUse", **VERIFIER)
    prompt = output["hookSpecificOutput"]["updatedInput"]["prompt"]
    [line] = [line for line in prompt.splitlines() if line.startswith(MODELS_LINE)]
    return line.removeprefix(MODELS_LINE).split(", ")


async def report(hooks: dict[HookEvent, list[HookMatcher]], **statuses: str) -> None:
    text = "".join(f"### {model}\nCHECK 1: {status} — x\n" for model, status in statuses.items())
    await call(
        hooks,
        "SubagentStop",
        agent_id="a1",
        agent_type="verifier",
        agent_transcript_path=str(TRANSCRIPT),
        last_assistant_message=text,
        stop_hook_active=False,
    )


def edit(workspace: Path, model: str, sql: str) -> None:
    (workspace / "project" / "models" / f"{model}.sql").write_text(sql)


ALL_PASS = dict.fromkeys("abcde", "PASS")


async def test_first_dispatch_lists_every_changed_model(run: Any) -> None:
    _, _, hooks = run
    assert await dispatch(hooks) == ["a", "b", "c", "d", "e"]


async def test_redispatch_lists_a_changed_model_and_its_downstream(run: Any) -> None:
    """`a` changed after an all-PASS report: b refs a and d refs b, so both are listed though
    their own files did not change."""
    workspace, _, hooks = run
    await dispatch(hooks)
    await report(hooks, **ALL_PASS)
    edit(workspace, "a", "SELECT 2 AS x\n")
    assert await dispatch(hooks) == ["a", "b", "d"]


async def test_redispatch_lists_a_model_no_report_named(run: Any) -> None:
    _, _, hooks = run
    await dispatch(hooks)
    await report(hooks, a="PASS", b="PASS", d="PASS", e="PASS")
    assert await dispatch(hooks) == ["c"]


async def test_redispatch_lists_a_failed_model_and_its_downstream(run: Any) -> None:
    _, _, hooks = run
    await dispatch(hooks)
    await report(hooks, **ALL_PASS | {"b": "FAIL"})
    assert await dispatch(hooks) == ["b", "d"]


async def test_redispatch_lists_a_needs_info_model(run: Any) -> None:
    """NEEDS_INFO does not block Stop, but the main agent dispatches again for it."""
    _, _, hooks = run
    await dispatch(hooks)
    await report(hooks, **ALL_PASS | {"c": "NEEDS_INFO"})
    assert await call(hooks, "Stop", stop_hook_active=False) == {}
    assert await dispatch(hooks) == ["c"]


async def test_redispatch_with_nothing_to_verify_again_lists_every_changed_model(
    run: Any,
) -> None:
    _, _, hooks = run
    await dispatch(hooks)
    await report(hooks, **ALL_PASS)
    assert await dispatch(hooks) == ["a", "b", "c", "d", "e"]


async def test_redispatch_lists_a_reported_model_reverted_to_the_snapshot(run: Any) -> None:
    """Stop rule 2 blocks a reported model whose file changed after its report, even back to
    the snapshot's text (model_diff no longer lists it), so the dispatch answering it lists it;
    `e` refs it and is changed, so it comes along."""
    workspace, _, hooks = run
    await dispatch(hooks)
    await report(hooks, **ALL_PASS)
    edit(workspace, "raw", "SELECT 9 AS r\n")
    assert await dispatch(hooks) == ["e", "raw"]
    await report(hooks, e="PASS", raw="PASS")
    edit(workspace, "raw", WORKSPACE_MODELS["raw"])
    assert await dispatch(hooks) == ["e", "raw"]


@pytest.mark.parametrize(
    ("sql", "refs"),
    [
        ("{{ ref('m') }}", {"m"}),
        ('{{ref( "m" )}}', {"m"}),
        ("{{ ref('pkg', 'm') }}", {"m"}),
        ("{{ ref('m', v=2) }}", {"m"}),
        ("{{ ref('m', version='2') }}", {"m"}),
        ("{{ source('s', 'm') }} JOIN {{ preference('x') }}", set()),
        ("{{ ref('a') }} JOIN {{ ref('b') }}", {"a", "b"}),
    ],
)
def test_referenced_models_reads_ref_calls(sql: str, refs: set[str]) -> None:
    assert referenced_models(sql) == refs
