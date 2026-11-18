"""The docker backend on real Docker (ticket 97): network on and off, nothing of the host outside
the workspace, files the sandbox writes show on the host, dbt parses with the stub profile.

colima mounts only $HOME into its VM: a directory elsewhere (pytest's tmp_path, under
/var/folders) is empty in the container, so these workspaces live under ~/.cache.
"""

import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from data_agent.gateway import QueryResult
from data_agent.run_config import REPO_ROOT, skills_path
from data_agent.sandbox import (
    RUN_LABEL,
    SANDBOX_IMAGE,
    DockerSandbox,
    Execution,
    QueryResults,
    execute_tool,
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


def running(sandbox: DockerSandbox) -> Execution:
    """The command line of every process in the container, one per line. From /proc: the image
    has no `ps`."""
    listing = run(sandbox, 'for f in /proc/[0-9]*/cmdline; do tr "\\0" " " < "$f"; echo; done')
    assert listing.exit_code == 0 and "bash" in listing.stdout, listing
    return listing


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
    left = running(sandbox)
    assert "sleep 60" not in left.stdout


def test_a_command_that_ignores_term_is_killed_and_times_out(
    root: Path, sandboxes: list[DockerSandbox]
) -> None:
    sandbox = make(sandboxes, root)
    done = run(sandbox, "trap '' TERM; echo started; sleep 60", timeout=2)
    assert done.timed_out and done.stdout == "started\n"


def test_a_child_that_ignores_term_is_killed_with_the_command(
    root: Path, sandboxes: list[DockerSandbox]
) -> None:
    """Codex review, round 2: `timeout` stops waiting once the shell dies; the child it leaves
    must not live on in the container."""
    sandbox = make(sandboxes, root)
    child = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    done = run(sandbox, f'python3 -c "{child}" & wait', timeout=2)
    assert done.timed_out
    left = running(sandbox)
    assert "time.sleep(60)" not in left.stdout, left.stdout


def test_data_files_go_into_a_data_directory_the_sandbox_made(
    root: Path, sandboxes: list[DockerSandbox]
) -> None:
    """Commands run as the host's user, so the host can still write in what they make."""
    sandbox = make(sandboxes, root)
    results = QueryResults()
    result_id = results.add(QueryResult(["x"], [(1,)]))
    execute_tool(sandbox, results, "mkdir -p data && echo old > data/query_1.csv", 60, [])
    reply = execute_tool(sandbox, results, "cat data/query_1.csv", 60, [result_id])
    assert reply == "exit_code: 0\nstdout:\nx\n1\n"
    owner = (root / "workspace" / "data").stat().st_uid
    assert owner == os.getuid()


def test_the_runs_container_is_removed(root: Path, sandboxes: list[DockerSandbox]) -> None:
    sandbox = make(sandboxes, root)
    run(sandbox, "true")
    assert labelled(f"{RUN_LABEL}={sandbox.run_id}")
    remove_run_containers(sandbox.run_id)
    assert labelled(f"{RUN_LABEL}={sandbox.run_id}") == []


# Started in a session of its own two ways: `setsid`, and Python's start_new_session.
ESCAPED = (
    "setsid sleep 600 & "
    'python3 -c \'import subprocess; subprocess.Popen(["sleep", "601"], '
    "start_new_session=True)'; "
    "sleep 60"
)


def test_processes_that_left_the_commands_process_group_are_killed_at_the_timeout(
    root: Path, sandboxes: list[DockerSandbox]
) -> None:
    """Codex PR review, round 1 return: the kill reached only `timeout`'s process group, so a
    process in a session of its own lived on in the container the run keeps using."""
    sandbox = make(sandboxes, root)
    done = run(sandbox, ESCAPED, timeout=2)
    assert done.timed_out
    left = running(sandbox)
    assert "sleep 600" not in left.stdout and "sleep 601" not in left.stdout, left.stdout


def test_a_process_an_earlier_call_left_running_outlives_a_later_timeout(
    root: Path, sandboxes: list[DockerSandbox]
) -> None:
    """Processes stay between calls (the tool says so); a timeout ends only its command's."""
    sandbox = make(sandboxes, root)
    assert run(sandbox, "setsid sleep 602 >/dev/null 2>&1 &").exit_code == 0
    assert run(sandbox, "sleep 60", timeout=2).timed_out
    left = running(sandbox)
    assert "sleep 602" in left.stdout, left.stdout
