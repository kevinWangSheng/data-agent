"""The Stop state (spec "Stop 钩子"): per model, the check statuses from the last report that
named it and the hash of its SQL file at that time. `stop_decision` applies the three rules.
"""

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from data_agent.verifier_v2.model_diff import model_diff
from data_agent.verifier_v2.project import dbt_projects, model_config, model_files, project_config
from data_agent.verifier_v2.report import parse_report

RULES = {
    1: "changed since the snapshot but never named in a verifier report",
    2: "changed after the last report that named it",
    3: "the last report that named it has a FAIL",
}


def changed_models(workspace: Path, snapshot: Path) -> list[str]:
    """The models `model_diff` lists as new or changed (not the removed ones)."""
    return [d["model"] for d in model_diff(workspace, snapshot) if d["status"] != "removed"]


def model_hashes(workspace: Path) -> dict[str, str]:
    """A SHA-256 fingerprint of each model, by name, over every dbt project in the workspace:
    its SQL file and what dbt_project.yml configures for it, the same surface `model_diff`
    reports changes on."""
    hashes: dict[str, str] = {}
    for project in dbt_projects(workspace):
        config = project_config(project)
        for name, path in model_files(project, config).items():
            sql = path.read_bytes()
            digest = hashlib.sha256(sql)
            settings = model_config(config, project, path, sql.decode("utf-8", "replace")).lines()
            digest.update("\n".join(sorted(settings.values())).encode())
            hashes.setdefault(name, digest.hexdigest())
    return hashes


def record_report(
    state: Mapping[str, Any],
    report: str,
    workspace: Path,
    hashes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """`state` with the models the report names set to their statuses and fingerprint: `hashes`
    as taken when the verifier was dispatched (what its report describes), else the current
    ones. The other models keep what they had; a model no longer in the workspace is dropped.
    A section whose heading is no model in the workspace (the report's `### Summary`) records
    nothing."""
    if hashes is None:
        hashes = model_hashes(workspace)
    return record_checks(state, parse_report(report, workspace=workspace)["models"], hashes)


def record_checks(
    state: Mapping[str, Any],
    models: Mapping[str, Mapping[int, str]],
    hashes: Mapping[str, str],
) -> dict[str, Any]:
    """`record_report` on an already parsed report (`parse_report`'s `models`), for a caller
    that parsed it for its own record."""
    updated = {model: record for model, record in state.items() if model in hashes}
    for model, checks in models.items():
        if model in hashes:
            updated[model] = {"checks": dict(checks), "sha256": hashes[model]}
    return updated


def stop_decision(state: Mapping[str, Any], workspace: Path, snapshot: Path) -> dict[str, Any]:
    """`{"block", "reasons": [{"model", "rule", "detail"}]}`: rule 1 for a new or changed model
    no report named; rules 2 and 3 for a reported model whose file changed since, or whose
    last report holds a FAIL. NEEDS_INFO never blocks; nor does a reported model deleted since
    (model_diff reports it removed, for the verifier to see)."""
    changed = set(changed_models(workspace, snapshot))
    hashes = model_hashes(workspace)
    reasons: list[dict[str, Any]] = []
    for model in sorted(changed - set(state)):
        reasons.append({"model": model, "rule": 1, "detail": RULES[1]})
    for model in sorted(set(state) & set(hashes)):
        record = state[model]
        if hashes[model] != record["sha256"]:
            reasons.append({"model": model, "rule": 2, "detail": RULES[2]})
        failed = sorted(n for n, status in record["checks"].items() if status == "FAIL")
        if failed:
            checks = ", ".join(f"CHECK {n}" for n in failed)
            reasons.append({"model": model, "rule": 3, "detail": f"{RULES[3]} ({checks})"})
    return {"block": bool(reasons), "reasons": reasons}


def describe(reasons: list[dict[str, Any]]) -> str:
    return "; ".join(f"{r['model']}: rule {r['rule']}, {r['detail']}" for r in reasons)
