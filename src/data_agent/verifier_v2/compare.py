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
from typing import Any, NamedTuple

from mcp.server.mcpserver.exceptions import ToolError

from data_agent.db_facts import LoadCatalog, Table
from data_agent.gateway import QueryBlocked, QueryFailed
from data_agent.verifier_v2 import sql as model_sql
from data_agent.verifier_v2.db import (
    Queryable,
    catalog_loader,
    column_named,
    find_model,
    quote,
    workspace_aliases,
)
from data_agent.verifier_v2.project import bare_name

SAMPLE_LIMIT = 20
# The `__kind` of a joined row that differs; NULL for a matching one.
ONLY_MODEL, ONLY_REFERENCE, MISMATCH = 1, 2, 3
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
# information_schema.columns names these Postgres types without a castable spelling (`ARRAY`,
# `USER-DEFINED`) or without the length CAST would need (`character`, `bit` read as length 1);
# a key of such a type joins uncast, as every key did before the cast was added.
UNCASTABLE_TYPES = {"ARRAY", "USER-DEFINED", "CHARACTER", "BIT"}


class Key(NamedTuple):
    """One grain key as the join sees it."""

    model: str  # quoted model column
    reference: str  # quoted reference column
    sentinel: str  # quoted sentinel column (`s` CTE)
    data_type: str  # the model column's type, which the reference key is cast to


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
    that does not run (the gateway blocks the reference, the database rejects a cast or a
    comparison of incomparable types, a timeout) is a ToolError carrying the reason."""
    try:
        return _compare_by_key(
            gateway, model, key_columns, reference_sql, changed_models, catalog, workspace
        )
    except (QueryBlocked, QueryFailed) as exc:
        raise ToolError(f"Error: the comparison could not run: {exc}") from exc


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
    # A qualifier that is not a schema the catalog knows does not place the reference elsewhere,
    # so the bare name decides. DuckDB reads `<dbname>.<table>` from the default schema (and
    # allows no schema named like the database); on Postgres such a reference fails anyway.
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
    pairs = [
        Key(
            quote(k),
            quote(reference_lower[k.lower()]),
            quote(f"__s_{k}"),
            model_columns[k.lower()][1],
        )
        for k in keys
    ]
    sentinels = _model_has_key_values(table, pairs, gateway)
    statement = _comparison_sql(table, reference_sql, pairs, sentinels, compared, gateway)
    result = gateway.query(statement, 3 * SAMPLE_LIMIT)
    at = {column: index for index, column in enumerate(result.columns)}
    first = result.rows[0]
    for index, key in enumerate(pairs):
        example = first[at[f"__lossy_{index}"]]
        if example is not None:
            raise ToolError(
                f"Error: the reference key {key.model} has values the model's {key.data_type} "
                f"key cannot hold exactly (for example {example}); cast or round the key in "
                "the reference SQL to the model's type, or fix the reference"
            )
    by_kind: dict[int, list[tuple[Any, ...]]] = {ONLY_MODEL: [], ONLY_REFERENCE: [], MISMATCH: []}
    for row in result.rows:
        if row[at["__kind"]] is not None:
            by_kind[row[at["__kind"]]].append(row)

    def key_of(row: tuple[Any, ...]) -> dict[str, Any]:
        return {key: row[at[key]] for key in keys}

    mismatches: list[dict[str, Any]] = []
    for row in by_kind[MISMATCH]:
        for column, _, _ in compared:
            if not row[at[f"__eq_{column}"]] and len(mismatches) < SAMPLE_LIMIT:
                mismatches.append(
                    {
                        "key": key_of(row),
                        "column": column,
                        "model": row[at[f"__m_{column}"]],
                        "reference": row[at[f"__r_{column}"]],
                    }
                )
    return {
        "accepted": True,
        "relation": table.ref,
        "dialect": dialect,
        "compared_columns": [c for c, _, _ in compared],
        "unmatched_reference_columns": unmatched,
        "only_in_model": int(first[at["__n_only_model"]] or 0),
        "only_in_reference": int(first[at["__n_only_reference"]] or 0),
        "mismatched": int(first[at["__n_mismatched"]] or 0),
        "samples": {
            "only_in_model": [key_of(row) for row in by_kind[ONLY_MODEL]],
            "only_in_reference": [key_of(row) for row in by_kind[ONLY_REFERENCE]],
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
    # One read of the workspace for every changed model, not one per model (ticket 50).
    aliases = workspace_aliases(workspace, list(changed_models)) if workspace else {}
    for changed in changed_models:
        try:
            built: Table | None = find_model(load, workspace, changed, aliases)
        except ToolError:
            built = None
        schema = built.schema if built else None
        forbid(changed, schema)
        alias = aliases.get(changed)
        if alias:
            forbid(alias, schema)
    return forbidden


def _bare(relation: str) -> str:
    """The relation's own name, lower-cased, without schema or database qualifiers."""
    return bare_name(relation).lower()


