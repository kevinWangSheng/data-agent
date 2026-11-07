"""Readiness before a batch (eval-readiness ticket 02; ADR-0008 preflight).

`evals ablate` runs it once per invocation, in the preflight before the first job, beside the
balance, wheel, smoke and scrub checks it keeps. Findings come in two kinds:

- a blocker is a systemic problem that every trial of the batch would meet; it stops the batch
  before the first job (`NotReady`);
- a warning is an isolated efficiency finding in one smoke trial; it is recorded with its
  reason in OUT/readiness/<UTC time>.json and carried into the ablation table, and is not a
  correctness failure.

Blockers, checked before the wheel fill and the smoke:

- `agent_timeout`: the trial limit (`agent_timeout_sec`) must exceed the margin the agent stops
  itself by before it (ticket 54), else every trial stops at its start;
- `docker_memory`: Docker's memory must hold the trials that run at once, each at the highest
  single-trial peak measured, TRIAL_PEAK_MEMORY_BYTES (docs/research/2026-11-05-foundation-
  latency-audit.md: 1762 MiB; 4 concurrent trials peaked at 3676 of 7940 MB together). DuckDB's
  own cap (2 GiB, data_agent.gateway) is not used: no trial reached it, and a 2 GiB Docker VM,
  which runs one trial, would fail it. A Docker that does not answer blocks too;
- `disk`: Docker's data root (`docker info`'s DockerRootDir, where the containers write) must
  have room for the temporary files those trials' DuckDB may write (its cap, data_agent.gateway).
  Where that root is not on this host (Colima keeps it in its VM) the disk is not measured: the
  check is left out of `checked` and blocks nothing.

Warnings, from each config's smoke trial:

- `deadline`: it stopped itself at its trial deadline (human decision 2026-11-06: a warning);
- `tool_timeout`: a tool call hit the gateway's statement timeout;
- `full_redispatch`: a later verifier dispatch re-sent every model (evals.process, ticket 04);
- `prompt_cache`: less than MIN_CACHED_SHARE of its input tokens were cache hits, so most of
  each turn's input was sent again uncached (every dev run up to 2026-11-07 had 0.94-0.99).

Cost and time are bounded: no model call, no Harbor run beyond the existing smoke (one trial per
config), nothing per task or per trial of the batch. The environment checks are one `docker
info` (at most DOCKER_TIMEOUT_S) and at most one disk-usage call; the smoke checks read each
config's one smoke trial, already paid for.
"""

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

from data_agent.agent import DEADLINE_MARGIN_S
from data_agent.gateway import DUCKDB_MAX_TEMP_DIRECTORY_SIZE
from evals.harbor import Trial
from evals.process import DEADLINE, trial_metrics

READINESS_DIR = "readiness"
SMOKE_CHECKS = ("deadline", "tool_timeout", "full_redispatch", "prompt_cache")
GIB = 2**30
TRIAL_PEAK_MEMORY_BYTES = 1762 * 2**20
DOCKER_TIMEOUT_S = 30.0
MIN_CACHED_SHARE = 0.5
AGENT_TIMEOUT_KWARG = "agent_timeout_sec"


class NotReady(Exception):
    """A blocker: the batch stops before its first job."""


def gib(size: str) -> int:
    """Bytes of a DuckDB size setting written as `<n>GiB`."""
    return int(size.removesuffix("GiB")) * GIB


TRIAL_TEMP_BYTES = gib(DUCKDB_MAX_TEMP_DIRECTORY_SIZE)


def docker_info() -> tuple[int, str]:
    """Bytes of memory Docker's containers share, and Docker's data root (`docker info`)."""
    command = ["docker", "info", "--format", "{{.MemTotal}} {{.DockerRootDir}}"]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=DOCKER_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NotReady(f"docker info failed: {exc}") from exc
    if proc.returncode != 0:
        raise NotReady(f"docker info failed: {proc.stderr.strip() or f'exit {proc.returncode}'}")
    memory, _, root = proc.stdout.strip().partition(" ")
    try:
        return int(memory), root
    except ValueError as exc:
        raise NotReady(f"docker info gave no memory size: {proc.stdout.strip()!r}") from exc


def free_disk(path: Path) -> int:
    return shutil.disk_usage(path).free


def agent_timeout(agent_kwargs: list[str]) -> float:
    """The trial limit the agent gets: the last `agent_timeout_sec` kwarg, else the agent's
    default (evals.harbor_agent)."""
    values = [
        value
        for key, _, value in (kwarg.partition("=") for kwarg in agent_kwargs)
        if key.strip() == AGENT_TIMEOUT_KWARG
    ]
    if not values:
        from evals.harbor_agent import DataAgentOptions  # imports Harbor

        return float(DataAgentOptions.model_fields[AGENT_TIMEOUT_KWARG].default)
    try:
        return float(values[-1])
    except ValueError as exc:
        raise NotReady(f"{AGENT_TIMEOUT_KWARG}={values[-1]} is not a number of seconds") from exc


