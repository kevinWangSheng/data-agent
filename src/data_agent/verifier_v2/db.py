"""What the v2 tools need from the database: a gateway to query through and the catalog (ticket
39's cache when the MCP server provides it, a fresh read otherwise) to find a model's relation.
"""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from mcp.server.mcpserver.exceptions import ToolError

from data_agent.db_facts import LoadCatalog, Table, find_table, load_catalog
from data_agent.gateway import MAX_ROWS, QueryResult
from data_agent.verifier_v2.project import dbt_projects, model_aliases


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


def find_model(
    catalog: LoadCatalog,
    workspace: Path | None,
    model: str,
    aliases: Mapping[str, str | None] | None = None,
) -> Table:
    """The relation a model is built as: its own name, else the alias the workspace's dbt
    project gives it (from `aliases`, `workspace_aliases` of the caller, when given). Raises
    ToolError when neither is in the catalog."""
    tables = catalog()
    try:
        return find_table(tables, model, hint="the model is not built")
    except ToolError:
        if aliases is not None:
            alias = aliases.get(model)
        else:
            alias = workspace_alias(workspace, model) if workspace else None
        if alias is None or alias.lower() == model.lower():
            raise
        return find_table(tables, alias, hint=f"the model {model!r} is not built as {alias!r}")


def workspace_aliases(workspace: Path, models: Sequence[str]) -> dict[str, str | None]:
    """model -> the alias the first dbt project in `workspace` holding it gives it (None when
    it keeps its name), for each of `models` some project holds; each project's files and YML
    are read once, however many models are asked for."""
    found: dict[str, str | None] = {}
    for project in dbt_projects(workspace):
        missing = [model for model in models if model not in found]
        if not missing:
            break
        found.update(model_aliases(project, missing))
    return found


def workspace_alias(workspace: Path, model: str) -> str | None:
    """The alias the dbt project in `workspace` that holds `model` gives it; None without one."""
    return workspace_aliases(workspace, [model]).get(model)


def column_named(table: Table, name: str) -> str:
    """The table's own spelling of a column named case-insensitively; ToolError when absent."""
    for column, _ in table.columns:
        if column.lower() == name.strip().lower():
            return column
    raise ToolError(f"Error: {table.ref} has no column {name.strip()!r}")


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def scalar_count(gateway: Queryable, sql: str) -> int:
    """The single integer a counting statement returns; 0 for no row or NULL."""
    rows = gateway.query(sql, 1).rows
    return int(rows[0][0]) if rows and rows[0][0] is not None else 0


def duplicate_rows(gateway: Queryable, table: Table, columns: list[str], where: str = "") -> int:
    """Rows beyond the first in each group of equal `columns` (SQL expressions), optionally
    only rows matching `where`."""
    sql = (
        f"SELECT COALESCE(SUM(n - 1), 0) FROM (SELECT COUNT(*) AS n FROM {table.sql_name} "
        f"{f'WHERE {where} ' if where else ''}GROUP BY {', '.join(columns)} "
        "HAVING COUNT(*) > 1) AS d"
    )
    return scalar_count(gateway, sql)
