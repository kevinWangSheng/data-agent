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

import argparse
import asyncio
import functools
import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from data_agent.agent import ConfigError, ModelSettings, result_fields
from data_agent.events import known_secrets
from evals.ablate import Stop, header, spent
from evals.accept import evaluate_screening, render
from evals.attribute import (
    FAILURE_MODES,
    MAX_ATTEMPTS,
    MAX_INSTRUCTION_CHARS,
    MAX_PREVIOUS_REPLY_CHARS,
    MAX_VERIFIER_CHARS,
    NO_SKILL,
    BudgetReached,
    Judge,
    JudgeReply,
    attribute_one,
    dev_failures,
    mask,
    named_in,
    parse_reply,
    read_events,
    sdk_judge,
    skill_sets,
    specific_names,
)
from evals.harbor import Trial, event_files
from evals.report import MERGE_HELP, check_dev_list, is_infra_error, load_jobs

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


# --- ticket 126: choices behind the contract --------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
# Where the server's checkout keeps the benchmark tasks and the split manifest (as in the
# earlier server-side ablate runs, e.g. ticket 47).
REMOTE_TASKS_DIR = "../da-benchmarks/data-eng-bench/tasks"
REMOTE_MANIFEST = "evals/manifest.json"
# A failed or unreadable `da-remote status` (an ssh hiccup during hours of polling) is retried on
# the next poll; this many in a row end the candidate's screening as a failure.
MAX_STATUS_ERRORS = 5
# Edit replies rejected for one of these are asked once more, with the problem (ticket 126
# Context 10.4, as `attribute.MAX_ATTEMPTS`); `outside-skills` is not retried.
RETRIED: frozenset[Discard] = frozenset({"bad-reply", "no-match", "ambiguous-match", "net-lines"})
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
# report.md's line when a candidate is `rerun-old`: `improve` never starts a dev-set run itself
# (spec "输入与旧版", Update 2026-11-24).
NEW_DEV_RUN = (
    "The input run cannot be reused as the old version (see each `rerun-old` candidate): run "
    "the dev set anew (30 tasks x 3, `full`) before the next round"
)
CENT = Decimal("0.01")

EDIT_SYSTEM = """\
You edit the Skills of a data agent, the workflow guidance it follows. You propose one small, \
general edit to one Skill file. You reply with one JSON object and nothing else."""

EDIT_PROMPT = """\
Runs of a data agent failed the same way, and their reviews blamed the same Skill passage. \
Propose one small edit to the Skill that would prevent this kind of failure.

## Failure mode

{mode}: {mode_text}

{failures} failed run(s), over {n_targets} task(s).

## Skill passage involved

{passage}

## Edits the reviews suggested

{suggestions}

## Skill files

{files}

## Reply

Reply with only this JSON object:

{{"file": "skills/<skill>/SKILL.md", "op": "replace" | "insert_after",
  "anchor": "<text copied from the file>", "text": "<new text>"}}

Rules:
- `file` is one of the files above, by the path given.
- `anchor` is copied from that file byte for byte (same spaces, case and line breaks) and \
occurs in it exactly once. Prefer whole lines, with the trailing newline.
- `replace` puts `text` where `anchor` was; `insert_after` puts `text` right after `anchor`. \
`text` carries any newline it needs.
- Prefer narrowing or replacing an existing item to adding new ones; never rewrite the file. \
The edit adds at most {max_net} lines net (lines added minus lines deleted).
- The edit must hold for any database or dbt project. Do not name a task, table, column, \
model, value or domain from the runs; describe the general pattern instead.
"""

EDIT_RETRY = """

## Your previous reply was rejected

{previous}

Problem: {problem}

Reply again with the corrected JSON object only."""


class ToolFailed(Exception):
    """A `git`, `da-remote` or `gh` call that did not succeed."""


class RoundRefused(ValueError):
    """A round of this date already left branches or runs behind (ticket 126 Context 10.7)."""


@dataclass(frozen=True)
class _Problem:
    code: Discard
    reason: str


def _run(
    argv: Sequence[str], repo: Path, *, data: bytes | None = None, env: Mapping[str, str] | None
) -> subprocess.CompletedProcess[bytes]:
    """One tool call by bare name (contract 1), with `repo` as its working directory."""
    return subprocess.run(
        list(argv), cwd=repo, input=data, capture_output=True, env=None if env is None else {**env}
    )


def _tool(
    repo: Path, *argv: str, data: bytes | None = None, env: Mapping[str, str] | None = None
) -> str:
    """The call's stdout, stripped; `ToolFailed` with its output when it exits non-zero."""
    proc = _run(argv, repo, data=data, env=env)
    out = proc.stdout.decode(errors="replace").strip()
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip()
        detail = (err or out).splitlines()[-3:]
        raise ToolFailed(f"`{' '.join(argv[:2])}` exited {proc.returncode}: {' / '.join(detail)}")
    return out


def _git(
    repo: Path, *args: str, data: bytes | None = None, env: Mapping[str, str] | None = None
) -> str:
    return _tool(repo, "git", *args, data=data, env=env)


def branch_ref(branch: str) -> str:
    """`refs/heads/<branch>` for a `skill-improve/` branch; any other ref is refused, so nothing
    else is ever pushed or deleted (contract 6)."""
    if not branch.startswith(BRANCH_PREFIX) or len(branch) == len(BRANCH_PREFIX):
        raise ValueError(f"refusing to push or delete {branch!r}: not a {BRANCH_PREFIX} branch")
    if any(c in branch for c in (" ", ":", "..", "~", "^", "*", "?", "[", "\\")):
        raise ValueError(f"refusing to push or delete {branch!r}: not a plain branch name")
    return f"refs/heads/{branch}"


def push_branch(repo: Path, branch: str) -> None:
    ref = branch_ref(branch)
    _git(repo, "push", "origin", f"{ref}:{ref}")


