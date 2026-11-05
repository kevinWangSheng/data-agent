"""The Skills preloaded for dbt runs and the scripts dbt-workflow runs."""

import contextlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from data_agent.agent import (
    DBT_PROMPT,
    DBT_SKILLS,
    MCP_SERVER,
    SKILLS_DIR,
    TASK_FILE_NOTE,
    dbt_scripts,
    load_skills,
)
from data_agent.run_config import load_config
from data_agent.verifiers import AGENTS_DIR, VERIFIERS_PROMPT

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


VALIDATE = SKILLS_DIR / "dbt-workflow" / "validate_project.py"


def test_validate_waits_five_minutes_for_dbt_parse_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full re-parse of a dbt project can take minutes; 60 seconds cut 18 of 91 calls short."""
    spec = importlib.util.spec_from_file_location("validate_project", VALIDATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls: list[dict[str, object]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(module.shutil, "which", lambda _: "/usr/bin/dbt")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.validate(tmp_path)["success"]
    assert calls[0]["timeout"] == 300


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


def test_workflow_names_no_dialect_skill() -> None:
    """A run loads its database's SQL Skill (duckdb-sql, postgres-sql); the workflow names none."""
    text = (SKILLS_DIR / "dbt-workflow" / "SKILL.md").read_text()
    for name in ("duckdb-sql", "postgres-sql"):
        assert name not in text


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


def test_workflow_compares_sums_and_averages_only_up_to_rounding() -> None:
    assert "sums and averages up to rounding" in step_8()
    assert "1e-6" in step_8()
    assert "10%" not in body("dbt-workflow")


def step_8() -> str:
    return " ".join(body("dbt-workflow").split("### Step 8", 1)[1].split("\n---", 1)[0].split())


def test_workflow_passes_the_spec_to_the_verifiers_as_intent_only() -> None:
    """Ticket 32: the verifiers judge against the task text; the spec is the main agent's intent."""
    text = step_8()
    assert "technical_spec.md" in text and "intent" in text and "task text" in text
    assert "so verifiers can reference it" not in text
    assert "Do NOT include column definitions, SQL logic" in text


def test_workflow_says_not_to_switch_vars_between_dbt_calls() -> None:
    """Ticket 53: two trials switched `--vars` back and forth, five extra 60-68 s full re-parses.
    A general dbt rule beside the build commands; task names are checked for every Skill file in
    test_skills_and_verifier_prompts_name_no_task."""
    text = " ".join(
        body("dbt-workflow").split("### Step 7", 1)[1].split("### Step 8", 1)[0].split()
    )
    assert "Do NOT switch `--vars` back and forth between dbt calls" in text
    assert "re-parse the whole project" in text
    assert "--vars" not in step_8()


MANIFEST = Path(__file__).resolve().parents[1] / "evals" / "manifest.json"


def task_names() -> set[str]:
    """Every dev and holdout task name, as written and in the forms a prompt might spell it."""
    manifest = json.loads(MANIFEST.read_text())
    names: set[str] = set()
    for name in [*manifest["dev"], *manifest["holdout"]]:
        for form in (name, name.removeprefix("dbt-")):
            names |= {form, form.replace("-", "_"), form.replace("-", " ")}
    return names


def test_skills_and_verifier_prompts_name_no_task() -> None:
    """ADR-0005: the prompts state general rules, never one task's name."""
    texts = {
        str(path.relative_to(SKILLS_DIR)): path.read_text()
        for name in DBT_SKILLS
        for path in (SKILLS_DIR / name).iterdir()
        if path.is_file()
    }
    texts |= {path.name: path.read_text() for path in AGENTS_DIR.glob("*.md")}
    texts |= {"VERIFIERS_PROMPT": VERIFIERS_PROMPT, "TASK_FILE_NOTE": TASK_FILE_NOTE}
    names = task_names()
    assert "rfm-customer-segmentation" in names and "rfm customer segmentation" in names
    for where, text in texts.items():
        lowered = text.lower()
        for name in names:
            assert name not in lowered, (where, name)


def write_section(number: int) -> str:
    text = body("dbt-write").split(f"\n## {number}. ", 1)[1].split("\n## ", 1)[0]
    return " ".join(text.split())


def test_workflow_checks_values_per_key_against_an_independent_recomputation() -> None:
    """A grand total can match while single keys are wrong, and a check that reuses the model's
    own expression repeats its error; Step 8 runs whether or not the verifiers passed."""
    text = step_8()
    assert "output grain" in text
    assert "never copy the model's expression" in text
    assert "each branch" in text
    assert "same rounding allowance" in text
    assert "key-level check" in text.split("STOP when", 1)[1]


