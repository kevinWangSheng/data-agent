"""An installed agent (a wheel, as Harbor installs it) must find its Skills without the checkout."""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from data_agent.agent import QUERY_SKILLS

REPO_ROOT = Path(__file__).resolve().parents[1]


def build_and_extract_wheel(tmp_path: Path, env: dict[str, str] | None = None) -> Path:
    uv = shutil.which("uv")
    assert uv is not None
    proc = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(tmp_path / "dist")],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    (wheel,) = (tmp_path / "dist").glob("*.whl")
    site = tmp_path / "site"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site)
    return site


def run_installed(tmp_path: Path, site: Path, probe: str) -> subprocess.CompletedProcess[str]:
    """The extracted package shadows the editable checkout; dependencies come from the venv."""
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=os.environ | {"PYTHONPATH": str(site)},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_installed_wheel_loads_its_skills(tmp_path: Path) -> None:
    site = build_and_extract_wheel(tmp_path)
    # The extracted package shadows the editable checkout; dependencies still come from the venv.
    probe = (
        "from data_agent import agent; "
        "print(agent.SKILLS_DIR); "
        f"assert agent.load_skills({QUERY_SKILLS!r}, agent.SKILLS_DIR)"
    )
    proc = run_installed(tmp_path, site, probe)
    assert proc.returncode == 0, proc.stderr
    assert Path(proc.stdout.strip()).is_relative_to(site)


def test_installed_wheel_loads_every_run_config(tmp_path: Path) -> None:
    site = build_and_extract_wheel(tmp_path)
    probe = (
        "from data_agent.run_config import CONFIG_NAMES, load_config; "
        "configs = [load_config(name) for name in CONFIG_NAMES]; "
        "assert len(configs) == 6, CONFIG_NAMES; "
        "print(*{c.skills_dir for c in configs}, sep='\\n')"
    )
    proc = run_installed(tmp_path, site, probe)
    assert proc.returncode == 0, proc.stderr
    assert all(Path(line).is_relative_to(site) for line in proc.stdout.splitlines())


def test_installed_wheel_reports_the_commit_it_was_built_from(tmp_path: Path) -> None:
    """Away from the checkout there is no git to ask, so the commit is written in at build time."""
    site = build_and_extract_wheel(tmp_path)
    proc = run_installed(tmp_path, site, "from data_agent import agent; print(agent.git_commit())")
    assert proc.returncode == 0, proc.stderr
    built_from = subprocess.run(
        ["git", "describe", "--always", "--dirty", "--abbrev=40"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert proc.stdout.strip() == built_from


def test_wheel_builds_without_git_and_reports_an_unknown_commit(tmp_path: Path) -> None:
    """An environment without git (a slim image) still builds the wheel."""
    no_git = tmp_path / "bin"
    no_git.mkdir()
    site = build_and_extract_wheel(tmp_path, env=os.environ | {"PATH": str(no_git)})
    assert (site / "data_agent" / "BUILD_COMMIT").read_text() == "unknown"


def test_installed_wheel_loads_the_verifier_agents(tmp_path: Path) -> None:
    site = build_and_extract_wheel(tmp_path)
    probe = (
        "from data_agent import verifiers; "
        "agents = verifiers.verifier_agents('data'); "
        "assert set(agents) == {'verifier', 'value-verifier'}; "
        "print(verifiers.AGENTS_DIR)"
    )
    proc = run_installed(tmp_path, site, probe)
    assert proc.returncode == 0, proc.stderr
    assert Path(proc.stdout.strip()).is_relative_to(site)
