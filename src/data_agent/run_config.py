"""Named run configs (spec "运行配置"): turn and spend caps per run.

Minimal placeholder: ticket 05 adds the layer switches and Skill versions; until then every name
means all layers on.
"""

from dataclasses import dataclass

CONFIG_NAMES = ("baseline", "full", "no-skill", "no-verifiers", "no-context", "full-old-skill")

# Max turns per task type, as in SignalPilot (dbt 200, query 50).
MAX_TURNS = {"dbt": 200, "query": 50}


@dataclass(frozen=True)
class RunConfig:
    name: str
    max_budget_usd: float


def load_config(name: str) -> RunConfig:
    if name not in CONFIG_NAMES:
        raise ValueError(f"unknown run config {name!r}; choose from {', '.join(CONFIG_NAMES)}")
    # $1 per run for deepseek-flash (spec).
    return RunConfig(name=name, max_budget_usd=1.0)
