"""LiveSQLBench beyond ticket 127's acceptance tests (ticket 128): the split's allocation, tasks
without gold answers, the evaluation script's own paths (pred.json, no gold answer, a failing
preprocess statement, its own errors), the job config, the adapter's copy of pred.json, and the
server steps of evals/livesqlbench_server.py with stand-ins for the network and Docker.

No database, model, download or server. Fixture data is tests/fixtures/livesqlbench/ (invented
records, ticket 127).
"""

import ast
import hashlib
import json
import random
import subprocess
import sys
import zipfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from harbor.models.task.task import Task
from test_harbor_agent import make_agent

from evals import livesqlbench, livesqlbench_server, livesqlbench_verifier
from evals.__main__ import main as evals_main
from evals.harbor_agent import DBT_ENV, DataAgent
from evals.livesqlbench import ExecutionError, Result

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "livesqlbench"
PUBLIC = FIXTURE / "public.jsonl"
DB_ASSETS = FIXTURE / "db_assets"


def records() -> list[dict[str, Any]]:
    return livesqlbench.read_jsonl(PUBLIC)


def query_records() -> list[dict[str, Any]]:
    return [r for r in records() if r["category"] == "Query"]


PASSWORD = "unitTestWrite" + "Credential0001"


def empty_answers(tmp_path: Path) -> Path:
    path = tmp_path / "answers.jsonl"
    path.write_text("")
    return path


def generate(tmp_path: Path, answers: Path) -> list[Path]:
    return livesqlbench.generate_tasks(
        PUBLIC, answers, DB_ASSETS, tmp_path / "tasks", write_password=PASSWORD
    )


# --- the split ---


def test_equal_databases_get_one_extra_dev_task_each_at_most() -> None:
    """18 databases of 10: 60 dev is 3 or 4 each (the committed split's shape)."""
    sizes = {f"db{d:02d}": 10 for d in range(18)}
    counts = livesqlbench.dev_counts(sizes, 60, random.Random(1))
    assert sum(counts.values()) == 60
    assert Counter(counts.values()) == {3: 12, 4: 6}


def test_the_same_seed_picks_the_same_extra_databases() -> None:
    sizes = {f"db{d:02d}": 10 for d in range(18)}
    first = livesqlbench.dev_counts(sizes, 60, random.Random(7))
    assert first == livesqlbench.dev_counts(sizes, 60, random.Random(7))


@pytest.mark.parametrize("dev_size", [-1, 181])
def test_a_dev_size_outside_the_records_is_refused(dev_size: int) -> None:
    with pytest.raises(ValueError, match="dev_size"):
        livesqlbench.dev_counts({"a": 90, "b": 90}, dev_size, random.Random(0))


def test_a_split_that_cannot_put_every_database_on_both_sides_is_refused() -> None:
    with pytest.raises(ValueError, match="both sides"):
        livesqlbench.dev_counts({"a": 2, "b": 2}, 4, random.Random(0))


def test_the_split_ignores_management_records_and_repeated_ids_are_refused() -> None:
    result = livesqlbench.split(records(), 1, dev_size=1)
    assert sorted(result.dev + result.holdout) == sorted(
        f"lsb-{r['instance_id']}" for r in query_records()
    )
    twice = [*query_records(), query_records()[0]]
    with pytest.raises(ValueError, match="repeat"):
        livesqlbench.split(twice, 1, dev_size=1)


def test_write_split_appends_after_data_eng_bench_and_rewrites_the_same_bytes(
    tmp_path: Path,
) -> None:
    public = tmp_path / "public.jsonl"
    lines = [
        {"instance_id": f"d{d}_{n}", "selected_database": f"d{d}", "category": "Query"}
        for d in range(18)
        for n in range(10)
    ]
    public.write_text("".join(json.dumps(r) + "\n" for r in lines))
    manifest, databases = tmp_path / "manifest.json", tmp_path / "databases.json"
    deb = {"seed": 5, "dev": ["dbt-a", "lsb-stale"], "holdout": ["dbt-b"]}
    manifest.write_text(json.dumps(deb, indent=2) + "\n")
    livesqlbench_server.write_split(public, 3, manifest, databases)
    first = manifest.read_bytes(), databases.read_bytes()
    data = json.loads(first[0])
    assert data["seed"] == 5
    assert data["dev"][:1] == ["dbt-a"] and data["holdout"][:1] == ["dbt-b"]
    assert "lsb-stale" not in data["dev"]
    assert len(data["dev"]) == 61 and len(data["holdout"]) == 121
    assert data["livesqlbench_seed"] == 3
    assert (
        data["livesqlbench_source"]
        == f"birdsql/livesqlbench-base-lite@{livesqlbench.HF_REVISION[:7]}"
    )
    livesqlbench_server.write_split(public, 3, manifest, databases)
    assert (manifest.read_bytes(), databases.read_bytes()) == first


