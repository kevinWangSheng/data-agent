"""MCP server (stdio) around DAB's own query tools, for the DAB runner (ADR-0005).

DAB's `QueryDBTool` and `ListDBTool` are imported from a local DAB clone (no DAB code is in this
repo) and served under our tool names, `query_database` and `list_tables`, so the agent and its
Skills see the same tools as on any other database. Their descriptions are DAB's, read from its
tool specs at start-up.

Every SQL statement passes the read-only gateway's AST check and row cap before it reaches DAB's
tool (CODING_STANDARDS "Never bypass the read-only gateway"). MongoDB queries are DAB's JSON `find`
requests, not SQL, and go to DAB's tool as given.

    python -m evals.dab_server --dab-root DAB --db-config DAB/query_x/db_config.yaml --log LOG
"""

import argparse
import json
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from data_agent.engine import inject_limit, validate_sql
from data_agent.gateway import MAX_ROWS
from evals.dab import scaffold

# DAB db_type -> sqlglot dialect. MongoDB is the one type that is not SQL; any other type is
# refused, since the gateway could not check it.
SQL_DIALECTS = {"sqlite": "sqlite", "duckdb": "duckdb", "postgres": "postgres"}
NOT_SQL = frozenset({"mongo"})
# Tool results longer than this are cut, as DAB's own agent previews them (its PREVIEW_LENGTH).
RESULT_CHARS = 10_000


def governed(sql: str, dialect: str) -> str:
    verdict = validate_sql(sql, dialect=dialect)
    if not verdict.ok:
        reason = verdict.blocked_reason or "not a read-only query"
        raise ToolError(f"Query blocked by the read-only gateway: {reason}")
    return inject_limit(sql, max_rows=MAX_ROWS, dialect=dialect)


def reply(outcome: dict[str, Any]) -> str:
    """DAB's `{"success", "result"}` as tool output: a failure becomes a tool error."""
    if not outcome["success"]:
        raise ToolError(str(outcome["result"]))
    text = json.dumps(outcome["result"], ensure_ascii=False, default=str)
    if len(text) > RESULT_CHARS:
        note = f"(truncated: {len(text)} characters in total; narrow the query)"
        text = f"{text[:RESULT_CHARS]}\n... {note}"
    return text


def build_server(query_tool: Any, list_tool: Any) -> MCPServer:
    query_spec = query_tool.get_spec()["function"]
    list_spec = list_tool.get_spec()["function"]
    query_args = query_spec["parameters"]["properties"]
    list_args = list_spec["parameters"]["properties"]
    db_types = {name: client["db_type"] for name, client in query_tool.db_clients.items()}
    server = MCPServer("dab")

    @server.tool(name="query_database", description=query_spec["description"])
    def query_database(
        db_name: Annotated[str, Field(description=query_args["db_name"].get("description"))],
        sql: Annotated[str, Field(description=query_args["query"].get("description"))],
    ) -> str:
        db_type = db_types.get(db_name)
        if db_type in SQL_DIALECTS:
            sql = governed(sql, SQL_DIALECTS[db_type])
        elif db_type is not None and db_type not in NOT_SQL:
            raise ToolError(f"Query blocked: the read-only gateway cannot check {db_type} queries")
        # An unknown db_name goes on to DAB's tool, which rejects it without running anything.
        return reply(query_tool.exec({"db_name": db_name, "query": sql}))

    @server.tool(name="list_tables", description=list_spec["description"])
    def list_tables(
        db_name: Annotated[str, Field(description=list_args["db_name"].get("description"))],
    ) -> str:
        return reply(list_tool.exec({"db_name": db_name}))

    return server


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.dab_server")
    parser.add_argument("--dab-root", type=Path, required=True)
    parser.add_argument("--db-config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True, help="DAB's tool log (JSONL)")
    args = parser.parse_args()
    tools = scaffold(args.dab_root)
    config = str(args.db_config)
    # As in DAB's own agent: the query tool loads the Postgres and MongoDB dumps, and clean-up
    # drops them again.
    query_tool = tools.query_tool(
        log_path=args.log, name="query_db", db_config_path=config, check_load=True
    )
    list_tool = tools.list_tool(
        log_path=args.log, name="list_db", db_config_path=config, check_load=False
    )
    try:
        build_server(query_tool, list_tool).run("stdio")
    finally:
        query_tool.clean_up()
        list_tool.clean_up()


if __name__ == "__main__":
    main()
