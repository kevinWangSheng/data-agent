"""`compare_by_key` (spec "工具"): the model against an independent reference query, every key
through a FULL OUTER JOIN, equality decided in SQL by the model column's type:

- DECIMAL: the reference rounded to the column's scale must equal the model value;
- floating point (DOUBLE, REAL, FLOAT): |model - ref| <= 1e-6 * |ref|; a zero reference needs a
  zero model value;
- everything else: exact equality;
- NULL on both sides is equal; NULL on one side is a mismatch.

A reference that reads the model itself or any model the agent changed is refused.
"""

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from data_agent.db_facts import LoadCatalog, Table
from data_agent.verifier_v2 import sql as model_sql
from data_agent.verifier_v2.db import (
    Queryable,
    catalog_loader,
    column_named,
    find_model,
    quote,
    workspace_alias,
)

SAMPLE_LIMIT = 20
DOUBLE_TYPE = {"duckdb": "DOUBLE", "postgres": "DOUBLE PRECISION"}
RELATIVE_TOLERANCE = "1e-6"
DECIMAL_RE = re.compile(r"^(decimal|numeric)(?:\s*\(\s*(\d+)\s*,\s*(\d+)\s*\))?", re.IGNORECASE)
FLOAT_RE = re.compile(r"^(double|real|float)", re.IGNORECASE)
# Postgres keeps a numeric column's scale here, not in data_type.
SCALE_SQL = (
    "SELECT numeric_scale FROM information_schema.columns "
    "WHERE table_schema = '{schema}' AND table_name = '{table}' AND column_name = '{column}'"
)


