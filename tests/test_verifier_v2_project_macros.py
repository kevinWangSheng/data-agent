"""Ticket 60: a macro in the project's own `macros/` that cannot reach a model (no `ref`,
`adapter`, `dispatch`, `run_query` or `graph` in its body, and calling only macros like it) does
not make the models calling it "refs unknown", so a redispatch lists only the models that need
verifying again."""

from pathlib import Path

import pytest

from data_agent.verifier_v2.project import project_config, project_macros, referenced_models
from data_agent.verifier_v2.stop import model_hashes, redispatch_models

PURE = """\
{# helpers; a comment naming ref('x') or run_query does not count #}
{% macro parse_amount(column) %}
    {%- if target.type == 'snowflake' -%}
    try_to_decimal({{ column }}, 18, 2)
    {%- else -%}
    try_cast({{ column }} as decimal(18,2))
    {%- endif -%}
{% endmacro %}

{%- macro clean_money(column, scale=2) -%}
    round({{ parse_amount(column) }}, {{ scale }})
{%- endmacro %}
"""

IMPURE = """\
{% macro uses_ref() %}select * from {{ ref('a') }}{% endmacro %}
{% macro uses_dispatch(x) %}{{ return(adapter.dispatch('m')(x)) }}{% endmacro %}
{% macro uses_run_query() %}{% set r = run_query('select 1') %}{{ r }}{% endmacro %}
{% macro uses_graph() %}{{ graph.nodes | length }}{% endmacro %}
{% macro uses_package(x) %}{{ dbt_utils.safe_cast(x, 'int') }}{% endmacro %}
{% macro uses_unknown(x) %}{{ not_defined_anywhere(x) }}{% endmacro %}
{% macro indirect(x) %}{{ parse_amount(x) }} + {{ uses_ref() }}{% endmacro %}
"""


def project(root: Path, models: dict[str, str], macros: dict[str, str] | None = None) -> Path:
    (root / "models").mkdir(parents=True)
    (root / "dbt_project.yml").write_text(
        'name: tiny\nversion: "1.0.0"\nconfig-version: 2\nprofile: tiny\n'
    )
    for name, sql in models.items():
        (root / "models" / f"{name}.sql").write_text(sql)
    for name, text in (macros or {}).items():
        path = root / "macros" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def macros_of(root: Path) -> frozenset[str]:
    return project_macros(root, project_config(root)).pure()


def test_pure_macros_are_those_that_cannot_reach_a_model(tmp_path: Path) -> None:
    root = project(tmp_path, {}, {"cleaning/pure.sql": PURE, "impure.sql": IMPURE})
    assert macros_of(root) == {"parse_amount", "clean_money"}


def test_a_model_calling_a_pure_project_macro_keeps_its_refs(tmp_path: Path) -> None:
    root = project(tmp_path, {}, {"pure.sql": PURE})
    sql = (
        "{{ config(materialized='table') }}\n"
        "SELECT {{ clean_money('o.amount') }}, {{ parse_amount(\"tax\") }}\n"
        "FROM {{ ref('stg_orders') }} o JOIN {{ ref('stg_tax') }} USING (id)\n"
    )
    assert referenced_models(sql, macros_of(root)) == {"stg_orders", "stg_tax"}


def test_macro_paths_from_dbt_project_yml(tmp_path: Path) -> None:
    root = project(tmp_path, {})
    (root / "dbt_project.yml").write_text('name: tiny\nmacro-paths: ["helpers"]\n')
    (root / "helpers").mkdir()
    (root / "helpers" / "h.sql").write_text(PURE)
    (root / "macros").mkdir()
    (root / "macros" / "ignored.sql").write_text("{% macro elsewhere() %}1{% endmacro %}")
    assert macros_of(root) == {"parse_amount", "clean_money"}


def test_package_macros_are_not_project_macros(tmp_path: Path) -> None:
    root = project(tmp_path, {}, {"pure.sql": PURE})
    package = root / "dbt_packages" / "util" / "macros"
    package.mkdir(parents=True)
    (package / "p.sql").write_text("{% macro pkg_pure(x) %}{{ x }}{% endmacro %}")
    # Also under macros/ itself: dbt_packages is never scanned.
    nested = root / "macros" / "dbt_packages"
    nested.mkdir()
    (nested / "n.sql").write_text("{% macro nested_pure(x) %}{{ x }}{% endmacro %}")
    assert macros_of(root) == {"parse_amount", "clean_money"}
    assert referenced_models("{{ pkg_pure('a') }} {{ ref('m') }}", macros_of(root)) is None


@pytest.mark.parametrize(
    "call",
    [
        "uses_ref()",
        "uses_dispatch('c')",
        "uses_run_query()",
        "uses_graph()",
        "uses_package('c')",
        "uses_unknown('c')",
        "indirect('c')",
        "dbt_utils.star(from=ref('m'))",
        "parse_amount(ref(var('t')))",
    ],
)
def test_a_model_calling_a_macro_that_could_reach_a_model_is_unknown(
    tmp_path: Path, call: str
) -> None:
    root = project(tmp_path, {}, {"pure.sql": PURE, "impure.sql": IMPURE})
    sql = f"SELECT {{{{ {call} }}}}, {{{{ parse_amount('x') }}}} FROM {{{{ ref('m') }}}}"
    assert referenced_models(sql, macros_of(root)) is None


