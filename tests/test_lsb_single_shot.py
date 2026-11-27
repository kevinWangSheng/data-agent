"""Ticket 138: the single-shot LiveSQLBench baseline (spec D2), no network and no database.

Seams: the prompt (`evals.lsb_baseline.build_prompt` on a database's asset files), the Harbor
agent's `run()` (a stand-in environment backed by a directory and a local stand-in model endpoint,
tests/fake_model.py), and the `lsb-single-shot` command with Harbor replaced at its subprocess
seam."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from harbor.agents.installed.base import ApiRateLimitError
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import TaskConfig, TrialConfig

from evals import ablate, lsb_baseline, lsb_single_shot, lsb_single_shot_agent
from evals.ablate import evals_cli
from evals.livesqlbench import instruction
from evals.livesqlbench_verifier import read_prediction
from evals.lsb_single_shot_agent import SingleShot, question
from evals.spend import file_spend
from tests.fake_model import FakeModel, serve

FIXTURE = Path(__file__).parent / "fixtures" / "livesqlbench"
QUERY = "List every gauge station with its average daily high-water mark for March, highest first."
TIDE = {"instance_id": "tide_q1", "selected_database": "tide_gauges", "query": QUERY}
REPLY = "Average of the daily maxima.\n\n```postgresql\nSELECT station, avg(m) FROM t;\n```"


# --- the prompt (evals.lsb_baseline) ---


def write_assets(directory: Path, db: str, kb: list[dict[str, object]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{db}_schema.txt").write_text("CREATE TABLE readings (level_cm int);\n")
    meanings = {"Shop|Readings|Level_CM": "Water level in cm."}
    (directory / f"{db}_column_meaning_base.json").write_text(json.dumps(meanings))
    (directory / f"{db}_kb.jsonl").write_text("".join(json.dumps(k) + "\n" for k in kb))
    return directory


def test_the_prompt_has_the_schema_column_meanings_and_the_visible_knowledge_fields(
    tmp_path: Path,
):
    kb: list[dict[str, object]] = [
        {
            "id": 0,
            "knowledge": "Daily High-Water Mark",
            "description": "The highest level a station read on one calendar day.",
            "definition": "max(level_cm) per station per day",
            "type": "calculation_knowledge",
            "children_knowledge": [3],
        }
    ]
    assets = write_assets(tmp_path / "shop", "shop", kb)
    prompt = lsb_baseline.build_prompt(assets, "shop", QUERY)

    assert "# Database Schema:\nCREATE TABLE readings (level_cm int);\n" in prompt
    # Upstream lower-cases the column meanings' keys (prompt_generator.py).
    assert '"shop|readings|level_cm": "Water level in cm."' in prompt
    knowledge = prompt.split("# External Knowledge:\n", 1)[1].split("\n\n# User Task:", 1)[0]
    assert json.loads(knowledge) == [
        {
            "id": 0,
            "knowledge": "Daily High-Water Mark",
            "description": "The highest level a station read on one calendar day.",
            "definition": "max(level_cm) per station per day",
        }
    ]
    assert f"# User Task:\n{QUERY}\n" in prompt
    assert prompt.endswith("# Your Generated SQL: \n```postgresql")


def test_the_prompt_of_a_fixture_database_holds_no_answer_or_task_file():
    prompt = lsb_baseline.build_prompt(FIXTURE / "db_assets" / "tide_gauges", "tide_gauges", QUERY)
    for hidden in ("external_knowledge", "sol_sql", "test_cases", "task_payload", "/tests/"):
        assert hidden not in prompt
    assert "Daily High-Water Mark" in prompt


# --- the Harbor agent (evals.lsb_single_shot_agent) ---


class DirEnvironment:
    """The two environment calls the agent makes, on a directory standing in for the container's
    file system."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def at(self, path: str) -> Path:
        return self.root / path.lstrip("/")

    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        Path(target_path).write_bytes(self.at(source_path).read_bytes())

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        self.at(target_path).parent.mkdir(parents=True, exist_ok=True)
        self.at(target_path).write_bytes(Path(source_path).read_bytes())


