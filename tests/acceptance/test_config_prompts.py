"""Prompts and business context in the product (.scratch/product-config/spec.md "分节" `[prompts]`,
`[instructions]`, `[sources.<名字>]`; decisions.md Q2, Q8).

Written by ticket 141. Every test here is xfail(strict) until ticket 148 makes it pass and removes
the marker, changing nothing else. The seam: `data-agent run` in-process, the session stopped at
the SDK's client, which records the task prompt it was sent (tests/acceptance/
product_config_support.py).

The contract fixed here (for the human review):

1. A product query run's task prompt no longer holds today's answer rule (`EVAL_ANSWER_RULE`
   below, `agent.ANSWER_RULE` at main e494e49) but the product's own (written by ticket 148; only
   its absence of the old rule is asserted); `[prompts].answer_rule` given, that text is in the
   task prompt instead.
2. `[instructions].files`: each file's text is in the system prompt.
3. `[sources.<name>].description` is in what the agent is told (the system prompt or the task
   prompt); `[sources.<name>].docs` (a directory) is readable in the run's workspace: a file of it
   is somewhere under the workspace, its text unchanged, while the session runs.
4. A product dbt run's task prompt has no `DB_TYPE` sentence, and always has the task file note
   (`The task above is also saved, read-only, at ...`), the verifier layer off too.

The evaluations keep today's prompts (`DB_TYPE` on data-eng-bench only; the answer rule of DAB,
LiveSQLBench and jaffle): tests/acceptance/test_config_snapshot.py guards that.
"""

import json
from pathlib import Path

import pytest
from product_config_support import (
    INSTRUCTIONS,
    INSTRUCTIONS_MARKER,
    SOURCE_DOCS,
    SOURCE_DOCS_FILE,
    SOURCE_DOCS_MARKER,
    USER_ANSWER_RULE,
    Capture,
    assert_session,
    dbt_args,
    fake_home,
    query_args,
    run_cli,
    stop_sessions,
    system_prompt_of,
    toml_path,
    write_file,
)
from seam_support import build_shop_duckdb

TICKET_148 = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="ticket 148: the prompts and business context do not come from the config yet",
)
pytestmark = TICKET_148

# `agent.ANSWER_RULE` at main e494e49 (agent.py:212-218): the evaluations' answer rule, which
# the product's prompt drops. Copied, so this test still knows it once ticket 148 moves it.
EVAL_ANSWER_RULE = (
    "End with a final message that gives the answer first. The answer holds only what the task "
    "asks for: no rows, columns or entities it did not ask about. Put each value right after its "
    "name, with no other column between them. Write names and text exactly as they appear in the "
    "data; do not shorten them or add an ellipsis. After the answer, state your assumptions in "
    "short sentences: no tables, no alternative answers or alternative methods.\n"
)
DB_TYPE_SENTENCE = "When the task asks you to print `DB_TYPE`"
TASK_FILE_NOTE = "The task above is also saved, read-only, at"
DESCRIPTION = "SHOP-DESCRIPTION-MARKER: the shop's orders since 2019, one row per order."


@pytest.fixture
def capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Capture:
    return stop_sessions(monkeypatch, fake_home(tmp_path))


def test_the_product_answer_rule_replaces_the_evaluations(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    argv, _ = query_args(tmp_path / "default")
    default = assert_session(run_cli(capsys, argv), capture).prompt
    capture.sessions.clear()
    config = write_file(
        tmp_path / "prompts.toml", f"[prompts]\nanswer_rule = {json.dumps(USER_ANSWER_RULE)}\n"
    )
    argv, _ = query_args(tmp_path / "given", "--config", str(config))
    given = assert_session(run_cli(capsys, argv), capture).prompt

    assert EVAL_ANSWER_RULE not in default
    assert USER_ANSWER_RULE in given
    assert EVAL_ANSWER_RULE not in given


def test_instructions_files_go_into_the_system_prompt(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_file(
        tmp_path / "rules.toml", f"[instructions]\nfiles = [{toml_path(INSTRUCTIONS)}]\n"
    )
    argv, _ = query_args(tmp_path / "run", "--config", str(config))
    options = assert_session(run_cli(capsys, argv), capture).options

    assert INSTRUCTIONS_MARKER in system_prompt_of(options)
    assert INSTRUCTIONS.read_text().strip() in system_prompt_of(options)


def docs_in_workspace(workspace: Path) -> list[str]:
    return [path.read_text() for path in workspace.rglob(SOURCE_DOCS_FILE) if path.is_file()]


def test_a_sources_description_and_docs_reach_the_agent(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    db = build_shop_duckdb(tmp_path / "shop.duckdb")
    config = write_file(
        tmp_path / "sources.toml",
        f'[sources.shop]\nkind = "duckdb"\npath = {toml_path(db)}\n'
        f"description = {json.dumps(DESCRIPTION)}\ndocs = {toml_path(SOURCE_DOCS)}\n",
    )
    argv, _ = query_args(tmp_path / "run", "--config", str(config), database=False)
    workspace = Path(argv[argv.index("--workspace") + 1])
    capture.probe = lambda options: docs_in_workspace(Path(str(options.cwd or workspace)))
    session = assert_session(run_cli(capsys, argv), capture)

    told = system_prompt_of(session.options) + session.prompt
    assert DESCRIPTION in told
    docs = session.probed
    assert isinstance(docs, list), f"the probe failed: {docs!r}"
    assert any(SOURCE_DOCS_MARKER in text for text in docs), "the docs are not in the workspace"


@pytest.mark.parametrize("verifiers", [True, False], ids=["verifiers-on", "verifiers-off"])
def test_a_product_dbt_prompt_has_no_db_type_and_always_the_task_file(
    verifiers: bool, tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    layer = "true" if verifiers else "false"
    config = write_file(tmp_path / "layers.toml", f"[layers]\nverifiers = {layer}\n")
    argv, _ = dbt_args(tmp_path / "run", "--config", str(config))
    prompt = assert_session(run_cli(capsys, argv), capture).prompt

    assert DB_TYPE_SENTENCE not in prompt
    assert TASK_FILE_NOTE in prompt
