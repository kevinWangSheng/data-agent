"""The data agent's MCP server (stdio). Every tool reaches the database through the gateway."""

from functools import partial
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from data_agent.context_tools import register_context_tools
from data_agent.db_facts import describe, plan_text, row_estimates, rows_label, timeout_report
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
       string_agg(c.column_name, ', ' ORDER BY c.ordinal_position) AS columns
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


def run_explain(gateway: Gateway, sql: str) -> str:
    """The plan for `sql`, which passes the gateway's check first; errors as in run_query."""
    try:
        return plan_text(gateway.explain(sql))
    except (QueryBlocked, QueryFailed) as exc:
        raise tool_error(exc) from exc


def format_rows(result: QueryResult) -> str:
    if not result.rows:
        return "Query returned 0 rows"
    lines = [" | ".join(result.columns)]
    lines.append("-" * len(lines[0]))
    lines.extend(" | ".join(str(value) for value in row) for row in result.rows)
    lines.append(f"({len(result.rows)} rows)")
    return "\n".join(lines)


def table_kind(kind: str, estimate: int | None) -> str:
    """`base table, ~N rows`; a view, which has no rows of its own, stays `view`."""
    kind = kind.lower()
    return kind if kind == "view" else f"{kind}, {rows_label(estimate)}"


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
            explain = partial(run_explain, gateway)
            raise ToolError(timeout_report(str(exc), sql, gateway.dialect, explain, run)) from exc
        except (QueryBlocked, QueryFailed) as exc:
            raise tool_error(exc) from exc

    @server.tool()
    def list_tables(name_contains: str | None = None) -> str:
        """List the database's tables and views, one per line with their column names.

        Each table shows the database's own row estimate (`~N rows`, from its statistics, not
        a count; `rows unknown` when it has none). Views have no estimate. Pass `name_contains`
        to keep only tables whose name contains that text (ignores case).
        """
        tables = run_query(gateway, LIST_TABLES_SQL, row_limit=MAX_ROWS).rows
        estimates = row_estimates(run, gateway.dialect)
        needle = (name_contains or "").strip().lower()
        lines = [
            f"{schema}.{name} ({table_kind(kind, estimates.get((schema, name)))}): {columns}"
            for schema, name, kind, columns in tables
            if needle in name.lower()
        ]
        if not lines:
            return f"No tables found whose name contains {needle!r}" if needle else "No tables"
        return "\n".join(lines)

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
        return describe(run, gateway.dialect, table)

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
        register_context_tools(server, run, gateway.dialect)
    if config.verifiers and verifier_tools:
        register_verifier_tools(server, partial(run_query, gateway), workspace, gateway.dialect)
    return server
