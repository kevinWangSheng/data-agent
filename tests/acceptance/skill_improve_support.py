"""Stand-ins and builders for the skill-improve acceptance tests (.scratch/skill-improve/spec.md
"Testing Decisions", ticket 125): test_skill_improve*.py.

The seam is `evals.improve.improve`, called in process with everything it reaches injected or
replaced, so no model, server, GitHub or network is touched:

- the judges are `FakeJudge`s answering from tests/fixtures/skill_improve/replies.json;
- `da-remote` and `gh` are fake executables put first on `PATH`; they log every call, with the
  bare origin's branches at that moment, to one JSON-lines file the judges also log to, so the
  order of judge calls, pushes and screenings can be read back;
- the repo is a temporary git checkout of tests/fixtures/skill_improve/repo/ whose `origin` is a
  local bare repo; git's global and system config, `HOME` and `GH_CONFIG_DIR` point into the
  test's temporary directory, every `GIT_*` variable is removed and the working directory is a
  temporary non-repo directory, so a git command that ignores the given repo finds none;
- `da-remote pull` builds the screening job from the arguments its `start` was given and the
  test's plan for that screening.

The fake `da-remote` and `gh` run this file as a script entry, so its top-level imports stay in
the standard library (the agent package is imported inside the functions that need it).

Copied from existing tests, not imported (ticket 125's Authority): the trial layout and version
header of tests/test_eval_accept.py:23 (`write_job`), the fake judge of
tests/test_eval_attribute.py:80 (`FakeJudge`), the fake executables on `PATH` and the bare origin
of tests/test_da_remote.py:17-109.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from evals.attribute import JudgeReply
    from evals.improve import ImproveReport, InputRefused

SUPPORT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SUPPORT_DIR.parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "skill_improve"
MANIFEST: dict[str, Any] = json.loads((FIXTURES / "manifest.json").read_text())
DEV: list[str] = MANIFEST["dev"]
HOLDOUT: list[str] = MANIFEST["holdout"]
REGRESSION_FILE = FIXTURES / "regression.json"
REGRESSION: list[str] = json.loads(REGRESSION_FILE.read_text())
REPLIES: dict[str, Any] = json.loads((FIXTURES / "replies.json").read_text())

NOT_IMPLEMENTED = "ticket 126: improve is not implemented yet"
TODAY = date(2026, 11, 24)
# Contract values, written out here so the tests do not take them from the code under test.
BUDGET_USD = 25.0
TRIALS = 3
MAX_TASKS = 30
NOISE_NOTE = "开发集噪声大（单配置标准误约 ±2.2–2.5 分），通过初筛不等于显著提升"  # noqa: RUF001
REMOTE_TASKS_DIR = "../da-benchmarks/data-eng-bench/tasks"
REMOTE_MANIFEST = "evals/manifest.json"
JUDGE_COST_USD = 0.02

# A DeepSeek model priced from the price table, so `evals spend` bills an off-peak run at half.
MODEL = "deepseek-chat"
VERSION: dict[str, Any] = {
    "config": "full",
    "layers": {"skill": True, "verifiers": True, "context": True},
    "model": MODEL,
    "sdk_version": "0.2",
    "cli_version": "2.1",
}
RECORD: dict[str, Any] = {"benchmark_commit": "b" * 40, "machine": "box", "model": MODEL}
EVENTS_FILE = Path("agent", "events", "run-20261124T120000Z-0000abcd.jsonl")
MARKER = re.compile(r"\[marker:([\w-]+)\]")


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def off_peak(moment: datetime) -> str:
    """Noon UTC of the moment's day: outside DeepSeek's peak hours on any day (evals.spend)."""
    return iso(moment.astimezone(UTC).replace(hour=12, minute=0, second=0, microsecond=0))


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def append_log(log: Path, entry: dict[str, Any]) -> None:
    with log.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# --- Harbor jobs ---------------------------------------------------------------------------------


def write_trial(
    job: Path,
    task: str,
    index: int,
    *,
    reward: float,
    header: Mapping[str, Any],
    started: datetime,
    finished: datetime,
    text: str,
    cost_usd: float,
    name: str | None = None,
) -> Path:
    """One trial as Harbor and the agent write it: `result.json`, and an event file whose first
    line is the version header and whose result event carries a price-table cost."""
    trial = job / (name or f"{task}__{index}")
    (trial / EVENTS_FILE).parent.mkdir(parents=True)
    result = {
        "task_name": f"org/{task}",
        "trial_name": trial.name,
        "started_at": iso(started),
        "finished_at": iso(finished),
        "verifier_result": {"rewards": {"reward": reward}},
    }
    (trial / "result.json").write_text(json.dumps(result))
    events = [
        {"seq": 0, "type": "version", "ts": off_peak(started), **header},
        {"seq": 1, "type": "text", "ts": off_peak(started), "text": text},
        {
            "seq": 2,
            "type": "result",
            "ts": off_peak(started),
            "subtype": "success",
            "num_turns": 1,
            "cost_usd": cost_usd,
            "cost_basis": "price_table",
        },
    ]
    (trial / EVENTS_FILE).write_text("".join(json.dumps(e) + "\n" for e in events))
    return trial


def write_out_files(out: Path, tasks: Sequence[str], stamp: str) -> None:
    """The run record and request `evals.ablate` writes into an OUT."""
    (out / "records").mkdir(parents=True, exist_ok=True)
    (out / "records" / f"{stamp}.json").write_text(json.dumps(RECORD))
    request = {"model": MODEL, "split": "dev", "tasks": list(tasks)}
    (out / "request.json").write_text(json.dumps(request) + "\n")


@dataclass(frozen=True)
class TaskRun:
    """One task of an input job: the first `wins` of its trials pass, the others fail with
    `marker` in their traces (the fake attribution judge answers by it)."""

    wins: int = TRIALS
    marker: str | None = None


def trace_text(trial_name: str, marker: str | None) -> str:
    tag = f" [marker:{marker}]" if marker else ""
    return f"Trace of {trial_name}: the agent answered.{tag}"


def write_input_job(
    out: Path,
    tasks: Mapping[str, TaskRun],
    *,
    commit: str,
    skill_version: str,
    started: datetime,
) -> Path:
    """OUT `out` with one job, `jobs/full`, of `TRIALS` trials per task, as a dev run of
    `evals ablate` leaves it; returns the job directory."""
    job = out / "jobs" / "full"
    header = {**VERSION, "git_commit": commit, "skill_version": skill_version}
    for task, run in tasks.items():
        for i in range(TRIALS):
            name = f"{task}__{i}"
            failed = i >= run.wins
            write_trial(
                job,
                task,
                i,
                reward=0.0 if failed else 1.0,
                header=header,
                started=started,
                finished=started + timedelta(minutes=10),
                text=trace_text(name, run.marker if failed else None),
                cost_usd=0.05,
            )
    (job / "config.json").write_text("{}")
    write_out_files(out, list(tasks), started.strftime("%Y%m%dT%H%M%SZ"))
    return job


def write_dab_run(out: Path) -> Path:
    """A DAB run directory (evals/dab.py:8-12): per-run event files and a submission, no
    `<trial>/result.json`."""
    run = out / "stockindex" / "query1" / "run0"
    run.mkdir(parents=True)
    header = {"seq": 0, "type": "version", "ts": "2026-11-23T12:00:00+00:00", **VERSION}
    failed = {"seq": 1, "type": "text", "text": "Trace of a DAB run. [marker:alpha]"}
    (run / "events.jsonl").write_text(json.dumps(header) + "\n" + json.dumps(failed) + "\n")
    submission = [{"dataset": "stockindex", "query": "1", "run": 0, "answer": "42"}]
    (out / "submission.json").write_text(json.dumps(submission))
    return out


# --- the fake server: one screening per `da-remote start` ----------------------------------------


@dataclass(frozen=True)
class Screen:
    """What the k-th screening (`da-remote start`, reruns included) gives: per task how many of
    its trials pass (the rest pass all), each trial's listed cost, the smoke trial's, and when
    it finished (default: now)."""

    wins: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.04
    smoke_cost_usd: float = 0.10
    finished: str | None = None


def option_values(args: Sequence[str]) -> dict[str, list[str]]:
    """`--key value` and `--key=value` pairs of an argument list, each key's values in order."""
    out: dict[str, list[str]] = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--") and "=" in arg:
            key, value = arg.split("=", 1)
            out.setdefault(key, []).append(value)
        elif arg.startswith("--") and i + 1 < len(args):
            out.setdefault(arg, []).append(args[i + 1])
            i += 1
        i += 1
    return out


