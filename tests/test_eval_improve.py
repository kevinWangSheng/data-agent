"""`evals improve` beyond the acceptance tests (ticket 126): the push guard, the edit judge's
prompt and reply parsing, retries, tool failures, the same-day guard and the CLI. The round runs
in the acceptance tests' world (tests/acceptance/skill_improve_support.py): fake judges, fake
`da-remote` and `gh`, a temporary repo with a bare origin."""

import asyncio
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from acceptance.skill_improve_support import (
    FIXTURES,
    MANIFEST,
    REGRESSION_FILE,
    REMOTE_MANIFEST,
    REMOTE_TASKS_DIR,
    REPLIES,
    TODAY,
    TaskRun,
    World,
    git,
    real_repo_branches,
    write_dab_run,
    write_exe,
    write_input_job,
)

from evals.__main__ import main
from evals.improve import (
    EditReply,
    Group,
    InputRefused,
    RoundRefused,
    _Ledger,  # pyright: ignore[reportPrivateUsage]
    apply_edit,
    branch_ref,
    candidate_tasks,
    delete_remote_branch,
    edit_prompt,
    improve,
    parse_edit,
    push_branch,
    skill_path,
)

SKILL_FILE = "skills/sql-workflow/SKILL.md"
SKILL = (FIXTURES / "repo" / SKILL_FILE).read_bytes()


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    before = real_repo_branches()
    yield World(tmp_path, monkeypatch)
    assert real_repo_branches() == before, "a test made a skill-improve branch in this repo"


def wrap(world: World, tool: str, script: str) -> None:
    """Put a `tool` first on PATH that runs `script` (bash) and otherwise hands the call to the
    world's fake of the same name."""
    fake = world.tmp / "bin" / tool
    write_exe(world.tmp / "wrap" / tool, f'#!/bin/bash\n{script}\nexec {fake} "$@"\n')
    os.environ["PATH"] = f"{world.tmp / 'wrap'}{os.pathsep}{os.environ['PATH']}"


# --- the push guard ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "branch", ["main", "feature/x", "skill-improve/", "skill-improve/a:main", "x/skill-improve/1"]
)
def test_only_skill_improve_branches_are_ever_pushed_or_deleted(
    branch: str, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="refusing"):
        branch_ref(branch)
    # Refused before git runs: the working directory does not even exist.
    missing = tmp_path / "no-repo"
    with pytest.raises(ValueError, match="refusing"):
        push_branch(missing, branch)
    with pytest.raises(ValueError, match="refusing"):
        delete_remote_branch(missing, branch)


def test_a_skill_improve_branch_is_pushed_by_its_full_ref(world: World) -> None:
    assert branch_ref("skill-improve/2026-11-24-1") == "refs/heads/skill-improve/2026-11-24-1"
    git("branch", "skill-improve/2026-11-24-1", world.base, cwd=world.repo)
    push_branch(world.repo, "skill-improve/2026-11-24-1")
    assert world.origin_branches()["skill-improve/2026-11-24-1"] == world.base
    delete_remote_branch(world.repo, "skill-improve/2026-11-24-1")
    assert "skill-improve/2026-11-24-1" not in world.origin_branches()
    git("branch", "-D", "skill-improve/2026-11-24-1", cwd=world.repo)


# --- edit replies, paths and prompts -------------------------------------------------------------


