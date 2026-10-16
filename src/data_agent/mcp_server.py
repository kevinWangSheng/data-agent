"""The data agent's MCP server (stdio). Every tool reaches the database through the gateway."""

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from data_agent.gateway import MAX_ROWS, DuckDBGateway, QueryBlocked, QueryFailed, QueryResult
from data_agent.run_config import RunConfig

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


def run_query(gateway: DuckDBGateway, sql: str, row_limit: int) -> QueryResult:
    """Run through the gateway; blocks and database errors become readable tool errors."""
    try:
        return gateway.query(sql, row_limit)
    except QueryBlocked as exc:
        raise ToolError(f"Query blocked by the read-only gateway: {exc}") from exc
    except QueryFailed as exc:
        raise ToolError(f"Query error: {exc}") from exc


def format_rows(result: QueryResult) -> str:
    if not result.rows:
        return "Query returned 0 rows"
    lines = [" | ".join(result.columns)]
    lines.append("-" * len(lines[0]))
    lines.extend(" | ".join(str(value) for value in row) for row in result.rows)
    lines.append(f"({len(result.rows)} rows)")
    return "\n".join(lines)


def build_server(gateway: DuckDBGateway, config: RunConfig) -> MCPServer:
    """The base tools are served in every config. A layer's tools (context strategy, verifiers)
    are registered only when `config` turns that layer on, so a run with it off cannot see them."""
    server = MCPServer("data-agent")

    @server.tool()
    def query_database(sql: str, row_limit: int = 1000) -> str:
        """Run one read-only SQL query (SELECT only) and return the rows as text.

        DDL, DML, multiple statements and file or network functions are blocked. At most
        `row_limit` rows come back (default 1000, max 10000).
        """
        return format_rows(run_query(gateway, sql, row_limit))

    @server.tool()
    def list_tables(name_contains: str | None = None) -> str:
        """List the database's tables and views, one per line with their column names.

        Pass `name_contains` to keep only tables whose name contains that text (ignores case).
        """
        tables = run_query(gateway, LIST_TABLES_SQL, row_limit=MAX_ROWS).rows
        needle = (name_contains or "").strip().lower()
        lines = [
            f"{schema}.{name} ({kind.lower()}): {columns}"
            for schema, name, kind, columns in tables
            if needle in name.lower()
        ]
        if not lines:
            return f"No tables found whose name contains {needle!r}" if needle else "No tables"
        return "\n".join(lines)

    return server