def trial(
    tmp_path: Path, record: dict[str, str] = TIDE, dev: bool = True
) -> tuple[Path, DirEnvironment, Path]:
    """A trial directory of the task, the container's files (the database's public assets at
    /app/db_assets/) and a manifest whose dev set holds the task or not."""
    task = f"lsb-{record['instance_id']}"
    trial_dir = tmp_path / "job" / f"{task}__abc"
    trial_dir.mkdir(parents=True)
    config = TrialConfig(task=TaskConfig(path=Path("tasks") / task), trial_name=trial_dir.name)
    (trial_dir / "config.json").write_text(config.model_dump_json())
    env = DirEnvironment(tmp_path / "container")
    assets = env.at("/app/db_assets")
    assets.mkdir(parents=True)
    for path in (FIXTURE / "db_assets" / record["selected_database"]).iterdir():
        (assets / path.name).write_bytes(path.read_bytes())
    manifest = tmp_path / "manifest.json"
    split = {"dev": [task], "holdout": []} if dev else {"dev": [], "holdout": [task]}
    manifest.write_text(json.dumps(split))
    return trial_dir, env, manifest


def make_agent(trial_dir: Path, manifest: Path, url: str) -> SingleShot:
    return SingleShot(
        logs_dir=trial_dir / "agent",
        model_name="deepseek/deepseek-flash",
        extra_env={"DATA_AGENT_API_KEY": "sk-fake", "DATA_AGENT_BASE_URL": url},
        manifest=str(manifest),
    )


def run_agent(agent: SingleShot, env: DirEnvironment, record: dict[str, str] = TIDE):
    context = AgentContext()
    asyncio.run(agent.run(instruction(record), cast(BaseEnvironment, env), context))
    agent.populate_context_post_run(context)
    return context


def reply_with(text: str, **kwargs: object) -> FakeModel:
    return FakeModel(label=lambda _t: None, rewrite=lambda _j: text, **kwargs)  # pyright: ignore[reportArgumentType]


def test_one_call_with_the_agents_reasoning_settings_writes_the_official_prediction(
    tmp_path: Path,
):
    trial_dir, env, manifest = trial(tmp_path)
    with serve(reply_with(REPLY, thinking="hmm")) as fake:
        context = run_agent(make_agent(trial_dir, manifest, fake.url), env)

    (request,) = fake.requests
    body = request["body"]
    assert body["model"] == "deepseek-flash"
    assert body["temperature"] == 0
    # What the agent's CLI sends when no effort is set (ticket 138 Comments: a request captured
    # from `data-agent run`): adaptive thinking, effort high, 32000 output tokens.
    assert body["thinking"] == {"type": "adaptive", "display": "updates"}
    assert body["output_config"] == {"effort": "high"}
    assert body["max_tokens"] == 32000
    prompt = lsb_baseline.build_prompt(FIXTURE / "db_assets" / "tide_gauges", "tide_gauges", QUERY)
    assert body["messages"] == [{"role": "user", "content": prompt}]
    assert "system" not in body  # upstream sends the prompt alone

    expected = {"instance_id": "tide_q1", "predicted_sql": ["SELECT station, avg(m) FROM t;"]}
    assert json.loads(env.at("/app/pred.json").read_text()) == expected
    # Kept in the trial as the agent adapter keeps the agent's answer (evals.harbor_agent).
    assert json.loads((trial_dir / "agent" / "pred.json").read_text()) == expected
    assert (trial_dir / "agent" / "response.txt").read_text() == REPLY

    # The fake's usage: 1000 uncached, 200 cached, 50 output tokens, at deepseek-flash's prices
    # ($0.30, $0.006, $1.2 per million): 0.0003 + 0.0000012 + 0.00006.
    assert context.n_input_tokens == 1200
    assert context.n_cache_tokens == 200
    assert context.n_output_tokens == 50
    assert context.cost_usd is not None and abs(context.cost_usd - 0.0003612) < 1e-12