def test_an_edit_reply_is_read_from_a_fenced_json_object() -> None:
    reply = parse_edit(
        'Here:\n```json\n{"file": "skills/a/SKILL.md", "op": "insert_after", '
        '"anchor": "x\\n", "text": "y\\n"}\n```'
    )
    assert reply == EditReply("skills/a/SKILL.md", "insert_after", "x\n", "y\n")


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("no json here", "no JSON object"),
        ('{"file": "skills/a/SKILL.md", "op": "replace", "anchor": "x"}', "text is missing"),
        ('{"file": "skills/a/SKILL.md", "op": "delete", "anchor": "x", "text": ""}', "op"),
        ('{"file": "skills/a/SKILL.md", "op": "replace", "anchor": "", "text": "y"}', "empty"),
    ],
)
def test_an_edit_reply_that_is_no_edit_is_refused(text: str, problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        parse_edit(text)


@pytest.mark.parametrize(
    ("file", "path"),
    [
        ("skills/a/SKILL.md", "skills/a/SKILL.md"),
        ("skills/./a/SKILL.md", "skills/a/SKILL.md"),
        ("skills/a/../b/SKILL.md", "skills/b/SKILL.md"),
        ("skills/../README.md", None),
        ("../skills/a/SKILL.md", None),
        ("/skills/a/SKILL.md", None),
        ("skills", None),
        ("src/skills/a.md", None),
    ],
)
def test_an_edit_file_is_taken_only_under_skills(file: str, path: str | None) -> None:
    assert skill_path(file) == path


def test_an_anchor_occurring_twice_is_ambiguous() -> None:
    problem = apply_edit(b"a\nb\na\n", EditReply("skills/x/SKILL.md", "replace", "a\n", "c\n"))
    assert not isinstance(problem, bytes)
    assert problem.code == "ambiguous-match"


def test_candidate_tasks_put_sorted_targets_first_then_the_other_regression_tasks() -> None:
    assert candidate_tasks(["t-b", "r-2", "t-a"], ["r-1", "r-2", "r-3"]) == (
        "r-2", "t-a", "t-b", "r-1", "r-3",
    )  # fmt: skip


def group(skill: str = "sql-workflow", quote: str = "Prefer one query per question.") -> Group:
    return Group("FM2", skill, quote, 2, ("task-e",), ("Split a question into parts.",))


def test_the_edit_prompt_holds_the_group_and_the_skill_file_and_nothing_else() -> None:
    prompt = edit_prompt(group(), {SKILL_FILE: SKILL})
    assert "FM2: wrong plan" in prompt
    assert "> Prefer one query per question." in prompt
    assert "- Split a question into parts." in prompt
    assert f'<file path="{SKILL_FILE}">\n{SKILL.decode()}</file>' in prompt
    assert "task-e" not in prompt  # targets are counted, not named
    assert "at most 10 lines net" in prompt


def test_an_edit_prompt_without_skill_text_or_usable_suggestions_still_asks_for_a_rule() -> None:
    bare = Group("FM1", "none", "", 1, ("task-a",), ())
    prompt = edit_prompt(bare, {SKILL_FILE: SKILL, "skills/dbt-workflow/SKILL.md": b"dbt\n"})
    assert "No Skill text covered these failures" in prompt
    assert "none usable" in prompt
    assert 'path="skills/dbt-workflow/SKILL.md"' in prompt


# --- the round's own checks ----------------------------------------------------------------------


def test_the_budget_left_rounds_down_to_the_cent() -> None:
    ledger = _Ledger(25.0)
    ledger.judges = Decimal("0.125")
    assert ledger.left() == Decimal("24.87")
    ledger.screenings = Decimal("24.87")
    assert ledger.left() == Decimal("0.00")
    assert not ledger.can_screen()


def test_a_second_round_of_the_same_date_is_refused_before_any_judge_call(world: World) -> None:
    git("branch", "skill-improve/2026-11-24-7", world.base, cwd=world.repo)
    job = world.input_job({"task-a": TaskRun(0, "alpha")})
    try:
        with pytest.raises(RoundRefused, match="skill-improve/2026-11-24-7"):
            world.run([job])
    finally:
        git("branch", "-D", "skill-improve/2026-11-24-7", cwd=world.repo)
    assert world.attribution_judge.prompts == []


def test_an_earlier_pulled_run_of_the_date_refuses_the_round(world: World) -> None:
    (world.repo / "runs" / "improve-2026-11-24-1").mkdir(parents=True)
    job = world.input_job({"task-a": TaskRun(0, "alpha")})
    with pytest.raises(RoundRefused, match="improve-2026-11-24-1"):
        world.run([job])


def test_a_run_on_a_dirty_checkout_is_refused(world: World) -> None:
    job = write_input_job(
        world.tmp / "runs-in" / "dirty",
        {t: TaskRun(0, "alpha") if t == "task-a" else TaskRun() for t in world_dev()},
        commit=f"{world.base}-dirty",
        skill_version=world.skill_version,
        started=datetime.now(UTC) - timedelta(days=1),
    )
    with pytest.raises(InputRefused, match="not a clean full commit"):
        world.run([job])
    assert world.attribution_judge.prompts == []


def world_dev() -> list[str]:
    return json.loads((FIXTURES / "manifest.json").read_text())["dev"]


def test_an_edit_naming_a_task_is_retried_once_then_discarded(world: World) -> None:
    world.edits["alpha"] = {**REPLIES["edits"]["alpha"], "text": "1. Read task-a's schema.\n"}
    job = world.input_job({"task-a": TaskRun(0, "alpha")})

    report = world.run([job])

    (candidate,) = report.candidates
    assert (candidate.discarded, candidate.outcome) == ("bad-reply", "discarded")
    assert "task-a" in (candidate.reason or "")
    assert len(world.edit_judge.prompts) == 2
    assert "Your previous reply was rejected" in world.edit_judge.prompts[1]
    assert world.starts() == []


def test_an_edit_outside_skills_is_not_retried(world: World) -> None:
    world.edits["alpha"] = {**REPLIES["edits"]["alpha"], "file": "README.md"}
    job = world.input_job({"task-a": TaskRun(0, "alpha")})
    report = world.run([job])
    assert report.candidates[0].discarded == "outside-skills"
    assert len(world.edit_judge.prompts) == 1


# --- tool failures stop the round, delete the remote branch and keep the report ------------------


def three_candidates(world: World) -> Path:
    return world.input_job(
        {"task-a": TaskRun(0, "alpha"), "task-c": TaskRun(1, "beta"), "task-d": TaskRun(2, "gamma")}
    )


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        (
            'if [ "$1" = status ]; then echo "$2: stopped without an exit code"; exit 0; fi',
            "stopped without an exit code",
        ),
        ('if [ "$1" = status ]; then echo "$2: finished, exit 1"; exit 0; fi', "ablate exited 1"),
        ('if [ "$1" = setup ]; then echo "setup: a run is alive" >&2; exit 1; fi', "setup"),
    ],
    ids=["stopped", "ablate-exit-1", "setup-refused"],
)
def test_a_failed_screening_leaves_the_candidate_undecided_and_stops_the_rest(
    world: World, script: str, reason: str
) -> None:
    wrap(world, "da-remote", script)
    report = world.run([three_candidates(world)])

    first, *rest = report.candidates
    assert first.outcome == "undecided"
    assert reason in (first.reason or "")
    assert [c.outcome for c in rest] == ["not-screened", "not-screened"]
    assert len(world.starts()) <= 1
    assert set(world.origin_branches()) == {"main"}
    assert world.calls("gh") == []
    report_md = world.report_md()
    assert "Screening stopped after candidate 1" in report_md
    assert first.reason is not None
    assert f"Screening stopped after candidate 1: {first.reason.split(': ', 1)[1]}" in report_md
    assert json.loads((world.out / "improve.json").read_text())["candidates"][0]["outcome"] == (
        "undecided"
    )


