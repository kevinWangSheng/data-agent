"""An installed agent (a wheel, as Harbor installs it) must find its Skills without the checkout."""

import os
import subprocess
import sys
import zipfile
from pathlib import Path

from data_agent.agent import QUERY_SKILLS

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_installed_wheel_loads_its_skills(tmp_path: Path) -> None:
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path / "dist")],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        timeout=120,
    )
    (wheel,) = (tmp_path / "dist").glob("*.whl")
    site = tmp_path / "site"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site)

    # The extracted package shadows the editable checkout; dependencies still come from the venv.
    probe = (
        "from data_agent import agent; "
        "print(agent.SKILLS_DIR); "
        f"assert agent.load_skills({QUERY_SKILLS!r})"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=os.environ | {"PYTHONPATH": str(site)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert Path(proc.stdout.strip()).is_relative_to(site)
