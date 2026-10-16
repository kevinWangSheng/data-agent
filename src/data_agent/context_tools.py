"""Context-strategy tools (ADR-0003): discover → hydrate, dictionary search, `_id` join candidates.

Contracts copied from ktx (ideas only, no code): `discover` returns references plus a summary of at
most 200 characters; `hydrate` reads one object in full; `dictionary_search` finds a keyword among
the real values of text columns. Join candidates follow SignalPilot `analyze_project_db`'s `_id`
matching, capped at ktx's 25 candidates per column. All SQL goes through the gateway.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from data_agent.gateway import MAX_ROWS, QueryResult

# Runs one statement through the gateway; blocks and database errors raise ToolError.
RunQuery = Callable[[str, int], QueryResult]

SUMMARY_LIMIT = 200
JOIN_CANDIDATES_PER_COLUMN = 25
# dictionary_search output bounds: values per column, characters per value, columns in all.
VALUES_PER_COLUMN = 20
VALUE_CHARS = 80
COLUMNS_SHOWN = 50
KEYWORD_CHARS = 200
# A dictionary statement must fit the gateway's row cap and its 100,000-character length limit.
COLUMNS_PER_STATEMENT = MAX_ROWS // VALUES_PER_COLUMN
STATEMENT_CHARS = 90_000

CATALOG_SQL = """
SELECT c.table_schema, c.table_name, t.table_type, c.column_name, c.data_type
FROM information_schema.columns AS c
JOIN information_schema.tables AS t
  ON t.table_catalog = c.table_catalog
 AND t.table_schema = c.table_schema
 AND t.table_name = c.table_name
