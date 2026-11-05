"""`model_diff` (spec "工具"): every new or changed model between the snapshot and the workspace,
with its changes by category. Facts only, no judgment; unchanged models and unchanged logic are
left out.

The snapshot side (each project's configuration, model files, their text and resolved config)
is read once per process (ticket 52): the snapshot does not change after it is taken. The
workspace side is read on every call.
"""

import difflib
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from data_agent.verifier_v2 import sql as model_sql
from data_agent.verifier_v2.project import (
    ModelConfig,
    dbt_projects,
    model_config,
    model_files,
    project_config,
    read_text,
    sql_config_call,
)


def model_diff(workspace: Path, snapshot: Path, dialect: str = "duckdb") -> list[dict[str, Any]]:
    """One entry per new, changed or removed model, sorted by name: `{"model", "project",
    "status", "parsed", "changes", "text_diff"}`. A change is `{"category", "kind", "before",
    "after"}`. A model whose SQL has Jinja statements or does not parse gets `parsed=False`, no
    changes and a unified `text_diff`."""
    diffs: list[dict[str, Any]] = []
    for project in dbt_projects(workspace):
        relative = project.relative_to(workspace)
        snapshot_project = snapshot / relative
        after_config = project_config(project)
        after_files = model_files(project, after_config)
        before_models = _snapshot_models(snapshot_project)
        for name in sorted(set(after_files) | set(before_models)):
            after = after_files.get(name)
            after_text = read_text(after) if after else None
            after_cfg = model_config(after_config, project, after, after_text) if after else None
            before_text, before_cfg = before_models.get(name, (None, None))
            if after_text == before_text and after_cfg == before_cfg:
                continue
            entry = _entry(name, str(relative), before_text, after_text, dialect)
            entry["changes"] += _config_changes(before_cfg, after_cfg)
            entry["changes"] += _config_call_changes(before_text, after_text)
            diffs.append(entry)
    return diffs


_snapshot_lock = threading.Lock()
_snapshot_cache: dict[Path, dict[str, tuple[str, ModelConfig]]] = {}


def _snapshot_models(snapshot_project: Path) -> dict[str, tuple[str, ModelConfig]]:
    """model name -> (SQL text, configuration) in a snapshot project; empty when the snapshot
    has no such project. Read once per process."""
    with _snapshot_lock:
        models = _snapshot_cache.get(snapshot_project)
        if models is None:
            models = {}
            if snapshot_project.is_dir():
                config = project_config(snapshot_project)
                for name, path in model_files(snapshot_project, config).items():
                    text = read_text(path)
                    models[name] = (text, model_config(config, snapshot_project, path, text))
            _snapshot_cache[snapshot_project] = models
        return models


def _entry(
    model: str, project: str, before: str | None, after: str | None, dialect: str
) -> dict[str, Any]:
    status = {"added": "new", "removed": "removed"}.get(_kind(before, after), "changed")
    entry: dict[str, Any] = {
        "model": model,
        "project": project,
        "status": status,
        "parsed": True,
        "changes": [],
        "text_diff": None,
    }
    if before == after:
        # Configuration-only change; the SQL itself is untouched.
        entry["parsed"] = not model_sql.has_jinja_statements(after or "")
        return entry
    before_tree = model_sql.parse(before, dialect) if before is not None else None
    after_tree = model_sql.parse(after, dialect) if after is not None else None
    if (before is not None and before_tree is None) or (after is not None and after_tree is None):
        entry["parsed"] = False
        entry["text_diff"] = _text_diff(model, before, after)
        return entry
    before_facts = model_sql.facts(before_tree, dialect) if before_tree is not None else {}
    after_facts = model_sql.facts(after_tree, dialect) if after_tree is not None else {}
    for category in model_sql.CATEGORIES:
        entry["changes"] += _category_changes(
            category,
            before_facts.get(category, Counter()),
            after_facts.get(category, Counter()),
        )
    return entry


def _category_changes(
    category: str, before: Counter[str], after: Counter[str]
) -> list[dict[str, Any]]:
    removed = sorted((before - after).elements())
    added = sorted((after - before).elements())
    if len(removed) == 1 and len(added) == 1:
        return [_change(category, "changed", removed[0], added[0])]
    return [_change(category, "removed", item, None) for item in removed] + [
        _change(category, "added", None, item) for item in added
    ]


def _change(category: str, kind: str, before: str | None, after: str | None) -> dict[str, Any]:
    return {"category": category, "kind": kind, "before": before, "after": after}


def _config_changes(before: ModelConfig | None, after: ModelConfig | None) -> list[dict[str, Any]]:
    before_lines = before.lines() if before else {}
    after_lines = after.lines() if after else {}
    changes: list[dict[str, Any]] = []
    for key in sorted(set(before_lines) | set(after_lines)):
        old, new = before_lines.get(key), after_lines.get(key)
        if old == new:
            continue
        changes.append(_change("config", _kind(old, new), old, new))
    return changes


def _config_call_changes(before: str | None, after: str | None) -> list[dict[str, Any]]:
    """The model's own `{{ config(...) }}`, compared as text."""
    old = sql_config_call(before) if before else None
    new = sql_config_call(after) if after else None
    if old == new:
        return []
    return [
        _change(
            "config",
            _kind(old, new),
            f"config({old})" if old is not None else None,
            f"config({new})" if new is not None else None,
        )
    ]


def _kind(before: object | None, after: object | None) -> str:
    return "added" if before is None else "removed" if after is None else "changed"


def _text_diff(model: str, before: str | None, after: str | None) -> str:
    lines = difflib.unified_diff(
        (before or "").splitlines(),
        (after or "").splitlines(),
        fromfile=f"snapshot/{model}.sql",
        tofile=f"workspace/{model}.sql",
        lineterm="",
    )
    return "\n".join(lines)
