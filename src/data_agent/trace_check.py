"""`data-agent trace-check`: read a run's trace back from Langfuse and print its structure
(.scratch/observability/spec.md "读回 trace").

The trace id comes from the run's `trace` event; the observations from Langfuse's
`GET /api/public/v2/observations` (the older `/api/public/traces` answers 410 on a new Cloud
project). What is printed is structure only: each observation's type, name and depth, whether it
has usage and cost, and the names of its metadata attributes. No attribute value, no input or
output, no key.
"""

import http.client
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from data_agent.tracing import LangfuseSettings

OBSERVATIONS_PATH = "/api/public/v2/observations"
# The v2 field groups asked for: no `io` (inputs and outputs) and no `model`, which are content
# or not needed. `core` (id, trace id, parent, type, times) always comes back.
FIELDS = "core,basic,metadata,usage"
PAGE_LIMIT = 100
TIMEOUT_S = 30.0
RUNS_DIR = Path("runs")


class CheckFailed(Exception):
    """What stops the check, worded for the user; never holds a key or a value of the trace."""


def find_events(target: str, runs_dir: Path = RUNS_DIR) -> Path:
    """The event file `target` names: the path itself when it is a file, else the one
    `<run_id>.jsonl` under `runs_dir` (any depth). None found, or several, fails."""
    path = Path(target)
    if path.is_file():
        return path
    found = sorted(runs_dir.rglob(f"{target}.jsonl")) if runs_dir.is_dir() else []
    if not found:
        raise CheckFailed(f"no event file {target!r}, and no {target}.jsonl under {runs_dir}/")
    if len(found) > 1:
        raise CheckFailed(
            f"{len(found)} event files named {target}.jsonl under {runs_dir}/; give its path"
        )
    return found[0]


def trace_id(events: Path) -> str:
    """The trace id of the run's (last) `trace` event."""
    found: str | None = None
    for line in events.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "trace":
            found = str(event.get("trace_id") or "") or found
    if found is None:
        raise CheckFailed(f"{events} has no trace event: the run exported no trace")
    return found


def observations(settings: LangfuseSettings, trace: str) -> Iterator[dict[str, Any]]:
    """Every observation of the trace, page by page (`meta.cursor`)."""
    cursor: str | None = None
    while True:
        query = {"traceId": trace, "fields": FIELDS, "limit": str(PAGE_LIMIT)}
        if cursor:
            query["cursor"] = cursor
        url = f"{settings.base_url}{OBSERVATIONS_PATH}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, headers=settings.headers())
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                page = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise CheckFailed(f"Langfuse answered HTTP {exc.code} for the observations") from None
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise CheckFailed(
                f"cannot read the observations from Langfuse: {type(exc).__name__}"
            ) from None
        yield from (o for o in page.get("data") or [] if isinstance(o, dict))
        cursor = (page.get("meta") or {}).get("cursor")
        if not cursor:
            return


def names(value: Any) -> str:
    return ", ".join(sorted(str(k) for k in value)) if isinstance(value, Mapping) else "-"


def structure(trace: str, found: list[dict[str, Any]]) -> list[str]:
    """The printed lines: the tree (each observation under its parent, by start time; one whose
    parent is not in the trace at the top), then the count of each type and name."""
    ids = {str(o.get("id")) for o in found}
    children: dict[str | None, list[dict[str, Any]]] = {}
    for o in found:
        parent = o.get("parentObservationId")
        key = str(parent) if parent is not None and str(parent) in ids else None
        children.setdefault(key, []).append(o)
    lines = [f"trace {trace}: {len(found)} observations"]

    def walk(parent: str | None, depth: int, seen: frozenset[str]) -> None:
        for o in sorted(children.get(parent, []), key=lambda o: str(o.get("startTime") or "")):
            key = str(o.get("id"))
            if key in seen:  # a cycle of parents: printed once
                continue
            indent = "  " * depth
            usage, cost = o.get("usageDetails"), o.get("costDetails")
            has_usage = isinstance(usage, Mapping) and any(usage.values())
            has_cost = isinstance(cost, Mapping) and any(cost.values())
            lines.append(
                f"{indent}{o.get('type')} {o.get('name')}"
                f"  usage: {names(usage) if has_usage else 'none'}"
                f"  cost: {'yes' if has_cost else 'no'}"
            )
            metadata = o.get("metadata")
            if isinstance(metadata, Mapping):
                for group, attributes in sorted(metadata.items()):
                    lines.append(f"{indent}  metadata.{group}: {names(attributes)}")
            walk(key, depth + 1, seen | {key})

    walk(None, 1, frozenset())
    counts: dict[tuple[str, str], int] = {}
    for o in found:
        kind = (str(o.get("type")), str(o.get("name")))
        counts[kind] = counts.get(kind, 0) + 1
    lines.append("counts:")
    lines += [f"  {n} {kind} {name}" for (kind, name), n in sorted(counts.items())]
    return lines


def check(target: str, env: Mapping[str, str], runs_dir: Path = RUNS_DIR) -> list[str]:
    settings = LangfuseSettings.from_env(env)
    if settings is None:
        raise CheckFailed(
            "set DATA_AGENT_LANGFUSE_PUBLIC_KEY, DATA_AGENT_LANGFUSE_SECRET_KEY and "
            "DATA_AGENT_LANGFUSE_BASE_URL to read the trace back"
        )
    trace = trace_id(find_events(target, runs_dir))
    found = list(observations(settings, trace))
    if not found:
        # Not a pass: Langfuse may not have ingested it yet, or the keys are another project's.
        raise CheckFailed(f"Langfuse has no observations of trace {trace} (yet?)")
    return structure(trace, found)


def main(target: str, env: Mapping[str, str]) -> int:
    try:
        lines = check(target, env)
    except CheckFailed as exc:
        print(f"trace-check: {exc}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0
