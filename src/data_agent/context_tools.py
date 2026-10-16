"""Context-strategy tools (ADR-0003): discover → hydrate, dictionary search, `_id` join candidates.

Contracts copied from ktx (ideas only, no code): `discover` returns references plus a summary of at
most 200 characters and never a column list in full; `hydrate` reads one object in full;
`dictionary_search` finds a keyword among the real values of text columns. Join candidates follow
SignalPilot `analyze_project_db`'s `_id` matching. All SQL goes through the gateway.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from data_agent.gateway import MAX_ROWS, QueryResult

# Runs one statement through the gateway; blocks and database errors raise ToolError.
Run = Callable[[str, int], QueryResult]

SUMMARY_LIMIT = 200
# Distinct values shown per column by dictionary_search, and columns searched per statement so
# that every column's values fit under the gateway's row cap.
VALUES_PER_COLUMN = 20
COLUMNS_PER_STATEMENT = MAX_ROWS // VALUES_PER_COLUMN

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


@dataclass(frozen=True)
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


def load_catalog(run: Run) -> list[Table]:
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
    return [term for term in re.split(r"[^a-z0-9]+", text.lower()) if term]


def matches(term: str, identifier: str) -> bool:
    """Substring match, also on the singular, so "events" finds event_name."""
    name = identifier.lower()
    return term in name or (len(term) > 3 and term.endswith("s") and term[:-1] in name)


def score(table: Table, terms: list[str]) -> tuple[int, list[str]]:
    """Per term: 3 if the table name holds it whole, 2 if only its singular, 1 if a column does."""
    total = 0
    matched_columns: list[str] = []
    for term in terms:
        if term in table.name.lower():
            total += 3
        elif matches(term, table.name):
            total += 2
        hits = [column for column, _ in table.columns if matches(term, column)]
        if hits:
            total += 1
            matched_columns.extend(column for column in hits if column not in matched_columns)
    return total, matched_columns


def summarize(table: Table, matched_columns: list[str]) -> str:
    rest = [column for column, _ in table.columns if column not in matched_columns]
    text = f"{table.kind}, {len(table.columns)} columns"
    if matched_columns:
        text += "; matching: " + ", ".join(matched_columns)
    text += "; columns: " + ", ".join(rest) if rest else ""
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


def join_candidates(catalog: list[Table], table: Table) -> list[str]:
    """For each `<x>_id` column: tables named after x (x, xs, stg_xs, dim_xs, as SignalPilot does),
    joined on the same column or on their `id`."""
    lines: list[str] = []
    for column, _ in table.columns:
        low = column.lower()
        if not low.endswith("_id") or low == "_id":
            continue
        prefix = low.removesuffix("_id")
        names = {prefix, f"{prefix}s", f"stg_{prefix}", f"stg_{prefix}s", f"dim_{prefix}s"}
        for other in catalog:
            if other is table or other.name.lower() not in names:
                continue
            keys = {name.lower(): name for name, _ in other.columns}
            key = keys.get(low) or keys.get("id")
            if key:
                lines.append(f"{column} -> {other.ref}.{key}")
    return lines


def dictionary_sql(table: Table, columns: list[str], keyword: str) -> str:
    """Per column, its most frequent VALUES_PER_COLUMN distinct values that contain the keyword,
    with the number of distinct matching values (`distinct_values`, counted before the limit)."""
    needle = f"lower({literal(keyword)})"
    parts = [
        f"SELECT {literal(column)} AS column_name, value, n, distinct_values FROM ("
        f"SELECT CAST({quote(column)} AS VARCHAR) AS value, count(*) AS n, "
        f"count(*) OVER () AS distinct_values FROM {table.sql_name} "
        f"WHERE strpos(lower(CAST({quote(column)} AS VARCHAR)), {needle}) > 0 "
        f"GROUP BY {quote(column)} ORDER BY n DESC, value LIMIT {VALUES_PER_COLUMN}) AS c{i}"
        for i, column in enumerate(columns)
    ]
    return "\nUNION ALL\n".join(parts)


def search_values(run: Run, table: Table, keyword: str) -> list[str]:
    text_columns = [column for column, data_type in table.columns if is_text(data_type)]
    shown: dict[str, list[str]] = {}
    distinct: dict[str, int] = {}
    for start in range(0, len(text_columns), COLUMNS_PER_STATEMENT):
        chunk = text_columns[start : start + COLUMNS_PER_STATEMENT]
        for column, value, n, distinct_values in run(
            dictionary_sql(table, chunk, keyword), MAX_ROWS
        ).rows:
            shown.setdefault(column, []).append(f"{value!r} ({n})")
            distinct[column] = distinct_values
    lines: list[str] = []
    for column in text_columns:
        if column in shown:
            extra = distinct[column] - len(shown[column])
            more = f", … {extra} more" if extra > 0 else ""
            lines.append(f"{table.ref}.{column}: {', '.join(shown[column])}{more}")
    return lines


def register_context_tools(server: MCPServer, run: Run) -> None:
    """Add the context-strategy tools to `server`; called only when the run config turns the
    context layer on."""

    @server.tool()
    def discover(query: str, limit: int = 10) -> dict[str, list[dict[str, str]]]:
        """Search table, view and column names; returns refs with a short summary, best first.

        Each result is `{"ref", "summary"}` with a summary of at most 200 characters, never the
        full column list: call `hydrate(ref)` for one object's full detail. `limit` is 1-50.
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
        their row counts, to match dirty keys and enum spellings before filtering or joining."""
        if not keyword.strip():
            raise ToolError("Error: keyword is empty")
        lines = [line for table in load_catalog(run) for line in search_values(run, table, keyword)]
        return "\n".join(lines) if lines else f"No values contain {keyword!r}"
