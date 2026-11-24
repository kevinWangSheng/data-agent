"""Skill improvement from dev-set failures (.scratch/skill-improve/spec.md "Testing Decisions"):
input scope, candidate generation, branches and commits, screening, verdicts and budget.

Written by ticket 125 on the shell evals/improve.py, whose docstring holds the contract (1-10)
these tests pin. Every test is marked `TICKET_126`; ticket 126 builds `improve`, then drops the
marks and changes nothing else. Today each fails at its call of `improve`, with "ticket 126:
improve is not implemented yet" (skill_improve_support.World.run): every fixture is built before
that call, and no negative assertion comes before a positive one, so none passes on the shell.

No model, server, GitHub or network is reached, and nothing of this repo's git state is touched:
see skill_improve_support. The `world` fixture checks after each test that this repository has
no `skill-improve/*` branch the test did not find there.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from skill_improve_support import (
    BUDGET_USD,
    DEV,
    FIXTURES,
    JUDGE_COST_USD,
    MAX_TASKS,
    NOISE_NOTE,
    REGRESSION,
    REMOTE_MANIFEST,
    REMOTE_TASKS_DIR,
    REPLIES,
    Screen,
    TaskRun,
    World,
    git,
    option_values,
    real_repo_branches,
    write_dab_run,
)

from evals.improve import Candidate, ImproveReport

SKILL_FILE = "skills/sql-workflow/SKILL.md"
ORIGINAL_SKILL = (FIXTURES / "repo" / SKILL_FILE).read_bytes()
BETA_LINE = "2. Stop exploring after 3-5 tool calls.\n"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    before = real_repo_branches()
    yield World(tmp_path, monkeypatch)
    assert real_repo_branches() == before, "a test made a skill-improve branch in this repo"


def key(marker: str) -> tuple[str, str, str]:
    reply = REPLIES["attribution"][marker]
    return reply["failure_mode"], reply["skill"], reply["skill_quote"]


def group_key(candidate: Candidate) -> tuple[str, str, str]:
    group = candidate.group
    return group.failure_mode, group.skill, group.skill_quote


def only_candidate(report: ImproveReport) -> Candidate:
    assert len(report.candidates) == 1, f"{len(report.candidates)} candidates, expected one"
    return report.candidates[0]


def edit(
    world: World, marker: str, op: str, anchor: str, text: str, file: str = SKILL_FILE
) -> None:
    world.edits[marker] = {"file": file, "op": op, "anchor": anchor, "text": text}


def assert_no_branch_and_no_screening(world: World) -> None:
    assert set(world.local_branches()) == {"main"}
    assert set(world.origin_branches()) == {"main"}
    assert world.starts() == []


# --- input scope ---------------------------------------------------------------------------------


def test_holdout_trials_reach_neither_judge_nor_any_candidate(world: World) -> None:
    job = world.input_job({"task-a": TaskRun(0, "alpha"), "held-1": TaskRun(0, "alpha")})

    report = world.run([job])

    candidate = only_candidate(report)
    assert candidate.group.targets == ("task-a",)
    assert len(world.attribution_judge.prompts) == 3  # task-a's three failed trials
    assert world.edit_judge.prompts
    assert report.skipped_non_dev_trials == 3
    (start,) = world.starts()
    assert "task-a" in start.tasks
    prompts = world.attribution_judge.prompts + world.edit_judge.prompts
    assert [p for p in prompts if "held-1" in p] == []
    assert "held-1" not in candidate.tasks
    assert "held-1" not in start.tasks


def test_a_dab_run_is_refused_before_any_judge_branch_or_screening(world: World) -> None:
    dab = write_dab_run(world.tmp / "dab-out")

    refused = world.refused([dab])

    assert str(refused)
    assert world.attribution_judge.prompts == []
    assert world.edit_judge.prompts == []
    assert_no_branch_and_no_screening(world)
    assert world.calls("da-remote") == []
    assert world.calls("gh") == []


def test_a_run_on_a_skill_version_not_in_the_skill_sets_is_refused(world: World) -> None:
    job = world.input_job({"task-a": TaskRun(0, "alpha")}, skill_version="000000000000")

    refused = world.refused([job])

    assert str(refused)
    assert world.attribution_judge.prompts == []
    assert_no_branch_and_no_screening(world)


def test_the_failures_of_two_input_jobs_are_attributed_together(world: World) -> None:
    first = world.input_job({"task-a": TaskRun(1, "alpha")}, name="u1", cover=["task-a", "task-b"])
    rest = [t for t in DEV if t not in ("task-a", "task-b")]
    second = world.input_job({"task-d": TaskRun(1, "alpha")}, name="u2", cover=rest)

    report = world.run([first, second])

    candidate = only_candidate(report)
    assert candidate.group.targets == ("task-a", "task-d")
    assert candidate.group.failures == 4
    prompts = world.attribution_judge.prompts
    for trial in ("task-a__1", "task-a__2", "task-d__1", "task-d__2"):
        assert any(f"Trace of {trial}:" in p for p in prompts), trial


# --- candidate generation ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "text", "expected", "net"),
    [
        (
            "replace",
            "2. Stop exploring after 3-5 tool calls; then write a first answer.\n",
            ORIGINAL_SKILL.replace(
                BETA_LINE.encode(),
                b"2. Stop exploring after 3-5 tool calls; then write a first answer.\n",
            ),
            0,
        ),
        (
            "insert_after",
            "   Then write a first answer and refine it.\n",
            ORIGINAL_SKILL.replace(
                BETA_LINE.encode(),
                BETA_LINE.encode() + b"   Then write a first answer and refine it.\n",
            ),
            1,
        ),
    ],
    ids=["replace", "insert_after"],
)
def test_an_edit_whose_anchor_matches_byte_for_byte_is_applied(
    world: World, op: str, text: str, expected: bytes, net: int
) -> None:
    edit(world, "beta", op, BETA_LINE, text)
    job = world.input_job({"task-c": TaskRun(0, "beta")})

    report = world.run([job])

    candidate = only_candidate(report)
    assert candidate.discarded is None
    assert candidate.branch is not None
    assert world.show(candidate.branch, SKILL_FILE) == expected
    assert candidate.net_lines == net


@pytest.mark.parametrize(
    "anchor",
    [
        "2. Stop exploring after 3-6 tool calls.\n",  # other words
        "2. Stop exploring  after 3-5 tool calls.\n",  # only the whitespace differs
    ],
    ids=["other-words", "whitespace-only"],
)
def test_an_anchor_not_in_the_file_byte_for_byte_gives_no_candidate(
    world: World, anchor: str
) -> None:
    edit(world, "beta", "replace", anchor, "2. Stop exploring after 3 tool calls.\n")
    job = world.input_job({"task-c": TaskRun(0, "beta")})

    report = world.run([job])

    candidate = only_candidate(report)
    assert candidate.discarded == "no-match"
    assert candidate.outcome == "discarded"
    assert "no-match" in world.report_md()
    assert candidate.branch is None
    assert_no_branch_and_no_screening(world)


def lines_replacing_beta(count: int) -> str:
    return "".join(f"2.{i} Stop exploring after 3-5 tool calls, step {i}.\n" for i in range(count))


def test_an_edit_adding_net_ten_lines_is_a_candidate(world: World) -> None:
    # One line replaced by eleven: 11 added, 1 deleted.
    edit(world, "beta", "replace", BETA_LINE, lines_replacing_beta(11))
    job = world.input_job({"task-c": TaskRun(0, "beta")})

    report = world.run([job])

    candidate = only_candidate(report)
    assert candidate.discarded is None
    assert candidate.net_lines == 10
    assert candidate.branch is not None
    assert candidate.branch in world.local_branches()
    assert len(world.starts()) == 1


def test_an_edit_adding_net_eleven_lines_gives_no_candidate(world: World) -> None:
    # One line replaced by twelve: 12 added, 1 deleted.
    edit(world, "beta", "replace", BETA_LINE, lines_replacing_beta(12))
    job = world.input_job({"task-c": TaskRun(0, "beta")})

    report = world.run([job])

    candidate = only_candidate(report)
    assert candidate.discarded == "net-lines"
    assert "net-lines" in world.report_md()
    assert candidate.branch is None
    assert_no_branch_and_no_screening(world)


@pytest.mark.parametrize("file", ["README.md", "skills/../README.md"])
def test_an_edit_outside_skills_gives_no_candidate(world: World, file: str) -> None:
    # README.md holds the anchor line too, so only the path rules it out.
    edit(world, "beta", "replace", BETA_LINE, "2. Stop exploring after 3 tool calls.\n", file)
    job = world.input_job({"task-c": TaskRun(0, "beta")})

    report = world.run([job])

    candidate = only_candidate(report)
    assert candidate.discarded == "outside-skills"
    assert "outside-skills" in world.report_md()
    assert candidate.branch is None
    assert_no_branch_and_no_screening(world)


@pytest.mark.parametrize(("n_regression", "screened"), [(MAX_TASKS - 1, True), (MAX_TASKS, False)])
def test_a_candidate_over_thirty_tasks_is_discarded(
    world: World, n_regression: int, screened: bool
) -> None:
    regression = [*REGRESSION, *(f"bulk-{i:02d}" for i in range(1, 29))][:n_regression]
    assert len(regression) == n_regression
    job = world.input_job({"task-a": TaskRun(0, "alpha")})

    report = world.run([job], regression=world.regression_file(regression))

    candidate = only_candidate(report)
    assert len(candidate.tasks) == n_regression + 1
    if screened:
        assert candidate.discarded is None
        (start,) = world.starts()
        assert len(start.tasks) == MAX_TASKS
        assert set(start.tasks) == {"task-a", *regression}
    else:
        assert candidate.discarded == "too-many-tasks"
        assert "too-many-tasks" in world.report_md()
        assert_no_branch_and_no_screening(world)


def test_one_skill_passage_gives_one_candidate(world: World) -> None:
    # Two failure modes quote the same sentence: FM1 with three failures, FM4 with one.
    job = world.input_job({"task-c": TaskRun(0, "beta"), "task-d": TaskRun(2, "beta-fm4")})

    report = world.run([job])

    assert [(g.failure_mode, g.dropped) for g in report.groups] == [
        ("FM1", None),
        ("FM4", "same-passage"),
    ]
    candidate = only_candidate(report)
    assert group_key(candidate) == key("beta")
    assert len(world.starts()) == 1
    fm4_suggestion = REPLIES["attribution"]["beta-fm4"]["suggestion"]
    assert world.edit_judge.prompts
    assert [p for p in world.edit_judge.prompts if fm4_suggestion in p] == []


def test_only_the_three_groups_with_the_most_failures_become_candidates(world: World) -> None:
    job = world.input_job(
        {
            "task-a": TaskRun(0, "alpha"),  # alpha: 3 + 1 = 4 failures
            "task-b": TaskRun(2, "alpha"),
            "task-c": TaskRun(0, "beta"),  # beta: 3
            "task-d": TaskRun(1, "gamma"),  # gamma: 2
            "task-e": TaskRun(2, "delta"),  # delta: 1
        }
    )

    report = world.run([job])

    assert [group_key(c) for c in report.candidates] == [key("alpha"), key("beta"), key("gamma")]
    assert [c.number for c in report.candidates] == [1, 2, 3]
    assert [g.failures for g in report.groups] == [4, 3, 2, 1]
    assert report.groups[3].dropped == "not-selected"
    starts = world.starts()
    assert len(starts) == 3
    delta_suggestion = REPLIES["attribution"]["delta"]["suggestion"]
    assert [p for p in world.edit_judge.prompts if delta_suggestion in p] == []
    assert [s for s in starts if "task-e" in s.tasks] == []


# --- branches and commits ------------------------------------------------------------------------


def test_each_candidate_is_one_commit_on_the_input_commit_changing_skills_only(
    world: World,
) -> None:
    job = world.input_job({"task-a": TaskRun(0, "alpha"), "task-c": TaskRun(1, "beta")})

    report = world.run([job])

    assert len(report.candidates) == 2
    local = world.local_branches()
    for n, candidate in enumerate(report.candidates, start=1):
        assert candidate.branch == f"skill-improve/2026-11-24-{n}"
        assert candidate.commit is not None
        assert local.get(f"skill-improve/2026-11-24-{n}") == candidate.commit
        assert git("rev-parse", f"{candidate.commit}^", cwd=world.repo) == world.base
        assert (
            git("rev-list", "--count", f"{world.base}..{candidate.commit}", cwd=world.repo) == "1"
        )
        changed = git("diff", "--name-only", world.base, candidate.commit, cwd=world.repo)
        assert changed.splitlines() == [SKILL_FILE]
        assert world.skill_version_at(candidate.commit) != world.skill_version


def test_a_branch_is_on_origin_with_its_commit_when_its_screening_starts(world: World) -> None:
    job = world.input_job({"task-a": TaskRun(0, "alpha"), "task-c": TaskRun(1, "beta")})
    world.plan(Screen(wins={"task-a": 0, "task-b": 0}), Screen())  # candidate 1 fails

    report = world.run([job])

    starts = world.starts()
    assert len(starts) == len(report.candidates) == 2
    for start, candidate in zip(starts, report.candidates, strict=True):
        assert candidate.branch is not None
        assert start.origin.get(candidate.branch) == candidate.commit
        assert start.setup_rev == candidate.commit
    assert report.candidates[0].verdict == "fail"
    assert report.candidates[0].branch not in world.origin_branches()


# --- screening -----------------------------------------------------------------------------------


def test_each_screening_runs_targets_and_regression_on_full_three_times_within_budget(
    world: World,
) -> None:
    job = world.input_job(
        {"task-a": TaskRun(0, "alpha"), "task-b": TaskRun(1, "alpha"), "task-c": TaskRun(0, "beta")}
    )
    # The first screening bills (4 tasks x 3 trials x $1 + $0.50 smoke) x 0.5 off peak = $6.25.
    world.plan(Screen(cost_usd=1.0, smoke_cost_usd=0.5), Screen())

    report = world.run([job])

    starts = world.starts()
    assert len(starts) == len(report.candidates) == 2
    for start, candidate, spent in zip(
        starts, report.candidates, world.spend_before_each_start(), strict=True
    ):
        assert start.evals_args[0] == "ablate"
        assert start.options["--split"] == ["dev"]
        assert start.options["--config"] == ["full"]
        assert start.options["--trials"] == ["3"]
        assert start.options["--out"] == [f"runs/{start.name}"]
        assert start.options["--tasks-dir"] == [REMOTE_TASKS_DIR]
        assert start.options["--manifest"] == [REMOTE_MANIFEST]
        targets = list(candidate.group.targets)
        assert start.tasks == [*targets, *(t for t in REGRESSION if t not in targets)]
        assert len(start.tasks) <= MAX_TASKS
        (budget,) = start.options["--budget"]
        left = BUDGET_USD - spent
        assert left - 0.01 <= float(budget) <= left + 1e-9
    assert starts[0].tasks == ["task-a", "task-b", "reg-1", "reg-2"]
    judges = len(world.attribution_judge.prompts) + len(world.edit_judge.prompts)
    pulled = sum(e["billed_usd"] for e in world.calls("pull-spend"))
    assert abs(report.spend_usd - (judges * JUDGE_COST_USD + pulled)) < 1e-6


# --- verdicts ------------------------------------------------------------------------------------


def option(argv: list[str], name: str) -> list[str]:
    return option_values(argv).get(name, [])


def test_a_passing_candidate_keeps_its_branch_and_gets_a_draft_pr(world: World) -> None:
    job = world.input_job({"task-a": TaskRun(0, "alpha")})

    report = world.run([job])

    candidate = only_candidate(report)
    assert candidate.verdict == "pass"
    assert candidate.outcome == "pr"
    assert candidate.branch is not None
    assert world.origin_branches().get(candidate.branch) == candidate.commit
    (pr,) = world.pr_creates()
    assert "--draft" in pr["argv"]
    assert option(pr["argv"], "--head") == [candidate.branch]
    body = pr["body"]
    assert isinstance(body, str)
    assert NOISE_NOTE in body
    assert key("alpha")[0] in body
    assert key("alpha")[2] in body
    assert "task-a" in body
    assert REPLIES["edits"]["alpha"]["text"].strip() in body  # the diff


def test_a_failing_candidate_loses_its_remote_branch_and_gets_no_pr(world: World) -> None:
    job = world.input_job({"task-a": TaskRun(1, "alpha")})
    world.plan(Screen(wins={"task-a": 1}))  # no more successes than the old version

    report = world.run([job])

    candidate = only_candidate(report)
    (start,) = world.starts()
    assert "task-a" in start.tasks
    assert candidate.verdict == "fail"
    assert candidate.outcome == "rejected"
    assert candidate.branch is not None
    assert candidate.branch in world.report_md()
    assert "rejected" in world.report_md()
    assert "fail" in world.report_md()
    assert candidate.branch in world.local_branches()
    assert candidate.branch not in world.origin_branches()
    assert world.calls("gh") == []


def test_a_regression_task_below_three_is_rerun_alone_then_judged(world: World) -> None:
    job = world.input_job({"task-a": TaskRun(0, "alpha")})
    world.plan(Screen(wins={"reg-1": 2}), Screen())  # 2/3, then 3/3 on the rerun

    report = world.run([job])

    candidate = only_candidate(report)
    starts = world.starts()
    assert len(starts) == 2
    assert starts[1].tasks == ["reg-1"]
    assert starts[1].options["--config"] == ["full"]
    assert starts[1].options["--trials"] == ["3"]
    assert [s.verdict for s in candidate.screenings] == ["rerun-regression", "pass"]
    assert candidate.verdict == "pass"
    assert candidate.outcome == "pr"
    (pr,) = world.pr_creates()
    assert option(pr["argv"], "--head") == [candidate.branch]


@pytest.mark.parametrize("blocked_by", ["task-cap", "budget"])
def test_a_rerun_past_the_task_cap_or_the_budget_leaves_the_candidate_undecided(
    world: World, blocked_by: str
) -> None:
    if blocked_by == "task-cap":
        # 1 target + 29 regression tasks = 30; one rerun task would make 31.
        regression = [*REGRESSION, *(f"bulk-{i:02d}" for i in range(1, 28))]
        screen = Screen(wins={"reg-1": 2})
    else:
        regression = list(REGRESSION)
        # 3 tasks x 3 trials x $7 + $0.10, billed at half: $31.55, over the $25.
        screen = Screen(wins={"reg-1": 2}, cost_usd=7.0)
    job = world.input_job({"task-a": TaskRun(0, "alpha")})
    world.plan(screen, Screen())

    report = world.run([job], regression=world.regression_file(regression))

    candidate = only_candidate(report)
    (start,) = world.starts()
    assert "reg-1" in start.tasks
    assert [s.verdict for s in candidate.screenings] == ["rerun-regression"]
    assert candidate.outcome == "undecided"
    assert "undecided" in world.report_md()
    assert candidate.branch is not None
    assert candidate.branch not in world.origin_branches()
    assert world.calls("gh") == []


def test_an_old_version_too_old_to_reuse_leaves_the_candidate_undecided(world: World) -> None:
    started = datetime.now(UTC) - timedelta(days=20)
    job = world.input_job({"task-a": TaskRun(0, "alpha")}, started=started)

    report = world.run([job])

    candidate = only_candidate(report)
    assert len(world.starts()) == 1
    assert candidate.verdict == "rerun-old"
    assert candidate.outcome == "undecided"
    assert "undecided" in world.report_md()
    assert candidate.branch is not None
    assert candidate.branch not in world.origin_branches()
    assert world.calls("gh") == []


def test_candidates_of_one_round_are_judged_and_handled_each_on_its_own(world: World) -> None:
    # alpha (3 failures) is candidate 1, beta (2) candidate 2.
    job = world.input_job({"task-a": TaskRun(0, "alpha"), "task-c": TaskRun(1, "beta")})
    world.plan(Screen(), Screen(wins={"task-c": 1}))  # candidate 1 passes, candidate 2 fails

    report = world.run([job])

    first, second = report.candidates
    assert (first.verdict, second.verdict) == ("pass", "fail")
    assert (first.outcome, second.outcome) == ("pr", "rejected")
    (pr,) = world.pr_creates()
    assert option(pr["argv"], "--head") == [first.branch]
    branches = world.origin_branches()
    assert first.branch in branches
    assert second.branch not in branches
    assert branches["main"] == world.head
    assert [b for b in branches if b != "main" and not b.startswith("skill-improve/")] == []


# --- budget --------------------------------------------------------------------------------------


def test_spend_over_the_budget_stops_the_remaining_screenings(world: World) -> None:
    job = world.input_job(
        {"task-a": TaskRun(0, "alpha"), "task-c": TaskRun(1, "beta"), "task-d": TaskRun(2, "gamma")}
    )
    # Candidate 1's screening lists 3 tasks x 3 trials x $7 + $0.10 smoke = $63.10: over $25 at
    # full price and at the off-peak half ($31.55).
    world.plan(Screen(cost_usd=7.0))

    report = world.run([job])

    assert len(report.candidates) == 3
    (start,) = world.starts()
    assert "task-a" in start.tasks
    assert report.spend_usd > BUDGET_USD
    assert report.stopped_at == 2
    assert [c.outcome for c in report.candidates[1:]] == ["not-screened", "not-screened"]
    report_md = world.report_md()
    assert "not-screened" in report_md
    second = report.candidates[1].branch
    assert second is not None
    stop_lines = [line for line in report_md.splitlines() if "stopped" in line.lower()]
    assert [line for line in stop_lines if second in line], "report.md names no stop at candidate 2"
    later = {c.branch for c in report.candidates[1:]}
    assert later.isdisjoint(world.origin_branches())