def test_the_split_reads_three_fields_and_prints_counts_only(tmp_path: Path) -> None:
    public = tmp_path / "public.jsonl"
    secret = "QUESTION-TEXT-" + "zz9"
    lines = [
        {
            "instance_id": f"d{d}_{n}",
            "selected_database": f"d{d}",
            "category": "Query",
            "query": secret,
        }
        for d in range(2)
        for n in range(3)
    ]
    public.write_text("".join(json.dumps(r) + "\n" for r in lines))
    assert {k for r in livesqlbench_server.projected(public) for k in r} == {
        "instance_id",
        "selected_database",
        "category",
    }
    result = livesqlbench.split(livesqlbench_server.projected(public), 1, dev_size=2)
    printed = livesqlbench_server.split_counts(result)
    assert printed == ["d0: dev 1, holdout 2", "d1: dev 1, holdout 2", "total: dev 2, holdout 4"]


# --- tasks ---


def test_tasks_without_answers_are_generated_and_marked(tmp_path: Path) -> None:
    tasks = generate(tmp_path, empty_answers(tmp_path))
    assert len(tasks) == len(query_records())
    for task_dir in tasks:
        assert Task(task_dir).config.metadata["gold_answer"] is False
        assert not livesqlbench.has_gold_answer(task_dir)
        payload = json.loads((task_dir / "tests" / "task_payload.json").read_text())
        assert "sol_sql" not in payload or not payload["sol_sql"]


def test_a_task_with_its_answer_is_marked_and_its_payload_holds_it(tmp_path: Path) -> None:
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"instance_id": "tide_q1", "sol_sql": ["SELECT 1"]}) + "\n")
    tasks = {t.name: t for t in generate(tmp_path, answers)}
    assert livesqlbench.has_gold_answer(tasks["lsb-tide_q1"])
    assert not livesqlbench.has_gold_answer(tasks["lsb-orchard_q1"])
    payload = json.loads((tasks["lsb-tide_q1"] / "tests" / "task_payload.json").read_text())
    assert payload["sol_sql"] == ["SELECT 1"]


def test_a_generated_task_uses_local_images_and_the_reader_url(tmp_path: Path) -> None:
    task_dir = {t.name: t for t in generate(tmp_path, empty_answers(tmp_path))}["lsb-tide_q1"]
    config = Task(task_dir).config
    assert config.environment.docker_image == livesqlbench.main_image()
    url = config.environment.env[livesqlbench.DATABASE_URL_ENV]
    assert url.startswith(f"postgresql://{livesqlbench.READER_ROLE}:") and url.endswith(
        "/tide_gauges"
    )
    assert config.verifier.env["PGPASSWORD"] == "${LSB_PG_WRITE_PASSWORD}"
    assert config.verifier.env["PGUSER"] == livesqlbench.WRITER_ROLE
    compose = yaml.safe_load((task_dir / "environment" / "docker-compose.yaml").read_text())
    services = compose["services"]
    assert services["main"]["pull_policy"] == services["postgresql"]["pull_policy"] == "never"
    assert services["postgresql"]["image"] == livesqlbench.db_image("tide_gauges")
    assert "/app/pred.json" in (task_dir / "instruction.md").read_text()


def test_a_generated_task_verifies_in_a_separate_environment_with_the_prediction(
    tmp_path: Path,
) -> None:
    from harbor.models.task.config import VerifierEnvironmentMode
    from harbor.models.task.verifier_mode import (
        resolve_task_verifier_mode,
        resolve_verifier_environment_definition,
    )

    task = Task({t.name: t for t in generate(tmp_path, empty_answers(tmp_path))}["lsb-tide_q1"])
    assert resolve_task_verifier_mode(task.config) == VerifierEnvironmentMode.SEPARATE
    assert task.config.artifacts == ["/app/pred.json"]
    # No [verifier.environment]: a fresh copy of the task's own compose environment and image.
    definition = resolve_verifier_environment_definition(task.config, task.paths)
    assert definition is not None
    assert definition.config.docker_image == livesqlbench.main_image()
    assert (definition.directory / "docker-compose.yaml").is_file()
    # The keys sit in their tables, not as metadata or as verifier variables.
    assert "environment_mode" not in task.config.verifier.env
    assert "artifacts" not in task.config.metadata