WHERE c.table_schema NOT IN ('information_schema', 'pg_catalog')
ORDER BY c.table_schema, c.table_name, c.ordinal_position
LIMIT {limit} OFFSET {offset}
"""


@dataclass
class Table:
    schema: str
    name: str
    kind: str
    columns: list[tuple[str, str]]  # (name, data type), in table order

    @property
    def ref(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def sql_name(self) -> str:
        return f"{quote(self.schema)}.{quote(self.name)}"


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def is_text(data_type: str) -> bool:
    upper = data_type.upper()
    return "CHAR" in upper or "TEXT" in upper or upper == "STRING"


def load_catalog(run: RunQuery) -> list[Table]:
    """Every column of every table, read in pages: one gateway query returns at most MAX_ROWS."""
    tables: dict[tuple[str, str], Table] = {}
    offset = 0
    while True:
        rows = run(CATALOG_SQL.format(limit=MAX_ROWS, offset=offset), MAX_ROWS).rows
        for schema, name, kind, column, data_type in rows:
            table = tables.setdefault(
                (schema, name), Table(schema, name, kind.lower().removeprefix("base "), [])
            )
            table.columns.append((column, data_type))
        if len(rows) < MAX_ROWS:
            return list(tables.values())
        offset += MAX_ROWS


def terms_of(text: str) -> list[str]:
    return [term for term in re.split(r"[\W_]+", text.lower()) if term]


def singular(term: str) -> str:
    return term[:-1] if len(term) > 3 and term.endswith("s") else term


def matches(term: str, identifier: str) -> bool:
    """Substring match, also on the singular, so "events" finds event_name."""
    name = identifier.lower()
    return term in name or singular(term) in name


def score(table: Table, terms: list[str]) -> tuple[int, list[str]]:
    """Per term: 3 if the table name holds it whole, 2 if only its singular, 1 if a column does."""
    total = 0
    matched_columns: list[str] = []
    name = table.name.lower()
    for term in terms:
        if term in name:
            total += 3
        elif singular(term) in name:
            total += 2
        hits = [column for column, _ in table.columns if matches(term, column)]
        if hits:
            total += 1
            matched_columns.extend(column for column in hits if column not in matched_columns)
    return total, matched_columns


def summarize(table: Table, matched_columns: list[str]) -> str:
    """Kind and column count, then matching columns, then as many other columns as fit."""
    text = f"{table.kind}, {len(table.columns)} columns"
    if matched_columns:
        text += "; matching: " + ", ".join(matched_columns)
    rest = [column for column, _ in table.columns if column not in matched_columns]
    if rest:
        text += "; columns: " + ", ".join(rest)
    if len(text) > SUMMARY_LIMIT:
        text = text[: SUMMARY_LIMIT - 1].rstrip(", ") + "…"
    return text


def find_table(catalog: list[Table], ref: str) -> Table:
    wanted = ref.strip().lower()
    hits = [table for table in catalog if wanted in (table.ref.lower(), table.name.lower())]
    if len(hits) == 1:
        return hits[0]
    if hits:
        options = ", ".join(table.ref for table in hits)
        raise ToolError(f"Error: {ref!r} is ambiguous; use one of: {options}")
    raise ToolError(f"Error: no table or view {ref!r}; use a ref returned by discover")


def named_after(prefix: str) -> set[str]:
    """Table names a `<prefix>_id` column points at, as in SignalPilot (customers, stg_customer)."""
    plurals = {f"{prefix}s"} | ({f"{prefix[:-1]}ies"} if prefix.endswith("y") else set())
    bases = {prefix, *plurals}
    return bases | {f"stg_{base}" for base in bases} | {f"dim_{base}" for base in bases}


def join_candidates(catalog: list[Table], table: Table) -> list[str]:
    """For each `<x>_id` column: tables named after x (joined on the same column or their `id`)
    first, then other tables that have the same `_id` column."""
    lines: list[str] = []
    for column, _ in table.columns:
        low = column.lower()
        if not low.endswith("_id") or low == "_id":
            continue
        names = named_after(low.removesuffix("_id"))
        lookups: list[str] = []
        shared: list[str] = []
        for other in catalog:
            if other is table:
                continue
            keys = {name.lower(): name for name, _ in other.columns}
            if other.name.lower() in names and (key := keys.get(low) or keys.get("id")):
                lookups.append(f"{column} -> {other.ref}.{key}")
            elif low in keys:
                shared.append(f"{column} -> {other.ref}.{keys[low]}")
        lines.extend([*lookups, *shared][:JOIN_CANDIDATES_PER_COLUMN])
    return lines


def value_query(table: Table, column: str, keyword: str, index: int) -> str:
    """A column's most frequent VALUES_PER_COLUMN distinct values that contain the keyword, with
    the number of distinct matching values (`distinct_values`, counted before the limit)."""
    value = f"CAST({quote(column)} AS VARCHAR)"
    return (
        f"SELECT {literal(column)} AS column_name, value, n, distinct_values FROM ("
        f"SELECT {value} AS value, count(*) AS n, count(*) OVER () AS distinct_values "
        f"FROM {table.sql_name} WHERE strpos(lower({value}), lower({literal(keyword)})) > 0 "
        f"GROUP BY {quote(column)} ORDER BY n DESC, value LIMIT {VALUES_PER_COLUMN}) AS c{index}"
    )


def dictionary_statements(table: Table, keyword: str) -> list[str]:
    """The table's text columns, batched into statements under the gateway's row and size caps."""
    separator = "\nUNION ALL\n"
    columns = [column for column, data_type in table.columns if is_text(data_type)]
    statements: list[str] = []
    batch: list[str] = []
    size = 0
    for index, column in enumerate(columns):
        part = value_query(table, column, keyword, index)
        if batch and (
            len(batch) == COLUMNS_PER_STATEMENT
            or size + len(separator) + len(part) > STATEMENT_CHARS
        ):
            statements.append(separator.join(batch))
            batch, size = [], 0
        size += len(part) + (len(separator) if batch else 0)
        batch.append(part)
    if batch:
        statements.append(separator.join(batch))
    return statements


def short(value: object) -> str:
    text = str(value)
    return repr(text if len(text) <= VALUE_CHARS else text[:VALUE_CHARS] + "…")


def search_values(run: RunQuery, table: Table, keyword: str) -> list[str]:
    shown: dict[str, list[str]] = {}
    distinct: dict[str, int] = {}
    for sql in dictionary_statements(table, keyword):
        for column, value, n, distinct_values in run(sql, MAX_ROWS).rows:
            shown.setdefault(column, []).append(f"{short(value)} ({n})")
            distinct[column] = distinct_values
    lines: list[str] = []
    for column, values in shown.items():
        extra = distinct[column] - len(values)
        more = f", … {extra} more" if extra > 0 else ""
        lines.append(f"{table.ref}.{column}: {', '.join(values)}{more}")
    return lines


def register_context_tools(server: MCPServer, run: RunQuery) -> None:
    """Add the context-strategy tools to `server`; called only when the run config turns the
    context layer on."""

    @server.tool()
    def discover(query: str, limit: int = 10) -> dict[str, list[dict[str, Any]]]:
        """Search table, view and column names; returns refs with a short summary, best first.

        Each result is `{"ref", "summary"}`; the summary has at most 200 characters, so a wide
        table's column list is cut short. Call `hydrate(ref)` for one object's full detail.
        `limit` is 1-50.
        """
        terms = terms_of(query)
        if not terms:
            raise ToolError("Error: query has no words to search for")
        scored = [(*score(table, terms), table) for table in load_catalog(run)]
        ranked = sorted(
            (item for item in scored if item[0] > 0), key=lambda item: (-item[0], item[2].ref)
        )
        return {
            "results": [
                {"ref": table.ref, "summary": summarize(table, matched)}
                for _, matched, table in ranked[: max(1, min(limit, 50))]
            ]
        }

    @server.tool()
    def hydrate(ref: str) -> str:
        """Read one table or view in full: row count, every column with its type, and join
        candidates found from `<x>_id` columns. `ref` is a ref from discover (schema.table) or a
        bare table name."""
        catalog = load_catalog(run)
        table = find_table(catalog, ref)
        count = run(f"SELECT count(*) FROM {table.sql_name}", 1).rows[0][0]
        lines = [f"{table.ref} ({table.kind}, {count} rows, {len(table.columns)} columns)"]
        lines.extend(f"  {column} {data_type}" for column, data_type in table.columns)
        lines.append("Join candidates (by _id column name; check the key values before joining):")
        lines.extend(f"  {line}" for line in join_candidates(catalog, table) or ["none found"])
        return "\n".join(lines)

    @server.tool()
    def dictionary_search(keyword: str) -> str:
        """Find the real values of text columns that contain `keyword` (ignores case), with
        their row counts, to match dirty keys and enum spellings before filtering or joining.

        Shows up to 20 values per column and 50 columns; a broader match asks for a narrower
        keyword."""
        if not keyword.strip():
            raise ToolError("Error: keyword is empty")
        if len(keyword) > KEYWORD_CHARS:
            raise ToolError(f"Error: keyword is longer than {KEYWORD_CHARS} characters")
        lines: list[str] = []
        for table in load_catalog(run):
            lines.extend(search_values(run, table, keyword))
            if len(lines) > COLUMNS_SHOWN:
                lines = lines[:COLUMNS_SHOWN]
                lines.append(f"… more columns match {keyword!r}; use a more specific keyword")
                break
        return "\n".join(lines) if lines else f"No values contain {keyword!r}"
