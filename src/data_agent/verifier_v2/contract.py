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
from data_agent.verifier_v2.db import Queryable, catalog_loader, duplicate_rows, find_model, quote
from data_agent.verifier_v2.model_diff import model_diff
from data_agent.verifier_v2.project import (
    YmlFile,
    dbt_projects,
    declared_columns,
    entries_in,
    model_name,
    unreadable,
    yml_documents,
)


def contract_check(
    workspace: Path,
    snapshot: Path,
    gateway: Queryable,
    models: Sequence[str],
    task_columns: Mapping[str, Sequence[str]] | None = None,
    catalog: LoadCatalog | None = None,
) -> dict[str, dict[str, Any]]:
    """model -> `{"exists", "columns", "window"}`, each `{"status": PASS|FAIL|N/A, "evidence"}`.
    `columns` adds `source` ("task" or "snapshot_yml"), `missing` and `extra`; `window` adds
    `functions`, the changed window functions it checked. `task_columns` maps a model to the
    output columns the task text requires; `catalog` is the server's cache. A model may be
    named as in the project, with a schema qualifier, or by the alias it is built under; the
    diff and the snapshot YML are looked up under its project name."""
    load = catalog_loader(gateway, catalog)
    diffs = {d["model"]: d for d in model_diff(workspace, snapshot, gateway.dialect)}
    documents = [
        document for project in dbt_projects(snapshot) for document in yml_documents(project)
    ]
    results: dict[str, dict[str, Any]] = {}
    for model in models:
        try:
            table: Table | None = find_model(load, workspace, model)
            exists = {"status": "PASS", "evidence": f"{table.ref} exists ({table.kind})"}
        except ToolError as exc:
            table = None
            exists = {"status": "FAIL", "evidence": str(exc)}
        name = model_name(workspace, model, table.name if table else None)
        results[model] = {
            "exists": exists,
            "columns": _columns_check(
                snapshot, documents, name, table, (task_columns or {}).get(model)
            ),
            "window": _window_check(gateway, table, diffs.get(name)),
        }
    return results


def _columns_check(
    snapshot: Path,
    documents: list[YmlFile],
    model: str,
    table: Table | None,
    task: Sequence[str] | None,
) -> dict[str, Any]:
    snapshot_columns = declared_columns(entries_in(documents, model))
    not_read = unreadable(documents, snapshot)
    expected: dict[str, None] = {}
    for column in [*(task or []), *snapshot_columns]:
        expected.setdefault(column.strip(), None)
    if task:
        source = "task"
    elif snapshot_columns:
        source = "snapshot_yml"
    elif not_read:
        return {
            "status": "N/A",
            "evidence": "not checked: the task names no columns for this model and the "
            f"snapshot YML could not be read: {'; '.join(not_read)}",
            "source": None,
            "missing": [],
            "extra": [],
        }
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
    if not_read:
        parts.append(
            f"snapshot YML could not be read, its columns not included: {'; '.join(not_read)}"
        )
    return {
        "status": "FAIL" if missing else "PASS",
        "evidence": "; ".join(parts),
        "source": source,
        "missing": missing,
        "extra": extra,
    }


def _window_check(
    gateway: Queryable, table: Table | None, diff: dict[str, Any] | None
) -> dict[str, Any]:
    if diff is not None and not diff["parsed"]:
        return {
            "status": "N/A",
            "evidence": "not checked: the model's SQL was not parsed (Jinja statements or SQL "
            "sqlglot could not read), so its window functions are unknown; see model_diff's "
            "line diff",
            "functions": [],
        }
    windows = [
        c["after"]
        for c in (diff["changes"] if diff else [])
        if c["category"] == "window" and c["kind"] in ("added", "changed")
    ]
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
    columns = [_as_model_column(table, e) for e in [*partition, *ordering]]
    return duplicate_rows(gateway, table, columns)


def _as_model_column(table: Table, expression: str) -> str:
    """A bare column name quoted as the built model spells it; any other expression as is."""
    for column, _ in table.columns:
        if column.lower() == expression.strip('"').lower():
            return quote(column)
    return expression