def test_status_failures_are_polled_again_until_the_run_finishes(world: World) -> None:
    counter = world.tmp / "status-failures"
    wrap(
        world,
        "da-remote",
        f'if [ "$1" = status ] && [ ! -e {counter}.2 ]; then '
        f"if [ -e {counter}.1 ]; then touch {counter}.2; else touch {counter}.1; fi; "
        'echo "ssh: connection reset" >&2; exit 255; fi',
    )
    job = world.input_job({"task-a": TaskRun(0, "alpha")})
    report = world.run([job])
    assert report.candidates[0].outcome == "pr"


def test_a_rejected_push_leaves_the_candidate_undecided_and_stops_the_rest(world: World) -> None:
    write_exe(world.origin / "hooks" / "pre-receive", "#!/bin/sh\necho no >&2\nexit 1\n")
    report = world.run([three_candidates(world)])
    assert report.candidates[0].outcome == "undecided"
    assert "push failed" in (report.candidates[0].reason or "")
    assert world.starts() == []
    assert [c.outcome for c in report.candidates[1:]] == ["not-screened", "not-screened"]


def test_a_failed_pr_create_keeps_the_passing_branch_on_origin(world: World) -> None:
    wrap(world, "gh", 'echo "gh: not logged in" >&2; exit 4')
    job = world.input_job({"task-a": TaskRun(0, "alpha")})
    report = world.run([job])
    (candidate,) = report.candidates
    assert (candidate.verdict, candidate.outcome) == ("pass", "undecided")
    assert "gh pr create" in (candidate.reason or "")
    assert candidate.branch in world.origin_branches()


def test_an_old_version_that_cannot_be_reused_asks_for_a_new_dev_run(world: World) -> None:
    job = world.input_job(
        {"task-a": TaskRun(0, "alpha"), "task-c": TaskRun(1, "beta")},
        started=datetime.now(UTC) - timedelta(20),
    )
    report = world.run([job])
    assert [c.outcome for c in report.candidates] == ["undecided", "undecided"]
    assert world.report_md().count("run the dev set anew") == 1


