"""The Stop state (spec "Stop 钩子"): per model, the check statuses from the last report that
named it, the hash of its SQL file at that time, the number of the dispatch the report answered
and its disputed checks. `stop_decision` applies the three rules.

A disputed check (ticket 64) is a FAIL the verifier gave twice on the same fingerprint, the
second time on a dispatch that carried the main agent's counter-evidence: the two disagree and
another round would not settle it, so rule 3 skips it and the run may end, listing it
(`disputed_checks`).
"""

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from data_agent.verifier_v2.model_diff import model_diff
from data_agent.verifier_v2.project import (
    dbt_projects,
    model_config,
    model_files,
    project_config,
    read_text,
    referenced_models,
)
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
    dispatch: int | None = None,
    counter_evidence: bool = False,
) -> dict[str, Any]:
    """`record_report` on an already parsed report (`parse_report`'s `models`), for a caller
    that parsed it for its own record. `dispatch` numbers the dispatch the report answers and
    `counter_evidence` says whether it carried the main agent's counter-evidence; with both, a
    FAIL that the model's previous record also holds, on the same fingerprint, is disputed
    (`disputed`: check -> the two dispatch numbers). A dispute stands while the same check keeps
    failing on that fingerprint."""
    updated = {model: record for model, record in state.items() if model in hashes}
    for model, checks in models.items():
        if model not in hashes:
            continue
        before = state.get(model)
        disputed: dict[int, list[int]] = {}
        if before is not None and before["sha256"] == hashes[model]:
            for check, status in checks.items():
                if status != "FAIL":
                    continue
                if check in before.get("disputed", {}):
                    disputed[check] = before["disputed"][check]
                elif (
                    counter_evidence
                    and dispatch is not None
                    and before.get("dispatch") is not None
                    and before["checks"].get(check) == "FAIL"
                ):
                    disputed[check] = [before["dispatch"], dispatch]
        updated[model] = {
            "checks": dict(checks),
            "sha256": hashes[model],
            "dispatch": dispatch,
            "disputed": disputed,
        }
    return updated


def stop_decision(state: Mapping[str, Any], workspace: Path, snapshot: Path) -> dict[str, Any]:
    """`{"block", "reasons": [{"model", "rule", "detail"}]}`: rule 1 for a new or changed model
    no report named; rules 2 and 3 for a reported model whose file changed since, or whose
    last report holds a FAIL that is not disputed. NEEDS_INFO never blocks; nor does a reported
    model deleted since (model_diff reports it removed, for the verifier to see)."""
    changed = set(changed_models(workspace, snapshot))
    hashes = model_hashes(workspace)
    reasons: list[dict[str, Any]] = []
    for model in sorted(changed - set(state)):
        reasons.append({"model": model, "rule": 1, "detail": RULES[1]})
    for model in sorted(set(state) & set(hashes)):
        record = state[model]
        if hashes[model] != record["sha256"]:
            reasons.append({"model": model, "rule": 2, "detail": RULES[2]})
        disputed = record.get("disputed", {})
        failed = sorted(
            n for n, status in record["checks"].items() if status == "FAIL" and n not in disputed
        )
        if failed:
            checks = ", ".join(f"CHECK {n}" for n in failed)
            reasons.append({"model": model, "rule": 3, "detail": f"{RULES[3]} ({checks})"})
    return {"block": bool(reasons), "reasons": reasons}


def disputed_checks(state: Mapping[str, Any], workspace: Path) -> list[dict[str, Any]]:
    """`[{"model", "check", "dispatches"}]`, sorted, for every disputed check of a model still in
    the workspace and unchanged since its report (a change ends the dispute: rule 2 applies)."""
    hashes = model_hashes(workspace)
    return [
        {"model": model, "check": check, "dispatches": list(dispatches)}
        for model in sorted(set(state) & set(hashes))
        if hashes[model] == state[model]["sha256"]
        for check, dispatches in sorted(state[model].get("disputed", {}).items())
    ]


def describe_disputes(disputes: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{d['model']}: CHECK {d['check']} FAIL on dispatches "
        f"{', '.join(map(str, d['dispatches']))}, the model unchanged and counter-evidence given"
        for d in disputes
    )


def redispatch_models(state: Mapping[str, Any], workspace: Path, snapshot: Path) -> list[str]:
    """The models a verifier dispatch after the first lists, sorted: every model `stop_decision`
    names (never reported, changed since its report, FAIL in it), every model whose last report
    has a NEEDS_INFO (it does not block, but the main agent dispatches again for it), and the
    changed models downstream of one of these (`downstream_of`): a model's fingerprint covers
    only its own files, so an upstream edit leaves it unchanged. Empty when none."""
    files: dict[str, Path] = {}
    for project in dbt_projects(workspace):
        for name, path in model_files(project).items():
            files.setdefault(name, path)
    again = {reason["model"] for reason in stop_decision(state, workspace, snapshot)["reasons"]}
    again |= {
        model
        for model, record in state.items()
        if model in files and "NEEDS_INFO" in record["checks"].values()
    }
    if not again:
        return []
    refs = {model: referenced_models(read_text(path)) for model, path in files.items()}
    downstream = downstream_of(again, refs) & set(changed_models(workspace, snapshot))
    return sorted(again | downstream)


def downstream_of(seeds: set[str], refs: Mapping[str, set[str] | None]) -> set[str]:
    """The models that `ref()` one of `seeds`, transitively, through any model in `refs` (an
    unchanged model between two changed ones passes the change on). A model whose refs are
    unknown (None: a macro could hide one) is taken as downstream of every seed."""
    reached = set(seeds)
    grew = True
    while grew:
        found = {
            model
            for model, used in refs.items()
            if model not in reached and (used is None or used & reached)
        }
        reached |= found
        grew = bool(found)
    return reached - seeds


def describe(reasons: list[dict[str, Any]]) -> str:
    return "; ".join(f"{r['model']}: rule {r['rule']}, {r['detail']}" for r in reasons)
