"""`python -m evals attribute`: failure-attribution drafts for dev-set failures (ticket 14).

The model is replaced by a fake judge; the real one runs only by hand (ticket Comments).
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
# `evals/` sits beside the package rather than in it; the CLI runs from the repo root.
sys.path.insert(0, str(REPO_ROOT))

from evals.attribute import JudgeReply, attribute, load_skills  # noqa: E402

MANIFEST: dict[str, Any] = {"seed": 0, "dev": ["dev-task"], "holdout": ["held-task"]}
SKILLS = load_skills()
QUOTE = "Stop exploring after 3-5 tool calls."  # sql-workflow, section 1


def event(seq: int, type: str, **fields: Any) -> dict[str, Any]:
    return {"seq": seq, "type": type, **fields}


def write_trial(job: Path, name: str, task: str, reward: float, sql: str = "SELECT 1") -> Path:
    trial = job / name
    (trial / "agent").mkdir(parents=True)
    result = {
        "task_name": f"org/{task}",
        "trial_name": name,
        "verifier_result": {"rewards": {"reward": reward}},
    }
    (trial / "result.json").write_text(json.dumps(result))
    events = [
        event(0, "version", skill_version="abc123", config="full", model="m"),
        event(1, "tool_start", tool="mcp__data__query_database", input={"sql": sql}),
        event(2, "sql", sql=sql),
        event(3, "tool_end", tool="mcp__data__query_database", is_error=False, content="42"),
        event(4, "text", text="The answer is 42."),
        event(5, "result", subtype="success", num_turns=3, cost_usd=0.01),
    ]
    (trial / "agent" / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return trial


def reply(**overrides: str) -> str:
    fields = {
        "failure_mode": "FM3",
        "evidence": "#1 read the wrong table",
        "skill": "sql-workflow",
        "skill_quote": QUOTE,
        "suggestion": "Before choosing a source table, compare candidate tables' row counts.",
        **overrides,
    }
    return json.dumps(fields)


class FakeJudge:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> JudgeReply:
        self.prompts.append(prompt)
        return JudgeReply(text=self.replies.pop(0), cost_usd=0.02)


def run(job: Path, out: Path, judge: FakeJudge, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(attribute(job, MANIFEST, out, judge, skills=SKILLS, **kwargs))


def test_report_labels_each_dev_failure_and_quotes_the_skill(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "dev-task__a", "dev-task", 0.0, sql="SELECT * FROM raw_orders")
    write_trial(job, "dev-task__b", "dev-task", 1.0)
    judge = FakeJudge(reply())

    report = run(job, tmp_path / "out", judge)

    assert len(judge.prompts) == 1  # the passing trial is not attributed
    assert "SELECT * FROM raw_orders" in judge.prompts[0]
    assert QUOTE in judge.prompts[0]  # the Skill text is in front of the judge
    [entry] = report["failures"]
    assert entry["task"] == "dev-task"
    assert entry["trial"] == "dev-task__a"
    assert entry["failure_mode"] == "FM3"
    assert entry["skill"] == "sql-workflow"
    assert entry["skill_quote"] == QUOTE
    assert entry["quote_verified"] is True
    assert report["cost_usd"] == 0.02
    saved = json.loads((tmp_path / "out" / "attribution.json").read_text())
    assert saved == report
    markdown = (tmp_path / "out" / "report.md").read_text()
    assert "FM3" in markdown
    assert QUOTE in markdown


def test_holdout_trials_are_never_read(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "dev-task__a", "dev-task", 0.0)
    held = write_trial(job, "held-task__a", "held-task", 0.0, sql="SELECT secret_holdout_col")
    (held / "agent" / "events.jsonl").chmod(0)  # reading it would raise
    judge = FakeJudge(reply())

    report = run(job, tmp_path / "out", judge)

    assert [e["task"] for e in report["failures"]] == ["dev-task"]
    assert report["skipped_non_dev_trials"] == 1
    out_text = "".join(p.read_text() for p in (tmp_path / "out").iterdir())
    assert "held-task" not in out_text
    assert "secret_holdout_col" not in "".join(judge.prompts)


def test_benchmark_specific_suggestion_is_retried_then_withheld(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "dev-task__a", "dev-task", 0.0, sql="SELECT amount FROM raw_orders")
    specific = "In dev-task, read raw_orders first."
    judge = FakeJudge(reply(suggestion=specific), reply(suggestion="Mention held-task here."))

    report = run(job, tmp_path / "out", judge)

    assert len(judge.prompts) == 2
    assert "raw_orders" in judge.prompts[1]  # the retry says what to remove
    [entry] = report["failures"]
    assert entry["suggestion"] is None
    assert entry["specific_names"] == ["held-task"]
    assert "held-task" not in (tmp_path / "out" / "report.md").read_text()


def test_a_corrected_retry_is_kept(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "dev-task__a", "dev-task", 0.0, sql="SELECT amount FROM raw_orders")
    judge = FakeJudge(reply(suggestion="Use raw_orders."), reply())

    [entry] = run(job, tmp_path / "out", judge)["failures"]

    assert entry["suggestion"] == json.loads(reply())["suggestion"]
    assert entry["specific_names"] == []
    assert entry["attempts"] == 2


def test_quote_not_in_the_skill_is_flagged(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "dev-task__a", "dev-task", 0.0)
    made_up = "Always trust the first table you find."
    judge = FakeJudge(reply(skill_quote=made_up), reply(skill_quote=made_up))

    [entry] = run(job, tmp_path / "out", judge)["failures"]

    assert "not verbatim" in judge.prompts[1]
    assert entry["quote_verified"] is False


def test_no_rule_covered_it(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "dev-task__a", "dev-task", 0.0)
    judge = FakeJudge(reply(skill="none", skill_quote="", failure_mode="FM1"))

    [entry] = run(job, tmp_path / "out", judge)["failures"]

    assert len(judge.prompts) == 1
    assert entry["skill"] == "none"
    assert entry["quote_verified"] is True


def test_unusable_reply_is_recorded_not_guessed(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "dev-task__a", "dev-task", 0.0)
    judge = FakeJudge("not json", reply(failure_mode="FM9"))

    [entry] = run(job, tmp_path / "out", judge)["failures"]

    assert entry["failure_mode"] is None
    assert "FM9" in entry["error"]


def test_reply_in_a_code_fence_is_parsed(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "dev-task__a", "dev-task", 0.0)
    judge = FakeJudge(f"Here it is:\n```json\n{reply()}\n```")

    [entry] = run(job, tmp_path / "out", judge)["failures"]

    assert entry["failure_mode"] == "FM3"


def test_budget_stops_further_calls(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "dev-task__a", "dev-task", 0.0)
    write_trial(job, "dev-task__b", "dev-task", 0.0)
    judge = FakeJudge(reply(), reply())

    report = run(job, tmp_path / "out", judge, budget_usd=0.02)

    assert len(judge.prompts) == 1
    assert report["failures"][1]["error"] == "not attributed: spend reached the $0.02 budget"
    assert report["cost_usd"] == 0.02


def test_infra_errors_and_missing_events_are_not_attributed(tmp_path: Path) -> None:
    job = tmp_path / "job"
    infra = write_trial(job, "dev-task__a", "dev-task", 0.0)
    result = json.loads((infra / "result.json").read_text())
    result["exception_info"] = {"exception_type": "ConnectionError"}
    (infra / "result.json").write_text(json.dumps(result))
    no_events = write_trial(job, "dev-task__b", "dev-task", 0.0)
    (no_events / "agent" / "events.jsonl").unlink()
    judge = FakeJudge()

    report = run(job, tmp_path / "out", judge)

    assert judge.prompts == []
    assert report["infra_errors"] == 1
    [entry] = report["failures"]
    assert entry["trial"] == "dev-task__b"
    assert entry["error"] == "no event file"


def test_cli_without_dev_failures_needs_no_model(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_trial(job, "dev-task__a", "dev-task", 1.0)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(MANIFEST))
    out = tmp_path / "out"

    command = ["attribute", str(job), "--manifest", str(manifest), "--out", str(out)]
    proc = subprocess.run(
        [sys.executable, "-m", "evals", *command],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin"},  # no DATA_AGENT_API_KEY
    )

    assert proc.returncode == 0, proc.stderr
    assert "0 dev failures" in proc.stdout
    assert json.loads((out / "attribution.json").read_text())["failures"] == []