def test_the_trial_events_are_ones_the_spend_guard_counts(tmp_path: Path):
    trial_dir, env, manifest = trial(tmp_path)
    with serve(reply_with(REPLY)) as fake:
        run_agent(make_agent(trial_dir, manifest, fake.url), env)

    (events,) = (trial_dir / "agent" / "events").glob("run-*.jsonl")
    lines = [json.loads(line) for line in events.read_text().splitlines()]
    assert [e["type"] for e in lines] == ["version", "result"]
    version, result = lines
    assert version["model"] == "deepseek-flash"
    assert version["config"] == "single-shot"
    assert result["cost_basis"] == "price_table"
    assert (result["uncached_input_tokens"], result["cached_input_tokens"]) == (1000, 200)
    assert result["output_tokens"] == 50
    spent = file_spend(events)
    assert float(spent.listed) == result["cost_usd"]
    assert spent.period is not None  # a zoned start time: peak or off-peak is known


def test_a_reply_without_sql_still_writes_a_prediction_the_verifier_scores_0(tmp_path: Path):
    """A reply cut off while thinking has no text: upstream's extraction gives [""]; the agent
    writes an empty list instead, which the verifier scores 0 as `no_prediction` (no database
    call), never an infrastructure error."""
    trial_dir, env, manifest = trial(tmp_path)
    with serve(reply_with("", thinking="long", stop_reason="max_tokens")) as fake:
        context = run_agent(make_agent(trial_dir, manifest, fake.url), env)

    prediction = json.loads(env.at("/app/pred.json").read_text())
    assert prediction == {"instance_id": "tide_q1", "predicted_sql": []}
    assert read_prediction(env.at("/app/pred.json")) == ("unparsable", [])
    assert context.cost_usd is not None and context.cost_usd > 0


def test_a_held_out_task_is_refused_before_any_model_call(tmp_path: Path):
    trial_dir, env, manifest = trial(tmp_path, dev=False)
    with serve(reply_with(REPLY)) as fake, pytest.raises(ValueError, match="dev"):
        run_agent(make_agent(trial_dir, manifest, fake.url), env)
    assert fake.requests == []
    assert not env.at("/app/pred.json").exists()


def test_a_task_the_repo_holds_out_is_refused_whatever_the_manifest_says(tmp_path: Path):
    held_out = repo_holdout_task()
    record = {
        "instance_id": held_out.removeprefix("lsb-"),
        **{k: TIDE[k] for k in TIDE if k != "instance_id"},
    }
    trial_dir, env, manifest = trial(tmp_path, record)
    with serve(reply_with(REPLY)) as fake, pytest.raises(ValueError, match="holds out"):
        run_agent(make_agent(trial_dir, manifest, fake.url), env, record)
    assert fake.requests == []


def test_a_refused_call_is_a_config_error_without_the_key_or_the_response(tmp_path: Path):
    """The fake's failure message quotes the Authorization header, as a careless endpoint might;
    Harbor writes the exception and its traceback into the trial."""
    trial_dir, env, manifest = trial(tmp_path)
    fake = FakeModel(label=lambda _t: None, fail=lambda _t: True)  # HTTP 400
    with (
        serve(fake),
        pytest.raises(lsb_single_shot_agent.UnsupportedModelParametersError) as caught,
    ):
        run_agent(make_agent(trial_dir, manifest, fake.url), env)
    assert "sk-fake" not in str(caught.value) and "HTTP 400" in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert type(caught.value).__name__ in ablate.CONFIG_ERRORS


