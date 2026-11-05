"""Text the MCP tools return for the v2 results: compact, one fact per line, nothing judged."""

from typing import Any


def render_model_diff(diffs: list[dict[str, Any]]) -> str:
    if not diffs:
        return "No model was created, changed or removed since the snapshot."
    lines: list[str] = []
    for diff in diffs:
        lines.append(f"### {diff['model']} ({diff['status']}, project {diff['project']})")
        if not diff["parsed"]:
            lines.append(
                "未解析 (not parsed: Jinja statements or SQL sqlglot could not read); "
                "line diff against the snapshot:"
            )
            lines.append(diff["text_diff"] or "(no SQL change)")
        if diff["parsed"] and not diff["changes"]:
            lines.append("(no change in the categories compared)")
        for change in diff["changes"]:
            lines.append(_change_line(change))
        lines.append("")
    return "\n".join(lines).rstrip()


def _change_line(change: dict[str, Any]) -> str:
    category, kind = change["category"], change["kind"]
    if kind == "added":
        return f"- [{category}] added: {change['after']}"
    if kind == "removed":
        return f"- [{category}] removed: {change['before']}"
    return f"- [{category}] changed: {change['before']}  ->  {change['after']}"


def render_contract(results: dict[str, dict[str, Any]]) -> str:
    lines: list[str] = []
    for model, checks in results.items():
        lines.append(f"### {model}")
        lines.append(f"exists: {checks['exists']['status']} — {checks['exists']['evidence']}")
        columns = checks["columns"]
        lines.append(f"columns: {columns['status']} — {columns['evidence']}")
        window = checks["window"]
        lines.append(f"window ordering: {window['status']} — {window['evidence']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_grain(facts: dict[str, Any]) -> str:
    lines = [
        f"relation: {facts['relation']}",
        f"key columns: {', '.join(facts['key_columns'])}",
        f"rows with a NULL key: {facts['null_rows']}",
        f"duplicate rows (beyond the first per key): {facts['duplicate_rows']}",
    ]
    exemption = facts["exemption"]
    if exemption is None:
        lines.append(
            "exemption source in the snapshot YML: none (only the task text could still exempt)"
        )
    else:
        lines.append(
            f"exemption candidate: the snapshot YML at {exemption['path']} declares "
            f"{', '.join(exemption['columns'])} unique"
        )
    return "\n".join(lines)


def render_compare(result: dict[str, Any]) -> str:
    if not result["accepted"]:
        return f"Rejected: {result['reason']}"
    lines = [
        f"relation: {result['relation']}",
        f"compared columns: {', '.join(result['compared_columns']) or '(none)'}",
    ]
    if result["unmatched_reference_columns"]:
        lines.append(
            "reference columns the model lacks (not compared): "
            + ", ".join(result["unmatched_reference_columns"])
        )
    samples = result["samples"]
    lines.append(f"keys only in the model: {result['only_in_model']}")
    lines += [f"  {_key(k)}" for k in samples["only_in_model"]]
    lines.append(f"keys only in the reference: {result['only_in_reference']}")
    lines += [f"  {_key(k)}" for k in samples["only_in_reference"]]
    lines.append(f"keys with a value mismatch: {result['mismatched']}")
    lines += [
        f"  {_key(m['key'])} {m['column']}: model={m['model']!r} reference={m['reference']!r}"
        for m in samples["mismatched"]
    ]
    return "\n".join(lines)


def _key(key: dict[str, Any]) -> str:
    return ", ".join(f"{name}={value!r}" for name, value in key.items())
