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
requests, not SQL, and go to DAB's tool as given. For the same reason `list_tables` lists a SQL
database's tables with a query of ours through the gateway and DAB's query tool (DAB's list tool
would send its own SQL); only MongoDB collections are listed by DAB's list tool.

Every query is also stopped after the gateway's timeout (`QueryTimeout`), in the database where
there is one, and fails with the gateway's message; the next query runs as usual.

    python -m evals.dab_server --dab-root DAB --db-config DAB/query_x/db_config.yaml --log LOG \
        --workspace WORKSPACE
"""

import argparse
import contextlib
import functools
import importlib
import json
import math
import os
import threading
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from data_agent.engine import inject_limit, validate_sql
from data_agent.gateway import MAX_ROWS, TIMEOUT_SECONDS
from evals.dab import scaffold

# DAB db_type -> sqlglot dialect. MongoDB is the one type that is not SQL; any other type is
# refused, since the gateway could not check it.
SQL_DIALECTS = {"sqlite": "sqlite", "duckdb": "duckdb", "postgres": "postgres"}
NOT_SQL = frozenset({"mongo"})
# The tables DAB's list tool returns for each SQL type, as a query whose first column is the name.
LIST_TABLES = {
    "sqlite": "SELECT name FROM sqlite_master WHERE type = 'table'",
    "duckdb": "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'",
    "postgres": "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'",
}
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


# DAB's query module for each database type. Every call opens a new connection through the name the
# module binds its driver to, so wrapping that name reaches every connection without changing DAB.
DB_UTILS = "common_scaffold.tools.db_utils"
# The driver errors our timeout raises in Postgres and MongoDB, as DAB's error text names them (it
# returns text only, so text is what there is to match): Postgres's statement_timeout (in
# SQLAlchemy's wording), and MongoDB's maxTimeMS or pymongo's socket giving up at `timeoutMS`.
SERVER_TIMEOUTS = ("(psycopg2.errors.QueryCanceled)", "(ExecutionTimeout)", "(NetworkTimeout)")


class Interruptible:
    """A driver module (sqlite3, duckdb) whose `connect` hands each new connection to `opened`;
    everything else is the module's own."""

    def __init__(self, driver: ModuleType, opened: Callable[[Any], None]) -> None:
        self._driver = driver
        self._opened = opened

    def connect(self, *args: Any, **kwargs: Any) -> Any:
        con = self._driver.connect(*args, **kwargs)
        self._opened(con)
        return con

    def __getattr__(self, name: str) -> Any:
        return getattr(self._driver, name)


class StatementTimeout:
    """The sqlalchemy module, with every engine's connections opened with `statement_timeout`:
    added to the connection's options (where DAB's URL makes the session read-only), as the
    gateway does."""

    def __init__(self, sqlalchemy: ModuleType, timeout_ms: int) -> None:
        self._sqlalchemy = sqlalchemy
        self._timeout_ms = timeout_ms

    def create_engine(self, *args: Any, **kwargs: Any) -> Any:
        engine = self._sqlalchemy.create_engine(*args, **kwargs)
        self._sqlalchemy.event.listen(engine, "do_connect", self._add_timeout)
        return engine

    def _add_timeout(self, dialect: Any, record: Any, cargs: Any, cparams: dict[str, Any]) -> None:
        given = str(cparams.get("options") or "")
        cparams["options"] = f"{given} -c statement_timeout={self._timeout_ms}".strip()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sqlalchemy, name)


