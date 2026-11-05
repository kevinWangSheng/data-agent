"""`contract_check` (spec "工具"): per model, whether it exists, whether its columns meet the
contract (the task's required columns plus the snapshot's YML, never the agent's YML), and
whether a window function the agent added or changed orders its rows uniquely.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from data_agent.db_facts import LoadCatalog, Table
from data_agent.gateway import QueryBlocked, QueryFailed
from data_agent.verifier_v2 import sql as model_sql
from data_agent.verifier_v2.db import Queryable, catalog_loader, find_model, quote
from data_agent.verifier_v2.model_diff import model_diff
from data_agent.verifier_v2.project import dbt_projects, declared_columns, model_entries


def contract_check(
    workspace: Path,
    snapshot: Path,
    gateway: Queryable,
    models: Sequence[str],
    task_columns: Mapping[str, Sequence[str]] | None = None,
    catalog: LoadCatalog | None = None,
    diffs: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """model -> `{"exists", "columns", "window"}`, each `{"status": PASS|FAIL|N/A, "evidence"}`.
    `columns` adds `source` ("task" or "snapshot_yml"), `missing` and `extra`; `window` adds
    `functions`, the changed window functions it checked. `task_columns` maps a model to the
    output columns the task text requires. `diffs` is `model_diff`'s output when the caller has
    it; `catalog` the server's cache."""
    load = catalog_loader(gateway, catalog)
    if diffs is None:
        diffs = model_diff(workspace, snapshot, gateway.dialect)
    windows = {
        d["model"]: [
            c["after"]
            for c in d["changes"]
            if c["category"] == "window" and c["kind"] in ("added", "changed")
        ]
        for d in diffs
    }
    results: dict[str, dict[str, Any]] = {}
    for model in models:
        try:
            table: Table | None = find_model(load, workspace, model)
            exists = {"status": "PASS", "evidence": f"{table.ref} exists ({table.kind})"}
        except ToolError as exc:
            table = None
            exists = {"status": "FAIL", "evidence": str(exc)}
        results[model] = {
            "exists": exists,
            "columns": _columns_check(snapshot, model, table, (task_columns or {}).get(model)),
            "window": _window_check(gateway, table, windows.get(model, [])),
        }
    return results


def _columns_check(
    snapshot: Path, model: str, table: Table | None, task: Sequence[str] | None
) -> dict[str, Any]:
    snapshot_columns: list[str] = []
    for project in dbt_projects(snapshot):
        snapshot_columns += declared_columns(model_entries(project, model))
    expected: dict[str, None] = {}
    for column in [*(task or []), *snapshot_columns]:
        expected.setdefault(column.strip(), None)
    if task:
        source = "task"
    elif snapshot_columns:
        source = "snapshot_yml"
    else:
        return {
            "status": "N/A",
            "evidence": "no contract: the task names no columns for this model and the "
            "snapshot's YML has no entry for it (the agent's YML is not a contract)",
            "source": None,
            "missing": [],
            "extra": [],
        }
    if table is None:
        return {
            "status": "FAIL",
            "evidence": "the model is not built, so its columns cannot be checked",
            "source": source,
            "missing": list(expected),
            "extra": [],
        }
    actual = {column.lower(): column for column, _ in table.columns}
    missing = [column for column in expected if column.lower() not in actual]
    wanted = {column.lower() for column in expected}
    extra = [column for column, _ in table.columns if column.lower() not in wanted]
    parts = [f"contract from {source}: {', '.join(expected)}"]
    if task and snapshot_columns:
        parts[0] += " (task columns plus the snapshot YML's)"
    parts.append(f"missing: {', '.join(missing) if missing else 'none'}")
    parts.append(f"extra: {', '.join(extra) if extra else 'none'}")
    return {
        "status": "FAIL" if missing else "PASS",
        "evidence": "; ".join(parts),
        "source": source,
        "missing": missing,
        "extra": extra,
    }


def _window_check(gateway: Queryable, table: Table | None, windows: list[str]) -> dict[str, Any]:
    if not windows:
        return {
            "status": "N/A",
            "evidence": "no window function was added or changed in this model",
            "functions": [],
        }
    if table is None:
        return {
            "status": "N/A",
            "evidence": "the model is not built, so its window ordering cannot be checked",
            "functions": windows,
        }
    lines: list[str] = []
    failed = checked = False
    for window in windows:
        parts = model_sql.window_parts(window, gateway.dialect)
        if parts is None:
            lines.append(f"{window}: no ORDER BY, so ordering uniqueness does not apply")
            continue
        partition, ordering = parts
        try:
            duplicates = _duplicate_orderings(gateway, table, partition, ordering)
        except (QueryBlocked, QueryFailed, ToolError) as exc:
            lines.append(f"{window}: could not be evaluated ({exc})")
            continue
        checked = True
        where = f"within PARTITION BY {', '.join(partition)}" if partition else "over the model"
        if duplicates:
            failed = True
            lines.append(
                f"{window}: {duplicates} rows share their ORDER BY value "
                f"({', '.join(ordering)}) {where}; the ordering is not unique"
            )
        else:
            lines.append(f"{window}: ORDER BY {', '.join(ordering)} is unique {where}")
    status = "FAIL" if failed else "PASS" if checked else "N/A"
    return {"status": status, "evidence": "; ".join(lines), "functions": windows}


def _duplicate_orderings(
    gateway: Queryable, table: Table, partition: list[str], ordering: list[str]
) -> int:
    """Rows beyond the first that share (partition, ORDER BY) values."""
    columns = ", ".join(_as_model_column(table, e) for e in [*partition, *ordering])
    sql = (
        f"SELECT COALESCE(SUM(n - 1), 0) FROM (SELECT COUNT(*) AS n FROM {table.sql_name} "
        f"GROUP BY {columns} HAVING COUNT(*) > 1) AS d"
    )
    rows = gateway.query(sql, 1).rows
    return int(rows[0][0]) if rows and rows[0][0] is not None else 0


def _as_model_column(table: Table, expression: str) -> str:
    """A bare column name quoted as the built model spells it; any other expression as is."""
    for column, _ in table.columns:
        if column.lower() == expression.strip('"').lower():
            return quote(column)
    return expression
