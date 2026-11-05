"""What the v2 tools need from the database: a gateway to query through and the catalog (ticket
39's cache when the MCP server provides it, a fresh read otherwise) to find a model's relation.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from mcp.server.mcpserver.exceptions import ToolError

from data_agent.db_facts import LoadCatalog, Table, find_table, load_catalog
from data_agent.gateway import MAX_ROWS, QueryResult
from data_agent.verifier_v2.project import dbt_projects, model_files
from data_agent.verifier_v2.project import model_alias as project_alias

DOUBLE_TYPE = {"duckdb": "DOUBLE", "postgres": "DOUBLE PRECISION"}


class Queryable(Protocol):
    """A gateway: every statement passes the read-only check (`Gateway` satisfies this)."""

    dialect: str

    def query(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult: ...


class RunQueryable:
    """A `Queryable` over the MCP server's `run` (which turns gateway errors into ToolErrors)."""

    def __init__(self, run: Callable[[str, int], QueryResult], dialect: str) -> None:
        self._run = run
        self.dialect = dialect

    def query(self, sql: str, row_limit: int = MAX_ROWS) -> QueryResult:
        return self._run(sql, row_limit)


def catalog_loader(gateway: Queryable, catalog: LoadCatalog | None) -> LoadCatalog:
    if catalog is not None:
        return catalog
    tables: list[Table] | None = None

    def load() -> list[Table]:
        nonlocal tables
        if tables is None:
            tables = load_catalog(gateway.query)
        return tables

    return load


def find_model(catalog: LoadCatalog, workspace: Path | None, model: str) -> Table:
    """The relation a model is built as: its own name, else the alias the workspace's dbt
    project gives it. Raises ToolError when neither is in the catalog."""
    tables = catalog()
    try:
        return find_table(tables, model, hint="the model is not built")
    except ToolError:
        alias = model_alias(workspace, model) if workspace else None
        if alias is None or alias.lower() == model.lower():
            raise
        return find_table(tables, alias, hint=f"the model {model!r} is not built as {alias!r}")


def model_alias(workspace: Path, model: str) -> str | None:
    for project in dbt_projects(workspace):
        if model in model_files(project):
            return project_alias(project, model)
    return None


def column_named(table: Table, name: str) -> str:
    """The table's own spelling of a column named case-insensitively; ToolError when absent."""
    for column, _ in table.columns:
        if column.lower() == name.strip().lower():
            return column
    raise ToolError(f"Error: {table.ref} has no column {name.strip()!r}")


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def double_type(dialect: str) -> str:
    return DOUBLE_TYPE.get(dialect, "DOUBLE PRECISION")
