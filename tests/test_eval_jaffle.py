"""The jaffle-shop case runner's grading (ticket 08): the case file, answer checks, model checks and
the unchanged-data check for the direct-write cases. Grading SQL runs through the read-only
gateway; here a DuckDB one stands in for Postgres."""

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import duckdb
import psycopg
import pytest

from data_agent.events import REDACTED
from data_agent.gateway import DuckDBGateway
from evals import jaffle


def test_case_file_is_complete() -> None:
    cases = jaffle.load_cases(jaffle.EVAL_FILE)
    kinds = [case["kind"] for case in cases["tasks"]]
    assert len(cases["tasks"]) >= 5
    assert {"query", "build"} <= set(kinds)
    negatives = [case for case in cases["tasks"] if case.get("negative")]
    assert 1 <= len(negatives) <= 2
    assert all(case["grade"]["kind"] == "unchanged" for case in negatives)
    assert len(cases["project_commit"]) == 40
    for case in cases["tasks"]:
        assert (jaffle.EVAL_FILE.parent / case["prompt"]).is_file(), case["id"]


def test_load_cases_rejects_unknown_kind(tmp_path: Path) -> None:
    (tmp_path / "p.txt").write_text("q")
    bad = {"project_commit": "0" * 40, "tasks": [{"id": "x", "kind": "nope", "prompt": "p.txt"}]}
    (tmp_path / "eval.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="nope"):
        jaffle.load_cases(tmp_path / "eval.json")


@pytest.mark.parametrize(
    ("stdout", "passed"),
    [
        ("The total is **$1,103.00**.\n\nAssumptions: ...", True),
        ("1103", True),
        ("The total is 1102.5", False),
        ("I could not answer", False),
    ],
)
def test_query_answer_checks(stdout: str, passed: bool) -> None:
    ok, _ = jaffle.grade_answer(stdout, [{"name": "revenue", "value": 1103, "tolerance": 0.01}])
    assert ok is passed


def test_query_answer_uses_default_tolerance() -> None:
    ok, _ = jaffle.grade_answer("about 1.60", [{"name": "avg", "value": 1.6}], default=0.01)
    assert ok
    ok, _ = jaffle.grade_answer("1", [{"name": "avg", "value": 1.6}], default=0.01)
    assert not ok


@pytest.fixture
def gateway(tmp_path: Path) -> DuckDBGateway:
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA s")
    con.execute(
        "CREATE TABLE s.good AS SELECT * FROM (VALUES (1, 'a', 10.0), (2, 'b', 5.5)) "
        "AS t(customer_id, name, value)"
    )
    con.execute("CREATE TABLE s.fanned AS SELECT * FROM s.good UNION ALL SELECT * FROM s.good")
    con.close()
    return DuckDBGateway(db)


EXPECT = {"row_count": 2, "grain": ["customer_id"], "columns": ["customer_id", "value"]}


def test_model_check_passes_a_correct_model(gateway: DuckDBGateway) -> None:
    ok, details = jaffle.grade_model(gateway, "s", "good", EXPECT | {"sums": {"value": 15.5}})
    assert ok, details


@pytest.mark.parametrize(
    ("model", "expect", "reason"),
    [
        ("missing", EXPECT, "does not exist"),
        ("fanned", EXPECT, "row_count"),
        ("fanned", EXPECT | {"row_count": 4}, "grain"),
        ("good", EXPECT | {"columns": ["customer_id", "ltv"]}, "ltv"),
        ("good", EXPECT | {"sums": {"value": 99}}, "value"),
    ],
)
def test_model_check_catches_defects(
    gateway: DuckDBGateway, model: str, expect: dict[str, object], reason: str
) -> None:
    ok, details = jaffle.grade_model(gateway, "s", model, expect)
    assert not ok
    assert reason in json.dumps(details)


def test_unchanged_compares_snapshots(gateway: DuckDBGateway) -> None:
    checks = [{"name": "rows", "sql": "SELECT count(*) FROM {schema}.good"}]
    before = jaffle.snapshot(gateway, "s", checks)
    assert before == {"rows": "2"}
    assert jaffle.grade_unchanged(before, jaffle.snapshot(gateway, "s", checks))[0]
    assert not jaffle.grade_unchanged(before, {"rows": "1"})[0]


def test_blocked_events_are_the_gateway_refusals() -> None:
    events = [
        {"type": "tool_end", "is_error": True, "content": "Query blocked by the read-only gateway"},
        {
            "type": "tool_end",
            "is_error": True,
            "content": [{"type": "text", "text": "Query error"}],
        },
        {"type": "tool_end", "is_error": False, "content": "blocked words in data"},
        {"type": "text", "text": "blocked"},
    ]
    assert jaffle.blocked_events(events) == [events[0]]


def test_profile_reads_credentials_from_the_environment() -> None:
    url = "postgresql://user:s3cret-pw@db.example:6543/shop"
    profile, env = jaffle.dbt_profile(url, "jaffle_x")
    assert "s3cret-pw" not in profile
    assert "db.example" not in profile
    assert "schema: jaffle_x" in profile
    # dbt scrubs only DBT_ENV_SECRET_* values from its logs, which `dbt debug` fills with the host.
    assert env == {
        "DBT_ENV_SECRET_DA_JAFFLE_HOST": "db.example",
        "DA_JAFFLE_PORT": "6543",
        "DA_JAFFLE_USER": "user",
        "DBT_ENV_SECRET_DA_JAFFLE_PASSWORD": "s3cret-pw",
        "DA_JAFFLE_DBNAME": "shop",
    }
    for name in env:
        assert f"env_var('{name}')" in profile


def test_case_env_pins_dbt_to_the_case_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Inherited DBT_* settings must not send setup or the agent to another project or database.
    monkeypatch.chdir(tmp_path)
    base = {
        "PATH": "/bin",
        "DBT_PROJECT_DIR": "/elsewhere",
        "DBT_PROFILES_DIR": "/elsewhere",
        "DBT_PROFILE": "prod",
        "DBT_TARGET": "prod",
    }
    env = jaffle.case_env(base, {"DA_JAFFLE_PORT": "6543"}, Path("out/q1/jaffle_shop"))
    workspace = str(tmp_path / "out" / "q1" / "jaffle_shop")
    assert env == {
        "PATH": "/bin",
        "DA_JAFFLE_PORT": "6543",
        "DBT_PROJECT_DIR": workspace,
        "DBT_PROFILES_DIR": workspace,
        "DBT_PROFILE": "jaffle_shop",
        "DBT_TARGET": "dev",
    }


def test_setup_edit_must_match_once(tmp_path: Path) -> None:
    (tmp_path / "m.sql").write_text("group by a\n")
    jaffle.apply_edits(tmp_path, [{"file": "m.sql", "old": "group by a\n", "new": "group by b\n"}])
    assert (tmp_path / "m.sql").read_text() == "group by b\n"
    with pytest.raises(ValueError, match=r"m\.sql"):
        jaffle.apply_edits(tmp_path, [{"file": "m.sql", "old": "group by a\n", "new": "x"}])


BLOCK = {"type": "tool_end", "is_error": True, "content": "Query blocked by the read-only gateway"}
RESULT = {"type": "result"}


@pytest.mark.parametrize(
    ("unchanged", "exit_code", "events", "passed"),
    [
        (True, 0, [BLOCK, RESULT], True),
        (True, 1, [BLOCK, RESULT], True),
        (True, 1, [RESULT], False),  # never reached the gateway: no evidence that it blocks
        (False, 0, [BLOCK, RESULT], False),
        (True, 2, [BLOCK, RESULT], False),
        (True, 0, [BLOCK], False),  # ended before the result event
    ],
)
def test_negative_case_needs_a_gateway_block(
    unchanged: bool, exit_code: int, events: list[dict[str, Any]], passed: bool
) -> None:
    assert jaffle.negative_passed(unchanged, exit_code, events) is passed


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError('connection to server at "db.example" (10.1.2.3) failed: s3cret-pw'),
        subprocess.TimeoutExpired(["dbt", "run"], 600),
    ],
)
def test_case_failures_are_recorded_without_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    monkeypatch.setenv("DA_TEST_URL", "postgresql://u:s3cret-pw@db.example:6543/shop")
    monkeypatch.setattr(jaffle, "clone_project", lambda *_: tmp_path)

    def fail(*_: object) -> None:
        raise error

    monkeypatch.setattr(jaffle, "run_case", fail)
    args = argparse.Namespace(
        postgres_env="DA_TEST_URL",
        out=tmp_path / "out",
        config="full",
        case=["q2_repeat_customers"],
    )
    assert jaffle.run(args) == 1
    summary = (tmp_path / "out" / "summary.json").read_text()
    result = json.loads((tmp_path / "out" / "q2_repeat_customers" / "result.json").read_text())
    assert result["passed"] is False
    assert result["error"]
    for secret in ("s3cret-pw", "db.example", "10.1.2.3"):
        assert secret not in summary


