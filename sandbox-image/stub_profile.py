"""Write dbt profiles without credentials for the dbt projects in /workspace.

Adapted from SignalPilot (Apache-2.0, see NOTICE): the stub profile `_seed_project` writes in
`signalpilot/gateway/gateway/mcp/tools/sandbox_vm.py`. Changes: a script of its own, run before
each sandbox command (bash_env.sh), instead of a string sent to the sandbox once; the profile goes
to $DBT_PROFILES_DIR; the target is named `sandbox`; projects are looked for at most two levels
under /workspace instead of at any depth; include every discovered project's profile name.

With it `dbt parse`, `dbt compile` and `dbt ls` work in the sandbox against an empty DuckDB file
in /tmp; dbt commands that reach the data go through the run's own dbt path, never this profile.
"""

import glob
import os
import pathlib

import yaml

# At most two levels down: it runs before every command, and a workspace can hold a large tree.
candidates = [
    path
    for pattern in ("dbt_project.yml", "*/dbt_project.yml", "*/*/dbt_project.yml")
    for path in glob.glob(f"/workspace/{pattern}")
]
profiles: set[str] = set()
for path in candidates:
    try:
        project = yaml.safe_load(pathlib.Path(path).read_text())
    except (OSError, UnicodeError, yaml.YAMLError):
        # dbt will report this project's config error; it needn't block the other profiles.
        continue
    if isinstance(project, dict):
        name = project.get("profile") or project.get("name")
        if isinstance(name, str):
            profiles.add(name)

stub = {
    profile: {
        "target": "sandbox",
        "outputs": {"sandbox": {"type": "duckdb", "path": "/tmp/stub.duckdb", "threads": 1}},
    }
    for profile in sorted(profiles or {"default"})
}
out = pathlib.Path(os.environ["DBT_PROFILES_DIR"])
out.mkdir(parents=True, exist_ok=True)
(out / "profiles.yml").write_text(yaml.safe_dump(stub))
