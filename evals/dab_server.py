"""MCP server (stdio) around DAB's own tools, for the DAB runner (ADR-0005).

DAB's `QueryDBTool`, `ListDBTool` and `ExecTool` are imported from a local DAB clone (no DAB code
is in this repo) and served as `query_database` and `list_tables` (our tool names, so the agent and
its Skills see the same tools as on any other database) and `execute_python`. Their descriptions
are DAB's, read from its tool specs at start-up.

As in DAB's own agent, every successful result is kept as a variable (`var_1`, `var_2`, ...) that
`execute_python` code can use, so rows from several databases can be combined outside them. A
result too long to show is saved as JSON under the workspace's `file_storage/`, its variable holds
that path, and the agent sees a preview. `execute_python` runs in DAB's Docker image
(`python-data:3.12`, built from DAB's Dockerfile) with the workspace as its working directory.

Every SQL statement passes the read-only gateway's AST check and row cap before it reaches DAB's
tool (CODING_STANDARDS "Never bypass the read-only gateway"). MongoDB queries are DAB's JSON `find`
requests, not SQL, and go to DAB's tool as given.

    python -m evals.dab_server --dab-root DAB --db-config DAB/query_x/db_config.yaml --log LOG \
        --workspace WORKSPACE
"""

import argparse
import json
import os
import threading
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
# Tool results longer than this are saved to a file and previewed, as in DAB's own agent (its
# PREVIEW_LENGTH); failures longer than this are cut.
RESULT_CHARS = 10_000
STORAGE_DIR = "file_storage"  # under the workspace, as DAB's agent names it
PRINT_NOTE = (
    "Earlier tool results are variables named as their result says (`var_1`, ...); a result "
    "saved to a file is that file's path, relative to the working directory. End the code by "
    "printing {marker} followed by the result as JSON, e.g. print({marker!r} + json.dumps(x)); "
    "the JSON after the last {marker} becomes this tool's result."
)


def governed(sql: str, dialect: str) -> str:
    verdict = validate_sql(sql, dialect=dialect)
    if not verdict.ok:
        reason = verdict.blocked_reason or "not a read-only query"
        raise ToolError(f"Query blocked by the read-only gateway: {reason}")
    return inject_limit(sql, max_rows=MAX_ROWS, dialect=dialect)


class Results:
    """Successful tool results by variable name, for `execute_python`.

    Tools run in worker threads; the lock serializes calls, which DAB's tools expect (its exec
    tool drives one container from one event loop).
    """

    def __init__(self, workspace: Path) -> None:
        self.storage = workspace / STORAGE_DIR
        self.variables: dict[str, Any] = {}
        self.lock = threading.Lock()

    def reply(self, outcome: dict[str, Any]) -> str:
        """DAB's `{"success", "result"}` as tool output: a failure becomes a tool error, a result
        is kept under the next variable name."""
        if not outcome["success"]:
            error = str(outcome["result"])
            if len(error) > RESULT_CHARS:
                error = f"{error[:RESULT_CHARS]}\n... (cut at {RESULT_CHARS} characters)"
            raise ToolError(error)
        text = json.dumps(outcome["result"], ensure_ascii=False, default=str)
        name = f"var_{len(self.variables) + 1}"
        if len(text) <= RESULT_CHARS:
            # Through JSON, so the value is plain data whatever types the database returned.
            self.variables[name] = json.loads(text)
            return f"Result `{name}`:\n{text}"
        self.save(f"{name}.json", text)
        path = f"{STORAGE_DIR}/{name}.json"
        self.variables[name] = path
        return (
            f"Result `{name}` is {len(text)} characters, saved in `{path}` (the value of "
            f"`{name}`); its first {RESULT_CHARS} characters:\n{text[:RESULT_CHARS]}"
        )

    def save(self, file_name: str, text: str) -> None:
        """Write a new file in the storage directory. execute_python code can change the workspace
        (it runs as root on it), so neither the directory nor the file may be an existing link or
        file: the write never leaves the workspace or overwrites anything."""
        try:
            self.storage.mkdir(exist_ok=True)
            directory = os.open(self.storage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                fd = os.open(file_name, flags, 0o644, dir_fd=directory)
            finally:
                os.close(directory)
        except OSError as exc:
            raise ToolError(f"Could not save the result in {STORAGE_DIR}/: {exc}") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)


def build_server(
    query_tool: Any, list_tool: Any, exec_tool: Any, marker: str, workspace: Path
) -> MCPServer:
    query_spec = query_tool.get_spec()["function"]
    list_spec = list_tool.get_spec()["function"]
    exec_spec = exec_tool.get_spec()["function"]
    query_args = query_spec["parameters"]["properties"]
    list_args = list_spec["parameters"]["properties"]
    exec_args = exec_spec["parameters"]["properties"]
    db_types = {name: client["db_type"] for name, client in query_tool.db_clients.items()}
    results = Results(workspace)
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
        with results.lock:
            return results.reply(query_tool.exec({"db_name": db_name, "query": sql}))

    @server.tool(name="list_tables", description=list_spec["description"])
    def list_tables(
        db_name: Annotated[str, Field(description=list_args["db_name"].get("description"))],
    ) -> str:
        with results.lock:
            return results.reply(list_tool.exec({"db_name": db_name}))

    exec_description = f"{exec_spec['description']}\n\n{PRINT_NOTE.format(marker=marker)}"

    @server.tool(name="execute_python", description=exec_description)
    def execute_python(
        code: Annotated[str, Field(description=exec_args["code"].get("description"))],
    ) -> str:
        with results.lock:
            env = dict(results.variables)
            return results.reply(exec_tool.exec({"code": code, "env": env}))

    return server


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.dab_server")
    parser.add_argument("--dab-root", type=Path, required=True)
    parser.add_argument("--db-config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True, help="DAB's tool log (JSONL)")
    parser.add_argument("--workspace", type=Path, required=True, help="execute_python's directory")
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
    # Starts DAB's container now: without Docker the server fails to start, an infra error.
    exec_tool = tools.exec_tool(
        log_path=str(args.log), name="execute_python", work_dir=args.workspace.resolve()
    )
    try:
        server = build_server(query_tool, list_tool, exec_tool, tools.result_marker, args.workspace)
        server.run("stdio")
    finally:
        query_tool.clean_up()
        list_tool.clean_up()
        exec_tool.clean_up()


if __name__ == "__main__":
    main()
