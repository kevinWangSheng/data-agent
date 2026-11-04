"""The data agent's MCP server (stdio). Every tool reaches the database through the gateway.

The display bounds follow SignalPilot (Apache-2.0, see NOTICE): `render_table_listing` is adapted
from `gateway/mcp/tools/schema/catalog.py` (`_render_table_listing`; no header lines, this server's
table line, and the row estimate from `db_facts`), and the row display cap in `format_rows` from
`gateway/mcp/tools/query.py` (`query_database`; the same floor, budget, maximum and notice).
"""

from functools import partial
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from data_agent.context_tools import register_context_tools
from data_agent.db_facts import (
    CatalogCache,
    describe,
    plan_text,
    row_estimates,
    rows_label,
    timeout_report,
)
from data_agent.gateway import (
    MAX_ROWS,
    Gateway,
    QueryBlocked,
    QueryFailed,
    QueryResult,
    QueryTimedOut,
)
from data_agent.run_config import RunConfig
from data_agent.verifier_tools import register_verifier_tools

# One row per table, column names in order; plain SELECTs, so they pass the gateway like any other.
LIST_TABLES_SQL = """
SELECT t.table_schema, t.table_name, t.table_type,
       string_agg(c.column_name, ', ' ORDER BY c.ordinal_position) AS columns,
       count(*) AS column_count
FROM information_schema.tables AS t
JOIN information_schema.columns AS c
  ON c.table_catalog = t.table_catalog
 AND c.table_schema = t.table_schema
 AND c.table_name = t.table_name
WHERE t.table_schema NOT IN ('information_schema', 'pg_catalog')
GROUP BY t.table_schema, t.table_name, t.table_type
ORDER BY t.table_schema, t.table_name
"""


def tool_error(exc: QueryBlocked | QueryFailed) -> ToolError:
    if isinstance(exc, QueryBlocked):
        return ToolError(f"Query blocked by the read-only gateway: {exc}")
    return ToolError(f"Query error: {exc}")


def run_query(gateway: Gateway, sql: str, row_limit: int) -> QueryResult:
    """Run through the gateway; blocks and database errors become readable tool errors."""
    try:
        return gateway.query(sql, row_limit)
    except (QueryBlocked, QueryFailed) as exc:
        raise tool_error(exc) from exc


def run_explain(gateway: Gateway, sql: str, row_limit: int = MAX_ROWS) -> str:
    """The plan for `sql`, which passes the gateway's check first; errors as in run_query."""
    try:
        return plan_text(gateway.explain(sql, row_limit))
    except (QueryBlocked, QueryFailed) as exc:
        raise tool_error(exc) from exc


# Display caps on query_database (SignalPilot query.py): the first DISPLAY_FLOOR rows always show;
# after that, rows keep printing within DISPLAY_CHAR_BUDGET characters, up to DISPLAY_MAX_ROWS.
# `row_limit` still bounds what the database returns; this bounds what the model reads.
DISPLAY_FLOOR = 50
DISPLAY_CHAR_BUDGET = 12_000
DISPLAY_MAX_ROWS = 400

# Rendered size above which list_tables drops the column lists, then tables (SignalPilot
# catalog.py). It sits under Claude Code's 25,000-token MCP output cap.
LIST_TABLES_CHAR_BUDGET = 36_000
LIST_TABLES_DEFAULT_MAX = 200


def format_rows(result: QueryResult) -> str:
    if not result.rows:
        return "Query returned 0 rows"
    lines = [" | ".join(result.columns)]
    lines.append("-" * len(lines[0]))
    shown = 0
    chars = 0
    for row in result.rows:
        if shown >= DISPLAY_MAX_ROWS:
            break
        line = " | ".join(str(value) for value in row)
        if shown >= DISPLAY_FLOOR and chars + len(line) > DISPLAY_CHAR_BUDGET:
            break
        lines.append(line)
        shown += 1
        chars += len(line) + 1
    total = len(result.rows)
    lines.append(f"({total} rows)")
    if total > shown:
        lines.append(
            f"[INCOMPLETE DISPLAY] {total} rows total; only the first {shown} are shown above. "
            f"The remaining {total - shown} rows exist but are not displayed. "
            f"Do not treat the list above as complete: re-run the query with a WHERE filter or "
            f"OFFSET {shown} to see every remaining row before concluding anything about what the "
            "full result does or does not contain."
        )
    return "\n".join(lines)


def missing_line(missing: int) -> str:
    return f"{missing} more tables not shown; narrow with schema= or name_contains="


def render_table_listing(
    tables: list[tuple[str, str]], max_tables: int, columns: bool, budget: int
) -> str:
    """`tables` are (line with columns, line without) in display order. The first `max_tables`
    render within `budget` characters: when the lines with columns do not fit, every line loses
    its columns; when that still does not fit, tables drop from the end. Whenever a table is not
    shown, the last line says how many and how to narrow the call."""
    selected = tables[:max_tables]
    body = [full if columns else compact for full, compact in selected]
    if columns and sum(len(line) + 1 for line in body) > budget:
        body = [compact for _, compact in selected]
    tail_reserve = len(missing_line(len(tables))) + 1
    used = 0
    shown = 0
    for line in body:
        if used + len(line) + 1 > budget - tail_reserve:
            break
        used += len(line) + 1
        shown += 1
    body = body[:shown]
    missing = len(tables) - shown
    if missing > 0:
        body.append(missing_line(missing))
    return "\n".join(body)


