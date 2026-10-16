"""`attribute JOB_DIR --manifest M --out DIR`: draft a failure-attribution report (plan §3).

Reads only the dev set's failed trials: a trial of any other task is skipped before its files
are opened (ADR-0004). For each failure a judge model reads the run's event file, the Skills and,
when available, the task instruction and verifier output, and drafts one FM1-FM4 label
(CONTEXT: Failure mode), a verbatim quote of the Skill text involved and a generic Skill edit.
Nothing is applied: a human reviews the report before any Skill changes.

Each reply is checked before it is kept: the label is one of FM1-FM4, the quote appears verbatim
in the named Skill, and the suggestion names no task id, table or other identifier taken from the
benchmark (ADR-0005). A rejected reply is retried once with the problems listed; a suggestion
that still names one is withheld.

The judge prompt is our own (ADR-0005 Update: DAB's `failure_analysis/` text is not copied).
"""

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from sqlglot import exp
from sqlglot.errors import SqlglotError

from data_agent.agent import SKILLS_DIR, ConfigError, ModelSettings, result_fields, skill_version
from data_agent.events import REDACTED, known_secrets
from evals.harbor import Trial, load_trials
from evals.metrics import is_success
from evals.report import is_infra_error

FAILURE_MODES = {
    "FM1": "no attempt: the agent never produced a real answer or model (gave up, stalled, "
    "ran out of turns before acting)",
    "FM2": "wrong plan: the approach itself could not give the asked-for result (wrong "
    "steps, wrong grain or output shape, a misread question)",
    "FM3": "right plan, wrong data: the approach was sound but used the wrong database, "
    "table, column or field",
    "FM4": "right plan and data, wrong implementation: formula, join, filter, parsing or "
    "type handling was wrong",
}
NO_SKILL = "none"
MAX_ATTEMPTS = 2
DEFAULT_BUDGET_USD = 2.0
# A trace longer than this keeps its start and its end: the plan and the final answer.
MAX_TRACE_CHARS = 60_000
MAX_EVENT_CHARS = 1_500
MAX_VERIFIER_CHARS = 4_000

JUDGE_SYSTEM = """\
You review failed runs of a data agent to improve its Skills, the workflow guidance it follows. \
You read one run's trace and decide why it failed. You reply with one JSON object and nothing \
else."""

JUDGE_PROMPT = """\
The run below failed its benchmark check. Decide why.

## Failure modes

Pick exactly one. Judge only the steps that fed the final answer or model; ignore dead ends the \
agent recovered from.

{modes}

## Skills the agent was given

{skills}

## Task instruction

{instruction}

## Verifier output

{verifier}

## Trace

One line per event, `#seq type`. Long events are cut.

{trace}

## Reply

Reply with only this JSON object:

{{"failure_mode": "FM1|FM2|FM3|FM4",
  "evidence": "<the steps (cite #seq) that show the failure, and what went wrong>",
  "skill": "<the Skill whose guidance was involved, by name, or \\"none\\">",
  "skill_quote": "<the exact sentence(s) of that Skill involved, copied verbatim; \\"\\" if none>",
  "suggestion": "<a concrete edit to the Skill text that would prevent this kind of failure>"}}

Rules:
- `skill_quote` is copied character for character from the Skill. If no Skill text covered \
the mistake, set `skill` to "none" and `skill_quote` to "", and suggest the rule to add.
- `suggestion` must hold for any database or dbt project. Do not name this task, its tables, \
columns, models, values or domain; describe the general pattern instead.
"""

RETRY = """

## Your previous reply was rejected

{previous}

Problems:
{problems}

Reply again with the corrected JSON object only."""


@dataclass(frozen=True)
class JudgeReply:
    text: str
    cost_usd: float


Judge = Callable[[str], Awaitable[JudgeReply]]


