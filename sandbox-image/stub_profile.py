"""Write a dbt profile without credentials for the dbt project in /workspace.

Adapted from SignalPilot (Apache-2.0, see NOTICE): the stub profile `_seed_project` writes in
`signalpilot/gateway/gateway/mcp/tools/sandbox_vm.py`. Changes: a script of its own, run before
each sandbox command (bash_env.sh), instead of a string sent to the sandbox once; the profile goes
to $DBT_PROFILES_DIR; the target is named `sandbox`.

With it `dbt parse`, `dbt compile` and `dbt ls` work in the sandbox against an empty DuckDB file
in /tmp; dbt commands that reach the data go through the run's own dbt path, never this profile.
"""

import glob
import os
import pathlib

import yaml

candidates = glob.glob("/workspace/**/dbt_project.yml", recursive=True)
profile = "default"
if candidates:
    project = yaml.safe_load(pathlib.Path(sorted(candidates, key=len)[0]).read_text()) or {}
    profile = project.get("profile") or project.get("name") or "default"
stub = {
    profile: {
        "target": "sandbox",
        "outputs": {"sandbox": {"type": "duckdb", "path": "/tmp/stub.duckdb", "threads": 1}},
    }
}
out = pathlib.Path(os.environ["DBT_PROFILES_DIR"])
out.mkdir(parents=True, exist_ok=True)
(out / "profiles.yml").write_text(yaml.safe_dump(stub))
