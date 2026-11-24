"""The Harbor adapter's export helpers (ticket 112), below what
tests/acceptance/test_observability_evals.py pins: a registry task's name, the manifest that cannot
be read, and where the one-export claim is kept."""

import json
from pathlib import Path

from harbor.models.trial.config import TaskConfig, TrialConfig

from evals import harbor_agent


def write_trial(trial_dir: Path, task: TaskConfig) -> Path:
    trial_dir.mkdir(parents=True)
    config = TrialConfig(task=task, trial_name=trial_dir.name)
    (trial_dir / "config.json").write_text(config.model_dump_json())
    return trial_dir


def test_a_registry_task_is_named_without_its_org(tmp_path: Path) -> None:
    """`evals board` runs registry tasks, named `<org>/<task>`; the manifest names the task."""
    trial = write_trial(
        tmp_path / "job" / "t__1", TaskConfig(name="data-eng-bench/dbt-alpha", ref="sha256:ab")
    )
    assert harbor_agent.task_name(trial) == "dbt-alpha"
    local = write_trial(tmp_path / "job" / "t__2", TaskConfig(path=Path("tasks/dbt-delta")))
    assert harbor_agent.task_name(local) == "dbt-delta"
    assert harbor_agent.task_name(tmp_path / "missing") is None
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "config.json").write_text("{not json")
    assert harbor_agent.task_name(tmp_path / "broken") is None


def test_a_manifest_that_cannot_be_read_gives_the_held_out_tier(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"dev": ["dbt-alpha"], "holdout": ["dbt-delta"]}))
    assert harbor_agent.split_tier("dbt-alpha", manifest) == "full"
    assert harbor_agent.split_tier(None, manifest) == "structure"
    for broken in ("{", json.dumps({"holdout": []}), json.dumps({"dev": "dbt-alpha"})):
        manifest.write_text(broken)
        assert harbor_agent.split_tier("dbt-alpha", manifest) == "structure"
    assert harbor_agent.split_tier("dbt-alpha", tmp_path / "missing.json") == "structure"


def test_the_claim_is_a_plain_file_at_the_jobs_top_level(tmp_path: Path) -> None:
    """Harbor's resume deletes the job's directories without a result.json, and keeps files."""
    job = tmp_path / "job"
    job.mkdir()
    assert harbor_agent.claim_trace(job, "dbt-alpha")
    assert not harbor_agent.claim_trace(job, "dbt-alpha")
    assert harbor_agent.claim_trace(job, "dbt-delta")
    claims = sorted(p.name for p in job.iterdir())
    assert claims == [".data-agent-trace-dbt-alpha", ".data-agent-trace-dbt-delta"]
    assert all((job / name).is_file() for name in claims)
    assert not harbor_agent.claim_trace(tmp_path / "no-such-job", "dbt-alpha")