def table_kind(kind: str, estimate: int | None) -> str:
    """`base table, ~N rows`; a view, which has no rows of its own, stays `view`."""
    kind = kind.lower()
    return kind if kind == "view" else f"{kind}, {rows_label(estimate)}"


def table_lines(
    schema: str, name: str, kind: str, columns: str, column_count: int, estimate: int | None
) -> tuple[str, str]:
    """The table's line with its column names and its compact line with the column count."""
    head = f"{schema}.{name} ({table_kind(kind, estimate)}"
    return f"{head}): {columns}", f"{head}, {column_count} cols)"


def build_server(
    gateway: Gateway,
    config: RunConfig,
    workspace: Path | None = None,
    verifier_tools: bool = True,
) -> MCPServer:
    """The base tools are served in every config. A layer's tools are registered only when
    `config` turns that layer on, so a run with it off cannot see them; `verifier_tools=False`
    also leaves out the verifier tools for a run whose task type has no verifiers. `workspace`
    bounds the files a tool may read (map_columns)."""
    server = MCPServer("data-agent")
    run = partial(run_query, gateway)
    # One catalog per server process, shared by every tool that looks a table up.
    catalog = CatalogCache(run, gateway.catalog_key).get

    @server.tool()
    def query_database(sql: str, row_limit: int = 1000) -> str:
        """Run one read-only SQL query (SELECT only) and return the rows as text.

        DDL, DML, multiple statements and file or network functions are blocked. At most
        `row_limit` rows come back (default 1000, max 10000). A query stopped by the timeout
        fails with the query's plan (EXPLAIN) and the indexes on the tables it reads.
        """
        try:
            return format_rows(gateway.query(sql, row_limit))
        except QueryTimedOut as exc:
            explain = partial(run_explain, gateway, row_limit=row_limit)
            raise ToolError(timeout_report(str(exc), sql, gateway.dialect, explain, run)) from exc
        except (QueryBlocked, QueryFailed) as exc:
            raise tool_error(exc) from exc

    @server.tool()
    def list_tables(
        schema: str | None = None,
        name_contains: str | None = None,
        max_tables: int = LIST_TABLES_DEFAULT_MAX,
        columns: bool = True,
    ) -> str:
        """List the database's tables and views, one per line with their column names.

        Each table shows the database's own row estimate (`~N rows`, from its statistics, not
        a count; `rows unknown` when it has none). Views have no estimate.

        Narrow a large listing. Pass `schema` to keep one schema and `name_contains` to keep
        only tables whose name contains that text (both ignore case). Set `columns=false` for a
        compact listing of names, estimates and column counts. At most `max_tables` tables
        (default 200) appear, in (schema, name) order, and the reply never exceeds about 36,000
        characters: when it would, the tool drops the column lists, then drops tables, and the
        last line says how many tables are missing.
        """
        if max_tables < 1:
            raise ToolError("max_tables must be at least 1")
        tables = run_query(gateway, LIST_TABLES_SQL, row_limit=MAX_ROWS).rows
        estimates = row_estimates(run, gateway.dialect)
        wanted_schema = (schema or "").strip().lower()
        needle = (name_contains or "").strip().lower()
        lines = [
            table_lines(table_schema, name, kind, cols, n, estimates.get((table_schema, name)))
            for table_schema, name, kind, cols, n in tables
            if (not wanted_schema or table_schema.lower() == wanted_schema)
            and needle in name.lower()
        ]
        if not lines:
            filters = []
            if wanted_schema:
                filters.append(f"schema {wanted_schema!r}")
            if needle:
                filters.append(f"name containing {needle!r}")
            if not filters:
                return "No tables"
            return f"No tables found with {' and '.join(filters)}"
        return render_table_listing(lines, max_tables, columns, LIST_TABLES_CHAR_BUDGET)

    @server.tool()
    def describe_table(table: str) -> str:
        """One table or view in detail, as the database reports it: its row estimate, every
        column with its type and whether it may be null, declared primary and foreign keys, and
        its indexes (name and definition). `table` is schema.table or a bare table name.

        On Postgres each column also shows `n_distinct` and `null_frac` from pg_stats (a negative
        n_distinct is minus the distinct values' share of the rows); a table with no statistics
        says so. DuckDB lists only indexes made with CREATE INDEX: the index behind a primary key
        shows as the key.
        """
        return describe(run, gateway.dialect, table, catalog)

    @server.tool()
    def explain_query(sql: str) -> str:
        """The database's plan for one read-only SQL query, without running it (EXPLAIN, never
        ANALYZE), as the database prints it: each step with its estimated rows, the join methods,
        and the join and filter conditions.

        The query passes the same read-only checks as query_database; the plan is of the query
        as query_database would run it, row cap included.
        """
        return run_explain(gateway, sql)

    if config.context:
        register_context_tools(server, run, catalog, gateway.dialect)
    if config.verifiers and verifier_tools:
        register_verifier_tools(server, run, catalog, workspace, gateway.dialect)
    return server
