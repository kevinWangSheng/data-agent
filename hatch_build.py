"""Hatch build hook: write the source commit and the built web UI into the wheel.

An installed agent (Harbor installs a wheel) has no checkout to ask git, so `agent.git_commit()`
reads `BUILD_COMMIT` first. `data-agent web` serves the UI from `data_agent/web/static`
(data_agent.web.app), so whoever installs the wheel needs no Node; the build output is not in git,
so it is built here, with pnpm, for each wheel. Where pnpm is missing (a slim image building the
agent's wheel for an evaluation) the wheel is built without the UI and the build warns; the API
still works. `DATA_AGENT_WHEEL_UI=0` skips the UI on purpose, without the warning: an evaluation
builds the agent's wheel that way (evals.harbor_agent.build_artifacts), so it needs no pnpm, network
or frontend build. Editable installs skip both: they keep asking git, and a checkout builds its UI
with `pnpm --dir web build:local`.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Read back by data_agent.agent.git_commit().
PACKAGED_PATH = "data_agent/BUILD_COMMIT"
# Served by data_agent.web.app (UI_DIR).
UI_PACKAGED_PATH = "data_agent/web/static"
# Set to "0" for a wheel without the web UI (evals.harbor_agent.build_artifacts).
UI_SWITCH = "DATA_AGENT_WHEEL_UI"


class BuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version == "editable":
            return
        self._tmp = Path(tempfile.mkdtemp())
        path = self._tmp / "BUILD_COMMIT"
        path.write_text(self.commit() or "unknown")
        build_data["force_include"][str(path)] = PACKAGED_PATH
        ui = self._tmp / "ui"
        if self.build_ui(ui):
            build_data["force_include"][str(ui)] = UI_PACKAGED_PATH

    def commit(self) -> str:
        try:
            proc = subprocess.run(
                ["git", "-C", self.root, "describe", "--always", "--dirty", "--abbrev=40"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except OSError:  # no git in this environment
            return ""
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def build_ui(self, out: Path) -> bool:
        """Builds the local UI (web/package.json `build:local`, into `out`); False when turned off
        or without pnpm. A build that fails fails the wheel."""
        if os.environ.get(UI_SWITCH) == "0":
            return False
        pnpm = shutil.which("pnpm")
        if pnpm is None:
            self.app.display_warning(
                "pnpm not found: this wheel has no web UI (`data-agent web` serves the API only)"
            )
            return False
        web = Path(self.root) / "web"
        if not (web / "node_modules").is_dir():
            subprocess.run([pnpm, "--dir", str(web), "install", "--frozen-lockfile"], check=True)
        subprocess.run(
            [
                pnpm,
                "--dir",
                str(web),
                "exec",
                "vite",
                "build",
                "--outDir",
                str(out),
                "--emptyOutDir",
            ],
            env=os.environ | {"VITE_DATA_SOURCE": "local"},
            check=True,
        )
        return True

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        if tmp := getattr(self, "_tmp", None):
            shutil.rmtree(tmp, ignore_errors=True)