def test_workflow_final_check_covers_types_of_every_required_model() -> None:
    text = step_8()
    assert "column types" in text
    assert "every model the task asks for" in text


def test_write_measures_rows_each_filter_removes_before_adding_it() -> None:
    text = write_section(5)
    assert "without the predicate" in text
    assert "alone and their overlap" in text
    edit_rule = body("dbt-workflow").split("Do NOT rewrite pre-existing SQL", 1)[1]
    assert "predicate by predicate" in edit_rule.split("\n\n", 1)[0]


def test_null_rows_stay_except_under_a_date_bound() -> None:
    text = write_section(5)
    assert "A row with some NULL columns is real data - keep it" in text
    assert "NULL date" in text.split("A row with some NULL columns", 1)[1]


def test_percentage_default_is_a_ratio_and_self_checks_use_it() -> None:
    text = write_section(13)
    assert "output a 0-1 ratio" in text
    assert "output a 0-100 scale" not in text
    assert "same scale" in text


def test_write_keeps_column_precision() -> None:
    text = write_section(1)
    assert "timestamp stays a timestamp" in text


def test_edit_changes_a_predicate_the_task_names_as_wrong() -> None:
    edit_rule = body("dbt-workflow").split("Do NOT rewrite pre-existing SQL", 1)[1]
    edit_rule = edit_rule.split("\n\n", 1)[0]
    assert "removal or correction" in edit_rule
    assert "keep every other predicate" in edit_rule


def big_project(tmp_path: Path) -> Path:
    """dbt_shop plus 800 orphan SQL files: a report far over the summary budget whose five
    Step 1 sections stay small."""
    project = tmp_path / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    for i in range(800):
        (project / "models" / f"orphan_model_{i:04d}.sql").write_text("select 1 as x\n")
    return project


def test_scan_writes_the_full_report_and_prints_a_bounded_summary(tmp_path: Path) -> None:
    project = big_project(tmp_path)
    output = scan(project)
    report_path = project / ".data-agent" / "scan" / "dbt_shop.md"
    assert output.splitlines()[-1] == f"Full report: {report_path}"
    assert len(output) <= 20_000, len(output)
    report = report_path.read_text()
    assert len(report) > 20_000
    assert report.count("orphan_model_") == 800
    assert "… " in output and f" more lines in {report_path}" in output
    # The five Step 1 sections come first and whole, after the title line.
    headings = [line for line in output.splitlines() if line and line[0] != " "]
    assert headings[0].startswith("## dbt Project Scan: dbt_shop")
    assert headings[1].startswith("STUBS TO REWRITE")
    assert headings[2].startswith("MODELS TO BUILD")
    assert headings[3].startswith("DEPENDENCIES")
    assert headings[4].startswith("REQUIRED COLUMNS")
    assert "COMPLETE MODELS" in output
    # Lines the summary dropped are only in the report file.
    assert output.count("orphan_model_") < 800


def test_scan_prints_a_small_project_whole(tmp_path: Path) -> None:
    project = tmp_path / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    output = scan(project)
    report_path = project / ".data-agent" / "scan" / "dbt_shop.md"
    report = report_path.read_text()
    assert output == report.rstrip("\n") + f"\n\nFull report: {report_path}\n"
    assert "more lines in" not in output
    assert [line for line in report.splitlines() if line and line[0] != " "] == [
        "## dbt Project Scan: dbt_shop",
        "Do NOT run `dbt deps` — packages are pre-installed.",
        "MODELS TO BUILD (defined in YML but no SQL file — create EXACT filenames):",
        "STUBS TO REWRITE (SQL file exists but is incomplete):",
        "COMPLETE MODELS (have a non-stub SQL file — call list_tables to confirm which are "
        "materialized):",
        "SIBLING PATTERNS (complete models in same directory — read for column conventions):",
        "REFERENCING MODELS (models that ref() these — read for column conventions):",
        "DEPENDENCIES: (check YML refs and existing SQL for dependency info)",
        "REQUIRED COLUMNS: (read YML files for column specs)",
        "AVAILABLE SOURCES:",
        "DB HINTS: use list_tables and query_database to check lookup joins, staging-vs-raw gaps, "
        "and parent-child driving tables.",
    ]


