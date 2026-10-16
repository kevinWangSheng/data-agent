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


def scan(project: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(SCAN), str(project)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def section(output: str, heading: str) -> str:
    return output.split(heading, 1)[1].split("\n\n", 1)[0]


def test_scan_finds_the_models_wherever_the_project_lives(tmp_path: Path) -> None:
    """Directories the scan skips (target, macros, .claude) only count inside the project."""
    project = tmp_path / ".claude" / "target" / "macros" / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    output = scan(project)
    complete = section(output, "COMPLETE MODELS")
    for model in ("stg_customers", "stg_orders", "customer_orders"):
        assert model in complete, output


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
    output = scan(project)
    stubs = section(output, "STUBS TO REWRITE")
    assert "stg_items" in stubs, output
    assert "stg_customers" not in stubs and "stg_orders" not in stubs, output


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


# Names and values from specific projects the upstream Skills were tuned on (ADR-0005); the rules
# that carried them are now stated generally.
PROJECT_SPECIFIC = (
    "circuits",
    "results.sql",
    "Brunei",
    "RussianFederation",
    "Muenchen",
    "supplier_id",
    "International Business Machines",
    "eng_headcount",
    "'ACTOR'",
    "return_pct",
    "reporting_grain",
    "total_parts",
    "guide per day",
    "extract_hour",
    "multiple-choice",
    "driver",
    "is_won",
    "converted_date",
    "guaranteed fail",
)


@pytest.mark.parametrize("name", [n for n in DBT_SKILLS if n != "duckdb-sql"])
def test_dbt_skills_carry_no_project_specific_names(name: str) -> None:
    for path in (p for p in (SKILLS_DIR / name).iterdir() if p.is_file()):
        text = body(name) if path.name == "SKILL.md" else path.read_text()
        for term in PROJECT_SPECIFIC:
            assert term not in text, (path.name, term)


def test_workflow_never_skips_building_a_model_the_task_asks_for() -> None:
    """The scan only sees models already declared in YAML; a task that asks for a new model must
    still run the build steps."""
    skip_rule = body("dbt-workflow").split("## The 8-Step Workflow", 1)[1].split("\n\n", 2)[1]
    assert "skip Steps 4-7" in skip_rule
    assert "create" in skip_rule


def test_scan_reads_no_sql_outside_the_project(tmp_path: Path) -> None:
    """A model path or a symlinked file leading out of the project is not read."""
    outside = tmp_path / "other" / "models"
    outside.mkdir(parents=True)
    (outside / "secret_model.sql").write_text("select 1 as secret_column\n")
    project = tmp_path / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    (project / "models" / "linked_model.sql").symlink_to(outside / "secret_model.sql")
    (outside / "secret.yml").write_text("models:\n  - name: secret_yml_model\n")
    (project / "models" / "linked.yml").symlink_to(outside / "secret.yml")
    (outside / "secret_macro.sql").write_text("{% macro secret_macro() %}1{% endmacro %}\n")
    (project / "macros").mkdir()
    (project / "macros" / "linked_macro.sql").symlink_to(outside / "secret_macro.sql")
    config = project / "dbt_project.yml"
    config.write_text(config.read_text().replace('["models"]', '["models", "../other/models"]'))
    output = scan(project)
    for name in ("secret_model", "linked_model", "secret_yml_model", "secret_macro"):
        assert name not in output, output
    assert "stg_orders" in section(output, "COMPLETE MODELS"), output


def test_scan_does_not_point_at_snapshots_to_run(tmp_path: Path) -> None:
    """Bash cannot run `dbt snapshot`, so the scan must not suggest it."""
    project = tmp_path / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    (project / "snapshots").mkdir()
    (project / "snapshots" / "orders_snapshot.sql").write_text("select 1\n")
    snapshots = section(scan(project), "SNAPSHOTS DIRECTORY")
    assert "cannot" in snapshots and "dbt snapshot" in snapshots, snapshots


def test_scan_counts_only_sql_under_the_model_paths(tmp_path: Path) -> None:
    """A data test, analysis or snapshot named like a missing model does not make it complete."""
    project = tmp_path / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    schema = project / "models" / "schema.yml"
    schema.write_text(schema.read_text() + "  - name: customer_ltv\n")
    for folder in ("tests", "analyses", "snapshots"):
        (project / folder).mkdir()
        (project / folder / "customer_ltv.sql").write_text("select 1 as customer_id\n")
    output = scan(project)
    assert "customer_ltv.sql" in section(output, "MODELS TO BUILD"), output
    assert "customer_ltv" not in section(output, "COMPLETE MODELS"), output
    assert "SQL FILES WITHOUT YML" not in output, output


@pytest.mark.parametrize(
    "setting",
    [
        'model-paths: ["transform"]',
        "model-paths:\n  - transform\n",
        "model-paths: [transform]",
        'model-paths: [\n  "models",\n  "transform"\n]',
        'source-paths: ["transform"]',
    ],
)
def test_scan_reads_models_from_the_configured_model_paths(tmp_path: Path, setting: str) -> None:
    project = tmp_path / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    (project / "models").rename(project / "transform")
    config = project / "dbt_project.yml"
    config.write_text(config.read_text().replace('model-paths: ["models"]', setting))
    (project / "models").mkdir()  # an unlisted default directory is not scanned
    complete = section(scan(project), "COMPLETE MODELS")
    for model in ("stg_customers", "stg_orders", "customer_orders"):
        assert model in complete, complete


def test_scan_does_not_follow_a_symlink_out_of_the_workspace(tmp_path: Path) -> None:
    """Without a dbt_project.yml at the root, a subdirectory linking outside is not the project."""
    outside = tmp_path / "outside" / "dbt_shop"
    shutil.copytree(DBT_SHOP, outside)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "linked").symlink_to(outside, target_is_directory=True)
    output = scan(workspace)
    assert "no dbt_project.yml found" in output, output
    assert "stg_orders" not in output, output
    shutil.copytree(DBT_SHOP, workspace / "dbt_shop")
    assert "stg_orders" in section(scan(workspace), "COMPLETE MODELS")