@pytest.mark.parametrize(
    "signature", ["m(x=ref('a'))", "m(x=graph)", "m(x=helper())", "m(x=pkg.helper())"]
)
def test_a_macro_whose_defaults_could_reach_a_model_is_not_pure(
    tmp_path: Path, signature: str
) -> None:
    """The signature counts with the body (Codex review, round 2)."""
    root = project(tmp_path, {}, {"m.sql": f"{{% macro {signature} %}}{{{{ x }}}}{{% endmacro %}}"})
    assert macros_of(root) == frozenset()


def test_a_duplicate_macro_name_counts_only_when_every_definition_is_pure(
    tmp_path: Path,
) -> None:
    root = project(
        tmp_path,
        {},
        {
            "a.sql": "{% macro twice(x) %}{{ x }}{% endmacro %}",
            "b.sql": "{% macro twice(x) %}{{ ref(x) }}{% endmacro %}",
        },
    )
    assert macros_of(root) == frozenset()


def test_redispatch_skips_models_that_only_call_pure_project_macros(tmp_path: Path) -> None:
    """One seed changed after an all-PASS report: only the models that ref it (directly or
    through an unchanged model) come along, not every model calling a project macro."""
    workspace, snapshot = tmp_path / "ws", tmp_path / "snapshot"
    models = {
        "stg": "SELECT 1 AS amount\n",
        "int_a": "SELECT {{ clean_money('amount') }} FROM {{ ref('stg') }}\n",
        "fct_a": "SELECT {{ parse_amount('amount') }} FROM {{ ref('int_a') }}\n",
        "int_b": "SELECT {{ parse_amount('amount') }} FROM {{ ref('other') }}\n",
        "other": "SELECT 2 AS amount\n",
    }
    project(snapshot / "project", {})
    project(workspace / "project", models, {"cleaning/pure.sql": PURE})
    hashes = model_hashes(workspace)
    state = {
        name: {"checks": {1: "PASS"}, "sha256": "stale" if name == "stg" else hashes[name]}
        for name in models
    }
    assert redispatch_models(state, workspace, snapshot) == ["fct_a", "int_a", "stg"]


def test_redispatch_macros_are_read_per_project(tmp_path: Path) -> None:
    """`m` is pure in p1 and refs a model in p2: p1's `lone` calling it is not downstream."""
    workspace, snapshot = tmp_path / "ws", tmp_path / "snapshot"
    project(snapshot / "p1", {})
    project(
        workspace / "p1",
        {"stg": "SELECT 1\n", "lone": "SELECT {{ m() }}\n"},
        {"m.sql": "{% macro m() %}1{% endmacro %}"},
    )
    project(workspace / "p2", {}, {"m.sql": "{% macro m() %}{{ ref('stg') }}{% endmacro %}"})
    hashes = model_hashes(workspace)
    state = {
        "stg": {"checks": {1: "PASS"}, "sha256": "stale"},
        "lone": {"checks": {1: "PASS"}, "sha256": hashes["lone"]},
    }
    assert redispatch_models(state, workspace, snapshot) == ["stg"]


def test_redispatch_lists_the_callers_of_an_edited_project_macro(tmp_path: Path) -> None:
    """A pure macro edited after the report changes what its callers build (also through
    clean_money -> parse_amount): they are listed with the other seed, the model that does
    not call it is not (Codex review, round 1)."""
    workspace, snapshot = tmp_path / "ws", tmp_path / "snapshot"
    models = {
        "stg": "SELECT 1 AS amount\n",
        "direct": "SELECT {{ parse_amount('amount') }} FROM {{ ref('other') }}\n",
        "nested": "SELECT {{ clean_money('amount') }} FROM {{ ref('other') }}\n",
        "plain": "SELECT amount FROM {{ ref('other') }}\n",
        "other": "SELECT 2 AS amount\n",
    }
    project(snapshot / "project", {})
    project(workspace / "project", models, {"pure.sql": PURE})
    hashes = model_hashes(workspace)
    state = {
        name: {"checks": {1: "PASS"}, "sha256": "stale" if name == "stg" else hashes[name]}
        for name in models
    }
    (workspace / "project" / "macros" / "pure.sql").write_text(
        PURE.replace("decimal(18,2)", "decimal(18,4)")
    )
    assert redispatch_models(state, workspace, snapshot) == ["direct", "nested", "stg"]


def test_macro_paths_inside_dbt_packages_are_not_read(tmp_path: Path) -> None:
    """A macro-paths entry naming a directory in dbt_packages still reads no package macro
    (Codex review, round 1)."""
    root = project(tmp_path, {}, {"h.sql": PURE})
    (root / "dbt_project.yml").write_text(
        'name: tiny\nmacro-paths: ["dbt_packages/util/macros", "macros"]\n'
    )
    package = root / "dbt_packages" / "util" / "macros"
    package.mkdir(parents=True)
    (package / "p.sql").write_text("{% macro pkg_pure(x) %}{{ x }}{% endmacro %}")
    assert macros_of(root) == {"parse_amount", "clean_money"}