def _castable(data_type: str) -> bool:
    return data_type.strip().upper() not in UNCASTABLE_TYPES


def _as_model_type(expression: str, data_type: str) -> str:
    """The reference key `expression` cast to the model key's type, so that COALESCE with the
    sentinel and the join equality see one type on both sides (the old `IS NOT DISTINCT FROM`
    let DuckDB convert implicitly; COALESCE in either database does not). A value the
    database cannot cast fails the statement, which the tool reports with the reason."""
    return f"CAST({expression} AS {data_type})" if _castable(data_type) else expression


def _model_has_key_values(table: Table, pairs: list[Key], gateway: Queryable) -> list[bool]:
    """Per key, whether the model holds a non-NULL value of it, read from the model alone.
    When it holds none, the NULL flags alone pair rows exactly (a reference row pairs only
    when its key is NULL too), so the reference is never needed for the sentinel."""
    probes = ", ".join(
        f"(SELECT {key.model} FROM {table.sql_name} WHERE {key.model} IS NOT NULL LIMIT 1) "
        "IS NOT NULL"
        for key in pairs
    )
    return [bool(value) for value in gateway.query(f"SELECT {probes}", 1).rows[0]]


def _lossy_example(reference: str, key: Key) -> str:
    """A reference key value (as text) that the cast to the model key's type changes, else
    NULL: CAST(2.6 AS INTEGER) is 3 and a DOUBLE 1.505 lands on DECIMAL(5,2) as 1.51, which
    would pair keys that are not equal. Judged by the text of the value before and after the
    round trip, the one comparison both databases allow between any two types: a text '3'
    against an INTEGER key passes, a DECIMAL 3.0 is refused too (its text becomes '3') and the
    message asks for the cast to be written in the reference, where it is visible."""
    r = key.reference
    as_text = f"CAST({r} AS VARCHAR)"
    back = f"CAST({_as_model_type(r, key.data_type)} AS VARCHAR)"
    return (
        f"(SELECT MIN({as_text}) FROM {reference} WHERE {r} IS NOT NULL AND {back} <> {as_text})"
        if _castable(key.data_type)
        else "NULL"
    )


