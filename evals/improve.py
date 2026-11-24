"""`improve`: draft small Skill edits from dev-set failures, screen each, hand the passing ones to
a human as draft PRs (.scratch/skill-improve/spec.md).

Ticket 125 wrote this shell: types, constants and signatures, with the contract below. Ticket 126
implements it (and adds the `evals improve` subcommand, the SDK judges and `accept.py`'s screening
mode); the acceptance tests in tests/acceptance/test_skill_improve*.py fix the behaviour.

Contract (ticket 125, "要写进契约的细节" 1-10):

1. Entry and injection. `improve` is the whole round. The caller gives the input job directories
   (one config's Harbor jobs, `OUT/jobs/full`, merged as `evals.report.load_jobs` merges them),
   the repo checkout the branches are made in (`repo`), the split manifest, the regression set file
   (a JSON list of dev task names), the output directory, the two judges (attribution and edit,
   each of `evals.attribute.Judge`'s shape), the Skill sets (`evals.attribute.skill_sets()`'s
   shape: {Skill version: {Skill name: SKILL.md text}}), the date the branch and run names carry,
   and the server-side `ablate` paths. `git`, `da-remote` and `gh` are run by bare name from `PATH`
   (tests put fakes first), each with `repo` as its working directory, never the process's own.
   Optional: `tasks_dir` (the local benchmark tasks, for the attribution prompt's task
   instruction, as in `evals.attribute`), `secrets` (masked in everything written, as there) and
   `budget_usd`. Nothing else reaches the network: the model balance and the preflight are
   `ablate`'s own, on the server. Status polls wait `poll_seconds` between calls.
2. Groups and choice. Each attributed dev failure (an `attribute` entry with a `failure_mode`)
   falls in the group keyed by (`failure_mode`, `skill`, `skill_quote`); a group's size is its
   failed trials, its targets the distinct dev tasks of those trials. Groups are ranked by size,
   largest first (ties: by failure mode, Skill, quote). Walking the ranking, a group whose
   (`skill`, `skill_quote`) a higher group already took is dropped as `same-passage`; the first
   `MAX_CANDIDATES` groups left become candidates 1-3 in rank order, the rest are dropped as
   `not-selected`. A candidate that is later discarded is not replaced by a lower group.
3. Edit reply. For each candidate the edit judge is called (a second judge call, after
   attribution) with the group's failure mode, Skill name, quote and suggestions, and the Skill
   file's text at the input commit. It replies with one JSON object (`EditReply`):

       {"file": "skills/<skill>/SKILL.md", "op": "replace" | "insert_after",
        "anchor": "<text copied from the file>", "text": "<new text>"}

   `replace` puts `text` where `anchor` was; `insert_after` puts `text` right after `anchor` (the
   reply carries any newline it needs). The edit is applied to the file's bytes at the input
   commit: `anchor` must occur there exactly as given, byte for byte (no whitespace folding, no
   case folding), and exactly once. No candidate is made, and the report gives the reason, when the
   reply cannot be read as an `EditReply` (`bad-reply`), `anchor` does not occur (`no-match`) or
   occurs more than once (`ambiguous-match`), or `file` is not a file under `skills/` once
   normalised (absolute, `..` or outside it: `outside-skills`). Retrying the edit judge is 126's
   choice; the tests' fake answers every call for one group the same.
4. Net lines. `net_lines` = lines added minus lines deleted, which is the edited file's line count
   minus the original's (`str.splitlines`). At most `MAX_NET_LINES`: exactly 10 makes a candidate,
   11 is discarded as `net-lines`.
5. Tasks. A candidate's tasks are its targets (sorted), then the regression set's tasks not among
   them, in the file's order. More than `MAX_TASKS` discards it as `too-many-tasks`; exactly 30 is
   screened.
6. Branches. Each candidate that survives 3-5 is one commit on the local branch
   `skill-improve/<YYYY-MM-DD>-<n>` (`n` the candidate number), whose parent is the
   input run's `git_commit` (its version headers') and which changes files under `skills/` only.
   Before its screening the branch is pushed to `origin`; after a screening that does not end in
   `pass`, the remote branch is deleted (the local one stays). Only `skill-improve/` refs are ever
   pushed or deleted.
7. Screening. Candidates are screened one after another, in number order; a rerun comes right
   after its candidate's first screening. Each screening is, with `repo` as working directory:
   `git push origin <branch>`, `da-remote setup <the candidate's full commit hash>`, then
   `da-remote start <run name> -- ablate --tasks-dir <remote_tasks_dir> --manifest
   <remote_manifest> --out runs/<run name> --split dev --config full --trials 3 --task <t> ...
   --budget <USD>`, one `--task` per task of the candidate, `--budget` what is left of
   `budget_usd`, rounded down to the cent. Then `da-remote status <run name>` until its first line
   is `<run name>: finished, exit <code>`, and `da-remote pull <run name>`, which brings the run
   into `<repo>/runs/<run name>/` (its job is `jobs/full`). The run name is
   `improve-<YYYY-MM-DD>-<n>`, a rerun's `improve-<YYYY-MM-DD>-<n>-rerun`.
8. After the verdict (`accept.py`'s screening mode: the input jobs are the old version, the pulled
   job the new; targets, regression set, reruns):
   - `pass`: the pushed branch stays; `gh pr create --draft --base main --head <branch> --title
     <title> --body-file <file>`; the body has the failure mode, targets, the quoted Skill text,
     the diff, the screening results and verdict, the spend, and `NOISE_NOTE` word for word.
     Outcome `pr`.
   - `fail`: the remote branch is deleted, no PR, the report has it. Outcome `rejected`.
   - `rerun-regression`: rerun once, only the tasks the verdict lists, if the budget is not used up
     and the candidate's tasks plus the rerun's stay within `MAX_TASKS`; then judged again with the
     rerun, and handled by that verdict. Without a rerun it is `undecided`, and its remote branch
   is deleted.
   - `rerun-old`: recorded as undecided (未判定), the remote branch is deleted, no PR. Outcome
     `undecided`.
9. Spend. Spend is the judges' `cost_usd` plus the billed spend `evals spend` totals over each
   pulled run directory (the off-peak half rate included, the preflight smoke trial too). Before
   each screening (a rerun too) the spend so far is checked: once it is more than `budget_usd`,
   or what is left rounds down to $0.00 (nothing to give `--budget`), no further screening
   starts; the candidate that would have been screened next is `stopped_at`, it and the ones after
   it are `not-screened`, and report.md names it on its "stopped" line. Attribution and edit
   calls share the same budget.
10. Input. Only Harbor jobs are taken: a directory without `<trial>/result.json`, such as a DAB
    run (`OUT/<dataset>/query<id>/run<k>/events.jsonl`, `OUT/submission.json`), is refused with
    `InputRefused` before any judge call, branch or screening. So is a job whose dev trials ran a
    Skill version `skills` does not have. Trials of tasks outside the manifest's dev set are only
    counted (`skipped_non_dev_trials`): they reach neither judge, nor any candidate's tasks.

The round writes `report.md` (every group with what became of it: the discard or drop code, each
candidate's branch, screenings, verdict and outcome code, `stopped_at`, the spend) and
`improve.json` (the `ImproveReport`) into the output directory.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from evals.attribute import Judge

BUDGET_USD = 25.0
MAX_CANDIDATES = 3
MAX_NET_LINES = 10
MAX_TASKS = 30
TRIALS = 3
CONFIG = "full"
BRANCH_PREFIX = "skill-improve/"
POLL_SECONDS = 60.0
# Every passing candidate's PR body carries this sentence unchanged (spec "交给人").
NOISE_NOTE = "开发集噪声大（单配置标准误约 ±2.2–2.5 分），通过初筛不等于显著提升"  # noqa: RUF001

EditOp = Literal["replace", "insert_after"]
# Why a candidate got no branch (contract 3-5).
Discard = Literal[
    "bad-reply", "no-match", "ambiguous-match", "outside-skills", "net-lines", "too-many-tasks"
]
# Why a group got no candidate (contract 2).
Drop = Literal["same-passage", "not-selected"]
Verdict = Literal["pass", "fail", "rerun-regression", "rerun-old"]
# What became of a candidate: a draft PR, rejected by the screening, undecided (未判定), never
# screened for the budget, or discarded before a branch.
Outcome = Literal["pr", "rejected", "undecided", "not-screened", "discarded"]


class InputRefused(ValueError):
    """The input is not a dev-set Harbor run this round can use (contract 10)."""


@dataclass(frozen=True)
class EditReply:
    """The edit judge's reply (contract 3), as parsed from its JSON object."""

    file: str
    op: EditOp
    anchor: str
    text: str