def load_skills(skills_dir: Path = SKILLS_DIR) -> dict[str, str]:
    """{Skill name: SKILL.md text} for every Skill in the repo."""
    return {p.parent.name: p.read_text() for p in sorted(skills_dir.glob("*/SKILL.md"))}


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]} …[{len(text) - limit} chars cut]"


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def read_events(path: Path) -> list[dict[str, Any]]:
    # Only "\n" ends a JSONL line; splitlines() would also split at U+2028 inside a string.
    return [json.loads(line) for line in path.read_text().split("\n") if line.strip()]


def render_event(event: dict[str, Any]) -> str | None:
    kind = event.get("type")
    head = f"#{event.get('seq')} {kind}"
    if kind == "sql":  # the tool_start before it carries the same SQL
        return None
    if kind in ("text", "thinking"):
        body = event.get("text", "")
    elif kind == "tool_start":
        body = f"{event.get('tool')} {_as_text(event.get('input'))}"
    elif kind == "tool_end":
        error = " ERROR" if event.get("is_error") else ""
        body = f"{event.get('tool')}{error} {_as_text(event.get('content', event.get('output')))}"
    else:
        body = _as_text({k: v for k, v in event.items() if k not in ("seq", "type", "ts")})
    return f"{head} {_cut(body, MAX_EVENT_CHARS)}"


def render_trace(events: list[dict[str, Any]]) -> str:
    lines = [line for line in map(render_event, events) if line is not None]
    text = "\n".join(lines)
    if len(text) <= MAX_TRACE_CHARS:
        return text
    head, tail, size = [], [], 0
    for line in lines:  # first 40% from the start, the rest from the end
        if size + len(line) > MAX_TRACE_CHARS * 0.4:
            break
        head.append(line)
        size += len(line) + 1
    for line in reversed(lines[len(head) :]):
        if size + len(line) > MAX_TRACE_CHARS:
            break
        tail.append(line)
        size += len(line) + 1
    omitted = len(lines) - len(head) - len(tail)
    return "\n".join([*head, f"… {omitted} events omitted …", *reversed(tail)])


SNAKE_CASE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b")
WORD = re.compile(r"[\w-]+")
DOTTED = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b")


def sql_names(sql: str) -> set[str]:
    """Table and column names a statement reads; unparsable SQL gives none."""
    try:
        trees = sqlglot.parse(sql, read="duckdb")
    except SqlglotError:
        return set()
    names: set[str] = set()
    for tree in trees:
        if tree is not None:
            names |= {t.name for t in tree.find_all(exp.Table)}
            names |= {c.name for c in tree.find_all(exp.Column)}
    return {n for n in names if n}


def specific_names(
    manifest: Mapping[str, Any], events: list[dict[str, Any]], context: str, skills: dict[str, str]
) -> set[str]:
    """Names a generic suggestion must not use: every task id in the manifest, every table and
    column the run's SQL touched, every `schema.table` name and snake_case identifier anywhere in
    the trace (schema tools list tables the SQL may never touch), the task instruction or the
    verifier output. Words the Skills use are general by policy, so they are allowed.
    """
    names = {*manifest.get("dev", []), *manifest.get("holdout", [])}
    for event in events:
        if event.get("type") == "sql" and isinstance(event.get("sql"), str):
            names |= sql_names(event["sql"])
    text = f"{json.dumps(events, ensure_ascii=False)}\n{context}"
    names |= set(SNAKE_CASE.findall(text))
    names |= {part for dotted in DOTTED.findall(text) for part in dotted}
    allowed = {w.lower() for text in skills.values() for w in WORD.findall(text)}
    return {n for n in names if n.lower() not in allowed}


def named_in(text: str, names: set[str]) -> list[str]:
    return sorted(
        n for n in names if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text, re.IGNORECASE)
    )


def _normalize(text: str) -> str:
    return " ".join(text.split())


