"""Split benchmark tasks into the development set and a stratified evaluation holdout (ADR-0004)."""

import random
import tomllib
from collections import defaultdict
from pathlib import Path


def task_difficulty(tasks_dir: Path) -> dict[str, str]:
    """{task directory name: `[metadata] difficulty`} for every task under tasks_dir."""
    return {
        path.parent.name: tomllib.loads(path.read_text())["metadata"]["difficulty"]
        for path in sorted(tasks_dir.glob("*/task.toml"))
    }


def allocate(strata: dict[str, int], size: int) -> dict[str, int]:
    """Proportional allocation of `size` picks over strata, largest remainder first.

    Ties on the remainder go to the larger stratum, then to the stratum name, so the
    allocation never depends on the seed.
    """
    total = sum(strata.values())
    if not 0 < size <= total:
        raise ValueError(f"cannot sample {size} of {total} tasks")
    quotas = {name: size * count / total for name, count in strata.items()}
    picks = {name: int(quota) for name, quota in quotas.items()}
    leftover = size - sum(picks.values())
    by_remainder = sorted(
        strata, key=lambda name: (picks[name] - quotas[name], -strata[name], name)
    )
    for name in by_remainder[:leftover]:
        picks[name] += 1
    return picks


def sample(
    difficulty: dict[str, str], dev: list[str], size: int, seed: int
) -> dict[str, int | list[str]]:
    """Manifest `{"seed", "dev", "holdout"}`: holdout = `size` non-dev tasks, stratified."""
    unknown = sorted(set(dev) - set(difficulty))
    if unknown:
        raise ValueError(f"dev list names tasks not in the tasks dir: {unknown}")
    strata: dict[str, list[str]] = defaultdict(list)
    for task in sorted(set(difficulty) - set(dev)):
        strata[difficulty[task]].append(task)
    picks = allocate({level: len(tasks) for level, tasks in strata.items()}, size)
    rng = random.Random(seed)
    holdout: list[str] = []
    for level in sorted(strata):
        holdout += rng.sample(strata[level], picks[level])
    return {"seed": seed, "dev": sorted(dev), "holdout": sorted(holdout)}


def read_task_list(path: Path) -> list[str]:
    """One task name per line; blank lines and `#` comments are ignored."""
    lines = (line.split("#", 1)[0].strip() for line in path.read_text().splitlines())
    return [line for line in lines if line]