def test_a_rate_limited_call_raises_harbors_retried_error_and_writes_no_prediction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(lsb_single_shot_agent, "MODEL_RETRIES", 0)
    trial_dir, env, manifest = trial(tmp_path)
    fake = FakeModel(label=lambda _t: None, fail=lambda _t: True, fail_status=429)
    with serve(fake), pytest.raises(ApiRateLimitError):
        run_agent(make_agent(trial_dir, manifest, fake.url), env)
    assert not env.at("/app/pred.json").exists()


def test_the_question_is_read_back_from_the_generated_instruction():
    """The agent gets the instruction text only; its question is evals.livesqlbench's (ticket
    139 changes that function, so this pins the round trip)."""
    record = {
        "instance_id": "x_1",
        "selected_database": "orchard_ledger",
        "query": "Two lines:\n\nwith `ticks` and\nFiles describing the database: inside.",
    }
    assert question(instruction(record)) == ("orchard_ledger", record["query"])


# --- the command (`python -m evals lsb-single-shot`) ---

ORCHARD_QUERY = "Which orchard block had the heaviest total apple harvest last season?"
RECORDS = {
    "lsb-tide_q1": TIDE,
    "lsb-orchard_q1": {
        "instance_id": "orchard_q1",
        "selected_database": "orchard_ledger",
        "query": ORCHARD_QUERY,
    },
}
# Not in the repo manifest's holdout (evals.report.check_dev_list reads it too).
SPLIT = {"seed": 1, "dev": ["dbt-alpha", *RECORDS], "holdout": ["lsb-held_9", "dbt-delta"]}
SQL_REPLY = "```postgresql\nSELECT 1;\n```"

type StopWhen = Callable[[], str | None]


class HarborRunningTheAgent:
    """`harbor run` at the subprocess seam (`lsb_single_shot.run_harbor`): for each `-i` task and
    attempt, a trial directory as Harbor writes it, the real agent run on a directory standing in
    for its container (against `model`, a local stand-in endpoint), and a reward from a stand-in
    verifier (1 for upstream's `SELECT 1;`, 0 otherwise). A `job resume` fills what is missing."""

    def __init__(self, model: FakeModel) -> None:
        self.model = model
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], log: Path, stop_when: StopWhen) -> tuple[int, str | None]:
        self.calls.append(args)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("harbor output\n")
        if args[:2] == ["job", "resume"]:
            job = Path(args[args.index("-p") + 1])
            config = json.loads((job / "config.json").read_text())
        else:
            job = Path(args[args.index("-o") + 1]) / args[args.index("--job-name") + 1]
            job.mkdir(parents=True)
            kwargs = dict(a.split("=", 1) for a in args[1:] if "=" in a)
            config = {
                "tasks": [args[i + 1] for i, a in enumerate(args) if a == "-i"],
                "n_attempts": int(args[args.index("-k") + 1]),
                "manifest": kwargs["manifest"],
            }
            (job / "config.json").write_text(json.dumps(config))
        for task in config["tasks"]:
            done = len(list(job.glob(f"{task}__*/result.json")))
            for attempt in range(done, config["n_attempts"]):
                self.trial(job, task, attempt, config["manifest"])
                if reason := stop_when():
                    return -15, reason
        return 0, None

    def trial(self, job: Path, task: str, attempt: int, manifest: str) -> None:
        trial_dir = job / f"{task}__{attempt}"
        trial_dir.mkdir()
        config = TrialConfig(task=TaskConfig(path=Path("tasks") / task), trial_name=trial_dir.name)
        (trial_dir / "config.json").write_text(config.model_dump_json())
        record = RECORDS[task]
        env = DirEnvironment(trial_dir / "container")
        env.at("/app/db_assets").mkdir(parents=True)
        for path in (FIXTURE / "db_assets" / record["selected_database"]).iterdir():
            (env.at("/app/db_assets") / path.name).write_bytes(path.read_bytes())
        agent = SingleShot(
            logs_dir=trial_dir / "agent",
            model_name="deepseek/deepseek-flash",
            extra_env={"DATA_AGENT_API_KEY": "sk-fake", "DATA_AGENT_BASE_URL": self.model.url},
            manifest=manifest,
        )
        context = run_agent(agent, env, record)
        sqls = json.loads(env.at("/app/pred.json").read_text())["predicted_sql"]
        result = {
            "task_name": task,
            "trial_name": trial_dir.name,
            "verifier_result": {"rewards": {"reward": float(sqls == ["SELECT 1;"])}},
            "exception_info": None,
            "agent_result": {
                "n_input_tokens": context.n_input_tokens,
                "n_cache_tokens": context.n_cache_tokens,
                "n_output_tokens": context.n_output_tokens,
                "cost_usd": context.cost_usd,
            },
        }
        (trial_dir / "result.json").write_text(json.dumps(result))