def parse_reply(text: str) -> dict[str, Any]:
    """The JSON object in a reply, allowing a code fence or prose around it."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("the reply holds no JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("the reply is not a JSON object")
    return value


def check_reply(
    fields: dict[str, Any], skills: dict[str, str], names: set[str]
) -> tuple[list[str], bool, list[str]]:
    """(problems that make the reply unusable, whether the quote is verbatim, specific names)."""
    problems: list[str] = []
    if fields.get("failure_mode") not in FAILURE_MODES:
        problems.append(f"failure_mode {fields.get('failure_mode')!r} is not one of FM1-FM4")
    for key in ("evidence", "skill", "skill_quote", "suggestion"):
        if not isinstance(fields.get(key), str):
            problems.append(f"{key} is missing or not a string")
    if problems:
        return problems, False, []
    skill, quote = fields["skill"], fields["skill_quote"]
    if skill == NO_SKILL:
        verified = quote == ""
    else:
        verified = (
            skill in skills and bool(quote) and _normalize(quote) in _normalize(skills[skill])
        )
    named = named_in(fields["suggestion"], names)
    return problems, verified, named


def _feedback(problems: list[str], verified: bool, named: list[str], skill: Any) -> list[str]:
    out = list(problems)
    if not problems and not verified:
        out.append(
            f"skill_quote is not verbatim text of the Skill {skill!r} (Skills: see above), "
            'or skill is "none" with a non-empty quote'
        )
    if named:
        out.append(
            "suggestion names benchmark-specific identifiers "
            f"({', '.join(named)}); describe the general pattern instead"
        )
    return out


class BudgetReached(Exception):
    pass


class Meter:
    """Counts every judge call's cost as it happens and refuses a call once spend reaches the
    budget, so retries stay under it and a later failure never hides an earlier charge."""

    def __init__(self, judge: Judge, budget_usd: float) -> None:
        self._judge = judge
        self.budget_usd = budget_usd
        self.spent = 0.0

    async def __call__(self, prompt: str) -> JudgeReply:
        if self.spent >= self.budget_usd:
            raise BudgetReached(f"not attributed: spend reached the ${self.budget_usd} budget")
        reply = await self._judge(prompt)
        self.spent += reply.cost_usd
        return reply


@dataclass
class Failure:
    trial: Trial
    events_path: Path


def _read_optional(path: Path | None, limit: int) -> str | None:
    if path is None or not path.is_file():
        return None
    text = path.read_text(errors="replace")
    return text if len(text) <= limit else f"…[{len(text) - limit} chars cut]\n{text[-limit:]}"


async def attribute_one(
    failure: Failure,
    manifest: Mapping[str, Any],
    judge: Judge,
    skills: dict[str, str],
    tasks_dir: Path | None,
) -> dict[str, Any]:
    trial = failure.trial
    events = read_events(failure.events_path)
    header = events[0] if events and events[0].get("type") == "version" else {}
    instruction = _read_optional(
        tasks_dir / trial.task / "instruction.md" if tasks_dir else None, 20_000
    )
    verifier = _read_optional(trial.dir / "verifier" / "test-stdout.txt", MAX_VERIFIER_CHARS)
    names = specific_names(manifest, events, f"{instruction or ''}\n{verifier or ''}", skills)
    prompt = JUDGE_PROMPT.format(
        modes="\n".join(f"- {k}: {v}" for k, v in FAILURE_MODES.items()),
        skills="\n\n".join(f'<skill name="{n}">\n{t}\n</skill>' for n, t in skills.items()),
        instruction=instruction or "(not available)",
        verifier=verifier or "(not available)",
        trace=render_trace(events),
    )
    entry: dict[str, Any] = {
        "task": trial.task,
        "trial": trial.name,
        "events": str(failure.events_path),
        "run_skill_version": header.get("skill_version"),
        "run_config": header.get("config"),
    }
    attempt_prompt = prompt
    fields: dict[str, Any] = {}
    problems: list[str] = []
    verified, named = False, []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        reply = await judge(attempt_prompt)
        entry["attempts"] = attempt
        try:
            fields = parse_reply(reply.text)
        except ValueError as exc:
            problems, fields = [f"unreadable reply: {exc}"], {}
        else:
            problems, verified, named = check_reply(fields, skills, names)
        feedback = _feedback(problems, verified, named, fields.get("skill"))
        if not feedback:
            break
        attempt_prompt = prompt + RETRY.format(
            previous=_cut(reply.text, 4_000), problems="\n".join(f"- {p}" for p in feedback)
        )
    if problems:
        entry.update(failure_mode=None, error="; ".join(problems))
        return entry
    entry.update(
        failure_mode=fields["failure_mode"],
        evidence=fields["evidence"],
        skill=fields["skill"],
        skill_quote=fields["skill_quote"],
        quote_verified=verified,
        suggestion=None if named else fields["suggestion"],
        specific_names=named,
        error=None,
    )
    return entry


def dev_failures(trials: list[Trial], dev: set[str]) -> tuple[list[Trial], int, int]:
    """(failed dev trials to attribute, trials outside the dev set, dev infrastructure errors)."""
    in_dev = [t for t in trials if t.task in dev]
    failed = [t for t in in_dev if not is_success(t.reward)]
    infra = [t for t in failed if is_infra_error(t)]
    return [t for t in failed if t not in infra], len(trials) - len(in_dev), len(infra)


async def attribute(
    job_dir: Path,
    manifest: Mapping[str, Any],
    out_dir: Path,
    judge: Judge,
    skills: dict[str, str],
    tasks_dir: Path | None = None,
    budget_usd: float = DEFAULT_BUDGET_USD,
    secrets: Iterable[str] = (),
) -> dict[str, Any]:
    """Attribute every failed dev trial in the job; write `attribution.json` and `report.md`.

    Infrastructure errors are rerun, not attributed. Stops calling the judge once spend
    reaches `budget_usd`; the rest are listed as not attributed. `secrets` are masked in
    everything written, as in event files.
    """
    failures, skipped, infra = dev_failures(load_trials(job_dir), set(manifest["dev"]))
    meter = Meter(judge, budget_usd)
    entries: list[dict[str, Any]] = []
    for trial in failures:
        events = trial.dir / "agent" / "events.jsonl"
        base = {"task": trial.task, "trial": trial.name, "failure_mode": None}
        before = meter.spent
        if not events.is_file():
            entry = {**base, "error": "no event file"}
        else:
            try:
                entry = await attribute_one(
                    Failure(trial, events), manifest, meter, skills, tasks_dir
                )
            except ConfigError:
                raise
            except BudgetReached as exc:
                entry = {**base, "error": str(exc)}
            except Exception as exc:  # one broken call or event file must not lose the rest
                entry = {**base, "error": f"judge failed: {exc}"}
        entries.append({**entry, "cost_usd": round(meter.spent - before, 6)})
    spent = meter.spent
    report = {
        "skill_version": skill_version(),
        "n_failures": len(failures),
        "infra_errors": infra,
        "skipped_non_dev_trials": skipped,
        "cost_usd": round(spent, 6),
        "failures": entries,
    }
    report = mask(report, secrets)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "attribution.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    (out_dir / "report.md").write_text(render_markdown(report))
    return report


def mask(value: Any, secrets: Iterable[str]) -> Any:
    """`value` with every secret string replaced, longest first (as `EventLog` does)."""
    ordered = sorted({s for s in secrets if s}, key=len, reverse=True)
    if not ordered:
        return value
    text = json.dumps(value, ensure_ascii=False)
    for secret in ordered:
        text = text.replace(json.dumps(secret, ensure_ascii=False)[1:-1], REDACTED)
    return json.loads(text)


def _quote_block(text: str) -> str:
    return "\n".join(f"> {line}" for line in text.splitlines())


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Failure attribution (dev set, draft for human review)",
        "",
        f"- Dev failures: {report['n_failures']} (infrastructure errors, not attributed: "
        f"{report['infra_errors']})",
        f"- Skill version now: `{report['skill_version']}`; quotes are checked against it",
        f"- Judge spend: ${report['cost_usd']:.4f}",
        "",
        "Nothing here has been applied. Review each suggestion before editing a Skill.",
    ]
    counts = {m: 0 for m in FAILURE_MODES}
    for entry in report["failures"]:
        if entry.get("failure_mode") in counts:
            counts[entry["failure_mode"]] += 1
    lines += ["", "| Failure mode | Count |", "|---|---|"]
    lines += [f"| {m}: {FAILURE_MODES[m].split(':')[0]} | {n} |" for m, n in counts.items()]
    for entry in report["failures"]:
        lines += ["", f"## {entry['task']} — `{entry['trial']}`", ""]
        if entry.get("error"):
            lines.append(f"Not attributed: {entry['error']}")
            continue
        version = entry.get("run_skill_version")
        if version and version != report["skill_version"]:
            lines += [f"The run used Skill version `{version}`, not the current one.", ""]
        lines += [f"**{entry['failure_mode']}** — {entry['evidence']}", ""]
        if entry["skill"] == NO_SKILL:
            lines.append("Skill: none covered this.")
        else:
            check = "" if entry["quote_verified"] else " (NOT found verbatim in the Skill)"
            lines += [f"Skill `{entry['skill']}`{check}:", "", _quote_block(entry["skill_quote"])]
        lines.append("")
        if entry["suggestion"] is None:
            lines.append(
                "Suggestion withheld: it named benchmark-specific identifiers "
                "(listed in attribution.json)."
            )
        else:
            lines.append(f"Suggestion: {entry['suggestion']}")
    return "\n".join(lines) + "\n"


def sdk_judge(env: Mapping[str, str], max_budget_usd: float) -> Judge:
    """The judge as one tool-less Claude Agent SDK turn on the `DATA_AGENT_*` model."""
    settings = ModelSettings.from_env(env)

    async def judge(prompt: str) -> JudgeReply:
        stderr: list[str] = []
        options = ClaudeAgentOptions(
            model=settings.model,
            env=settings.sdk_env(),
            system_prompt=JUDGE_SYSTEM,
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
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                final = message
        if final is None:
            raise RuntimeError(f"the judge returned no result: {''.join(stderr[-5:])}")
        cost = float(result_fields(final, settings.model)["cost_usd"])
        return JudgeReply(text="" if final.is_error else final.result or "", cost_usd=cost)

    return judge


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser("attribute", help="draft a failure-attribution report (dev only)")
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tasks-dir", type=Path, help="benchmark tasks, for instruction.md")
    parser.add_argument(
        "--budget", type=float, default=DEFAULT_BUDGET_USD, help="stop calling the judge at USD"
    )
    parser.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    manifest = json.loads(args.manifest.read_text())
    lazy: list[Judge] = []

    async def judge(prompt: str) -> JudgeReply:
        if not lazy:  # built on first use, so a job without failures needs no model settings
            lazy.append(sdk_judge(os.environ, max_budget_usd=args.budget))
        return await lazy[0](prompt)

    try:
        report = asyncio.run(
            attribute(
                args.job_dir,
                manifest,
                args.out,
                judge,
                load_skills(),
                args.tasks_dir,
                args.budget,
                known_secrets(os.environ),
            )
        )
    except (ValueError, ConfigError) as exc:
        print(f"attribute: {exc}", file=sys.stderr)
        return 2
    done = sum(1 for e in report["failures"] if e.get("failure_mode"))
    print(
        f"attribute: {done} of {report['n_failures']} dev failures attributed, "
        f"${report['cost_usd']:.4f} spent -> {args.out}"
    )
    return 0