def test_the_report_and_pr_body_mask_secrets(world: World) -> None:
    job = world.input_job({"task-a": TaskRun(0, "alpha")})

    secret = REPLIES["attribution"]["alpha"]["skill_quote"].split()[2]  # "schema"
    asyncio.run(
        improve(
            [job], repo=world.repo, manifest=MANIFEST, regression=REGRESSION_FILE,
            out_dir=world.out, attribution_judge=world.attribution_judge,
            edit_judge=world.edit_judge, skills=world.skills, today=TODAY,
            remote_tasks_dir=REMOTE_TASKS_DIR, remote_manifest=REMOTE_MANIFEST, poll_seconds=0,
            secrets=[secret],
        )
    )  # fmt: skip
    assert secret not in world.report_md()
    assert secret not in (world.out / "pr-1.md").read_text()
    assert secret not in (world.out / "improve.json").read_text()


# --- CLI -----------------------------------------------------------------------------------------


def test_the_improve_subcommand_has_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        main(["improve", "--help"])
    assert exit_.value.code == 0
    out = capsys.readouterr().out
    for option in ("--manifest", "--regression", "--out", "--repo", "--date", "--budget"):
        assert option in out


def test_the_cli_refuses_a_dab_run_without_model_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ("DATA_AGENT_BASE_URL", "DATA_AGENT_API_KEY", "DATA_AGENT_MODEL"):
        monkeypatch.delenv(key, raising=False)
    dab = write_dab_run(tmp_path / "dab")
    code = main(
        ["improve", str(dab), "--manifest", str(FIXTURES / "manifest.json"),
         "--regression", str(FIXTURES / "regression.json"), "--out", str(tmp_path / "out")]
    )  # fmt: skip
    assert code == 2
    assert "not a Harbor job" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


# --- review round 1 (da-review) ------------------------------------------------------------------


def test_a_manifest_listing_a_holdout_task_as_dev_is_refused_before_any_judge_call(
    world: World,
) -> None:
    job = world.input_job({"task-a": TaskRun(0, "alpha")})
    leaky = {**MANIFEST, "holdout": [*MANIFEST["holdout"], "task-a"]}
    with pytest.raises(InputRefused, match="holds out tasks the dev list names"):
        asyncio.run(
            improve(
                [job], repo=world.repo, manifest=leaky, regression=REGRESSION_FILE,
                out_dir=world.out, attribution_judge=world.attribution_judge,
                edit_judge=world.edit_judge, skills=world.skills, today=TODAY,
                remote_tasks_dir=REMOTE_TASKS_DIR, remote_manifest=REMOTE_MANIFEST,
                poll_seconds=0,
            )
        )  # fmt: skip
    assert world.attribution_judge.prompts == []


def test_a_run_pulled_without_its_job_has_unknown_spend_not_zero(world: World) -> None:
    # The preflight failed: ablate exits 2 and the pull brings back the summary only.
    fake = world.tmp / "bin" / "da-remote"
    top = '"$(git rev-parse --show-toplevel)"'
    wrap(
        world,
        "da-remote",
        'if [ "$1" = status ]; then echo "$2: finished, exit 2"; exit 0; fi\n'
        f'if [ "$1" = pull ]; then {fake} "$@"; rc=$?; '
        f'rm -rf {top}/runs/"$2"/jobs {top}/runs/"$2"/smoke; exit $rc; fi',
    )
    report = world.run([three_candidates(world)])

    first = report.candidates[0]
    assert first.outcome == "undecided"
    assert "spend is unknown" in (first.reason or "")
    assert "Spend of `improve-2026-11-24-1` is unknown here" in world.report_md()
    assert [c.outcome for c in report.candidates[1:]] == ["not-screened", "not-screened"]


# --- review round 2 (eng:code-review) ------------------------------------------------------------


def test_edit_calls_the_budget_refuses_are_named_on_a_stopped_line(world: World) -> None:
    job = world.input_job({"task-a": TaskRun(0, "alpha")})
    # Three attribution calls at $0.02 use up $0.06: no edit call is made.
    report = asyncio.run(
        improve(
            [job], repo=world.repo, manifest=MANIFEST, regression=REGRESSION_FILE,
            out_dir=world.out, attribution_judge=world.attribution_judge,
            edit_judge=world.edit_judge, skills=world.skills, today=TODAY,
            remote_tasks_dir=REMOTE_TASKS_DIR, remote_manifest=REMOTE_MANIFEST,
            budget_usd=0.06, poll_seconds=0,
        )
    )  # fmt: skip
    (candidate,) = report.candidates
    assert (candidate.outcome, candidate.discarded) == ("discarded", "bad-reply")
    assert "edit judge not called" in (candidate.reason or "")
    assert world.edit_judge.prompts == []
    stopped = [line for line in world.report_md().splitlines() if "stopped" in line.lower()]
    assert stopped == [
        "- Edit calls stopped at candidate 1: the budget is used up, so candidate(s) 1 got no edit"
    ]
