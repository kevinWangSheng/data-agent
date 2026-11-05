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
from data_agent.gateway import QueryFailed
from data_agent.verifier_v2 import sql as model_sql
from data_agent.verifier_v2.db import (
    Queryable,
    catalog_loader,
    column_named,
    find_model,
    quote,
    workspace_alias,
)
from data_agent.verifier_v2.project import bare_name

SAMPLE_LIMIT = 20
DOUBLE_TYPE = {"duckdb": "DOUBLE", "postgres": "DOUBLE PRECISION"}
RELATIVE_TOLERANCE = "1e-6"
DECIMAL_RE = re.compile(r"^(decimal|numeric)(?:\s*\(\s*(\d+)\s*,\s*(\d+)\s*\))?", re.IGNORECASE)
FLOAT_RE = re.compile(r"^(double|real|float)", re.IGNORECASE)
# DuckDB reads a table named by a string, which sqlglot's table list cannot see.
QUERY_TABLE_RE = re.compile(r"\bquery_table\s*\(", re.IGNORECASE)
# Postgres keeps a numeric column's scale here, not in data_type.
SCALE_SQL = (
    "SELECT numeric_scale FROM information_schema.columns "
    "WHERE table_schema = '{schema}' AND table_name = '{table}' AND column_name = '{column}'"
)
# information_schema.columns names these Postgres types without a castable spelling; a key of
# such a type joins uncast.
UNCASTABLE_TYPES = {"ARRAY", "USER-DEFINED"}
# (quoted model key, quoted reference key, quoted sentinel column, model key's data type)
Key = tuple[str, str, str, str]


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
    changed models be known by their aliases too; `catalog` is the server's cache. A statement
    the database rejects (a reference key it cannot cast to the model key's type, a value
    column of an incomparable type) is a ToolError carrying the database's reason."""
    try:
        return _compare_by_key(
            gateway, model, key_columns, reference_sql, changed_models, catalog, workspace
        )
    except QueryFailed as exc:
        raise ToolError(f"Error: the database rejected the comparison: {exc}") from exc


def _compare_by_key(
    gateway: Queryable,
    model: str,
    key_columns: Sequence[str],
    reference_sql: str,
    changed_models: Sequence[str],
    catalog: LoadCatalog | None,
    workspace: Path | None,
) -> dict[str, Any]:
    reference_sql = reference_sql.strip().rstrip(";")
    if QUERY_TABLE_RE.search(reference_sql):
        return {
            "accepted": False,
            "reason": "the reference SQL calls query_table(), which names its table in a string "
            "the check cannot see through; write the table name in the FROM clause",
        }
    references = model_sql.table_references(reference_sql, gateway.dialect)
    if references is None:
        return {"accepted": False, "reason": "the reference SQL could not be parsed"}
    load = catalog_loader(gateway, catalog)
    table = find_model(load, workspace, model)
    forbidden = _forbidden(load, workspace, model, table, changed_models)
    # A qualifier that is not a schema the catalog knows (DuckDB reads `<dbname>.<table>` from
    # the default schema) does not place the reference elsewhere: the bare name decides.
    schemas = {t.schema.lower() for t in load()}
    hits = sorted(
        {
            f"{schema}.{name}" if schema else name
            for schema, name in references
            if name in forbidden
            and (
                schema is None
                or schema not in schemas
                or not forbidden[name]
                or schema in forbidden[name]
            )
        }
    )
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
    pairs: list[Key] = [
        (
            quote(k),
            quote(reference_lower[k.lower()]),
            quote(f"__s_{k}"),
            model_columns[k.lower()][1],
        )
        for k in keys
    ]
    sentinels = _sentinels(table, reference_sql, pairs, gateway)
    joined = _joined_sql(table, reference_sql, pairs, sentinels, compared, gateway)
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


def _forbidden(
    load: LoadCatalog,
    workspace: Path | None,
    model: str,
    table: Table,
    changed_models: Sequence[str],
) -> dict[str, set[str]]:
    """Lower-cased bare relation name -> the schemas it is known to be built in, for the model
    as named and as built and for every changed model as named and (when the workspace is
    known) as aliased. An unqualified reference to a forbidden name is refused in any schema; a
    qualified one only in a known schema (or when none is known), so `raw.orders` may feed a
    reference for a model `orders` built in another schema."""
    forbidden: dict[str, set[str]] = {}

    def forbid(name: str, schema: str | None) -> None:
        schemas = forbidden.setdefault(_bare(name), set())
        if schema:
            schemas.add(schema.lower())

    forbid(model, table.schema)
    forbid(table.name, table.schema)
    for changed in changed_models:
        try:
            built: Table | None = find_model(load, workspace, changed)
        except ToolError:
            built = None
        schema = built.schema if built else None
        forbid(changed, schema)
        alias = workspace_alias(workspace, changed) if workspace else None
        if alias:
            forbid(alias, schema)
    return forbidden