def baseline_job(job: Path, rewards: dict[str, list[float]]) -> Path:
    """A finished agent job (D1's shape, `runs/lsb-dev-3/jobs/no-verifiers`)."""
    for task, task_rewards in rewards.items():
        for n, reward in enumerate(task_rewards):
            trial_dir = job / f"{task}__d{n}"
            trial_dir.mkdir(parents=True)
            result = {
                "task_name": task,
                "trial_name": trial_dir.name,
                "verifier_result": {"rewards": {"reward": reward}},
            }
            (trial_dir / "result.json").write_text(json.dumps(result))
    return job


@pytest.fixture
def command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The command's arguments for a dev run in tmp_path, with Harbor and the balance check
    replaced (no network), and a clean checkout at commit c0ffee."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(SPLIT))
    monkeypatch.setattr(lsb_single_shot, "check_balance", lambda out, budget: None)
    monkeypatch.setattr(lsb_single_shot_agent, "git_commit", lambda: "c0ffee")
    monkeypatch.setattr(lsb_single_shot, "agent_commit", lambda: "c0ffee")
    out = tmp_path / "out"
    args = [
        "lsb-single-shot",
        *("--tasks-dir", str(tmp_path / "tasks"), "--manifest", str(manifest)),
        *("--split", "dev", "--out", str(out), "--budget", "3"),
    ]
    return args, out, manifest


