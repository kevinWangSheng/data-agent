"""Hatch build hook: write the source commit into the wheel.

An installed agent (Harbor installs a wheel) has no checkout to ask git, so `agent.git_commit()`
reads this file first. Editable installs skip it and keep asking git.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Read back by data_agent.agent.git_commit().
PACKAGED_PATH = "data_agent/BUILD_COMMIT"


class BuildCommitHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version == "editable":
            return
        proc = subprocess.run(
            ["git", "-C", self.root, "describe", "--always", "--dirty", "--abbrev=40"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        commit = proc.stdout.strip() if proc.returncode == 0 else ""
        self._tmp = Path(tempfile.mkdtemp())
        path = self._tmp / "BUILD_COMMIT"
        path.write_text(commit or "unknown")
        build_data["force_include"][str(path)] = PACKAGED_PATH

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        if tmp := getattr(self, "_tmp", None):
            shutil.rmtree(tmp, ignore_errors=True)