class QueryTimeout:
    """Stops each query DAB's tools run after `seconds`, in the database where there is one:
    Postgres connections get `statement_timeout`, MongoDB clients `timeoutMS` (pymongo sends it as
    each operation's `maxTimeMS`). SQLite and DuckDB run in this process, so a timer interrupts the
    connections the call opened. A stopped query fails with the gateway's message."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._lock = threading.Lock()
        self._opened: list[Any] | None = None  # connections the running call opened

    def install(self) -> None:
        """Wrap the drivers in DAB's query modules (imported with its tools). A module without the
        expected name fails here, at start-up, rather than leaving its queries unbounded."""
        timeout_ms = max(1, int(self.seconds * 1000))
        wrappers: dict[str, tuple[str, Callable[[Any], Any]]] = {
            "sqlite_utils": ("sqlite3", lambda driver: Interruptible(driver, self._track)),
            "duckdb_utils": ("duckdb", lambda driver: Interruptible(driver, self._track)),
            "postgres_utils": ("sqlalchemy", lambda driver: StatementTimeout(driver, timeout_ms)),
            "mongo_utils": (
                "MongoClient",
                lambda client: functools.partial(client, timeoutMS=timeout_ms),
            ),
        }
        for module_name, (attribute, wrap) in wrappers.items():
            module = importlib.import_module(f"{DB_UTILS}.{module_name}")
            setattr(module, attribute, wrap(getattr(module, attribute)))

    def _track(self, con: Any) -> None:
        with self._lock:
            if self._opened is not None:
                self._opened.append(con)

    def _interrupt(self) -> bool:
        """Interrupt the running call's connections; False if it opened none (Postgres, MongoDB)."""
        with self._lock:
            for con in self._opened or []:
                # The query may have just finished and its connection closed; then nothing runs.
                with contextlib.suppress(Exception):
                    con.interrupt()
            return bool(self._opened)

    def run(self, call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """`call`'s DAB outcome; one stopped by the timeout fails with the gateway's message, any
        other failure keeps its own."""
        interrupted = threading.Event()

        def expire() -> None:
            if self._interrupt():
                interrupted.set()

        with self._lock:
            self._opened = []
        timer = threading.Timer(self.seconds, expire)
        timer.start()
        try:
            outcome = call()
        finally:
            timer.cancel()
            with self._lock:
                self._opened = None
        error = "" if outcome["success"] else str(outcome["result"])
        if error and (interrupted.is_set() or any(name in error for name in SERVER_TIMEOUTS)):
            return {"success": False, "result": f"Query timed out after {self.seconds:g}s"}
        return outcome


def governed(sql: str, dialect: str) -> str:
    verdict = validate_sql(sql, dialect=dialect)
    if not verdict.ok:
        reason = verdict.blocked_reason or "not a read-only query"
        raise ToolError(f"Query blocked by the read-only gateway: {reason}")
    return inject_limit(sql, max_rows=MAX_ROWS, dialect=dialect)


def finite(value: Any) -> Any:
    """`value` with NaN and infinities as None. DAB's exec tool writes each variable's repr into
    the code, where `nan` or `inf` is an undefined name."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [finite(v) for v in value]
    return value


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
        text = json.dumps(finite(outcome["result"]), ensure_ascii=False, default=str)
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
    query_tool: Any,
    list_tool: Any,
    exec_tool: Any,
    marker: str,
    workspace: Path,
    timeout: QueryTimeout,
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

    def checkable(db_name: str) -> str | None:
        """`db_name`'s type, None if DAB does not know it; a type the gateway cannot check is
        refused."""
        db_type = db_types.get(db_name)
        if db_type is not None and db_type not in SQL_DIALECTS and db_type not in NOT_SQL:
            raise ToolError(f"Query blocked: the read-only gateway cannot check {db_type} queries")
        return db_type

    @server.tool(name="query_database", description=query_spec["description"])
    def query_database(
        db_name: Annotated[str, Field(description=query_args["db_name"].get("description"))],
        sql: Annotated[str, Field(description=query_args["query"].get("description"))],
    ) -> str:
        db_type = checkable(db_name)
        if db_type in SQL_DIALECTS:
            sql = governed(sql, SQL_DIALECTS[db_type])
        # An unknown db_name goes on to DAB's tool, which rejects it without running anything.
        with results.lock:
            args = {"db_name": db_name, "query": sql}
            return results.reply(timeout.run(lambda: query_tool.exec(args)))

    @server.tool(name="list_tables", description=list_spec["description"])
    def list_tables(
        db_name: Annotated[str, Field(description=list_args["db_name"].get("description"))],
    ) -> str:
        db_type = checkable(db_name)
        if db_type not in SQL_DIALECTS:
            with results.lock:
                return results.reply(timeout.run(lambda: list_tool.exec({"db_name": db_name})))
        sql = governed(LIST_TABLES[db_type], SQL_DIALECTS[db_type])
        with results.lock:
            outcome = timeout.run(lambda: query_tool.exec({"db_name": db_name, "query": sql}))
            if outcome["success"]:
                # Rows to names, as DAB's list tool returns them.
                outcome["result"] = [next(iter(row.values())) for row in outcome["result"]]
            return results.reply(outcome)

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
    parser.add_argument(
        "--query-timeout", type=float, default=TIMEOUT_SECONDS, help="seconds a query may run"
    )
    args = parser.parse_args()
    tools = scaffold(args.dab_root)
    timeout = QueryTimeout(args.query_timeout)
    timeout.install()
    config = str(args.db_config)
    # The runner loads the Postgres and MongoDB dumps before the session and drops them after it
    # (`evals.dab.load_databases`): a large dump loads for longer than the SDK waits for this
    # server to start. So the tools here only connect, and their clean-up (which drops the
    # databases) is never called.
    query_tool = tools.query_tool(
        log_path=args.log, name="query_db", db_config_path=config, check_load=False
    )
    list_tool = tools.list_tool(
        log_path=args.log, name="list_db", db_config_path=config, check_load=False
    )
    # Starts DAB's container now: without Docker the server fails to start, an infra error.
    exec_tool = tools.exec_tool(
        log_path=str(args.log), name="execute_python", work_dir=args.workspace.resolve()
    )
    try:
        server = build_server(
            query_tool, list_tool, exec_tool, tools.result_marker, args.workspace, timeout
        )
        server.run("stdio")
    finally:
        exec_tool.clean_up()


if __name__ == "__main__":
    main()
