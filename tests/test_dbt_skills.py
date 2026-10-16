"""The Skills preloaded for dbt runs and the scripts dbt-workflow runs."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from data_agent.agent import DBT_SKILLS, SKILLS_DIR, dbt_scripts, load_skills
from data_agent.run_config import load_config

DBT_SHOP = Path(__file__).parent / "fixtures" / "dbt_shop"
SCAN = SKILLS_DIR / "dbt-workflow" / "scan_project.py"
# Tools and skills the upstream dbt Skills call that data-agent has no counterpart for. The
# verification tools (map_columns, verify_model_values) come with the verifier layer and are
# named only as "if available".
SIGNALPILOT_ONLY = {
    "analyze_project_db",
    "get_knowledge",
    "compare_join_types",
    "dbt_project_map",
    "dbt_project_validate",
    "get_date_boundaries",
    "describe_table",
    "fix_date_spine_hazards",
    "TaskCreate",
}


def test_scan_finds_the_models_wherever_the_project_lives(tmp_path: Path) -> None:
    """Directories the scan skips (target, macros, .claude) only count inside the project."""
    project = tmp_path / ".claude" / "target" / "macros" / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    proc = subprocess.run(
        [sys.executable, str(SCAN), str(project)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    complete = proc.stdout.split("COMPLETE MODELS", 1)[1].split("\n\n", 1)[0]
    for model in ("stg_customers", "stg_orders", "customer_orders"):
        assert model in complete, proc.stdout


def test_scan_counts_select_star_models_as_complete(tmp_path: Path) -> None:
    """A passthrough or filtered `select * from` model is real SQL, not a stub to rewrite."""
    project = tmp_path / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    staging = project / "models" / "staging"
    (staging / "stg_customers.sql").write_text("select * from {{ source('raw', 'customers') }}\n")
    (staging / "stg_orders.sql").write_text(
        "select * from {{ source('raw', 'orders') }} where status <> 'returned'\n"
    )
    (staging / "stg_items.sql").write_text("-- TODO: write this model\n")
    proc = subprocess.run(
        [sys.executable, str(SCAN), str(project)], capture_output=True, text=True, timeout=60
    )
    stubs = proc.stdout.split("STUBS TO REWRITE", 1)[1].split("\n\n", 1)[0]
    assert "stg_items" in stubs, proc.stdout
    assert "stg_customers" not in stubs and "stg_orders" not in stubs, proc.stdout


def test_dbt_skills_load_in_workflow_order() -> None:
    assert DBT_SKILLS[0] == "dbt-workflow"
    for name in DBT_SKILLS:
        assert (SKILLS_DIR / name / "SKILL.md").is_file(), name


def test_loaded_skills_point_at_the_real_scripts() -> None:
    text = load_skills(DBT_SKILLS, SKILLS_DIR)
    assert "${CLAUDE_SKILL_DIR}" not in text
    scripts = dbt_scripts(load_config("full"))
    assert len(scripts) == 2
    for script in scripts:
        assert script.is_file()
        assert f'python3 "{script}"' in text


def body(name: str) -> str:
    """A Skill's text after its modification note, which may name what was removed."""
    return (SKILLS_DIR / name / "SKILL.md").read_text().split("-->", 1)[-1]


@pytest.mark.parametrize("name", [n for n in DBT_SKILLS if n != "duckdb-sql"])
def test_dbt_skills_name_no_signalpilot_only_tools(name: str) -> None:
    words = set(re.findall(r"\w+", body(name)))
    assert not words & SIGNALPILOT_ONLY
    assert "/signalpilot" not in body(name)
    assert "connection_name" not in body(name)


@pytest.mark.parametrize("name", [n for n in DBT_SKILLS if n != "duckdb-sql"])
def test_dbt_skills_say_nothing_about_how_results_are_graded(name: str) -> None:
    """ADR-0005: no benchmark, evaluator or sandbox conventions."""
    text = body(name).lower()
    for term in ("benchmark", "evaluat", "equality test", "tolerance", "sandbox", "result.csv"):
        assert term not in text, (name, term)
