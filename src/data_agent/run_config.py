"""Run configs (spec "运行配置", ADR-0004): layer switches, Skill version, turn and spend caps.

A `RunConfig` comes from an old run config's name (a committed file in `configs/`, which the
evaluation entries still pass until ticket 146) or from the effective product config
(`data_agent.config`, ticket 143). A layer that is off leaves no trace in the run: its Skill text,
tools and events are all left out.
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from data_agent.config import EffectiveConfig, default_config

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
    # What our own MCP server's `--config` is given (`agent.build_options`): the old name when
    # empty, else the file the run wrote the effective config to (`data-agent run`).
    server_config: str = ""

    def server_arg(self) -> str:
        return self.server_config or self.name

    def layers(self) -> dict[str, bool]:
        return {"skill": self.skill, "verifiers": self.verifiers, "context": self.context}


def load_config(name: "str | EffectiveConfig") -> RunConfig:
    """The run config of an old name, or of an effective config (`[layers]`; named after its
    last file, `default` without one)."""
    if isinstance(name, EffectiveConfig):
        return from_effective(name)
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


def from_effective(config: EffectiveConfig) -> RunConfig:
    """The layers come from the config. The turn and spend caps stay the code defaults' until
    ticket 144 wires `[limits]` to the run; the Skill directory stays the current Skills' until
    ticket 146 gives `full-old-skill` its own."""
    defaults = default_config()
    return RunConfig(
        name=config.name(),
        skill=bool(config.value("layers.skill")),
        verifiers=bool(config.value("layers.verifiers")),
        context=bool(config.value("layers.context")),
        skills_dir=skills_path("skills"),
        max_turns={
            kind: int(defaults.values[f"limits.max_turns.{kind}"])
            for kind in ("dbt", "query", "large")
        },
        max_budget_usd=float(defaults.values["limits.max_budget_usd"]),
    )