def delete_remote_branch(repo: Path, branch: str) -> None:
    _git(repo, "push", "origin", "--delete", branch_ref(branch))


# --- input (contract 10) -------------------------------------------------------------------------


def _load_regression(path: Path, dev: set[str]) -> list[str]:
    tasks = json.loads(path.read_text())
    if not isinstance(tasks, list) or not all(isinstance(t, str) for t in tasks):
        raise ValueError(f"{path} must be a JSON list of task names")
    outside = sum(1 for t in tasks if t not in dev)
    if outside:
        raise ValueError(f"{outside} regression task(s) in {path} are not in the dev set")
    return list(dict.fromkeys(tasks))


def _check_input(
    trials: list[Trial], dev: set[str], skills: Mapping[str, dict[str, str]], repo: Path
) -> str:
    """The input run's commit, once every dev trial that ran has a version header naming one
    clean, local commit and a Skill version `skills` has; `InputRefused` otherwise. Only dev
    trials' event files are opened; errors give counts, never a task."""
    commits: set[Any] = set()
    unknown: set[Any] = set()
    unversioned = 0
    for trial in trials:
        if trial.task not in dev or is_infra_error(trial):
            continue
        files = event_files(trial)
        if not files:
            unversioned += 1
        for events in files:
            try:
                head = header(events)
            except (OSError, ValueError):
                head = {}
            if head.get("type") != "version":
                unversioned += 1
                continue
            commits.add(head.get("git_commit"))
            if head.get("skill_version") not in skills:
                unknown.add(head.get("skill_version"))
    if unversioned:
        raise InputRefused(f"{unversioned} dev trial(s) or event file(s) have no version header")
    if unknown:
        raise InputRefused(
            f"the dev trials ran Skill version(s) {', '.join(map(repr, sorted(map(str, unknown))))}"
            ", which no Skill directory here has; their quotes cannot be checked and no edit "
            "can be built on them"
        )
    if len(commits) != 1:
        raise InputRefused(f"the dev trials ran {len(commits)} different git commits, not one")
    (commit,) = commits
    if not isinstance(commit, str) or not FULL_COMMIT.fullmatch(commit):
        raise InputRefused(
            f"the input run's git_commit {commit!r} is not a clean full commit hash "
            "(a -dirty or unknown checkout cannot carry a branch)"
        )
    if _run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], repo, env=None).returncode != 0:
        raise InputRefused(f"the input run's commit {commit} is not in {repo}")
    return commit


