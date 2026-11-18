"""The docker backend on real Docker (ticket 97): network on and off, nothing of the host outside
the workspace, files the sandbox writes show on the host, dbt parses with the stub profile.

colima mounts only $HOME into its VM: a directory elsewhere (pytest's tmp_path, under
/var/folders) is empty in the container, so these workspaces live under ~/.cache.
"""

import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from data_agent.run_config import REPO_ROOT, skills_path
from data_agent.sandbox import (
    RUN_LABEL,
    SANDBOX_IMAGE,
    DockerSandbox,
    Execution,
    labelled,
    remove_run_containers,
)

pytestmark = pytest.mark.integration

TESTS_ROOT = Path.home() / ".cache" / "data-agent-tests"


@pytest.fixture(scope="module", autouse=True)
def image() -> None:
    """Built (or found in the build cache) once per test run, so it matches the Dockerfile."""
    subprocess.run(
        ["docker", "build", "-q", "-t", SANDBOX_IMAGE, str(REPO_ROOT / "sandbox-image")],
        check=True,
        capture_output=True,
        timeout=1800,
    )


@pytest.fixture
def root() -> Iterator[Path]:
    TESTS_ROOT.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(dir=TESTS_ROOT))
    (path / "workspace").mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def sandboxes() -> Iterator[list[DockerSandbox]]:
    """The sandboxes a test makes; their containers are removed after it."""
    made: list[DockerSandbox] = []
    yield made
    for sandbox in made:
        remove_run_containers(sandbox.run_id)


def make(sandboxes: list[DockerSandbox], root: Path, network: bool = False) -> DockerSandbox:
    sandbox = DockerSandbox(
        root / "workspace", skills_path("skills"), f"test-{uuid.uuid4().hex[:8]}", network
    )
    sandboxes.append(sandbox)
    return sandbox


def run(sandbox: DockerSandbox, command: str, timeout: float = 300) -> Execution:
    return sandbox.execute(command, timeout)


# Reinstalled, so pip has to download it even though the image has it already.
INSTALL = (
    "pip install --no-cache-dir --disable-pip-version-check --timeout 20 --retries 0 "
    "--force-reinstall --no-deps six"
)


def test_pip_install_works_with_the_network_on(root: Path, sandboxes: list[DockerSandbox]) -> None:
    done = run(make(sandboxes, root, network=True), f"{INSTALL} && python -c 'import six'")
    assert done.exit_code == 0, done.stderr


def test_pip_install_fails_with_the_network_off(root: Path, sandboxes: list[DockerSandbox]) -> None:
    done = run(make(sandboxes, root), INSTALL)
    assert done.exit_code != 0


def test_nothing_outside_the_workspace_is_visible(
    root: Path, sandboxes: list[DockerSandbox]
) -> None:
    (root / "outside-secret-marker.txt").write_text("secret")
    sandbox = make(sandboxes, root)
    done = run(
        sandbox,
        "cat ../outside-secret-marker.txt; "
        "find / -xdev -name 'outside-secret-marker*' 2>/dev/null; "
        f"ls {root} {Path.home()} 2>&1; env",
    )
    assert "secret\n" not in done.stdout
    assert "outside-secret-marker.txt\n" not in done.stdout
    assert "No such file or directory" in done.stdout
    assert "DATA_AGENT" not in done.stdout
    # The Skills are there, and read-only.
    skills = run(sandbox, "ls /skills/dbt-workflow && touch /skills/x")
    assert "scan_project.py" in skills.stdout and skills.exit_code != 0


def test_files_the_sandbox_writes_show_in_the_workspace(
    root: Path, sandboxes: list[DockerSandbox]
) -> None:
    sandbox = make(sandboxes, root)
    done = run(sandbox, "mkdir -p outputs && echo made > outputs/report.txt")
    assert done.exit_code == 0, done.stderr
    assert (root / "workspace" / "outputs" / "report.txt").read_text() == "made\n"


def test_dbt_parses_the_workspace_project_with_the_stub_profile(
    root: Path, sandboxes: list[DockerSandbox]
) -> None:
    shutil.copytree(REPO_ROOT / "tests" / "fixtures" / "dbt_shop", root / "workspace" / "shop")
    done = run(make(sandboxes, root), "cd shop && dbt parse")
    assert done.exit_code == 0, done.stdout + done.stderr


def test_timeout_stops_the_command_in_the_container(
    root: Path, sandboxes: list[DockerSandbox]
) -> None:
    sandbox = make(sandboxes, root)
    done = run(sandbox, "echo started; sleep 60", timeout=2)
    assert done.timed_out and done.stdout == "started\n"
    left = run(sandbox, "ps -eo args")
    assert "sleep 60" not in left.stdout


def test_the_runs_container_is_removed(root: Path, sandboxes: list[DockerSandbox]) -> None:
    sandbox = make(sandboxes, root)
    run(sandbox, "true")
    assert labelled(f"{RUN_LABEL}={sandbox.run_id}")
    remove_run_containers(sandbox.run_id)
    assert labelled(f"{RUN_LABEL}={sandbox.run_id}") == []
