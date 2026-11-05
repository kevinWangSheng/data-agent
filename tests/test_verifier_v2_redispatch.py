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
from data_agent.verifier_v2.dispatch import AGAIN_MODELS_LABEL, FIRST_MODELS_LABEL
from data_agent.verifier_v2.project import referenced_models

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "verifier_v2"
TRANSCRIPT = FIXTURES / "verifier_transcript.jsonl"
VERIFIER = {"tool_name": "Agent", "tool_input": {"subagent_type": "verifier", "prompt": "again"}}

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
    return workspace, snapshot, hooks_for(workspace, snapshot)


def hooks_for(workspace: Path, snapshot: Path) -> dict[HookEvent, list[HookMatcher]]:
    return run_hooks(
        load_config("full"),
        workspace=workspace,
        snapshot=snapshot,
        task_file=workspace.parent / "task.md",
        emit=lambda type, **fields: None,
    )


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
    labels = tuple(f"{label}: " for label in (FIRST_MODELS_LABEL, AGAIN_MODELS_LABEL))
    [line] = [line for line in prompt.splitlines() if line.startswith(labels)]
    return line.split(": ", 1)[1].split(", ")


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


async def test_only_the_first_dispatch_says_the_list_is_what_changed(run: Any) -> None:
    """A later list holds only the models to verify again: it must not read as "these are the
    changed models" (spec review, round 2)."""
    _, _, hooks = run
    first = (await call(hooks, "PreToolUse", **VERIFIER))["hookSpecificOutput"]
    second = (await call(hooks, "PreToolUse", **VERIFIER))["hookSpecificOutput"]
    assert f"{FIRST_MODELS_LABEL}: " in first["updatedInput"]["prompt"]
    assert FIRST_MODELS_LABEL not in second["updatedInput"]["prompt"]
    assert f"{AGAIN_MODELS_LABEL}: " in second["updatedInput"]["prompt"]


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
        ("{{ source('s', 'm') }} WHERE d > '{{ var(\"start\", '2020') }}'", set()),
        ("{{ ref('a') }} JOIN {{ ref('b') }}", {"a", "b"}),
        (
            "{{ config(materialized='incremental', unique_key=concat('a', 'b')) }}\n"
            "SELECT * FROM {{ ref('a') }}\n"
            "{% if is_incremental() %} WHERE t > (SELECT max(t) FROM {{ this }}) {% endif %}\n"
            "{# a comment naming my_macro('x') #}",
            {"a"},
        ),
    ],
)
def test_referenced_models_reads_ref_calls(sql: str, refs: set[str]) -> None:
    assert referenced_models(sql) == refs


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM {{ my_ref('a') }}",
        "SELECT * FROM {{ ref(var('upstream')) }}",
        "{% for t in ['a', 'b'] %} SELECT * FROM {{ ref(t) }} {% endfor %}",
        "{% set rel = upstream_relation() %} SELECT * FROM {{ rel }}",
        "SELECT {{ dbt_utils.star(from=ref('a')) }} FROM {{ ref('a') }}",
    ],
)
def test_referenced_models_is_unknown_when_jinja_calls_something_else(sql: str) -> None:
    """A macro or a computed ref could reach any model (Codex review, round 1)."""
    assert referenced_models(sql) is None


async def test_redispatch_lists_a_model_whose_refs_cannot_be_read(tmp_path: Path) -> None:
    """`via_macro` reaches `a` through a macro: when `a` changes after an all-PASS report it is
    listed, while the plain `c` is not."""
    workspace, snapshot = tmp_path / "ws", tmp_path / "snapshot"
    tiny_project(snapshot / "project", {})
    tiny_project(
        workspace / "project",
        {"a": "SELECT 1 AS x\n", "c": "SELECT 3\n", "via_macro": "SELECT * FROM {{ up('a') }}\n"},
    )
    hooks = hooks_for(workspace, snapshot)
    await dispatch(hooks)
    await report(hooks, a="PASS", c="PASS", via_macro="PASS")
    edit(workspace, "a", "SELECT 2 AS x\n")
    assert await dispatch(hooks) == ["a", "via_macro"]


async def test_redispatch_follows_refs_through_an_unchanged_model(tmp_path: Path) -> None:
    """`a` changed -> `mid` (untouched since the snapshot) refs a -> `top` (changed) refs mid:
    top's output follows a, so it is listed; mid is not, being no changed model (spec review,
    round 2)."""
    workspace, snapshot = tmp_path / "ws", tmp_path / "snapshot"
    mid = "SELECT * FROM {{ ref('a') }}\n"
    tiny_project(snapshot / "project", {"a": "SELECT 0 AS x\n", "mid": mid})
    tiny_project(
        workspace / "project",
        {
            "a": "SELECT 1 AS x\n",
            "mid": mid,
            "top": "SELECT * FROM {{ ref('mid') }}\n",
            "c": "SELECT 3\n",
        },
    )
    hooks = hooks_for(workspace, snapshot)
    assert await dispatch(hooks) == ["a", "c", "top"]
    await report(hooks, a="PASS", c="PASS", top="PASS")
    edit(workspace, "a", "SELECT 2 AS x\n")
    assert await dispatch(hooks) == ["a", "top"]
