"""The snapshot: the logic files of every dbt project in the workspace, copied before the agent
starts (spec "快照"). It is the "original" the tools compare against; the agent cannot Read it
because the workspace guard keeps Read inside the workspace.
"""

import hashlib
import json
import shutil
from pathlib import Path

from data_agent.verifier_v2.project import (
    LOGIC_SUFFIXES,
    PROJECT_FILE,
    SEED_PATHS_DEFAULT,
    SEED_PATHS_KEY,
    dbt_projects,
    files_under,
    logic_dirs,
    project_config,
    resource_dirs,
)

SEED_SIZE_LIMIT = 5 * 1024 * 1024  # bytes; larger seeds are recorded, not copied
LARGE_SEEDS_FILE = ".large-seeds.json"


def take_snapshot(workspace: Path, dest: Path) -> Path:
    """Copy each dbt project's dbt_project.yml, the .sql and .yml files under its model, macro,
    snapshot and test paths, and its seeds (those over SEED_SIZE_LIMIT only by name, size and
    SHA-256 in `.large-seeds.json`) to `dest/<project path relative to workspace>/...`.
    dbt_packages/, target/ and logs/ are left out. Returns `dest`."""
    root = workspace.resolve()
    if dest.resolve().is_relative_to(root):
        raise ValueError(f"snapshot destination {dest} is inside the workspace {root}")
    dest.mkdir(parents=True, exist_ok=True)
    for project in dbt_projects(workspace):
        target = dest / project.relative_to(workspace)
        config = project_config(project)
        files = [project / PROJECT_FILE]
        for directory in logic_dirs(config):
            files.extend(files_under(project, directory, LOGIC_SUFFIXES))
        large: list[dict[str, object]] = []
        for directory in resource_dirs(config, SEED_PATHS_KEY, SEED_PATHS_DEFAULT):
            for seed in files_under(project, directory, None):
                size = seed.stat().st_size
                if size <= SEED_SIZE_LIMIT:
                    files.append(seed)
                else:
                    large.append(
                        {
                            "path": str(seed.relative_to(project)),
                            "size": size,
                            "sha256": _sha256(seed),
                        }
                    )
        for path in files:
            copy = target / path.relative_to(project)
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, copy)
        if large:
            (target / LARGE_SEEDS_FILE).write_text(json.dumps(large, indent=1))
    return dest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
