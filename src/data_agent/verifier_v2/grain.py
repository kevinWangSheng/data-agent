"""`grain_facts` (spec "工具"): NULL and duplicate counts of a model's grain key, plus whether the
snapshot's YML (never the agent's) declares a grain that would exempt the duplicates. The verdict
is the verifier's; the task text may also exempt, which the tool cannot read.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from data_agent.db_facts import LoadCatalog
from data_agent.verifier_v2.db import Queryable, catalog_loader, column_named, find_model, quote
from data_agent.verifier_v2.project import (
    ModelEntry,
    dbt_projects,
    model_entries,
    unique_not_null_columns,
)


def grain_facts(
    workspace: Path,
    snapshot: Path,
    gateway: Queryable,
    model: str,
    key_columns: Sequence[str],
    catalog: LoadCatalog | None = None,
) -> dict[str, Any]:
    """`{"relation", "key_columns", "null_rows", "duplicate_rows", "exemption"}`. The key is the
    given columns plus the snapshot YML's unique+not_null columns. `duplicate_rows` counts rows
    beyond the first in each key group (rows with a NULL key are counted in `null_rows` only).
    `exemption` is None, or `{"source": "snapshot_yml", "path", "columns"}` (path relative to the
    snapshot) when the snapshot YML declares a different or finer grain than the key."""
    load = catalog_loader(gateway, catalog)
    table = find_model(load, workspace, model)
    entries: list[ModelEntry] = []
    for project in dbt_projects(snapshot):
        entries += model_entries(project, model)
    keys: dict[str, None] = {}
    for column in [*key_columns, *unique_not_null_columns(entries)]:
        keys.setdefault(column_named(table, column), None)
    if not keys:
        raise ValueError("grain_facts needs at least one key column")
    quoted = [quote(column) for column in keys]
    any_null = " OR ".join(f"{column} IS NULL" for column in quoted)
    null_rows = _count(gateway, f"SELECT COUNT(*) FROM {table.sql_name} WHERE {any_null}")
    duplicate_rows = _count(
        gateway,
        f"SELECT COALESCE(SUM(n - 1), 0) FROM (SELECT COUNT(*) AS n FROM {table.sql_name} "
        f"WHERE NOT ({any_null}) GROUP BY {', '.join(quoted)} HAVING COUNT(*) > 1) AS d",
    )
    return {
        "relation": table.ref,
        "key_columns": list(keys),
        "null_rows": null_rows,
        "duplicate_rows": duplicate_rows,
        "exemption": _exemption(entries, list(keys), snapshot),
    }


def _count(gateway: Queryable, sql: str) -> int:
    rows = gateway.query(sql, 1).rows
    return int(rows[0][0]) if rows and rows[0][0] is not None else 0


def _exemption(entries: list[ModelEntry], keys: list[str], snapshot: Path) -> dict[str, Any] | None:
    """The first unique declaration in the snapshot YML whose columns are not the key itself: a
    superset declares a finer grain, another set defines the grain with different columns. The
    path is relative to the snapshot (the verifier cannot read the snapshot itself)."""
    wanted = {key.lower() for key in keys}
    candidates: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        for columns in entry.unique_declarations():
            declared = {column.lower() for column in columns}
            if declared == wanted:
                continue
            # Finer grains (supersets of the key) first, then other declarations.
            rank = 0 if declared > wanted else 1
            candidates.append(
                (
                    rank,
                    {
                        "source": "snapshot_yml",
                        "path": str(entry.path.relative_to(snapshot)),
                        "columns": columns,
                    },
                )
            )
    if not candidates:
        return None
    return min(candidates, key=lambda c: c[0])[1]