def test_generation_refuses_a_task_file_holding_the_write_credential(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    for db in ("orchard_ledger", "tide_gauges"):
        (assets / db).mkdir(parents=True)
        for suffix in livesqlbench.ASSET_SUFFIXES:
            (assets / db / f"{db}{suffix}").write_text(f"notes {PASSWORD}\n")
    with pytest.raises(ValueError, match="write credential"):
        livesqlbench.generate_tasks(
            PUBLIC, empty_answers(tmp_path), assets, tmp_path / "tasks", write_password=PASSWORD
        )


def test_generation_needs_a_write_credential(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        livesqlbench.generate_tasks(
            PUBLIC, empty_answers(tmp_path), DB_ASSETS, tmp_path / "tasks", write_password=""
        )


def test_the_image_tag_follows_the_image_files() -> None:
    version = livesqlbench.image_version()
    assert len(version) == 12
    assert livesqlbench.db_image("x") == f"da-lsb-db-x:{version}"
    assert livesqlbench.main_image() == f"da-lsb-main:{version}"


# --- the evaluation script ---


def refusing(sqls: Sequence[str]) -> Result:
    raise AssertionError(f"nothing should run, got {list(sqls)}")


def diagnostics(out: Path) -> dict[str, Any]:
    return json.loads((out / livesqlbench_verifier.DIAGNOSTICS_FILE).read_text())


def test_without_a_gold_answer_the_score_is_0_and_nothing_runs(tmp_path: Path) -> None:
    payload = query_records()[0] | {"sol_sql": []}
    assert livesqlbench.evaluate(payload, ["SELECT 1"], refusing, tmp_path) == 0
    assert (tmp_path / "reward.txt").read_text().strip() == "0"
    assert diagnostics(tmp_path) | {} == {
        "reward": 0,
        "gold_answer": "missing",
        "outcome": "no_gold_answer",
    }


def test_without_a_prediction_the_score_is_0_and_nothing_runs(tmp_path: Path) -> None:
    payload = query_records()[0] | {"sol_sql": ["SELECT 1"]}
    assert livesqlbench.evaluate(payload, [], refusing, tmp_path) == 0
    assert diagnostics(tmp_path)["outcome"] == "no_prediction"


def test_a_failed_preprocess_statement_is_noted_and_the_evaluation_goes_on(tmp_path: Path) -> None:
    """Upstream `run_preprocessing` ignores the error flags of its statements."""
    seen: list[list[str]] = []

    def execute(sqls: Sequence[str]) -> Result:
        seen.append(list(sqls))
        if sqls == ["CREATE broken"]:
            raise ExecutionError("syntax error at CREATE broken")
        return Result(columns=("a",), rows=((1,),))

    payload = query_records()[0] | {"sol_sql": ["SELECT 1"], "preprocess_sql": ["CREATE broken"]}
    assert livesqlbench.evaluate(payload, ["SELECT 1"], execute, tmp_path) == 1
    found = diagnostics(tmp_path)
    assert found["preprocess_error"] == "ExecutionError" and found["outcome"] == "match"
    # Preprocess, the prediction as given, then both cleaned lists (upstream's order).
    assert seen == [["CREATE broken"], ["SELECT 1"], ["SELECT 1"], ["SELECT 1"]]
    assert "CREATE broken" not in json.dumps(found)


def test_an_error_is_recorded_by_the_drivers_class_only(tmp_path: Path) -> None:
    class SyntaxProblem(Exception):
        pass

    def execute(sqls: Sequence[str]) -> Result:
        try:
            raise SyntaxProblem("LINE 1: SELECT FORM t")
        except SyntaxProblem as exc:
            raise ExecutionError(str(exc)) from exc

    payload = query_records()[0] | {"sol_sql": ["SELECT 1"]}
    assert livesqlbench.evaluate(payload, ["SELECT FORM t"], execute, tmp_path) == 0
    assert diagnostics(tmp_path)["error"] == "SyntaxProblem"
    assert "FORM" not in (tmp_path / livesqlbench_verifier.DIAGNOSTICS_FILE).read_text()


@pytest.mark.parametrize(
    ("text", "state", "sqls"),
    [
        (None, "missing", []),
        ("not json", "unparsable", []),
        ('{"instance_id": "x"}', "unparsable", []),
        ('{"predicted_sql": []}', "unparsable", []),
        ('{"predicted_sql": "SELECT 1"}', "unparsable", []),
        ('{"instance_id": "x", "predicted_sql": ["SELECT 1"]}', "ok", ["SELECT 1"]),
        # Upstream evaluator.py: json.loads(repair_json(pred_text)), json-repair 0.58.5.
        ('{"instance_id": "x", "predicted_sql": ["SELECT 1"', "repaired", ["SELECT 1"]),
        ('```json\n{"predicted_sql": ["SELECT 1",],}\n```', "repaired", ["SELECT 1"]),
        ("[1", "unparsable", []),
        ("", "unparsable", []),
    ],
)
def test_pred_json_is_read_as_upstream_asks_for_it(
    tmp_path: Path, text: str | None, state: str, sqls: list[str]
) -> None:
    path = tmp_path / "pred.json"
    if text is not None:
        path.write_text(text)
    assert livesqlbench_verifier.read_prediction(path) == (state, sqls)


def test_the_script_without_gold_answer_or_pred_json_scores_0_without_a_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = tmp_path / "task_payload.json"
    payload.write_text(json.dumps(query_records()[0] | {"sol_sql": []}))
    out = tmp_path / "verifier"
    assert livesqlbench_verifier.main(payload, tmp_path / "pred.json", out) == 0
    assert (out / "reward.txt").read_text().strip() == "0"
    assert diagnostics(out) == {
        "reward": 0,
        "gold_answer": "missing",
        "outcome": "no_gold_answer",
        "pred_json": "missing",
    }
    assert "no_gold_answer" in capsys.readouterr().out


def test_the_scripts_own_failure_is_no_score_and_names_its_class_only(tmp_path: Path) -> None:
    payload = tmp_path / "task_payload.json"
    payload.write_text(json.dumps({"query": "no database named", "sol_sql": ["SELECT 'x9'"]}))
    out = tmp_path / "verifier"
    assert livesqlbench_verifier.main(payload, tmp_path / "pred.json", out) == 1
    # No reward file: Harbor records RewardFileNotFoundError and the trial has no reward.
    assert not (out / "reward.txt").exists()
    found = diagnostics(out)
    assert (found["outcome"], found["error"], found["reward"]) == (
        "verifier_error",
        "KeyError",
        None,
    )
    assert "x9" not in (out / livesqlbench_verifier.DIAGNOSTICS_FILE).read_text()


def test_a_verifier_database_it_cannot_reach_is_no_score_not_the_agents_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psycopg

    def refused(self: object) -> None:
        raise psycopg.OperationalError("connection to 10.9.9.9 refused for SELECT 'x9'")

    monkeypatch.setattr(livesqlbench_verifier.PostgresExecute, "_connect", refused)
    payload = tmp_path / "task_payload.json"
    payload.write_text(json.dumps(query_records()[0] | {"sol_sql": ["SELECT 'x9'"]}))
    prediction = tmp_path / "pred.json"
    prediction.write_text(json.dumps({"predicted_sql": ["SELECT 1"]}))
    out = tmp_path / "verifier"
    assert livesqlbench_verifier.main(payload, prediction, out) == 1
    assert not (out / "reward.txt").exists()
    found = diagnostics(out)
    assert (found["outcome"], found["error"]) == ("verifier_error", "OperationalError")
    text = (out / livesqlbench_verifier.DIAGNOSTICS_FILE).read_text()
    assert "x9" not in text and "10.9.9.9" not in text


def test_the_script_runs_with_the_standard_library_psycopg_and_json_repair_only() -> None:
    """It runs in a container of the task's `main` image, which has no copy of this repo."""
    tree = ast.parse(livesqlbench.VERIFIER_SCRIPT.read_text())
    imports = {
        alias.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for alias in n.names
    } | {
        n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert imports - set(sys.stdlib_module_names) == {"psycopg", "json_repair"}


def test_the_main_image_pins_the_verifiers_packages_as_uv_lock_does() -> None:
    dockerfile = (livesqlbench.IMAGES_DIR / "Dockerfile.main").read_text()
    lock = (REPO_ROOT / "uv.lock").read_text()
    for package, pin, version in (
        ("psycopg", "psycopg[binary]", "3.3.6"),
        ("json-repair", "json-repair", "0.58.5"),
    ):
        assert f'"{pin}=={version}"' in dockerfile
        assert f'name = "{package}"\nversion = "{version}"' in lock


def test_a_repairable_pred_json_is_scored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Stub:
        def __init__(self, database: str, environ: Any) -> None:
            self.database = database

        def __call__(self, sqls: Sequence[str]) -> Result:
            return Result(columns=("a",), rows=((1,),))

        def close(self) -> None:
            pass

    monkeypatch.setattr(livesqlbench_verifier, "PostgresExecute", Stub)
    payload = tmp_path / "task_payload.json"
    payload.write_text(json.dumps(query_records()[0] | {"sol_sql": ["SELECT 1"]}))
    prediction = tmp_path / "pred.json"
    # A trailing comma and an unclosed list and object: not JSON, but repairable.
    prediction.write_text('{"instance_id": "tide_q1", "predicted_sql": ["SELECT 1",')
    out = tmp_path / "verifier"
    assert livesqlbench_verifier.main(payload, prediction, out) == 0
    assert (out / "reward.txt").read_text().strip() == "1"
    found = diagnostics(out)
    assert (found["outcome"], found["pred_json"]) == ("match", "repaired")


# --- the job config and the adapter ---


def test_the_job_config_runs_livesqlbench_with_no_credential_or_agent_env() -> None:
    path = REPO_ROOT / "evals" / "configs" / "livesqlbench.data-agent.yaml"
    text = path.read_text()
    config = yaml.safe_load(text)
    (agent,) = config["agents"]
    assert agent["import_path"] == "evals.harbor_agent:DataAgent"
    assert agent["kwargs"]["benchmark"] == livesqlbench.BENCHMARK
    assert "env" not in agent and "env" not in config["environment"]
    for needle in (livesqlbench.WRITE_PASSWORD_ENV, "PGPASSWORD", "123123"):
        assert needle not in text
    for flag in ("network", "mcp_servers", "charts"):
        assert flag not in agent and flag not in agent["kwargs"]


def test_a_livesqlbench_trial_keeps_the_agents_pred_json_and_gets_no_dbt_target(
    tmp_path: Path,
) -> None:
    agent = make_agent(DataAgent, tmp_path, benchmark=livesqlbench.BENCHMARK)
    command = agent._run_command("Answer")  # pyright: ignore[reportPrivateUsage]
    logs = agent.environment_logs_dir
    assert f"cp /app/pred.json {logs}/pred.json 2>/dev/null || true" in command
    assert agent._benchmark_env() == {}  # pyright: ignore[reportPrivateUsage]
    dbt = make_agent(DataAgent, tmp_path / "dbt")
    assert dbt._benchmark_env() == DBT_ENV  # pyright: ignore[reportPrivateUsage]
    assert "pred.json" not in dbt._run_command("Fix")  # pyright: ignore[reportPrivateUsage]


# --- the server steps, with stand-ins for the network and Docker ---


def test_fetch_takes_the_pinned_revision_and_keeps_what_is_there(tmp_path: Path) -> None:
    got: list[str] = []

    def get(url: str, path: Path) -> None:
        got.append(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        if url.endswith(livesqlbench.PUBLIC_FILE):
            path.write_text(PUBLIC.read_text())
        else:
            path.write_text("asset")

    data = tmp_path / "data"
    assert livesqlbench_server.fetch(data, get) == 1 + 2 * 3
    assert all(f"/resolve/{livesqlbench.HF_REVISION}/" in url for url in got)
    assert any(url.endswith("/tide_gauges/tide_gauges_kb.jsonl") for url in got)
    assert livesqlbench_server.fetch(data, get) == 0


def test_dump_directories_are_found_by_name_or_template_name(tmp_path: Path) -> None:
    for name in ("postgre_table_dumps/alpha_template", "postgre_table_dumps/beta"):
        (tmp_path / name).mkdir(parents=True)
        (tmp_path / name / "t.sql").write_text("")
    found = livesqlbench_server.dump_dirs(tmp_path, ["alpha", "beta"])
    assert found["alpha"].name == "alpha_template" and found["beta"].name == "beta"
    with pytest.raises(SystemExit, match="missing for \\['gamma'\\]"):
        livesqlbench_server.dump_dirs(tmp_path, ["alpha", "gamma"])


def test_the_table_order_is_found_as_upstream_finds_it(tmp_path: Path) -> None:
    dump = tmp_path / "alpha_template"
    dump.mkdir()
    (dump / "a.sql").write_text("")
    (dump / "b.sql").write_text("")
    assert livesqlbench_server.table_order(dump, "alpha") is None
    (tmp_path / "init-databases.sh").write_text('  ["alpha_template"]="b a"\n')
    assert livesqlbench_server.table_order(dump, "alpha") == "b\na\n"
    (tmp_path / "alpha_table_orders.txt").write_text("a\nb\n")
    assert livesqlbench_server.table_order(dump, "alpha") == "a\nb\n"


def test_table_order_names_resolve_to_the_dump_files_regardless_of_case(tmp_path: Path) -> None:
    # Upstream's cybermarket order file capitalises names whose dump files are lower case; on a
    # case-sensitive file system init-db.sh skipped every table and built an empty database.
    dump = tmp_path / "alpha_template"
    dump.mkdir()
    for name in ("markets", "riskanalysis", "Exchange_Map", "enum_definitions"):
        (dump / f"{name}.sql").write_text("")
    (tmp_path / "alpha_table_orders.txt").write_text("Markets RiskAnalysis\nexchange_map\n")
    expected = "markets\nriskanalysis\nExchange_Map\n"
    assert livesqlbench_server.table_order(dump, "alpha") == expected


@pytest.mark.parametrize("full", ["alpha_full.sql", "alpha_template_full.sql"])
def test_a_dump_with_a_full_dump_file_needs_no_table_order(tmp_path: Path, full: str) -> None:
    # init-db.sh restores `<db>_full.sql` or `<db>_template_full.sql` and reads no order.
    dump = tmp_path / "alpha_template"
    dump.mkdir()
    (dump / full).write_text("")
    (tmp_path / "alpha_table_orders.txt").write_text("Markets Vendors\n")
    assert livesqlbench_server.table_order(dump, "alpha") is None


def test_a_table_order_name_without_a_dump_file_stops(tmp_path: Path) -> None:
    dump = tmp_path / "alpha_template"
    dump.mkdir()
    (dump / "markets.sql").write_text("")
    (tmp_path / "alpha_table_orders.txt").write_text("Markets Vendors\n")
    with pytest.raises(SystemExit, match="no dump file for \\['Vendors'\\]"):
        livesqlbench_server.table_order(dump, "alpha")


def test_a_table_order_name_matching_two_dump_files_stops(tmp_path: Path) -> None:
    dump = tmp_path / "alpha_template"
    dump.mkdir()
    (dump / "markets.sql").write_text("")
    (dump / "Markets.sql").write_text("")
    if len(list(dump.glob("*.sql"))) < 2:
        pytest.skip("case-insensitive file system: two names differing in case are one file")
    (tmp_path / "alpha_table_orders.txt").write_text("markets\n")
    with pytest.raises(SystemExit, match="more than one dump file for \\['markets'\\]"):
        livesqlbench_server.table_order(dump, "alpha")


def test_init_db_fails_on_a_mapped_table_without_a_dump_file() -> None:
    # The DATABASE_MAPPING fallback (no order file beside the dump) no longer skips silently.
    script = (livesqlbench.IMAGES_DIR / "init-db.sh").read_text()
    assert "Mapped dump file not found, skipping" not in script
    assert "ERROR: Mapped dump file not found" in script


class FakeDocker:
    """`subprocess.run` for the server steps: gdown writes a zip of `dumps`; docker builds and
    inspects images by tag, and `cat`s each built image's counts."""

    def __init__(self, dumps: dict[str, str], counts: tuple[int, int] = (1, 2)) -> None:
        self.dumps = dumps
        self.counts = counts
        self.images: set[str] = set()
        self.calls: list[list[str]] = []
        self.orders: dict[str, str] = {}

    def __call__(self, cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        done = subprocess.CompletedProcess(cmd, 0, "", "")
        if "gdown" in cmd:
            with zipfile.ZipFile(cmd[cmd.index("-O") + 1], "w") as z:
                for name, text in self.dumps.items():
                    z.writestr(name, text)
        elif cmd[:3] == ["docker", "image", "inspect"]:
            done.returncode = 0 if cmd[3] in self.images else 1
        elif cmd[:2] == ["docker", "build"]:
            self.images.add(cmd[cmd.index("-t") + 1])
            order = Path(cmd[-1]) / "db_seed" / "table_order.txt"
            if order.is_file():
                self.orders[cmd[cmd.index("-t") + 1]] = order.read_text()
        elif cmd[:2] == ["docker", "run"]:
            done.stdout = f"{self.counts[0]} {self.counts[1]}\n"
        return done


def test_images_build_what_is_missing_with_the_credential_as_a_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(livesqlbench.WRITE_PASSWORD_ENV, PASSWORD)
    monkeypatch.setattr(livesqlbench_server, "EXPECTED_TABLES", 2)
    monkeypatch.setattr(livesqlbench_server, "EXPECTED_COLUMNS", 4)
    docker = FakeDocker({"dumps/alpha_template/t.sql": "", "dumps/beta/t.sql": ""})
    livesqlbench_server.images(docker, tmp_path, ["alpha", "beta"])
    builds = [c for c in docker.calls if c[:2] == ["docker", "build"]]
    assert len(builds) == 3  # two databases and main
    for build in builds[:2]:
        assert (
            build[build.index("--secret") + 1] == "id=lsb_write_password,env=LSB_PG_WRITE_PASSWORD"
        )
    assert not any(PASSWORD in " ".join(c) for c in docker.calls)
    printed = capsys.readouterr().out
    assert PASSWORD not in printed and "2 tables, 4 columns" in printed
    recorded = (tmp_path / "dump" / "archive.sha256").read_text().strip()
    assert recorded == hashlib.sha256((tmp_path / "dump" / "archive").read_bytes()).hexdigest()

    docker.calls.clear()
    livesqlbench_server.images(docker, tmp_path, ["alpha", "beta"])
    assert not any(c[:2] == ["docker", "build"] or "gdown" in c for c in docker.calls)


def test_an_image_is_built_with_the_resolved_table_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(livesqlbench.WRITE_PASSWORD_ENV, PASSWORD)
    monkeypatch.setattr(livesqlbench_server, "EXPECTED_TABLES", 1)
    monkeypatch.setattr(livesqlbench_server, "EXPECTED_COLUMNS", 2)
    docker = FakeDocker(
        {
            "dumps/alpha_template/markets.sql": "",
            "dumps/alpha_template/vendors.sql": "",
            "dumps/alpha_table_orders.txt": "Markets Vendors",
        }
    )
    livesqlbench_server.images(docker, tmp_path, ["alpha"])
    assert docker.orders[livesqlbench.db_image("alpha")] == "markets\nvendors\n"


def test_an_unresolved_table_order_name_builds_no_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(livesqlbench.WRITE_PASSWORD_ENV, PASSWORD)
    docker = FakeDocker(
        {"dumps/alpha_template/markets.sql": "", "dumps/alpha_table_orders.txt": "Markets Buyers"}
    )
    with pytest.raises(SystemExit, match="no dump file for \\['Buyers'\\]"):
        livesqlbench_server.images(docker, tmp_path, ["alpha"])
    assert not any(c[:2] == ["docker", "build"] for c in docker.calls)


def test_a_dump_that_changed_since_its_first_download_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(livesqlbench.WRITE_PASSWORD_ENV, PASSWORD)
    docker = FakeDocker({"alpha/t.sql": ""})
    livesqlbench_server.fetch_dump(tmp_path, docker)
    (tmp_path / "dump" / "archive").write_bytes(b"other bytes")
    with pytest.raises(SystemExit, match="not the recorded"):
        livesqlbench_server.fetch_dump(tmp_path, docker)


def test_counts_unlike_upstreams_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(livesqlbench.WRITE_PASSWORD_ENV, PASSWORD)
    docker = FakeDocker({"alpha/t.sql": ""}, counts=(3, 9))
    with pytest.raises(SystemExit, match="3 tables, 9 columns"):
        livesqlbench_server.images(docker, tmp_path, ["alpha"])


def test_prepare_needs_the_write_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(livesqlbench.WRITE_PASSWORD_ENV, raising=False)
    with pytest.raises(SystemExit, match="LSB_PG_WRITE_PASSWORD is not set"):
        livesqlbench_server.prepare(tmp_path, tmp_path / "answers.jsonl", FakeDocker({}))


def test_check_reports_data_images_and_tasks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    databases = sorted(set(json.loads(livesqlbench.DATABASES.read_text()).values()))
    assert len(databases) == 18
    docker = FakeDocker({})
    assert livesqlbench_server.check(tmp_path, docker) == 4
    printed = capsys.readouterr().out
    assert "FAIL  LiveSQLBench database images: 0 of 18" in printed
    assert "FAIL  LiveSQLBench tasks: 0" in printed

    docker.images |= {livesqlbench.db_image(db) for db in databases} | {livesqlbench.main_image()}
    assert livesqlbench_server.check(tmp_path, docker) == 2
    printed = capsys.readouterr().out
    assert "ok    LiveSQLBench database images: 18 of 18" in printed
    assert "no gold answers" in printed


def test_check_flags_tasks_of_another_image_and_a_main_image_without_the_verifiers_packages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tasks = generate(tmp_path, empty_answers(tmp_path))
    old = tasks[0] / "task.toml"
    old.write_text(old.read_text().replace(livesqlbench.main_image(), "da-lsb-main:000000000000"))
    (tmp_path / "root").mkdir()
    tasks[0].parent.rename(tmp_path / "root" / "tasks")
    docker = FakeDocker({})
    docker.images.add(livesqlbench.main_image())
    livesqlbench_server.check(tmp_path / "root", docker)
    printed = capsys.readouterr().out
    main = livesqlbench.main_image()
    assert f"FAIL  LiveSQLBench tasks naming another image than {main}: 1" in printed
    assert f"ok    LiveSQLBench image {main} imports psycopg and json_repair" in printed
    probe = next(c for c in docker.calls if c[:2] == ["docker", "run"])
    assert probe[-2:] == ["-c", "import psycopg, json_repair"]
    assert probe[probe.index("--network") + 1] == "none"

    def no_packages(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        done = docker(cmd, **kw)
        if cmd[:2] == ["docker", "run"]:
            done.returncode = 1
        return done

    livesqlbench_server.check(tmp_path / "root", no_packages)
    assert f"FAIL  LiveSQLBench image {main} imports psycopg and json_repair" in (
        capsys.readouterr().out
    )


class Column:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeCursor:
    """psycopg 3's view of `SELECT 1; SELECT 2`: the first result set until `nextset()`."""

    def __init__(self, sets: list[tuple[list[str], list[tuple[Any, ...]]] | None]) -> None:
        self.sets = sets
        self.at = 0

    @property
    def description(self) -> list[Column] | None:
        found = self.sets[self.at]
        return None if found is None else [Column(n) for n in found[0]]

    def nextset(self) -> bool | None:
        if self.at + 1 < len(self.sets):
            self.at += 1
            return True
        return None

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        found = self.sets[self.at]
        assert found is not None
        return found[1][:size]


def test_a_multi_statement_entry_is_scored_by_its_last_result() -> None:
    """Upstream's psycopg2 returns the last statement's rows; psycopg 3 starts at the first."""
    cur = FakeCursor([(["a"], [(1,)]), None, (["b"], [(2,), (3,)])])
    assert livesqlbench_verifier.last_result(cur) == Result(columns=("b",), rows=((2,), (3,)))
    assert livesqlbench_verifier.last_result(FakeCursor([None])) == Result(columns=(), rows=())


def test_every_prepare_compares_the_dump_with_its_recorded_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(livesqlbench.WRITE_PASSWORD_ENV, PASSWORD)
    monkeypatch.setattr(livesqlbench_server, "EXPECTED_TABLES", 1)
    monkeypatch.setattr(livesqlbench_server, "EXPECTED_COLUMNS", 2)
    docker = FakeDocker({"alpha/t.sql": ""})
    livesqlbench_server.images(docker, tmp_path, ["alpha"])
    capsys.readouterr()
    livesqlbench_server.images(docker, tmp_path, ["alpha"])  # every image is there now
    assert "matches the recorded one" in capsys.readouterr().out
    (tmp_path / "dump" / "archive").write_bytes(b"changed")
    with pytest.raises(SystemExit, match="not the recorded"):
        livesqlbench_server.images(docker, tmp_path, ["alpha"])


def test_the_credential_is_counted_under_a_run_never_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(livesqlbench.WRITE_PASSWORD_ENV, PASSWORD)
    run = tmp_path / "runs" / "lsb-smoke"
    (run / "jobs" / "t").mkdir(parents=True)
    (run / "jobs" / "t" / "a.txt").write_text(f"x {PASSWORD} y {PASSWORD}")
    (run / "b.json").write_text("{}")
    assert livesqlbench_server.count_credential(run) == 2
    (run / "jobs" / "t" / "a.txt").write_text("clean")
    assert evals_main(["livesqlbench", "leaks", "--dir", str(run)]) == 0
    printed = capsys.readouterr().out
    assert printed.strip() == f"write credential: 0 occurrence(s) under {run}"


class ProbeDocker(FakeDocker):
    """`docker` for `readonly`: the probe passes every check; the inspect shows `env`."""

    def __init__(self, env: str) -> None:
        super().__init__({})
        self.env = env
        self.writer_env: dict[str, str] = {}

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[:4] == ["docker", "run", "-i", "--rm"]:
            self.calls.append(cmd)
            self.writer_env = kwargs.get("env") or {}
            return subprocess.CompletedProcess(cmd, 0, "PASS the write role clones\n", "")
        if cmd[:3] == ["docker", "run", "-i"]:
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "PASS read-only role reads\n", "")
        if cmd[:2] == ["docker", "inspect"]:
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, self.env, "")
        return super().__call__(cmd, **kwargs)


def test_readonly_runs_the_tasks_own_environment_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(livesqlbench.WRITE_PASSWORD_ENV, PASSWORD)
    task = {t.name: t for t in generate(tmp_path, empty_answers(tmp_path))}["lsb-tide_q1"]
    url = Task(task).config.environment.env[livesqlbench.DATABASE_URL_ENV]
    docker = ProbeDocker(json.dumps([f"{livesqlbench.DATABASE_URL_ENV}={url}"]))
    assert livesqlbench_server.readonly(task, docker) == 0
    db_run = next(c for c in docker.calls if c[:3] == ["docker", "run", "-d"])
    assert db_run[db_run.index("--network-alias") + 1] == "postgresql"
    assert db_run[-1] == livesqlbench.db_image("tide_gauges")
    probe = next(c for c in docker.calls if c[:3] == ["docker", "run", "-i"] and "--rm" not in c)
    assert f"{livesqlbench.DATABASE_URL_ENV}={url}" in probe
    assert not any(PASSWORD in " ".join(c) for c in docker.calls)
    assert sum(c[:3] == ["docker", "rm", "-f"] for c in docker.calls) == 2
    assert any(c[:3] == ["docker", "network", "rm"] for c in docker.calls)
    printed = capsys.readouterr().out
    assert "PASS the main container's environment holds no write credential" in printed
    assert "PASS the write role clones" in printed
    writer = next(c for c in docker.calls if c[:4] == ["docker", "run", "-i", "--rm"])
    assert "PGPASSWORD" in writer and docker.writer_env["PGPASSWORD"] == PASSWORD

    leaky = ProbeDocker(json.dumps([f"PGPASSWORD={PASSWORD}"]))
    assert livesqlbench_server.readonly(task, leaky) == 1
    assert PASSWORD not in capsys.readouterr().out


def test_a_macos_archive_mirror_is_not_a_second_dump_directory(tmp_path: Path) -> None:
    for name in ("dumps/alpha_template/t.sql", "__MACOSX/dumps/alpha_template/._t.sql"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    assert livesqlbench_server.dump_dirs(tmp_path, ["alpha"])["alpha"].parent.name == "dumps"