@dataclass(frozen=True)
class Group:
    """Attributed dev failures sharing a failure mode and a quoted Skill passage (contract 2)."""

    failure_mode: str
    skill: str
    skill_quote: str
    failures: int  # failed trials
    targets: tuple[str, ...]  # distinct dev tasks, sorted
    suggestions: tuple[str, ...]
    # None for a group that became a candidate.
    dropped: Drop | None = None


@dataclass(frozen=True)
class Screening:
    """One `da-remote start` of a candidate (a rerun is a second one)."""

    run_name: str
    tasks: tuple[str, ...]
    budget_usd: float  # the `--budget` given
    spend_usd: float  # billed, by `evals spend`, over the pulled run directory
    verdict: Verdict | None  # `accept.py`'s, after this screening


@dataclass(frozen=True)
class Candidate:
    number: int  # 1-3: rank among the selected groups; the branch's and run's `n`
    group: Group
    tasks: tuple[str, ...]  # targets, then the regression set's other tasks (contract 5)
    outcome: Outcome
    discarded: Discard | None = None
    reason: str | None = None  # a human-readable line behind `discarded` or an undecided outcome
    edit: EditReply | None = None
    net_lines: int | None = None
    branch: str | None = None
    commit: str | None = None
    screenings: tuple[Screening, ...] = ()
    verdict: Verdict | None = None  # the final one
    pr_url: str | None = None


@dataclass(frozen=True)
class ImproveReport:
    groups: tuple[Group, ...]  # every group, ranked (contract 2)
    candidates: tuple[Candidate, ...]  # the selected groups', by number
    judge_usd: float
    spend_usd: float  # judges plus screenings (contract 9)
    budget_usd: float
    stopped_at: int | None  # the candidate whose screening the budget stopped, if any
    skipped_non_dev_trials: int
    base_commit: str  # the input run's `git_commit`


async def improve(
    jobs: Sequence[Path],
    *,
    repo: Path,
    manifest: Mapping[str, Any],
    regression: Path,
    out_dir: Path,
    attribution_judge: Judge,
    edit_judge: Judge,
    skills: Mapping[str, dict[str, str]],
    today: date,
    remote_tasks_dir: str,
    remote_manifest: str,
    tasks_dir: Path | None = None,
    budget_usd: float = BUDGET_USD,
    poll_seconds: float = POLL_SECONDS,
    secrets: Iterable[str] = (),
) -> ImproveReport:
    """One round: attribute the input jobs' dev failures, group them, draft and apply up to three
    edits, screen each candidate on the server, judge it with `accept.py`, open draft PRs for the
    passing ones and write the report (module docstring, contract 1-10). Raises `InputRefused` for
    input it does not take (contract 10)."""
    raise NotImplementedError("ticket 126")