def test_a_dev_run_gives_a_job_that_evals_compare_pairs_with_the_agents(
    command: tuple[list[str], Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    args, out, manifest = command
    with serve(reply_with(SQL_REPLY)) as model:
        harbor = HarborRunningTheAgent(model)
        monkeypatch.setattr(lsb_single_shot, "run_harbor", harbor)
        code, printed, err = evals_cli(args)
    assert code == 0, err

    (call,) = harbor.calls
    assert call[:3] == ["run", "--config", str(lsb_single_shot.JOB_CONFIG)]
    assert sorted(call[i + 1] for i, a in enumerate(call) if a == "-i") == sorted(RECORDS)
    assert call[call.index("-k") + 1] == "3"
    assert "-y" in call and "--network" not in call
    assert len(model.requests) == 6

    job = out / "jobs" / "single-shot"
    baseline = baseline_job(
        tmp_path / "d1" / "jobs" / "no-verifiers",
        {"lsb-tide_q1": [0.0, 0.0, 0.0], "lsb-orchard_q1": [1.0, 0.0, 0.0]},
    )
    pair = ["--baseline", str(baseline), "--candidate", str(job), "--manifest", str(manifest)]
    code, compared, err = evals_cli(
        [
            *("compare", *pair, "--split", "dev", "--benchmark", "livesqlbench"),
            *("--trials", "3", "--json"),
        ]
    )
    assert code == 0, err
    # The baseline passes 1 of 6 trials, the single call all 6: +83.3 points.
    assert json.loads(compared)["diff"] == pytest.approx(100 - 100 / 6)

    (report_path,) = (out / "reports").glob("*/report.json")
    report = json.loads(report_path.read_text())
    assert report["accuracy"] == 100.0
    assert report["n_trials"] == 6
    # The fake's 1000 uncached, 200 cached and 50 output tokens per call, priced and billed.
    assert report["output_tokens"] == 300
    assert abs(report["cost_usd"] - 6 * 0.0003612) < 1e-9
    assert report["billed_cost_usd"] in (
        pytest.approx(6 * 0.0003612),
        pytest.approx(3 * 0.0003612),
    )
    assert "accuracy 100.0%" in printed


def test_only_dev_tasks_run(command: tuple[list[str], Path, Path], monkeypatch: pytest.MonkeyPatch):
    args, _, _ = command
    calls: list[list[str]] = []
    monkeypatch.setattr(lsb_single_shot, "run_harbor", lambda a, *_: calls.append(a) or (0, None))

    code, _, err = evals_cli([*args, "--task", "lsb-held_9"])
    assert code == 2 and "not LiveSQLBench dev tasks" in err
    assert "lsb-held_9" not in err  # counts only (ADR-0004)
    code, _, err = evals_cli([*args, "--task", "dbt-alpha"])  # dev, but not LiveSQLBench's
    assert code == 2
    assert calls == []

    holdout = [a if a != "dev" else "holdout" for a in args]
    code, _, _ = evals_cli(holdout)
    assert code == 2  # argparse: dev is the only choice
    assert calls == []


def test_the_run_stops_at_the_spend_cap(
    command: tuple[list[str], Path, Path], monkeypatch: pytest.MonkeyPatch
):
    """$10 a call at peak prices, billed at least $5: over 80% of the $3 budget after one."""
    args, _, _ = command
    with serve(reply_with(SQL_REPLY, usage=(10_000_000, 0, 5_833_334))) as model:
        harbor = HarborRunningTheAgent(model)
        monkeypatch.setattr(lsb_single_shot, "run_harbor", harbor)
        code, _, err = evals_cli(args)
        assert code == 1, err
        assert "budget" in err
        assert len(model.requests) == 1
        # Rerun with the same --out: the spend guard stops it before Harbor starts.
        code, _, err = evals_cli(args)
        assert code == 1
        assert len(harbor.calls) == 1


def repo_holdout_task() -> str:
    """A LiveSQLBench task the repo's manifest holds out (never printed)."""
    manifest = json.loads((Path(__file__).parents[1] / "evals" / "manifest.json").read_text())
    return next(t for t in manifest["holdout"] if t.startswith("lsb-"))


def test_another_manifest_cannot_move_a_held_out_task_into_the_dev_set(
    command: tuple[list[str], Path, Path], monkeypatch: pytest.MonkeyPatch
):
    args, _, manifest = command
    held_out = repo_holdout_task()
    manifest.write_text(json.dumps({"dev": [held_out], "holdout": []}))
    calls: list[list[str]] = []
    monkeypatch.setattr(lsb_single_shot, "run_harbor", lambda a, *_: calls.append(a) or (0, None))
    code, _, err = evals_cli(args)
    assert code == 2 and "holds out" in err
    assert held_out not in err
    assert calls == []


def test_a_rerun_under_other_code_does_not_reuse_the_job(
    command: tuple[list[str], Path, Path], monkeypatch: pytest.MonkeyPatch
):
    args, out, _ = command
    with serve(reply_with(SQL_REPLY)) as model:
        harbor = HarborRunningTheAgent(model)
        monkeypatch.setattr(lsb_single_shot, "run_harbor", harbor)
        assert evals_cli(args)[0] == 0
        monkeypatch.setattr(lsb_single_shot, "agent_commit", lambda: "beef00")
        code, _, err = evals_cli(args)
    assert code == 2 and "c0ffee" in err and "another --out" in err
    assert len(harbor.calls) == 1
    assert len(list((out / "reports").glob("*/report.json"))) == 1
