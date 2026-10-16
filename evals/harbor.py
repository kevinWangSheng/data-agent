"""Read a Harbor job directory: one `<trial>/result.json` per trial."""

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Trial:
    task: str
    name: str
    reward: float | None  # None: the verifier never produced a reward
    exception_type: str | None  # set when Harbor recorded an error for the trial
    cost_usd: float
    dir: Path


def task_id(task_name: str) -> str:
    """Harbor names a trial's task by `[task] name` (`org/name`); manifests use the bare name."""
    return task_name.rsplit("/", 1)[-1]


def _reward(result: dict[str, Any]) -> float | None:
    verifier = result.get("verifier_result") or {}
    reward = (verifier.get("rewards") or {}).get("reward")
    return None if reward is None else float(reward)


def load_trials(job_dir: Path) -> list[Trial]:
    """Every trial in the job, sorted by trial name. The job-level result.json is skipped."""
    trials: list[Trial] = []
    for path in sorted(job_dir.glob("*/result.json")):
        result = json.loads(path.read_text())
        exception = result.get("exception_info") or {}
        agent_result = result.get("agent_result") or {}
        trials.append(
            Trial(
                task=task_id(result["task_name"]),
                name=result.get("trial_name") or path.parent.name,
                reward=_reward(result),
                exception_type=exception.get("exception_type"),
                cost_usd=float(agent_result.get("cost_usd") or 0.0),
                dir=path.parent,
            )
        )
    return trials


def by_task(trials: list[Trial]) -> dict[str, list[float | None]]:
    """Per-task reward lists, the input shape of `evals.metrics`."""
    out: dict[str, list[float | None]] = defaultdict(list)
    for trial in trials:
        out[trial.task].append(trial.reward)
    return dict(out)