def check_environment(concurrent: int, agent_kwargs: list[str]) -> tuple[dict[str, Any], list[str]]:
    """The blockers, for `concurrent` trials at a time. When none fires: the resources seen and
    the checks that ran (`disk` only where Docker's data root is on this host)."""
    timeout = agent_timeout(agent_kwargs)
    if not (math.isfinite(timeout) and timeout > DEADLINE_MARGIN_S):
        raise NotReady(
            f"{AGENT_TIMEOUT_KWARG}={timeout:g} leaves the agent no time: it stops itself "
            f"{DEADLINE_MARGIN_S:g} s before its trial deadline, so every trial would stop at "
            f"once; give more than {DEADLINE_MARGIN_S:g}"
        )
    memory, root = docker_info()
    if memory < concurrent * TRIAL_PEAK_MEMORY_BYTES:
        raise NotReady(
            f"Docker has {memory / GIB:.1f} GiB of memory; {concurrent} concurrent trials need "
            f"{concurrent * TRIAL_PEAK_MEMORY_BYTES / GIB:.1f} GiB (a trial peaked at "
            f"{TRIAL_PEAK_MEMORY_BYTES / GIB:.2f} GiB): lower --n-concurrent or give Docker more "
            "memory"
        )
    checked = ["agent_timeout", "docker_memory"]
    disk = free_disk(Path(root)) if root and Path(root).is_dir() else None
    if disk is not None:
        checked.append("disk")
        if disk < concurrent * TRIAL_TEMP_BYTES:
            raise NotReady(
                f"the disk of Docker's data root {root} has {disk / GIB:.1f} GiB free; "
                f"{concurrent} concurrent trials need {concurrent * TRIAL_TEMP_BYTES / GIB:g} GiB "
                f"(each trial's DuckDB may write {DUCKDB_MAX_TEMP_DIRECTORY_SIZE} of temporary "
                "files): free disk space or lower --n-concurrent"
            )
    resources = {
        "concurrent_trials": concurrent,
        "docker_memory_bytes": memory,
        "docker_root": root,
        "free_disk_bytes": disk,
        "agent_timeout_sec": timeout,
    }
    return resources, checked


def warning(check: str, config: str, task: str, reason: str) -> dict[str, str]:
    return {"check": check, "config": config, "task": task, "reason": reason}


def smoke_warnings(config: str, task: str, trial: Trial) -> list[dict[str, str]]:
    """The efficiency findings of one passed smoke trial, from its event file."""
    metrics = trial_metrics(trial)
    found: list[dict[str, str]] = []
    if metrics["stopped"] == DEADLINE:
        found.append(
            warning(
                "deadline",
                config,
                task,
                f"the {config} smoke trial on {task} stopped itself at its trial deadline "
                "(agent_timeout_sec): a longer batch task may too",
            )
        )
    timeouts = {tool: n for tool, n in (metrics["timeouts"] or {}).items() if n}
    if timeouts:
        calls = ", ".join(f"{tool} x{n}" for tool, n in sorted(timeouts.items()))
        found.append(
            warning(
                "tool_timeout",
                config,
                task,
                f"the {config} smoke trial on {task} had tool calls hit the statement timeout "
                f"({calls})",
            )
        )
    if metrics["full_redispatches"]:
        found.append(
            warning(
                "full_redispatch",
                config,
                task,
                f"the {config} smoke trial on {task} re-dispatched every model of its first "
                f"verifier dispatch {metrics['full_redispatches']} time(s) (full redispatch)",
            )
        )
    input_tokens = trial.cached_input_tokens + trial.uncached_input_tokens
    share = trial.cached_input_tokens / input_tokens if input_tokens else None
    if share is not None and share < MIN_CACHED_SHARE:
        found.append(
            warning(
                "prompt_cache",
                config,
                task,
                f"the {config} smoke trial on {task} got {share:.0%} of its input tokens from "
                f"the prompt cache (under {MIN_CACHED_SHARE:.0%}): most of each turn's input "
                "was sent again uncached",
            )
        )
    return found


def record(
    out: Path,
    stamp: str,
    checked: list[str],
    warnings: list[dict[str, str]],
    resources: dict[str, Any],
) -> None:
    """OUT/readiness/<stamp>.json: the checks this preflight ran, its warnings and the resources
    it saw."""
    path = out / READINESS_DIR / f"{stamp}.json"
    path.parent.mkdir(exist_ok=True)
    found = {"checked": checked, "warnings": warnings, "resources": resources}
    path.write_text(json.dumps(found, indent=2) + "\n")


def summary(out: Path) -> dict[str, Any]:
    """Every preflight recorded under OUT: the checks any of them ran and their warnings, a
    finding several preflights made on one config and task (a rerun smokes again) once. `clean`
    is None when no preflight was recorded (no job ran under this OUT from this code)."""
    files = sorted((out / READINESS_DIR).glob("*.json"))
    found = [json.loads(path.read_text()) for path in files]
    first: dict[tuple[str, str, str], dict[str, str]] = {}
    for w in (w for f in found for w in f["warnings"]):
        first.setdefault((w["check"], w["config"], w["task"]), w)
    warnings = list(first.values())
    checked = sorted({c for f in found for c in f["checked"]})
    return {
        "clean": (not warnings) if found else None,
        "checked": checked,
        "warnings": warnings,
    }


def markdown(readiness: dict[str, Any] | None) -> list[str]:
    """The readiness section of the ablation table."""
    if readiness is None or readiness["clean"] is None:
        return ["Readiness: not recorded (no preflight ran under this OUT)."]
    checked = ", ".join(readiness["checked"])
    if readiness["clean"]:
        return [f"Readiness: clean (checked: {checked})."]
    n = len(readiness["warnings"])
    lines = [
        f"Readiness: not clean, {n} warning{'' if n == 1 else 's'} (checked: {checked}). A "
        "warning is an efficiency finding in a smoke trial: it did not stop the batch and is "
        "not a correctness failure.",
        "",
        "| check | config | task | reason |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {w['check']} | {w['config']} | {w['task']} | {w['reason']} |"
        for w in readiness["warnings"]
    ]
    return lines