def test_redact_masks_the_url_password_and_host() -> None:
    url = "postgresql://u:s3cret-pw@db.example:6543/shop"
    text = jaffle.redact(f"dbt debug: host=db.example password=s3cret-pw url={url}", url)
    assert "s3cret-pw" not in text
    assert "db.example" not in text


def test_redact_masks_a_single_label_host() -> None:
    url = "postgresql://u:s3cret-pw@postgres:5432/shop"
    text = jaffle.redact('could not translate host name "postgres" to address', url)
    assert text == f'could not translate host name "{REDACTED}" to address'


def test_rerun_replaces_the_case_directory(tmp_path: Path) -> None:
    # A failed or timed-out case leaves its workspace and events behind (PR #14 review).
    project = tmp_path / "project"
    (project / "models").mkdir(parents=True)
    (project / "models" / "orders.sql").write_text("select 1")
    case_dir = tmp_path / "out" / "q1"
    first = jaffle.prepare_case_dir(project, case_dir)
    (first / "models" / "orders.sql").write_text("edited by the agent")
    (case_dir / "events").mkdir()
    (case_dir / "events" / "old.jsonl").write_text("{}\n")
    second = jaffle.prepare_case_dir(project, case_dir)
    assert second == first
    assert (second / "models" / "orders.sql").read_text() == "select 1"
    assert not (case_dir / "events").exists()


@pytest.mark.integration
def test_reset_drops_only_case_schemas() -> None:
    url = os.environ["DATA_AGENT_TEST_POSTGRES_URL"]
    with psycopg.connect(url, autocommit=True) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS jafflebackup")
        con.execute("CREATE SCHEMA IF NOT EXISTS jaffle_case")
    jaffle.reset_schemas(url)
    with psycopg.connect(url, autocommit=True) as con:
        left = {
            row[0]
            for row in con.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name IN ('jafflebackup', 'jaffle_case')"
            )
        }
        con.execute("DROP SCHEMA IF EXISTS jafflebackup")
    assert left == {"jafflebackup"}
