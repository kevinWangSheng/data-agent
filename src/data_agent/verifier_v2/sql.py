"""Reading a dbt model's SQL with sqlglot (spec "工具": model_diff, 解析方法): Jinja is reduced to
plain SQL (`ref`/`source` become table names, `config()` goes, every other `{{ }}` becomes a
placeholder identifier), then the statement is parsed in the gateway's dialect and its facts are
listed by category. A model with Jinja statements (`{% %}`) or SQL that does not parse has no
facts; the caller falls back to a text diff.
"""

import re
from collections import Counter

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from data_agent.verifier_v2.project import CONFIG_CALL_RE

CATEGORIES = ("source", "filter", "join", "rows", "grain", "window", "config")
# category -> multiset of fact texts, each a SQL fragment in the gateway's dialect.
Facts = dict[str, Counter[str]]

COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)
STATEMENT_RE = re.compile(r"\{%")
REF_RE = re.compile(
    r"""\{\{-?\s*ref\s*\(\s*(?:['"][^'"]*['"]\s*,\s*)?['"]([^'"]+)['"]\s*(?:,\s*[^)]*)?\)\s*-?\}\}"""
)
SOURCE_RE = re.compile(
    r"""\{\{-?\s*source\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)\s*-?\}\}"""
)
EXPRESSION_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
SET_OPERATIONS = (exp.Union, exp.Intersect, exp.Except)


def has_jinja_statements(sql: str) -> bool:
    """`{% if %}`, `{% for %}`, `{% set %}` and the like: the SQL depends on rendering."""
    return STATEMENT_RE.search(COMMENT_RE.sub("", sql)) is not None


def render(sql: str) -> str:
    """The model's SQL as plain SQL sqlglot can read."""
    text = COMMENT_RE.sub("", sql)
    text = CONFIG_CALL_RE.sub("", text)
    text = REF_RE.sub(lambda m: m.group(1), text)
    text = SOURCE_RE.sub(lambda m: f"{m.group(1)}.{m.group(2)}", text)
    return EXPRESSION_RE.sub(_placeholder, text)


def _placeholder(match: re.Match[str]) -> str:
    inner = re.sub(r"\W+", "_", match.group(0).strip("{}- \n\t")).strip("_")
    return f"__dbt_{inner[:60]}__"


def parse(sql: str, dialect: str) -> exp.Expr | None:
    """The rendered SQL's syntax tree, or None when it has Jinja statements or does not parse."""
    if has_jinja_statements(sql):
        return None
    try:
        return sqlglot.parse_one(render(sql), read=dialect)
    except (SqlglotError, ValueError, RecursionError):
        return None


def facts(tree: exp.Expr, dialect: str) -> Facts:
    """The statement's facts by category: tables read (`source`), WHERE/HAVING predicates
    (`filter`), joins (`join`), row-count logic (`rows`: DISTINCT, QUALIFY, set operations,
    LIMIT, row_number() = 1), GROUP BY (`grain`) and window functions (`window`)."""
    found: Facts = {category: Counter() for category in CATEGORIES}
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not table.db and table.name.lower() in ctes:
            continue
        if table.name:
            found["source"][table.sql(dialect=dialect)] += 1
    for select in tree.find_all(exp.Select):
        for clause, key in (("WHERE", "where"), ("HAVING", "having")):
            node = select.args.get(key)
            if node is not None:
                for predicate in _conjuncts(node.this):
                    found["filter"][f"{clause} {predicate.sql(dialect=dialect)}"] += 1
                    if _is_first_row_predicate(predicate):
                        found["rows"][f"row_number() = 1 ({predicate.sql(dialect=dialect)})"] += 1
        for join in select.args.get("joins") or []:
            found["join"][join.sql(dialect=dialect)] += 1
        if select.args.get("distinct") is not None:
            found["rows"]["DISTINCT"] += 1
        qualify = select.args.get("qualify")
        if qualify is not None:
            found["rows"][f"QUALIFY {qualify.this.sql(dialect=dialect)}"] += 1
        group = select.args.get("group")
        if group is not None and group.expressions:
            columns = ", ".join(e.sql(dialect=dialect) for e in group.expressions)
            found["grain"][f"GROUP BY {columns}"] += 1
    for node in tree.find_all(exp.Select, *SET_OPERATIONS):
        limit = node.args.get("limit")
        if isinstance(limit, exp.Limit):
            found["rows"][f"LIMIT {limit.expression.sql(dialect=dialect)}"] += 1
    for operation in tree.find_all(*SET_OPERATIONS):
        name = operation.key.upper()
        found["rows"][name if operation.args.get("distinct") else f"{name} ALL"] += 1
    for window in tree.find_all(exp.Window):
        found["window"][window.sql(dialect=dialect)] += 1
    return found


def _conjuncts(node: exp.Expr) -> list[exp.Expr]:
    return list(node.flatten()) if isinstance(node, exp.And) else [node]


def _is_first_row_predicate(predicate: exp.Expr) -> bool:
    """`row_number() OVER (...) = 1`, the dedupe idiom, as a WHERE/HAVING predicate."""
    if not isinstance(predicate, exp.EQ):
        return False
    sides = (predicate.this, predicate.expression)
    return any(
        isinstance(side, exp.Window) and isinstance(side.this, exp.RowNumber) for side in sides
    ) and any(isinstance(side, exp.Literal) and side.this == "1" for side in sides)


def window_parts(window_sql: str, dialect: str) -> tuple[list[str], list[str]] | None:
    """(partition expressions, ORDER BY expressions) of one window function's text, table
    qualifiers dropped so they name the built model's columns; None when it has no ORDER BY
    or does not parse."""
    try:
        tree = sqlglot.parse_one(f"SELECT {window_sql}", read=dialect)
    except (SqlglotError, ValueError):
        return None
    window = tree.find(exp.Window)
    if window is None:
        return None
    order = window.args.get("order")
    if order is None or not order.expressions:
        return None
    partition = [
        _unqualified(e).sql(dialect=dialect) for e in window.args.get("partition_by") or []
    ]
    ordering = [
        _unqualified(e.this if isinstance(e, exp.Ordered) else e).sql(dialect=dialect)
        for e in order.expressions
    ]
    return partition, ordering


def _unqualified(expression: exp.Expr) -> exp.Expr:
    return expression.transform(
        lambda node: exp.column(node.this) if isinstance(node, exp.Column) else node
    )


def table_names(sql: str, dialect: str) -> list[str] | None:
    """The bare names of the tables a query reads (CTEs excluded), lower-cased; None when the
    SQL does not parse."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except (SqlglotError, ValueError):
        return None
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    names: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name and not (not table.db and name in ctes):
            names.append(name)
    return names