def test_scan_strips_quotes_from_yml_names(tmp_path: Path) -> None:
    """`- name: "stg_customers"` names the existing stg_customers.sql, not a missing model."""
    project = tmp_path / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    schema = project / "models" / "schema.yml"
    text = schema.read_text()
    text = text.replace("- name: stg_customers", '- name: "stg_customers"')
    text = text.replace("- name: stg_orders", "- name: 'stg_orders'")
    schema.write_text(text)
    output = scan(project)
    assert "none" in section(output, "MODELS TO BUILD"), output
    complete = section(output, "COMPLETE MODELS")
    assert "stg_customers" in complete and "stg_orders" in complete, output
    assert '"' not in complete and "'" not in complete, output
    assert "SQL FILES WITHOUT YML" not in output, output


def test_scan_ignores_parentheses_in_literals_and_comments(tmp_path: Path) -> None:
    """A complete model with `(` inside a string or comment is not a stub."""
    project = tmp_path / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    (project / "models" / "staging" / "stg_customers.sql").write_text(
        "-- strip the ( from names\n"
        "select replace(customer_name, '(', '') as customer_name, 'it''s (' as note /* ( */\n"
        "from {{ source('raw', 'customers') }}\n"
    )
    output = scan(project)
    assert "stg_customers" in section(output, "COMPLETE MODELS"), output
    assert "stg_customers" not in section(output, "STUBS TO REWRITE"), output


def test_current_date_fix_keeps_a_real_today() -> None:
    """Anchoring `current_date` to the data's last date is conditional, not a blanket rewrite."""
    fix = body("dbt-debugging").split("## 4.", 1)[1].split("## 5.", 1)[0]
    assert "real current date" in fix, fix


def test_scan_shows_the_model_description_not_a_column_one(tmp_path: Path) -> None:
    project = tmp_path / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    (project / "models" / "marts" / "customer_orders.sql").unlink()  # a model to build shows DESC
    schema = project / "models" / "schema.yml"
    schema.write_text(
        schema.read_text().replace(
            "      - name: customer_id\n",
            "      - name: customer_id\n        description: Column text\n",
        )
    )
    output = scan(project)
    assert "One row per customer." in output, output
    assert "Column text" not in output, output


def test_scan_does_not_read_a_project_config_outside_the_workspace(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "dbt_project.yml").write_text(
        (DBT_SHOP / "dbt_project.yml").read_text()
        + "vars:\n  shop:\n    secret_alias: \"{{ ref('secret_model') }}\"\n"
    )
    project = tmp_path / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    (project / "dbt_project.yml").unlink()
    (project / "dbt_project.yml").symlink_to(outside / "dbt_project.yml")
    output = scan(project)
    assert "secret" not in output, output
    assert "no dbt_project.yml found" in output, output