def compare_by_key(
    gateway: Queryable,
    model: str,
    key_columns: Sequence[str],
    reference_sql: str,
    changed_models: Sequence[str] = (),
    catalog: LoadCatalog | None = None,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """`{"accepted": False, "reason"}` when the reference reads `model` or a changed model (or
    does not parse); else `{"accepted": True, "relation", "compared_columns",
    "unmatched_reference_columns", "only_in_model", "only_in_reference", "mismatched",
    "samples"}`. `samples` holds up to 20 key dicts for each one-sided side and up to 20
    `{"key", "column", "model", "reference"}` entries for mismatches. Compared columns are the
    reference's non-key columns that the model also has. `workspace` lets the model and the
    changed models be known by their aliases too; `catalog` is the server's cache."""
    reference_sql = reference_sql.strip().rstrip(";")
    tables = model_sql.table_names(reference_sql, gateway.dialect)
    if tables is None:
        return {"accepted": False, "reason": "the reference SQL could not be parsed"}
    load = catalog_loader(gateway, catalog)
    table = find_model(load, workspace, model)
    # Bare, lower-cased relation names, as `table_names` reports them: the model as named and
    # as built, every changed model as named and, when the workspace is known, as aliased.
    forbidden = {_bare(model), table.name.lower()}
    for changed in changed_models:
        forbidden.add(_bare(changed))
        alias = workspace_alias(workspace, changed) if workspace else None
        if alias:
            forbidden.add(_bare(alias))
    hits = sorted({name for name in tables if name in forbidden})
    if hits:
        return {
            "accepted": False,
            "reason": (
                f"the reference SQL reads {', '.join(hits)}: a reference must not read the "
                "model under test or any model created or changed in this run (it would only "
                "re-check the implementation against itself)"
            ),
        }
    keys = [column_named(table, key) for key in key_columns]
    if not keys:
        raise ToolError("Error: key_columns is empty; name the grain key")
    reference_columns = gateway.query(
        f"SELECT * FROM ({reference_sql}) AS ref_ WHERE 1 = 0", 1
    ).columns
    reference_lower = {column.lower(): column for column in reference_columns}
    for key in keys:
        if key.lower() not in reference_lower:
            raise ToolError(f"Error: the reference SQL has no key column {key!r}")
    model_columns = {column.lower(): (column, data_type) for column, data_type in table.columns}
    compared: list[tuple[str, str, str]] = []  # (model column, reference column, data type)
    unmatched: list[str] = []
    for column in reference_columns:
        lowered = column.lower()
        if lowered in {key.lower() for key in keys}:
            continue
        if lowered in model_columns:
            compared.append((model_columns[lowered][0], column, model_columns[lowered][1]))
        else:
            unmatched.append(column)
    dialect = gateway.dialect
    joined = _joined_sql(table, reference_sql, keys, reference_lower, compared, gateway)
    both = '"__m_present" AND "__r_present"'
    any_mismatch = (
        "NOT (" + " AND ".join(quote(f"__eq_{c}") for c, _, _ in compared) + ")"
        if compared
        else "FALSE"
    )
    key_list = ", ".join(quote(k) for k in keys)

    def distinct_keys(condition: str) -> str:
        # Keys, not rows: a key repeated on either side still counts once (DISTINCT treats
        # NULL key values as equal).
        return f"(SELECT COUNT(*) FROM (SELECT DISTINCT {key_list} FROM j WHERE {condition}) AS k)"

    only_model, only_reference = '"__r_present" IS NULL', '"__m_present" IS NULL'
    counts = gateway.query(
        f"{joined} SELECT {distinct_keys(only_model)}, {distinct_keys(only_reference)}, "
        f"{distinct_keys(f'{both} AND {any_mismatch}')}",
        1,
    ).rows[0]
    only_in_model = gateway.query(
        f'{joined} SELECT {key_list} FROM j WHERE "__r_present" IS NULL ORDER BY {key_list} '
        f"LIMIT {SAMPLE_LIMIT}",
        SAMPLE_LIMIT,
    )
    only_in_reference = gateway.query(
        f'{joined} SELECT {key_list} FROM j WHERE "__m_present" IS NULL ORDER BY {key_list} '
        f"LIMIT {SAMPLE_LIMIT}",
        SAMPLE_LIMIT,
    )
    mismatches: list[dict[str, Any]] = []
    if compared:
        value_list = ", ".join(
            f"{quote(f'__m_{c}')}, {quote(f'__r_{c}')}, {quote(f'__eq_{c}')}"
            for c, _, _ in compared
        )
        rows = gateway.query(
            f"{joined} SELECT {key_list}, {value_list} FROM j WHERE {both} AND {any_mismatch} "
            f"ORDER BY {key_list} LIMIT {SAMPLE_LIMIT}",
            SAMPLE_LIMIT,
        ).rows
        for row in rows:
            key = dict(zip(keys, row[: len(keys)], strict=True))
            values = row[len(keys) :]
            for index, (column, _, _) in enumerate(compared):
                model_value, reference_value, equal = values[3 * index : 3 * index + 3]
                if not equal and len(mismatches) < SAMPLE_LIMIT:
                    mismatches.append(
                        {
                            "key": key,
                            "column": column,
                            "model": model_value,
                            "reference": reference_value,
                        }
                    )
    return {
        "accepted": True,
        "relation": table.ref,
        "dialect": dialect,
        "compared_columns": [c for c, _, _ in compared],
        "unmatched_reference_columns": unmatched,
        "only_in_model": int(counts[0] or 0),
        "only_in_reference": int(counts[1] or 0),
        "mismatched": int(counts[2] or 0),
        "samples": {
            "only_in_model": [dict(zip(keys, row, strict=True)) for row in only_in_model.rows],
            "only_in_reference": [
                dict(zip(keys, row, strict=True)) for row in only_in_reference.rows
            ],
            "mismatched": mismatches,
        },
    }


def _bare(relation: str) -> str:
    """The relation's own name, lower-cased, without schema or database qualifiers."""
    return relation.strip().rsplit(".", 1)[-1].strip('"').lower()


def _joined_sql(
    table: Table,
    reference_sql: str,
    keys: list[str],
    reference_lower: dict[str, str],
    compared: list[tuple[str, str, str]],
    gateway: Queryable,
) -> str:
    """`WITH m, r, j` where j is the FULL OUTER JOIN of model and reference on the keys, with
    both values and an equality flag per compared column."""
    key_select = ", ".join(
        f"COALESCE(m.{quote(k)}, r.{quote(reference_lower[k.lower()])}) AS {quote(k)}" for k in keys
    )
    on = " AND ".join(
        f"m.{quote(k)} IS NOT DISTINCT FROM r.{quote(reference_lower[k.lower()])}" for k in keys
    )
    value_select = ", ".join(
        f"m.{quote(c)} AS {quote(f'__m_{c}')}, r.{quote(r)} AS {quote(f'__r_{c}')}, "
        f"{_equality(table, c, r, data_type, gateway)} AS {quote(f'__eq_{c}')}"
        for c, r, data_type in compared
    )
    selected = ", ".join(
        part
        for part in (
            'm."__m_present"',
            'r."__r_present"',
            key_select,
            value_select,
        )
        if part
    )
    return (
        f'WITH m AS (SELECT *, TRUE AS "__m_present" FROM {table.sql_name}), '
        f'r AS (SELECT ref_.*, TRUE AS "__r_present" FROM ({reference_sql}) AS ref_), '
        f"j AS (SELECT {selected} FROM m FULL OUTER JOIN r ON {on})"
    )


def _equality(table: Table, column: str, reference: str, data_type: str, gateway: Queryable) -> str:
    m, r = f"m.{quote(column)}", f"r.{quote(reference)}"
    rule = _rule(table, column, m, r, data_type, gateway)
    return f"(({m} IS NULL AND {r} IS NULL) OR ({m} IS NOT NULL AND {r} IS NOT NULL AND {rule}))"


def _rule(table: Table, column: str, m: str, r: str, data_type: str, gateway: Queryable) -> str:
    decimal = DECIMAL_RE.match(data_type.strip())
    if decimal:
        scale = (
            int(decimal.group(3))
            if decimal.group(3) is not None
            else _scale(table, column, gateway)
        )
        if scale is None:
            return f"{m} = {r}"
        rounded = (
            f"ROUND({r}, {scale})"
            if gateway.dialect == "duckdb"
            else f"ROUND(CAST({r} AS NUMERIC), {scale})"
        )
        return f"CAST({rounded} AS DECIMAL(38, {scale})) = CAST({m} AS DECIMAL(38, {scale}))"
    if FLOAT_RE.match(data_type.strip()):
        double = DOUBLE_TYPE.get(gateway.dialect, "DOUBLE PRECISION")
        md, rd = f"CAST({m} AS {double})", f"CAST({r} AS {double})"
        return (
            f"(CASE WHEN {rd} = 0 THEN {md} = 0 "
            f"ELSE ABS({md} - {rd}) <= {RELATIVE_TOLERANCE} * ABS({rd}) END)"
        )
    return f"{m} = {r}"


def _scale(table: Table, column: str, gateway: Queryable) -> int | None:
    """A numeric column's scale from information_schema (Postgres reports `numeric` alone)."""
    rows = gateway.query(
        SCALE_SQL.format(
            schema=table.schema.replace("'", "''"),
            table=table.name.replace("'", "''"),
            column=column.replace("'", "''"),
        ),
        1,
    ).rows
    if rows and rows[0][0] is not None:
        return int(rows[0][0])
    return None