def _check_fresh(repo: Path, out_dir: Path, day: str) -> None:
    """Refuse a second round of the same date: its branches would collide, and on the server
    `ablate` would quietly reuse the earlier OUT (ticket 126 Context 10.7)."""
    prefix = f"{BRANCH_PREFIX}{day}-"
    found = _git(repo, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{prefix}*")
    remote = _git(repo, "ls-remote", "--heads", "origin", f"refs/heads/{prefix}*")
    run = re.compile(rf"improve-{re.escape(day)}-\d+(-rerun)?")
    runs = [p.name for p in (repo / "runs").glob(f"improve-{day}-*") if run.fullmatch(p.name)]
    left = [*found.splitlines(), *(line.split()[-1] for line in remote.splitlines()), *runs]
    if out_dir.exists() and any(out_dir.iterdir()):
        left.append(str(out_dir))
    if left:
        raise RoundRefused(
            f"a round of {day} already ran ({', '.join(left)}); pass another --date or remove them"
        )


# --- attribution and groups (contract 2) ---------------------------------------------------------


async def _attribute(
    failures: list[Trial],
    manifest: Mapping[str, Any],
    judge: Callable[[str], Awaitable[JudgeReply]],
    skills: Mapping[str, dict[str, str]],
    tasks_dir: Path | None,
) -> list[dict[str, Any]]:
    """One `attribute_one` entry per failed dev trial, as `evals.attribute.attribute` makes them."""
    entries: list[dict[str, Any]] = []
    for trial in failures:
        files = event_files(trial)
        base: dict[str, Any] = {"task": trial.task, "trial": trial.name, "failure_mode": None}
        if not files:
            entries.append({**base, "error": "no event file"})
            continue
        try:
            entry = await attribute_one(trial, files[-1], manifest, judge, skills, tasks_dir)
        except ConfigError:
            raise
        except BudgetReached as exc:
            entry = {**base, "error": str(exc)}
        except Exception as exc:  # one broken call or event file must not lose the rest
            entry = {**base, "error": f"judge failed: {exc}"}
        entries.append(entry)
    return entries


GroupKey = tuple[str, str, str]


def _key(entry: Mapping[str, Any]) -> GroupKey:
    return entry["failure_mode"], entry["skill"], entry["skill_quote"]


def group_members(entries: Sequence[Mapping[str, Any]]) -> dict[GroupKey, list[Mapping[str, Any]]]:
    """The attributed entries (those with a `failure_mode`) by group key (contract 2)."""
    members: dict[GroupKey, list[Mapping[str, Any]]] = {}
    for entry in entries:
        if entry.get("failure_mode"):
            members.setdefault(_key(entry), []).append(entry)
    return members


def make_groups(entries: Sequence[Mapping[str, Any]]) -> list[Group]:
    """Every group of attributed failures, ranked, with the drops of contract 2."""
    members = group_members(entries)
    ranked = sorted(
        (
            Group(
                failure_mode=fm,
                skill=skill,
                skill_quote=quote,
                failures=len(group),
                targets=tuple(sorted({e["task"] for e in group})),
                suggestions=tuple(dict.fromkeys(e["suggestion"] for e in group if e["suggestion"])),
            )
            for (fm, skill, quote), group in members.items()
        ),
        key=lambda g: (-g.failures, g.failure_mode, g.skill, g.skill_quote),
    )
    taken: set[tuple[str, str]] = set()
    out: list[Group] = []
    for group in ranked:
        passage = (group.skill, group.skill_quote)
        if passage in taken:
            out.append(replace(group, dropped="same-passage"))
        elif len(taken) >= MAX_CANDIDATES:
            out.append(replace(group, dropped="not-selected"))
        else:
            taken.add(passage)
            out.append(group)
    return out


def _read_limited(path: Path | None, limit: int) -> str:
    if path is None or not path.is_file():
        return ""
    text = path.read_text(errors="replace")
    return text if len(text) <= limit else text[-limit:]


def _group_names(
    entries: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    skills: Mapping[str, dict[str, str]],
    tasks_dir: Path | None,
    trial_dirs: Mapping[str, Path],
) -> set[str]:
    """The benchmark-specific names an edit for these failures must not use: `specific_names`
    of each failure's run, as attribution checked its suggestion."""
    names: set[str] = set()
    for entry in entries:
        instruction = _read_limited(
            tasks_dir / entry["task"] / "instruction.md" if tasks_dir else None,
            MAX_INSTRUCTION_CHARS,
        )
        verifier = _read_limited(
            trial_dirs[entry["trial"]] / "verifier" / "test-stdout.txt", MAX_VERIFIER_CHARS
        )
        events = read_events(Path(entry["events"]))
        run_skills = skills.get(entry.get("run_skill_version") or "", {})
        names |= specific_names(manifest, events, f"{instruction}\n{verifier}", run_skills)
    return names


# --- edits (contract 3, 4) -----------------------------------------------------------------------


def parse_edit(text: str) -> EditReply:
    """The edit judge's reply as an `EditReply`; ValueError when it is not one."""
    fields = parse_reply(text)
    for key in ("file", "op", "anchor", "text"):
        if not isinstance(fields.get(key), str):
            raise ValueError(f"{key} is missing or not a string")
    if fields["op"] not in ("replace", "insert_after"):
        raise ValueError(f"op {fields['op']!r} is not replace or insert_after")
    if not fields["anchor"]:
        raise ValueError("anchor is empty")
    op: EditOp = "replace" if fields["op"] == "replace" else "insert_after"
    return EditReply(file=fields["file"], op=op, anchor=fields["anchor"], text=fields["text"])


def skill_path(file: str) -> str | None:
    """`file` normalised to a path under `skills/`, or None when it is absolute, climbs out with
    `..` or lies outside `skills/`."""
    if not file or file.startswith("/") or "\\" in file:
        return None
    path = posixpath.normpath(file)
    parts = path.split("/")
    if len(parts) < 2 or parts[0] != "skills" or ".." in parts:
        return None
    return path


def apply_edit(original: bytes, edit: EditReply) -> bytes | _Problem:
    """The file's bytes with the edit applied, the anchor matched byte for byte, exactly once."""
    anchor = edit.anchor.encode()
    at = original.find(anchor)
    if at < 0:
        return _Problem("no-match", "the anchor does not occur in the file byte for byte")
    if original.find(anchor, at + 1) >= 0:
        return _Problem("ambiguous-match", "the anchor occurs more than once in the file")
    text = edit.text.encode()
    end = at + len(anchor)
    if edit.op == "replace":
        return original[:at] + text + original[end:]
    return original[:end] + text + original[end:]


def net_lines(original: bytes, edited: bytes) -> int:
    return len(edited.decode().splitlines()) - len(original.decode().splitlines())


def _show(repo: Path, commit: str, path: str) -> bytes | None:
    proc = _run(["git", "show", f"{commit}:{path}"], repo, env=None)
    return proc.stdout if proc.returncode == 0 else None


def _skill_files(repo: Path, commit: str, skill: str) -> dict[str, bytes]:
    """The SKILL.md files the edit judge is shown: the quoted Skill's, or every Skill's when no
    Skill covered the failures (ticket 126 Context 10.2)."""
    if skill != NO_SKILL:
        paths = [f"skills/{skill}/SKILL.md"]
    else:
        listed = _git(repo, "ls-tree", "-r", "--name-only", commit, "--", "skills/")
        paths = [p for p in listed.splitlines() if re.fullmatch(r"skills/[^/]+/SKILL\.md", p)]
    files = {p: _show(repo, commit, p) for p in paths}
    return {p: data for p, data in files.items() if data is not None}


def edit_prompt(group: Group, files: Mapping[str, bytes]) -> str:
    """The edit judge's prompt: this group's failure mode, passage and suggestions, and the
    Skill files at the input commit; nothing of any other group."""
    if group.skill == NO_SKILL:
        passage = "No Skill text covered these failures: add the rule where it fits best."
    else:
        quoted = "\n".join(f"> {line}" for line in group.skill_quote.splitlines())
        passage = f"Skill `{group.skill}`:\n\n{quoted}"
    suggestions = "\n".join(f"- {s}" for s in group.suggestions) or (
        "(none usable: the reviews' suggestions named benchmark-specific identifiers; propose "
        "the general rule)"
    )
    shown = "\n\n".join(
        f'<file path="{path}">\n{data.decode(errors="replace")}</file>'
        for path, data in files.items()
    )
    return EDIT_PROMPT.format(
        mode=group.failure_mode,
        mode_text=FAILURE_MODES.get(group.failure_mode, ""),
        failures=group.failures,
        n_targets=len(group.targets),
        passage=passage,
        suggestions=suggestions,
        files=shown or "(no Skill file found at the input commit)",
        max_net=MAX_NET_LINES,
    )


@dataclass(frozen=True)
class _Drafted:
    edit: EditReply | None
    path: str | None = None
    edited: bytes | None = None
    net: int | None = None
    problem: _Problem | None = None
    budget: bool = False  # the budget refused the edit call


def _check_edit(
    edit: EditReply, repo: Path, commit: str, files: Mapping[str, bytes], names: set[str]
) -> _Drafted:
    path = skill_path(edit.file)
    if path is None:
        reason = f"file {edit.file!r} is not a file under skills/"
        return _Drafted(edit, problem=_Problem("outside-skills", reason))
    original = files.get(path)
    if original is None:
        original = _show(repo, commit, path)
    if original is None:
        reason = f"{path} does not exist at the input commit"
        return _Drafted(edit, path, problem=_Problem("no-match", reason))
    edited = apply_edit(original, edit)
    if isinstance(edited, _Problem):
        return _Drafted(edit, path, problem=edited)
    if edited == original:
        return _Drafted(edit, path, problem=_Problem("bad-reply", "the edit changes nothing"))
    try:
        net = net_lines(original, edited)
    except UnicodeDecodeError:
        return _Drafted(edit, path, problem=_Problem("bad-reply", "the edited file is not UTF-8"))
    if net > MAX_NET_LINES:
        reason = f"the edit adds {net} lines net, over {MAX_NET_LINES}"
        return _Drafted(edit, path, edited, net, _Problem("net-lines", reason))
    named = named_in(edit.text, names)
    if named:
        reason = f"the edit names benchmark-specific identifiers ({', '.join(named)})"
        return _Drafted(edit, path, edited, net, _Problem("bad-reply", reason))
    return _Drafted(edit, path, edited, net)


async def _draft(
    group: Group,
    repo: Path,
    commit: str,
    judge: Callable[[str], Awaitable[JudgeReply]],
    names: set[str],
) -> _Drafted:
    """Ask the edit judge, retrying once with the problem for the RETRIED discards."""
    files = _skill_files(repo, commit, group.skill)
    prompt = edit_prompt(group, files)
    attempt_prompt = prompt
    drafted = _Drafted(None, problem=_Problem("bad-reply", "no reply"))
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            reply = await judge(attempt_prompt)
        except ConfigError:
            raise
        except BudgetReached as exc:
            reason = f"edit judge not called: {exc}"
            return _Drafted(drafted.edit, problem=_Problem("bad-reply", reason), budget=True)
        except Exception as exc:  # one broken call must not lose the round
            return _Drafted(drafted.edit, problem=_Problem("bad-reply", f"edit judge: {exc}"))
        try:
            drafted = _check_edit(parse_edit(reply.text), repo, commit, files, names)
        except ValueError as exc:
            drafted = _Drafted(None, problem=_Problem("bad-reply", f"unreadable reply: {exc}"))
        problem = drafted.problem
        if problem is None or problem.code not in RETRIED or attempt == MAX_ATTEMPTS:
            return drafted
        attempt_prompt = prompt + EDIT_RETRY.format(
            previous=reply.text[:MAX_PREVIOUS_REPLY_CHARS], problem=problem.reason
        )
    return drafted


def candidate_tasks(targets: Sequence[str], regression: Sequence[str]) -> tuple[str, ...]:
    """Contract 5: the targets, sorted, then the regression set's other tasks in file order."""
    first = sorted(targets)
    return (*first, *(t for t in regression if t not in first))


# --- branches (contract 6) -----------------------------------------------------------------------


def make_branch(repo: Path, base: str, branch: str, path: str, data: bytes, message: str) -> str:
    """One commit on `base` changing `path` to `data`, as the new local branch `branch`; built
    with git plumbing and a throwaway index, so the checkout's HEAD, index and working tree are
    never touched (ticket 126 Context 10.1). Refuses a branch that exists."""
    ref = branch_ref(branch)
    blob = _git(repo, "hash-object", "-w", "--stdin", data=data)
    listed = _git(repo, "ls-tree", base, "--", path)
    mode = listed.split()[0] if listed else "100644"
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git(repo, "read-tree", base, env=env)
        _git(repo, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{path}", env=env)
        tree = _git(repo, "write-tree", env=env)
    commit = _git(repo, "commit-tree", tree, "-p", base, "-m", message)
    _git(repo, "update-ref", ref, commit, "")  # "": the branch must not exist yet
    return commit


# --- spend (contract 9) --------------------------------------------------------------------------


class _Ledger:
    """The round's spend: the judges' cost as each call returns, and each pulled run's billed
    spend. Judge calls are capped at what is left and refused once nothing is."""

    def __init__(self, budget_usd: float) -> None:
        self.budget = Decimal(str(budget_usd))
        self.judges = Decimal(0)
        self.screenings = Decimal(0)
        # Runs whose spend could not be read back (no full pull): not in `spent`, named in
        # report.md.
        self.unknown: list[str] = []

    @property
    def spent(self) -> Decimal:
        return self.judges + self.screenings

    def left(self) -> Decimal:
        """What is left, rounded down to the cent: the `--budget` of the next screening."""
        return (self.budget - self.spent).quantize(CENT, rounding=ROUND_DOWN)

    def can_screen(self) -> bool:
        return self.spent <= self.budget and self.left() > 0

    def meter(self, judge: Judge) -> Callable[[str], Awaitable[JudgeReply]]:
        async def call(prompt: str) -> JudgeReply:
            remaining = self.budget - self.spent
            if remaining <= 0:
                raise BudgetReached(f"not called: spend reached the ${self.budget} budget")
            reply = await judge(prompt, float(remaining))
            self.judges += Decimal(str(reply.cost_usd))
            return reply

        return call


# --- screening (contract 7, 8) -------------------------------------------------------------------


@dataclass(frozen=True)
class _Remote:
    repo: Path
    tasks_dir: str
    manifest: str
    poll_seconds: float


STATUS = re.compile(r"(?P<name>[A-Za-z0-9._-]+): (?P<state>.*)")
FINISHED = re.compile(r"finished, exit (-?\d+)")


async def _wait(remote: _Remote, name: str) -> int:
    """Poll `da-remote status` until the run ends; its exit code. A run stopped without one, or
    too many failed polls in a row, is `ToolFailed`."""
    errors, last = 0, ""
    while True:
        proc = _run(["da-remote", "status", name], remote.repo, env=None)
        out = proc.stdout.decode(errors="replace")
        first = out.splitlines()[0].strip() if out.strip() else ""
        match = STATUS.fullmatch(first)
        state = match["state"] if match and match["name"] == name and proc.returncode == 0 else None
        if state is not None and (done := FINISHED.fullmatch(state)):
            return int(done[1])
        if state is not None and state.startswith("stopped"):
            raise ToolFailed(f"{name}: {state}")
        if state is not None and state.startswith("running"):
            errors = 0
        else:
            errors += 1
            last = (first or proc.stderr.decode(errors="replace").strip())[:200]
            if errors >= MAX_STATUS_ERRORS:
                raise ToolFailed(f"da-remote status {name} failed {errors} times in a row: {last}")
        await asyncio.sleep(remote.poll_seconds)


def ablate_args(remote: _Remote, name: str, tasks: Sequence[str], budget: Decimal) -> list[str]:
    return [
        "ablate",
        "--tasks-dir",
        remote.tasks_dir,
        "--manifest",
        remote.manifest,
        "--out",
        f"runs/{name}",
        "--split",
        "dev",
        "--config",
        CONFIG,
        "--trials",
        str(TRIALS),
        *(a for task in tasks for a in ("--task", task)),
        "--budget",
        f"{budget:.2f}",
    ]


async def _screen(
    remote: _Remote, commit: str, name: str, tasks: Sequence[str], ledger: _Ledger
) -> tuple[Screening, Path | None, str | None]:
    """One screening run: setup, start, wait, pull; (its record, the pulled job, why it failed).
    Whatever came back is counted in the spend, a failed run's too."""
    budget = ledger.left()
    screening = Screening(name, tuple(tasks), float(budget), 0.0, None)
    try:
        _tool(remote.repo, "da-remote", "setup", commit)
        _tool(
            remote.repo, "da-remote", "start", name, "--", *ablate_args(remote, name, tasks, budget)
        )
    except ToolFailed as exc:
        return screening, None, str(exc)
    failure: str | None = None
    try:
        code = await _wait(remote, name)
        if code != 0:
            failure = f"ablate exited {code} on the server (runs/{name}/run.log there)"
    except ToolFailed as exc:
        failure = str(exc)
    try:
        _tool(remote.repo, "da-remote", "pull", name)
    except ToolFailed as exc:
        ledger.unknown.append(name)
        return screening, None, failure or f"{exc}; its spend is unknown here"
    out = remote.repo / "runs" / name
    try:
        billed = spent(out) if out.is_dir() else Decimal(0)
    except Stop as exc:
        ledger.unknown.append(name)
        return screening, None, failure or f"cannot total its spend: {exc}"
    ledger.screenings += billed
    screening = replace(screening, spend_usd=float(billed))
    job = out / "jobs" / CONFIG
    if not job.is_dir():
        # `da-remote pull` brings an ablate run back whole only once its jobs show dev tasks
        # only; without a job (setup, preflight or smoke failed) it brings the summary, not the
        # smoke trial's event files, so the run's spend cannot be read here.
        ledger.unknown.append(name)
        unknown = f"the pulled run has no jobs/{CONFIG}, so its spend is unknown here"
        failure = f"{failure}; {unknown}" if failure else unknown
    return screening, None if failure else job, failure


def run_name(day: str, number: int) -> str:
    return f"improve-{day}-{number}"


def pr_title(day: str, candidate: Candidate) -> str:
    group = candidate.group
    return f"skill-improve {day}-{candidate.number}: {group.failure_mode} {group.skill}"


def pr_body(
    candidate: Candidate, diff: str, results: Sequence[dict[str, Any]], round_spend: Decimal
) -> str:
    group = candidate.group
    quote = "\n".join(f"> {line}" for line in group.skill_quote.splitlines()) or "> (none)"
    screened = "\n".join(
        f"| `{s.run_name}` | {len(s.tasks)} | ${s.spend_usd:.2f} | {s.verdict} |"
        for s in candidate.screenings
    )
    verdicts = "\n\n".join(f"```\n{render(r)}\n```" for r in results)
    spend = sum(s.spend_usd for s in candidate.screenings)
    return "\n".join(
        [
            f"Draft from `evals improve` (candidate {candidate.number}). {NOISE_NOTE}.",
            "",
            "## Failure mode",
            "",
            f"{group.failure_mode}: {FAILURE_MODES.get(group.failure_mode, '')}",
            f"{group.failures} failed dev trial(s); targets: {', '.join(group.targets)}",
            "",
            f"## Skill text quoted by the attribution (`{group.skill}`)",
            "",
            quote,
            "",
            "## Diff",
            "",
            "```diff",
            diff.rstrip("\n"),
            "```",
            "",
            "## Screening",
            "",
            f"Tasks ({len(candidate.tasks)}): {', '.join(candidate.tasks)}; `{CONFIG}`, "
            f"{TRIALS} trials each, judged by `accept --screening` against the input run.",
            "",
            "| Run | Tasks | Spend | Verdict |",
            "|---|---|---|---|",
            screened,
            "",
            verdicts,
            "",
            "## Spend",
            "",
            f"Screening ${spend:.2f}; the round so far ${round_spend:.2f}.",
            "",
            NOISE_NOTE,
            "",
        ]
    )


# --- report --------------------------------------------------------------------------------------


def _group_line(group: Group, number: int | None) -> str:
    fate = f"candidate {number}" if number is not None else f"dropped: `{group.dropped}`"
    quote = " ".join(group.skill_quote.split()) or "(no Skill text)"
    return (
        f'- **{group.failure_mode}** `{group.skill}` "{quote}": {group.failures} failure(s), '
        f"targets {', '.join(group.targets)} -> {fate}"
    )


def render_report(
    report: ImproveReport,
    day: str,
    entries: Sequence[Mapping[str, Any]],
    diffs: Mapping[int, str],
    notes: Sequence[str],
) -> str:
    attributed = sum(1 for e in entries if e.get("failure_mode"))
    lines = [
        f"# Skill improvement round {day}",
        "",
        f"- Input run commit: `{report.base_commit}`",
        f"- Spend: ${report.spend_usd:.2f} of the ${report.budget_usd:.2f} budget "
        f"(judges ${report.judge_usd:.2f}, screenings "
        f"${report.spend_usd - report.judge_usd:.2f})",
        f"- Dev failures attributed: {attributed} of {len(entries)}; trials outside the dev "
        f"set, only counted: {report.skipped_non_dev_trials}",
    ]
    if report.stopped_at is not None:
        stopped = next(c for c in report.candidates if c.number == report.stopped_at)
        lines.append(
            f"- Stopped at candidate {stopped.number} (`{stopped.branch}`): the budget is used "
            "up, so no further screening started"
        )
    lines += [f"- {note}" for note in notes]
    lines += ["", f"{NOISE_NOTE}.", "", "## Groups", ""]
    numbers = {(c.group.failure_mode, c.group.skill, c.group.skill_quote): c.number
               for c in report.candidates}  # fmt: skip
    lines += [
        _group_line(g, numbers.get((g.failure_mode, g.skill, g.skill_quote))) for g in report.groups
    ] or ["(no attributed failures)"]
    for c in report.candidates:
        lines += ["", f"## Candidate {c.number}: {c.group.failure_mode} `{c.group.skill}`", ""]
        lines.append(f"- Outcome: `{c.outcome}`" + (f" ({c.reason})" if c.reason else ""))
        if c.discarded:
            lines.append(f"- Discarded: `{c.discarded}`")
        lines.append(f"- Tasks ({len(c.tasks)}): {', '.join(c.tasks)}")
        if c.edit is not None:
            lines.append(f"- Edit: `{c.edit.op}` in `{c.edit.file}`, net lines {c.net_lines}")
        if c.branch:
            lines.append(f"- Branch: `{c.branch}` at `{c.commit}`")
        for s in c.screenings:
            lines.append(
                f"- Screening `{s.run_name}`: {len(s.tasks)} task(s), --budget "
                f"${s.budget_usd:.2f}, spend ${s.spend_usd:.2f}, verdict `{s.verdict}`"
            )
        if c.verdict:
            lines.append(f"- Verdict: `{c.verdict}`")
        if c.pr_url:
            lines.append(f"- Draft PR: {c.pr_url}")
        if c.number in diffs:
            lines += ["", "```diff", diffs[c.number].rstrip("\n"), "```"]
    unattributed = [e for e in entries if not e.get("failure_mode")]
    if unattributed:
        lines += ["", "## Not attributed", ""]
        lines += [f"- `{e['trial']}`: {e.get('error')}" for e in unattributed]
    return "\n".join(lines) + "\n"


def _write(
    out_dir: Path,
    report: ImproveReport,
    day: str,
    entries: Sequence[Mapping[str, Any]],
    diffs: Mapping[int, str],
    notes: Sequence[str],
    secrets: Sequence[str],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    text = render_report(report, day, entries, diffs, notes)
    (out_dir / "report.md").write_text(mask(text, secrets))
    data = mask(asdict(report), secrets)
    (out_dir / "improve.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def sdk_edit_judge(env: Mapping[str, str]) -> Judge:
    """The edit judge: `evals.attribute.sdk_judge`'s tool-less SDK turn with the edit system
    prompt (EDIT_SYSTEM)."""
    settings = ModelSettings.from_env(env)

    async def judge(prompt: str, max_budget_usd: float) -> JudgeReply:
        stderr: list[str] = []
        options = ClaudeAgentOptions(
            model=settings.model,
            env=settings.sdk_env(),
            system_prompt=EDIT_SYSTEM,
            tools=[],
            allowed_tools=[],
            permission_mode="dontAsk",
            strict_mcp_config=True,
            setting_sources=["user"],  # as the agent (ADR-0001)
            skills=[],
            max_turns=1,
            max_budget_usd=max_budget_usd,
            stderr=stderr.append,
        )
        final: ResultMessage | None = None
        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    final = message
        except Exception:
            if final is None:  # a failed call still carries its charge (as sdk_judge)
                raise
        if final is None:
            raise RuntimeError(f"the edit judge returned no result: {''.join(stderr[-5:])}")
        cost = float(result_fields(final, settings.model)["cost_usd"])
        return JudgeReply(text="" if final.is_error else final.result or "", cost_usd=cost)

    return judge


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
    dev = set(manifest["dev"])
    try:
        # As `ablate` does: a manifest relabelling a holdout task as dev would reach the judges.
        check_dev_list(dict(manifest), "the manifest")
    except ValueError as exc:
        raise InputRefused(str(exc)) from exc
    regression_tasks = _load_regression(regression, dev)
    try:
        trials = load_jobs(jobs)
    except (OSError, ValueError) as exc:
        raise InputRefused(
            f"not a Harbor job (<trial>/result.json) this round takes: {exc}"
        ) from exc
    base = _check_input(trials, dev, skills, repo)
    day = today.isoformat()
    _check_fresh(repo, out_dir, day)
    masked = sorted({s for s in secrets if s})
    ledger = _Ledger(budget_usd)

    failures, skipped, _infra = dev_failures(trials, dev)
    entries = await _attribute(
        failures, manifest, ledger.meter(attribution_judge), skills, tasks_dir
    )
    trial_dirs = {t.name: t.dir for t in failures}
    groups = make_groups(entries)
    members = group_members(entries)

    candidates: list[Candidate] = []
    drafts: dict[int, _Drafted] = {}
    edit_refused: list[int] = []  # candidates whose edit call the budget refused
    edit = ledger.meter(edit_judge)
    for group in (g for g in groups if g.dropped is None):
        number = len(candidates) + 1
        tasks = candidate_tasks(group.targets, regression_tasks)
        candidate = Candidate(number, group, tasks, outcome="not-screened")
        if len(tasks) > MAX_TASKS:  # checked first: no edit call for a candidate never screened
            reason = f"{len(tasks)} tasks (targets and regression set), over {MAX_TASKS}"
            candidates.append(
                replace(candidate, outcome="discarded", discarded="too-many-tasks", reason=reason)
            )
            continue
        key = (group.failure_mode, group.skill, group.skill_quote)
        names = _group_names(members[key], manifest, skills, tasks_dir, trial_dirs)
        drafted = await _draft(group, repo, base, edit, names)
        candidate = replace(candidate, edit=drafted.edit, net_lines=drafted.net)
        if drafted.budget:
            edit_refused.append(number)
        if drafted.problem is not None:
            problem = drafted.problem
            candidate = replace(
                candidate, outcome="discarded", discarded=problem.code, reason=problem.reason
            )
        else:
            drafts[number] = drafted
        candidates.append(candidate)

    # Every branch is made before the first push (the budget may stop the later screenings).
    diffs: dict[int, str] = {}
    for i, candidate in enumerate(candidates):
        drafted = drafts.get(candidate.number)
        if drafted is None or drafted.path is None or drafted.edited is None:
            continue
        branch = f"{BRANCH_PREFIX}{day}-{candidate.number}"
        message = (
            f"{pr_title(day, candidate)}\n\nDrafted by `evals improve`; screened on the dev set."
        )
        commit = make_branch(repo, base, branch, drafted.path, drafted.edited, message)
        candidates[i] = replace(candidate, branch=branch, commit=commit)
        diffs[candidate.number] = _git(repo, "diff", base, commit)

    remote = _Remote(repo, remote_tasks_dir, remote_manifest, poll_seconds)
    stopped_at: int | None = None
    halted: str | None = None
    notes: list[str] = []
    if edit_refused:
        listed = ", ".join(map(str, edit_refused))
        notes.append(
            f"Edit calls stopped at candidate {edit_refused[0]}: the budget is used up, so "
            f"candidate(s) {listed} got no edit"
        )

    def snapshot() -> ImproveReport:
        return ImproveReport(
            groups=tuple(groups),
            candidates=tuple(candidates),
            judge_usd=float(ledger.judges),
            spend_usd=float(ledger.spent),
            budget_usd=budget_usd,
            stopped_at=stopped_at,
            skipped_non_dev_trials=skipped,
            base_commit=base,
        )

    def write() -> None:
        unknown = [
            f"Spend of `{name}` is unknown here and not in the total above (the pull brought "
            "back its summary only); read it on the server"
            for name in ledger.unknown
        ]
        _write(out_dir, snapshot(), day, entries, diffs, [*notes, *unknown], masked)
        attribution = mask({"failures": entries}, masked)
        (out_dir / "attribution.json").write_text(
            json.dumps(attribution, indent=2, ensure_ascii=False) + "\n"
        )

    write()
    try:
        for i, candidate in enumerate(candidates):
            if candidate.branch is None or candidate.commit is None:
                continue
            if halted is not None or stopped_at is not None:
                why = halted or f"the budget stopped the round at candidate {stopped_at}"
                candidates[i] = replace(candidate, reason=f"not screened: {why}")
                continue
            if not ledger.can_screen():
                stopped_at = candidate.number
                candidates[i] = replace(candidate, reason="not screened: the budget is used up")
                continue
            handled = await _handle(
                candidate, remote, ledger, jobs, manifest, regression_tasks, out_dir, day, diffs,
                masked,
            )  # fmt: skip
            candidates[i] = handled.candidate
            if handled.budget_stop:
                stopped_at = candidate.number
            if handled.halt:
                halted = f"Screening stopped after candidate {candidate.number}: {handled.halt}"
                notes.append(halted)
            if handled.candidate.verdict == "rerun-old" and NEW_DEV_RUN not in notes:
                notes.append(NEW_DEV_RUN)
            write()
    finally:
        write()
    return snapshot()


@dataclass(frozen=True)
class _Handled:
    candidate: Candidate
    halt: str | None = None  # a tool or run failure: no further screening starts
    budget_stop: bool = False  # the budget stopped this candidate's rerun


async def _handle(
    candidate: Candidate,
    remote: _Remote,
    ledger: _Ledger,
    jobs: Sequence[Path],
    manifest: Mapping[str, Any],
    regression: list[str],
    out_dir: Path,
    day: str,
    diffs: Mapping[int, str],
    secrets: Sequence[str],
) -> _Handled:
    """Push, screen (and rerun), judge and hand over one candidate (contract 6-8). The remote
    branch is deleted on every way out but a draft PR."""
    assert candidate.branch is not None and candidate.commit is not None
    branch, commit = candidate.branch, candidate.commit
    keep_remote = False

    def undecided(reason: str, **changes: Any) -> Candidate:
        return replace(candidate, outcome="undecided", reason=reason, **changes)

    try:
        try:
            push_branch(remote.repo, branch)
        except ToolFailed as exc:
            return _Handled(undecided(f"push failed: {exc}"), halt=str(exc))
        name = run_name(day, candidate.number)
        screening, job, failure = await _screen(remote, commit, name, candidate.tasks, ledger)
        screenings = [screening]
        if failure is not None or job is None:
            reason = failure or "no job"
            return _Handled(
                undecided(f"screening failed: {reason}", screenings=(screening,)), reason
            )
        targets = list(candidate.group.targets)
        dev = list(manifest["dev"])
        try:
            result = evaluate_screening(list(jobs), [job], dev, targets, regression, [], TRIALS)
        except ValueError as exc:
            reason = f"cannot judge {name}: {exc}"
            return _Handled(undecided(reason, screenings=(screening,)), reason)
        results = [result]
        verdict = result["verdict"]
        screenings[0] = replace(screening, verdict=verdict)
        budget_stop = False
        reason: str | None = None
        if verdict == "rerun-regression":
            rerun = list(result["rerun_tasks"])
            if len(candidate.tasks) + len(rerun) > MAX_TASKS:
                reason = (
                    f"rerun of {len(rerun)} regression task(s) would make "
                    f"{len(candidate.tasks) + len(rerun)} tasks, over {MAX_TASKS}"
                )
            elif not ledger.can_screen():
                budget_stop = True
                reason = "the budget is used up before the regression rerun"
            else:
                rerun_name = f"{name}-rerun"
                again, rerun_job, failure = await _screen(remote, commit, rerun_name, rerun, ledger)
                screenings.append(again)
                if failure is not None or rerun_job is None:
                    reason = f"rerun failed: {failure}"
                    return _Handled(undecided(reason, screenings=tuple(screenings)), reason)
                try:
                    result = evaluate_screening(
                        list(jobs), [job], dev, targets, regression, [rerun_job], TRIALS
                    )
                except ValueError as exc:
                    reason = f"cannot judge {rerun_name}: {exc}"
                    return _Handled(undecided(reason, screenings=tuple(screenings)), reason)
                results.append(result)
                verdict = result["verdict"]
                screenings[-1] = replace(again, verdict=verdict)
                if verdict == "rerun-regression":
                    reason = "the regression rerun left tasks to rerun again"
        done = replace(candidate, screenings=tuple(screenings), verdict=verdict)
        if verdict == "pass":
            body = pr_body(done, diffs.get(candidate.number, ""), results, ledger.spent)
            body_file = out_dir / f"pr-{candidate.number}.md"
            body_file.write_text(mask(body, secrets))
            keep_remote = True  # it passed: the branch stays for a human either way
            try:
                url = _tool(
                    remote.repo, "gh", "pr", "create", "--draft", "--base", "main",
                    "--head", branch, "--title", pr_title(day, done), "--body-file", str(body_file),
                )  # fmt: skip
            except ToolFailed as exc:
                why = f"passed, but `gh pr create` failed ({exc}); {branch} stays on origin"
                return _Handled(replace(done, outcome="undecided", reason=why))
            return _Handled(
                replace(done, outcome="pr", pr_url=url.splitlines()[-1] if url else None)
            )
        if verdict == "fail":
            return _Handled(replace(done, outcome="rejected"))
        if verdict == "rerun-old":
            problems = "; ".join(result["reuse_problems"])
            why = f"the input run cannot be reused as the old version ({problems})"
            return _Handled(replace(done, outcome="undecided", reason=why))
        return _Handled(
            replace(done, outcome="undecided", reason=reason or verdict), budget_stop=budget_stop
        )
    finally:
        if not keep_remote:
            try:
                delete_remote_branch(remote.repo, branch)
            except ToolFailed as exc:
                print(f"improve: could not delete origin's {branch}: {exc}", file=sys.stderr)


# --- CLI -----------------------------------------------------------------------------------------


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    p = commands.add_parser(
        "improve", help="draft Skill edits from dev failures, screen them, open draft PRs"
    )
    p.add_argument("jobs", type=Path, nargs="+", metavar="job_dir", help=f"input run: {MERGE_HELP}")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--tasks-dir", type=Path, help="local benchmark tasks, for instruction.md")
    p.add_argument(
        "--regression",
        type=Path,
        default=REPO_ROOT / "evals" / "regression.json",
        help="JSON list of dev task names (default: evals/regression.json)",
    )
    p.add_argument("--repo", type=Path, default=REPO_ROOT, help="checkout the branches go in")
    p.add_argument(
        "--out", type=Path, help="output directory (default: <repo>/runs/improve-<date>)"
    )
    p.add_argument(
        "--date", type=date.fromisoformat, help="YYYY-MM-DD the branches and runs carry (today)"
    )
    p.add_argument("--budget", type=float, default=BUDGET_USD, help="USD for the whole round")
    p.add_argument("--remote-tasks-dir", default=REMOTE_TASKS_DIR)
    p.add_argument("--remote-manifest", default=REMOTE_MANIFEST)
    p.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    # Built on first use, so refused input, or input without dev failures, needs no model.
    attribution = functools.cache(lambda: sdk_judge(os.environ))
    editor = functools.cache(lambda: sdk_edit_judge(os.environ))

    async def attribution_judge(prompt: str, max_budget_usd: float) -> JudgeReply:
        return await attribution()(prompt, max_budget_usd)

    async def edit_judge(prompt: str, max_budget_usd: float) -> JudgeReply:
        return await editor()(prompt, max_budget_usd)

    today: date = args.date or date.today()
    repo: Path = args.repo.resolve()
    out: Path = args.out or repo / "runs" / f"improve-{today.isoformat()}"
    try:
        manifest = json.loads(args.manifest.read_text())
        if not isinstance(manifest, dict) or not isinstance(manifest.get("dev"), list):
            raise ValueError(f"{args.manifest} is not a split manifest with a dev list")
        report = asyncio.run(
            improve(
                [j.resolve() for j in args.jobs],
                repo=repo,
                manifest=manifest,
                regression=args.regression,
                out_dir=out.resolve(),
                attribution_judge=attribution_judge,
                edit_judge=edit_judge,
                skills=skill_sets(),
                today=today,
                remote_tasks_dir=args.remote_tasks_dir,
                remote_manifest=args.remote_manifest,
                tasks_dir=args.tasks_dir,
                budget_usd=args.budget,
                poll_seconds=args.poll_seconds,
                secrets=known_secrets(os.environ),
            )
        )
    except (OSError, ValueError, ConfigError, ToolFailed) as exc:
        print(f"improve: {exc}", file=sys.stderr)
        return 2
    outcomes = ", ".join(f"{c.number} {c.outcome}" for c in report.candidates) or "no candidates"
    print(
        f"improve: {len(report.groups)} group(s); candidates: {outcomes}; spend "
        f"${report.spend_usd:.2f} of ${report.budget_usd:.2f} -> {out / 'report.md'}"
    )
    return 0
