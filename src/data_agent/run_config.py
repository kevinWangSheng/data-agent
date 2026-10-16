"""Run configs (spec "运行配置", ADR-0004): layer switches, Skill version, turn and spend caps.

Each config is a committed file in `configs/`. A layer that is off leaves no trace in the run: its
Skill text, tools and events are all left out.
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
CONFIGS_DIR = PACKAGE_DIR / "configs"
CONFIG_NAMES = tuple(sorted(path.stem for path in CONFIGS_DIR.glob("*.toml")))


def skills_path(name: str) -> Path:
    """A wheel ships Skill directories inside the package (pyproject force-include); a checkout has
    them at the repo root."""
    packaged = PACKAGE_DIR / name
    return packaged if packaged.is_dir() else REPO_ROOT / name


@dataclass(frozen=True)
class RunConfig:
    name: str
    skill: bool
    verifiers: bool
    context: bool
    # The Skill version this config loads; old and new versions are separate directories.
    skills_dir: Path
    # Per task type, as in SignalPilot (dbt 200, query 50).
    max_turns: Mapping[str, int]
    max_budget_usd: float

    def layers(self) -> dict[str, bool]:
        return {"skill": self.skill, "verifiers": self.verifiers, "context": self.context}


def load_config(name: str) -> RunConfig:
    if name not in CONFIG_NAMES:
        raise ValueError(f"unknown run config {name!r}; choose from {', '.join(CONFIG_NAMES)}")
    data = tomllib.loads((CONFIGS_DIR / f"{name}.toml").read_text())
    skills_dir = skills_path(data["skills_dir"])
    if not skills_dir.is_dir():
        raise ValueError(f"run config {name!r} names a missing Skill directory: {skills_dir}")
    return RunConfig(
        name=name,
        skill=data["skill"],
        verifiers=data["verifiers"],
        context=data["context"],
        skills_dir=skills_dir,
        max_turns=data["max_turns"],
        max_budget_usd=data["max_budget_usd"],
    )