def _bare(relation: str) -> str:
    """The relation's own name, lower-cased, without schema or database qualifiers."""
    return bare_name(relation).lower()


def _as_model_type(expression: str, data_type: str) -> str:
    """The reference key `expression` cast to the model key's type, so that COALESCE with the
    sentinel and the join equality see one type on both sides (the old `IS NOT DISTINCT FROM`
    let DuckDB convert implicitly; COALESCE in either database does not). A value the
    database cannot cast fails the statement, which the tool reports with the reason."""
    if data_type.strip().upper() in UNCASTABLE_TYPES:
        return expression
    return f"CAST({expression} AS {data_type})"


def _sentinel_select(model: str, reference: str, pairs: list[Key]) -> str:
    """One row: per key, any non-NULL value of it from the `model` relation, else from the
    `reference` one (both FROM-clause text), in the model key's type."""
    return ", ".join(
        f"COALESCE((SELECT {m} FROM {model} WHERE {m} IS NOT NULL LIMIT 1), "
        f"(SELECT {_as_model_type(r, t)} FROM {reference} WHERE {r} IS NOT NULL LIMIT 1)) AS {s}"
        for m, r, s, t in pairs
    )


def _sentinels(
    table: Table, reference_sql: str, pairs: list[Key], gateway: Queryable
) -> list[bool]:
    """Per key, whether a sentinel exists: False when the key is NULL on every row of both
    sides, in which case the NULL flags alone decide the join."""
    selected = ", ".join(f"{s} IS NOT NULL" for _, _, s, _ in pairs)
    sentinel = _sentinel_select(table.sql_name, f"({reference_sql}) AS ref_", pairs)
    row = gateway.query(f"SELECT {selected} FROM (SELECT {sentinel}) AS s", 1).rows[0]
    return [bool(value) for value in row]


def _joined_sql(
    table: Table,
    reference_sql: str,
    pairs: list[Key],
    sentinels: list[bool],
    compared: list[tuple[str, str, str]],
    gateway: Queryable,
) -> str:
    """`WITH m0, r0, s, m, r, j` where j is the FULL OUTER JOIN of model (m) and reference (r)
    on the keys (`pairs`: quoted model key, reference key, sentinel column, model key type),
    with both values and an equality flag per compared column.

    NULL keys pair with NULL keys. Postgres runs a FULL JOIN only on merge- or hash-joinable
    conditions, which `IS NOT DISTINCT FROM` and `a = b OR (a IS NULL AND b IS NULL)` are not
    (it refuses the query), so each key joins on two plain equalities: the NULL flags, and the
    values with NULL replaced by a sentinel of the key's own type. The sentinel (`s`, one row)
    is any non-NULL value of that key on either side, carried onto both m and r by a cross
    join so the condition reads only each side's columns; the flag equality keeps a NULL from
    pairing with a real key equal to the sentinel. A key with no non-NULL value anywhere
    (`sentinels` False) joins on the flags alone, which both sides then always satisfy. The
    reference key is cast to the model key's type wherever the two meet."""
    on = " AND ".join(
        f"(m.{m} IS NULL) = (r.{r} IS NULL)"
        + (
            f" AND COALESCE(m.{m}, m.{s}) = COALESCE({_as_model_type(f'r.{r}', t)}, r.{s})"
            if has_sentinel
            else ""
        )
        for (m, r, s, t), has_sentinel in zip(pairs, sentinels, strict=True)
    )
    key_select = ", ".join(
        f"COALESCE(m.{m}, {_as_model_type(f'r.{r}', t)}) AS {m}" for m, r, _, t in pairs
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
        f"WITH m0 AS (SELECT * FROM {table.sql_name}), "
        f"r0 AS (SELECT ref_.* FROM ({reference_sql}) AS ref_), "
        f"s AS (SELECT {_sentinel_select('m0', 'r0', pairs)}), "
        f'm AS (SELECT m0.*, TRUE AS "__m_present", s.* FROM m0 CROSS JOIN s), '
        f'r AS (SELECT r0.*, TRUE AS "__r_present", s.* FROM r0 CROSS JOIN s), '
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
