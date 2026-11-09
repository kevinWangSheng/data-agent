# Copyright 2026 Snowflake Inc. Licensed under the Apache License, Version 2.0.
#
# Copied from data-eng-bench (https://github.com/Snowflake-Labs/data-eng-bench), commit
# a3278ad102829a6084dde086244a0ef665a8011c, leaderboard/src/leaderboard/ (the upstream files carry
# no per-file copyright header; the line above is from the upstream NOTICE):
#   ci/static_analysis.py: the override keys, _section_overrides, matching_agent,
#     _timeout_multiplier_ok, trial_config_failures (unmodified), and run()'s job-config checks
#     (as job_config_failures, modified: over one job config already fetched);
#   core/hub.py: trial_model (unmodified);
#   flows/filter.py: job_filter_keys (as source_filters, modified: over a job config and trial
#     rows already fetched, without the date; the DATASET check returns instead of exiting).
"""The leaderboard CI's checks of a job's config and its trials, for board's tests: what
data-eng-bench's `lb filter` derives a submission's `source_filter` from, and what its static
analysis then requires of the job config and of each trial's config."""

from __future__ import annotations

DATASET = "snowflake-labs/data-eng-bench"

_MULTIPLIERS = (
    "agent_timeout_multiplier",
    "verifier_timeout_multiplier",
    "agent_setup_timeout_multiplier",
    "environment_build_timeout_multiplier",
)
_AGENT_OVERRIDES = ("override_timeout_sec", "override_setup_timeout_sec", "max_timeout_sec")
_VERIFIER_OVERRIDES = ("override_timeout_sec", "max_timeout_sec")
_ENV_OVERRIDES = ("override_cpus", "override_gpus", "override_memory_mb", "override_storage_mb")


def _section_overrides(section: dict, keys: tuple[str, ...], prefix: str = "") -> list[str]:
    """Failure strings for any forbidden keys set on a config section."""
    return [f"{prefix}{k}={section.get(k)}" for k in keys if section.get(k) is not None]


def matching_agent(config: dict, sf: dict) -> dict | None:
    """The config.agents entry matching source_filter (recreates the filter).

    The config carries no agent version, so match on agent + model +
    reasoning effort (the version is checked trial-side, via the
    source_filter join)."""
    for a in config.get("agents", []):
        if (
            a.get("name") == sf["agent"]
            and a.get("model_name") == sf["model_name"]
            and (a.get("kwargs") or {}).get("reasoning_effort") == sf["reasoning_effort"]
        ):
            return a
    return None


def _timeout_multiplier_ok(value) -> bool:
    """Default (None) and 1.0 are both the canonical no-stretch setting."""
    return value is None or value == 1.0


def trial_config_failures(config: dict) -> list[str]:
    """Per-trial config checks (config.agent is a single agent here)."""
    fails: list[str] = []
    if not _timeout_multiplier_ok(config.get("timeout_multiplier")):
        fails.append(f"timeout_multiplier={config.get('timeout_multiplier')}")
    fails += _section_overrides(config, _MULTIPLIERS)
    fails += _section_overrides(config.get("agent") or {}, _AGENT_OVERRIDES, "agent.")
    fails += _section_overrides(config.get("verifier") or {}, _VERIFIER_OVERRIDES, "verifier.")
    fails += _section_overrides(config.get("environment") or {}, _ENV_OVERRIDES, "environment.")
    return fails


def job_config_failures(config: dict, sf: dict) -> list[str]:
    """run()'s checks of one job config: a matching agent, the default timeout multiplier, and no
    agent, verifier or resource override."""
    fails: list[str] = []
    agent = matching_agent(config, sf)
    if agent is None:
        fails.append("no matching agent in job config")
    if not _timeout_multiplier_ok(config.get("timeout_multiplier")) or _section_overrides(
        config, _MULTIPLIERS
    ):
        fails.append(f"got {config.get('timeout_multiplier')}")
    if agent:
        fails += _section_overrides(agent, _AGENT_OVERRIDES)
    fails += _section_overrides(config.get("verifier") or {}, _VERIFIER_OVERRIDES)
    fails += _section_overrides(config.get("environment") or {}, _ENV_OVERRIDES)
    return fails


def trial_model(trial: dict) -> str | None:
    """Full 'provider/model' id for a bulk trial row. The rows store the
    provider and bare model name split; job configs and submissions use the
    joined id (e.g. 'openai/gpt-5.5'), so join here for exact comparison.
    A missing or 'unknown' provider (an agent that didn't report one) joins
    to the bare model name, matching configs that name the model without a
    provider (e.g. 'glm-5.1')."""
    provider, name = trial.get("model_provider"), trial.get("model_name")
    if not provider or provider == "unknown":
        return name
    return f"{provider}/{name}"


def source_filters(config: dict, trials: list[dict]) -> list[dict] | None:
    """The source_filter of each (agent, agent_version, model_name, reasoning_effort) key of a
    job, as `lb filter` writes them; None when the job did not run DATASET."""
    datasets = [d.get("name") for d in config.get("datasets", [])]
    if DATASET not in datasets:
        return None
    efforts = {
        (a.get("name"), a.get("model_name")): (a.get("kwargs") or {}).get("reasoning_effort")
        for a in config.get("agents", [])
    }
    keys: list[tuple] = []
    for t in trials:
        if t.get("source") != DATASET:
            continue
        agent, model_name = t.get("agent_name"), trial_model(t)
        key = (
            agent,
            t.get("agent_version"),
            model_name,
            efforts.get((agent, model_name)),
        )
        if key not in keys:
            keys.append(key)
    return [
        {"agent": a, "agent_version": v, "model_name": m, "reasoning_effort": e}
        for a, v, m, e in keys
    ]
