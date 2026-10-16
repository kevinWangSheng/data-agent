"""The jaffle-shop case runner's grading (ticket 08): the case file, answer checks, model checks and
the unchanged-data check for the direct-write cases. Grading SQL runs through the read-only
gateway; here a DuckDB one stands in for Postgres."""

import json
from pathlib import Path

import duckdb
import pytest

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
    assert env == {
        "DA_JAFFLE_HOST": "db.example",
        "DA_JAFFLE_PORT": "6543",
        "DA_JAFFLE_USER": "user",
        "DA_JAFFLE_PASSWORD": "s3cret-pw",
        "DA_JAFFLE_DBNAME": "shop",
    }


def test_setup_edit_must_match_once(tmp_path: Path) -> None:
    (tmp_path / "m.sql").write_text("group by a\n")
    jaffle.apply_edits(tmp_path, [{"file": "m.sql", "old": "group by a\n", "new": "group by b\n"}])
    assert (tmp_path / "m.sql").read_text() == "group by b\n"
    with pytest.raises(ValueError, match=r"m\.sql"):
        jaffle.apply_edits(tmp_path, [{"file": "m.sql", "old": "group by a\n", "new": "x"}])