def _comparison_sql(
    table: Table,
    reference_sql: str,
    pairs: list[Key],
    sentinels: list[bool],
    compared: list[tuple[str, str, str]],
    gateway: Queryable,
) -> str:
    """One statement for the whole comparison. Its first row carries the three distinct-key
    counts (`__n_only_model`, `__n_only_reference`, `__n_mismatched`) and, per key, a value the
    cast to the model key's type would change (`__lossy_<i>`); its rows carry up to
    SAMPLE_LIMIT differing joined rows per `__kind`, ordered by the key.

    The model (m0) and the reference (r0) are MATERIALIZED CTEs, so each is evaluated once and
    planned on its own: inlined into this statement, a reference that ran in 0.5 s alone was
    planned into one that ran past the timeout (ticket 50).

    j is the FULL OUTER JOIN of model (m) and reference (r) on the keys, with both values and an
    equality flag per compared column. NULL keys pair with NULL keys. Postgres runs a FULL JOIN
    only on merge- or hash-joinable conditions, which `IS NOT DISTINCT FROM` and `a = b OR (a IS
    NULL AND b IS NULL)` are not, so each key joins on two plain equalities: the NULL flags, and
    the values with NULL replaced by a sentinel of the key's own type, any non-NULL model value
    of it (`s`, one row, carried onto both sides by a cross join so the condition reads only
    each side's columns). A key the model holds no value of (`sentinels` False) joins on the
    flags alone. The reference key is cast to the model key's type wherever the two meet."""

    def cast_reference(key: Key) -> str:
        return _as_model_type(f"r.{key.reference}", key.data_type)

    on = " AND ".join(
        f"(m.{key.model} IS NULL) = (r.{key.reference} IS NULL)"
        + (
            f" AND COALESCE(m.{key.model}, m.{key.sentinel}) = "
            f"COALESCE({cast_reference(key)}, r.{key.sentinel})"
            if has_sentinel
            else ""
        )
        for key, has_sentinel in zip(pairs, sentinels, strict=True)
    )
    sentinel_select = ", ".join(
        f"(SELECT {key.model} FROM m0 WHERE {key.model} IS NOT NULL LIMIT 1) AS {key.sentinel}"
        for key in pairs
    )
    key_select = ", ".join(
        f"COALESCE(m.{key.model}, {cast_reference(key)}) AS {key.model}" for key in pairs
    )
    value_select = "".join(
        f", m.{quote(c)} AS {quote(f'__m_{c}')}, r.{quote(r)} AS {quote(f'__r_{c}')}, "
        f"{_equality(table, c, r, data_type, gateway)} AS {quote(f'__eq_{c}')}"
        for c, r, data_type in compared
    )
    any_mismatch = (
        "NOT (" + " AND ".join(quote(f"__eq_{c}") for c, _, _ in compared) + ")"
        if compared
        else "FALSE"
    )
    key_list = ", ".join(key.model for key in pairs)

    def distinct_keys(kind: int) -> str:
        # Keys, not rows: a key repeated on either side still counts once (DISTINCT treats
        # NULL key values as equal).
        return (
            f"(SELECT COUNT(*) FROM (SELECT DISTINCT {key_list} FROM d "
            f'WHERE "__kind" = {kind}) AS k)'
        )

    lossy = ", ".join(
        f"{_lossy_example('r0', key)} AS {quote(f'__lossy_{index}')}"
        for index, key in enumerate(pairs)
    )
    return (
        f"WITH m0 AS MATERIALIZED (SELECT * FROM {table.sql_name}), "
        f"r0 AS MATERIALIZED (SELECT ref_.* FROM ({reference_sql}) AS ref_), "
        f"s AS (SELECT {sentinel_select}), "
        f'm AS (SELECT m0.*, TRUE AS "__m_present", s.* FROM m0 CROSS JOIN s), '
        f'r AS (SELECT r0.*, TRUE AS "__r_present", s.* FROM r0 CROSS JOIN s), '
        f'j AS (SELECT m."__m_present", r."__r_present", {key_select}{value_select} '
        f"FROM m FULL OUTER JOIN r ON {on}), "
        f'd AS MATERIALIZED (SELECT j.*, CASE WHEN "__r_present" IS NULL THEN {ONLY_MODEL} '
        f'WHEN "__m_present" IS NULL THEN {ONLY_REFERENCE} '
        f'WHEN {any_mismatch} THEN {MISMATCH} END AS "__kind" FROM j), '
        f'c AS (SELECT {distinct_keys(ONLY_MODEL)} AS "__n_only_model", '
        f'{distinct_keys(ONLY_REFERENCE)} AS "__n_only_reference", '
        f'{distinct_keys(MISMATCH)} AS "__n_mismatched", {lossy}), '
        f"x AS (SELECT * FROM (SELECT d.*, ROW_NUMBER() OVER "
        f'(PARTITION BY "__kind" ORDER BY {key_list}) AS "__rn" FROM d '
        f'WHERE "__kind" IS NOT NULL) AS y WHERE "__rn" <= {SAMPLE_LIMIT}) '
        f'SELECT c.*, x.* FROM c LEFT JOIN x ON TRUE ORDER BY x."__kind", x."__rn"'
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