def origin_branches(origin: Path) -> dict[str, str]:
    """{branch: commit} of the bare origin."""
    listed = subprocess.run(
        ["git", "--git-dir", str(origin), "for-each-ref", "--format=%(refname:short) %(objectname)",
         "refs/heads/"],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return dict(line.split(" ", 1) for line in listed.splitlines() if line)


def write_screening(top: Path, name: str, args: Sequence[str], rev: str, screen: Screen) -> float:
    """What `da-remote pull <name>` brings back of `ablate <args>`, run at commit `rev`, into
    `top/runs/<name>/`; returns its billed spend. Nothing comes back unless ablate's `--out` is
    `runs/<name>`, as on the server."""
    opts = option_values(args)
    if opts.get("--out") != [f"runs/{name}"]:
        return 0.0
    tasks = opts.get("--task", [])
    trials = int(opts.get("--trials", [str(TRIALS)])[0])
    config = opts.get("--config", ["full"])[0]
    finished = datetime.fromisoformat(screen.finished) if screen.finished else datetime.now(UTC)
    started = finished - timedelta(minutes=30)
    out = top / "runs" / name
    header = {**VERSION, "git_commit": rev, "skill_version": f"s-{rev[:12]}"}
    job = out / "jobs" / config
    for task in tasks:
        for i in range(trials):
            write_trial(
                job,
                task,
                i,
                reward=1.0 if i < screen.wins.get(task, trials) else 0.0,
                header=header,
                started=started,
                finished=finished,
                text=f"Screening trial {task}__{i}.",
                cost_usd=screen.cost_usd,
            )
    if tasks:
        (job / "config.json").write_text("{}")
        smoke = out / "smoke" / "20261124T120000.000000Z" / config
        write_trial(
            smoke,
            tasks[0],
            0,
            reward=1.0,
            header=header,
            started=started,
            finished=started,
            text="Smoke trial.",
            cost_usd=screen.smoke_cost_usd,
            name=f"{tasks[0]}__smoke",
        )
    write_out_files(out, tasks, started.strftime("%Y%m%dT%H%M%SZ"))
    listed = len(tasks) * trials * screen.cost_usd + (screen.smoke_cost_usd if tasks else 0.0)
    return listed * 0.5  # DeepSeek, price table, off peak (evals.spend)


def fake_da_remote(argv: list[str]) -> int:
    """`da-remote` without a server: logs each call with origin's branches at that moment;
    `setup` remembers the commit, `start` the arguments, `status` reports the run finished,
    `pull` writes the screening (`write_screening`) from the test's plan."""
    log = Path(os.environ["FAKE_LOG"])
    origin = Path(os.environ["FAKE_ORIGIN"])
    state = log.parent / "server"
    state.mkdir(exist_ok=True)
    entry: dict[str, Any] = {"tool": "da-remote", "argv": argv, "cwd": os.getcwd()}
    entry["origin"] = origin_branches(origin)
    append_log(log, entry)
    cmd = argv[0] if argv else ""
    if cmd == "setup" and len(argv) <= 2:
        rev = argv[1] if len(argv) == 2 else "origin/main"
        (state / "rev").write_text(rev)
        print(f"setup: server at {rev}")
        return 0
    if cmd == "start" and len(argv) >= 4 and argv[2] == "--":
        name = argv[1]
        index = len(list(state.glob("start-*.json")))
        rev = (state / "rev").read_text() if (state / "rev").exists() else "origin/main"
        start = {"name": name, "args": argv[3:], "rev": rev, "index": index}
        (state / f"start-{index:03d}.json").write_text(json.dumps(start))
        (state / f"run-{name}.json").write_text(json.dumps(start))
        print(f"start: {name} running in the background")
        return 0
    if cmd == "status" and len(argv) == 2:
        name = argv[1]
        if not (state / f"run-{name}.json").exists():
            print(f"status: no run named {name}")
            return 1
        print(f"{name}: finished, exit 0")
        print(f"--- last 20 lines of runs/{name}/run.log")
        print("---")
        return 0
    if cmd == "pull" and len(argv) == 2:
        name = argv[1]
        if not (state / f"run-{name}.json").exists():
            print(f"pull: no runs/{name} on the server", file=sys.stderr)
            return 1
        start = json.loads((state / f"run-{name}.json").read_text())
        plan = json.loads(Path(os.environ["FAKE_PLAN"]).read_text())
        screen = Screen(**plan[start["index"]]) if start["index"] < len(plan) else Screen()
        top = Path(git("rev-parse", "--show-toplevel", cwd=Path.cwd()))
        billed = write_screening(top, name, start["args"], start["rev"], screen)
        append_log(log, {"tool": "pull-spend", "name": name, "billed_usd": billed})
        print(f"pull: runs/{name}")
        return 0
    print(f"fake da-remote: unexpected call {argv}", file=sys.stderr)
    return 2


def fake_gh(argv: list[str]) -> int:
    """`gh` without GitHub: logs each call and the PR body it was given (`--body`, `--body-file
    <path>` or `--body-file -`), and prints a placeholder for a created PR's address."""
    log = Path(os.environ["FAKE_LOG"])
    body: str | None = None
    for i, arg in enumerate(argv):
        value = argv[i + 1] if i + 1 < len(argv) else None
        if arg in ("--body", "-b") and value is not None:
            body = value
        elif arg.startswith("--body="):
            body = arg.split("=", 1)[1]
        elif arg in ("--body-file", "-F") and value is not None:
            body = sys.stdin.read() if value == "-" else Path(value).read_text()
        elif arg.startswith("--body-file="):
            path = arg.split("=", 1)[1]
            body = sys.stdin.read() if path == "-" else Path(path).read_text()
    append_log(log, {"tool": "gh", "argv": argv, "cwd": os.getcwd(), "body": body})
    if argv[:2] == ["pr", "create"]:
        print(f"fake-pr-{sum(1 for _ in log.open())}")
    return 0


FAKE_ENTRY = """#!{python}
import sys
sys.path.insert(0, {support!r})
from skill_improve_support import {function}
sys.exit({function}(sys.argv[1:]))
"""


def write_exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


# --- judges --------------------------------------------------------------------------------------


class FakeJudge:
    """A judge (`evals.attribute.Judge`) answering each prompt with `answer(prompt)` at
    `JUDGE_COST_USD` a call; every prompt is kept and every call logged."""

    def __init__(self, kind: str, log: Path, answer: "Any") -> None:
        self.kind = kind
        self.log = log
        self.answer = answer
        self.prompts: list[str] = []

    async def __call__(self, prompt: str, max_budget_usd: float) -> "JudgeReply":
        from evals.attribute import JudgeReply

        self.prompts.append(prompt)
        append_log(self.log, {"tool": "judge", "kind": self.kind, "cost_usd": JUDGE_COST_USD})
        return JudgeReply(text=self.answer(prompt), cost_usd=JUDGE_COST_USD)


# --- the world one test runs in ------------------------------------------------------------------


@dataclass(frozen=True)
class Start:
    """One `da-remote start`, with the setup before it and origin's branches at that moment."""

    name: str
    evals_args: list[str]
    options: dict[str, list[str]]
    setup_rev: str | None
    origin: dict[str, str]

    @property
    def tasks(self) -> list[str]:
        return self.options.get("--task", [])


class World:
    """A temporary repo with a bare origin, fake `da-remote` and `gh` on `PATH`, fake judges,
    and the call log they all write."""

    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp = tmp
        self.log = tmp / "calls.jsonl"
        self.log.touch()
        self.plan_file = tmp / "plan.json"
        self.plan_file.write_text("[]")
        self.out = tmp / "improve-out"
        self.attribution = dict(REPLIES["attribution"])
        self.edits = dict(REPLIES["edits"])
        self.isolate(monkeypatch)
        self.repo, self.origin = self.make_repo()
        self.base = git("rev-parse", "HEAD", cwd=self.repo)
        from data_agent.agent import skill_version
        from evals.attribute import load_skills

        self.skill_version = skill_version(self.repo / "skills")
        self.skills = {self.skill_version: load_skills(self.repo / "skills")}
        self.attribution_judge = FakeJudge("attribution", self.log, self.attribute_answer)
        self.edit_judge = FakeJudge("edit", self.log, self.edit_answer)

    def isolate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bin_dir = self.tmp / "bin"
        for name, function in (("da-remote", "fake_da_remote"), ("gh", "fake_gh")):
            text = FAKE_ENTRY.format(
                python=sys.executable, support=str(SUPPORT_DIR), function=function
            )
            write_exe(bin_dir / name, text)
        for key in list(os.environ):
            if key.startswith("GIT_"):
                monkeypatch.delenv(key)
        (self.tmp / "home").mkdir()
        (self.tmp / "gitconfig").write_text("")
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("HOME", str(self.tmp / "home"))
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(self.tmp / "gitconfig"))
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        monkeypatch.setenv("GH_CONFIG_DIR", str(self.tmp / "gh"))
        monkeypatch.setenv("GH_TOKEN", "")
        monkeypatch.setenv("DA_REMOTE_HOST", "fake-host.invalid")
        monkeypatch.setenv("FAKE_LOG", str(self.log))
        monkeypatch.setenv("FAKE_ORIGIN", str(self.tmp / "origin.git"))
        monkeypatch.setenv("FAKE_PLAN", str(self.plan_file))
        monkeypatch.chdir(elsewhere)

    def make_repo(self) -> tuple[Path, Path]:
        repo = self.tmp / "repo"
        shutil.copytree(FIXTURES / "repo", repo)
        (repo / "gitignore").rename(repo / ".gitignore")
        git("init", "-q", "-b", "main", cwd=repo)
        git("config", "user.email", "t@example.com", cwd=repo)
        git("config", "user.name", "t", cwd=repo)
        git("add", ".", cwd=repo)
        git("commit", "-q", "-m", "fixture repo", cwd=repo)
        origin = self.tmp / "origin.git"
        git("clone", "-q", "--bare", str(repo), str(origin), cwd=self.tmp)
        git("remote", "add", "origin", str(origin), cwd=repo)
        git("fetch", "-q", "origin", cwd=repo)
        return repo, origin

    # Judges' answers.

    def attribute_answer(self, prompt: str) -> str:
        markers = sorted(set(MARKER.findall(prompt)))
        if len(markers) != 1 or markers[0] not in self.attribution:
            return f"no single known marker in the prompt: {markers}"
        return json.dumps(self.attribution[markers[0]])

    def edit_answer(self, prompt: str) -> str:
        """The edit of the first group whose suggestion is in the prompt (contract 3 puts the
        group's suggestions there; the Skill text in it holds every quote)."""
        for marker, reply in self.attribution.items():
            if reply["suggestion"] in prompt and marker in self.edits:
                return json.dumps(self.edits[marker])
            if reply["suggestion"] in prompt and marker == "beta-fm4":
                return json.dumps(self.edits["beta"])
        return "{}"

    # Inputs.

    def input_job(
        self,
        tasks: Mapping[str, TaskRun],
        *,
        name: str = "u1",
        cover: Sequence[str] | None = None,
        started: datetime | None = None,
        skill_version: str | None = None,
    ) -> Path:
        """An input job under `tmp/runs-in/<name>` with every task of `cover` (default: the whole
        dev set) at 3/3 unless `tasks` says otherwise, plus `tasks`' own."""
        runs = dict.fromkeys(DEV if cover is None else cover, TaskRun()) | dict(tasks)
        return write_input_job(
            self.tmp / "runs-in" / name,
            runs,
            commit=self.base,
            skill_version=skill_version or self.skill_version,
            started=started or datetime.now(UTC) - timedelta(days=1),
        )

    def regression_file(self, tasks: Sequence[str]) -> Path:
        path = self.tmp / "regression.json"
        path.write_text(json.dumps(list(tasks)))
        return path

    def plan(self, *screens: Screen) -> None:
        self.plan_file.write_text(json.dumps([asdict(s) for s in screens]))

    # The round.

    def run(self, jobs: Sequence[Path], *, regression: Path = REGRESSION_FILE) -> "ImproveReport":
        """`improve` on the jobs; today, while it raises NotImplementedError, an AssertionError
        (the expected failure of `TICKET_126`)."""
        from evals.improve import improve

        try:
            return asyncio.run(
                improve(
                    list(jobs),
                    repo=self.repo,
                    manifest=MANIFEST,
                    regression=regression,
                    out_dir=self.out,
                    attribution_judge=self.attribution_judge,
                    edit_judge=self.edit_judge,
                    skills=self.skills,
                    today=TODAY,
                    remote_tasks_dir=REMOTE_TASKS_DIR,
                    remote_manifest=REMOTE_MANIFEST,
                    budget_usd=BUDGET_USD,
                    poll_seconds=0,
                )
            )
        except NotImplementedError:
            raise AssertionError(NOT_IMPLEMENTED) from None

    def refused(self, jobs: Sequence[Path]) -> "InputRefused":
        """The `InputRefused` `improve` raises for the jobs; an AssertionError when it raises
        NotImplementedError (today) or takes them."""
        from evals.improve import InputRefused

        try:
            self.run(jobs)
        except InputRefused as exc:
            return exc
        raise AssertionError("improve took input it must refuse")

    def report_md(self) -> str:
        path = self.out / "report.md"
        assert path.is_file(), "improve wrote no report.md"
        return path.read_text()

    # What happened.

    def calls(self, tool: str) -> list[dict[str, Any]]:
        lines = self.log.read_text().splitlines()
        return [e for e in map(json.loads, lines) if e["tool"] == tool]

    def starts(self) -> list[Start]:
        out: list[Start] = []
        setup: str | None = None
        for call in self.calls("da-remote"):
            argv = call["argv"]
            if argv[:1] == ["setup"]:
                setup = argv[1] if len(argv) > 1 else None
            elif argv[:1] == ["start"] and len(argv) >= 4 and argv[2] == "--":
                evals_args = argv[3:]
                out.append(
                    Start(argv[1], evals_args, option_values(evals_args), setup, call["origin"])
                )
        return out

    def spend_before_each_start(self) -> list[float]:
        """Judge cost plus pulled billed spend logged before each `da-remote start`."""
        spent, out = 0.0, []
        for entry in map(json.loads, self.log.read_text().splitlines()):
            if entry["tool"] == "judge":
                spent += entry["cost_usd"]
            elif entry["tool"] == "pull-spend":
                spent += entry["billed_usd"]
            elif entry["tool"] == "da-remote" and entry["argv"][:1] == ["start"]:
                out.append(spent)
        return out

    def pr_creates(self) -> list[dict[str, Any]]:
        return [c for c in self.calls("gh") if c["argv"][:2] == ["pr", "create"]]

    def origin_branches(self) -> dict[str, str]:
        return origin_branches(self.origin)

    def local_branches(self) -> dict[str, str]:
        listed = git(
            "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads/", cwd=self.repo
        )
        return dict(line.split(" ", 1) for line in listed.splitlines() if line)

    def show(self, rev: str, path: str) -> bytes:
        return subprocess.run(
            ["git", "show", f"{rev}:{path}"], cwd=self.repo, check=True, capture_output=True
        ).stdout

    def skill_version_at(self, rev: str) -> str:
        """`skill_version` of the `skills/` directory as committed at `rev`."""
        from data_agent.agent import skill_version

        target = self.tmp / f"tree-{rev[:12]}"
        target.mkdir()
        archive = subprocess.run(
            ["git", "archive", rev, "skills"], cwd=self.repo, check=True, capture_output=True
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(target)], input=archive, check=True)
        return skill_version(target / "skills")


def real_repo_branches() -> str:
    """The `skill-improve/*` branches of this checkout's repository, which every worktree and the
    main checkout share."""
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "branch", "--list", "skill-improve/*"],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