def test_scan_names_the_step_1_sections_the_summary_had_to_cut(tmp_path: Path) -> None:
    """The five Step 1 sections are meant to come whole; when they alone overflow the summary,
    the summary says which were cut instead of trailing off silently."""
    project = tmp_path / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    models = "".join(f"  - name: planned_model_{i:04d}\n" for i in range(300))
    (project / "models" / "planned.yml").write_text(f"version: 2\nmodels:\n{models}")
    output = scan(project)
    report_path = project / ".data-agent" / "scan" / "dbt_shop.md"
    assert len(output) <= 20_000, len(output)
    assert output.splitlines()[-1] == f"Full report: {report_path}"
    assert report_path.read_text().count("planned_model_") >= 300
    warning = [line for line in output.splitlines() if line.startswith("WARNING: the Step 1")]
    assert len(warning) == 1, output[-2000:]
    assert "MODELS TO BUILD, DEPENDENCIES, REQUIRED COLUMNS" in warning[0]
    assert "STUBS TO REWRITE" not in warning[0]


# Ticket 49: in accept-2, 97 of 180 trials edited `profiles.yml` because the prompt taught
# `--project-dir`/`--profiles-dir` (whose relative paths then resolve from the workspace, not the
# project). The guard allows `cd <dir> && dbt ...` inside the workspace, which needs neither.
def test_dbt_prompt_teaches_cd_into_the_project_not_project_dir_flags() -> None:
    text = DBT_PROMPT.format(
        server=MCP_SERVER,
        database="DuckDB",
        workspace="/w",
        task="t",
        scripts="",
        task_file_note="",
    )
    assert "--project-dir" not in text
    assert "--profiles-dir" not in text
    assert "`cd <project_dir> && dbt ...`" in text
    assert "profiles.yml" in text and "do not edit" in text.lower()


def test_workflow_validate_section_states_the_real_default_timeout() -> None:
    """validate_project.py's default is 300 s; the Skill must not say 60."""
    text = body("dbt-workflow").split("## Validate Project (Standalone)", 1)[1]
    section = text.split("\n## ", 1)[0]
    assert "(default 300)" in section
    assert "default 60" not in section


def test_workflow_forbids_reading_dbt_generated_json() -> None:
    """`target/run_results.json` and `target/manifest.json` are single-line files too long for the
    context (ticket 41 screening: two oversized Read errors). Results come from dbt's own output."""
    rules = body("dbt-workflow").split("\n## Rules", 1)[1]
    assert "target/" in rules
    assert "run_results.json" in rules and "manifest.json" in rules


# Ticket 53: the scan re-read every yml under models/ once per model missing its SQL file (34
# missing models, 31 yml files: 1055 parses, 35.7 of 40 s on the base-image project).
SCAN_MULTI_YML = Path(__file__).parent / "fixtures" / "scan_multi_yml.md"


def multi_yml_project(tmp_path: Path) -> Path:
    """dbt_shop plus three yml files in nested directories, each naming models with no SQL."""
    project = tmp_path / "dbt_shop"
    shutil.copytree(DBT_SHOP, project)
    for directory, names in {
        "finance": ["fct_invoices", "fct_payments", "dim_accounts"],
        "finance/aging": ["aging_buckets", "aging_summary"],
        "marketing": ["dim_campaigns", "fct_clicks"],
    }.items():
        folder = project / "models" / directory
        folder.mkdir(parents=True)
        models = "".join(
            f"  - name: {n}\n    description: The {n} model.\n"
            f"    config:\n      materialized: {'view' if n.startswith('dim') else 'table'}\n"
            f"    columns:\n      - name: {n}_id\n"
            for n in names
        )
        yml = folder / f"_{directory.replace('/', '_')}.yml"
        yml.write_text(f"version: 2\nmodels:\n{models}")
    return project


def test_scan_parses_each_yml_once_and_reports_the_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml

    project = multi_yml_project(tmp_path)
    spec = importlib.util.spec_from_file_location("scan_project", SCAN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parsed: list[str] = []
    real_safe_load = yaml.safe_load

    def counting_safe_load(stream: Any) -> Any:
        parsed.append(str(getattr(stream, "name", "")))
        return real_safe_load(stream)

    monkeypatch.setattr(yaml, "safe_load", counting_safe_load)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        module.print_report(project)
    model_ymls = sorted(str(p) for p in (project / "models").rglob("*.yml"))
    assert sorted(p for p in parsed if p in model_ymls) == model_ymls
    # Pinned from the scan before ticket 53 (pre-commit trims the file's trailing blank line).
    assert out.getvalue().rstrip("\n") == SCAN_MULTI_YML.read_text().rstrip("\n")
