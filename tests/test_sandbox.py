"""`sandbox_exec`'s backends (ticket 97). The local backend runs for real in a temp directory; the
docker backend is checked by the docker commands it issues, against a stand-in for the docker CLI
(`FakeDocker`). Real Docker is in tests/test_sandbox_docker.py (integration)."""

import time
from pathlib import Path

import pytest
from fake_docker import FakeDocker, container_of, install

from data_agent.sandbox import RUN_LABEL, DockerSandbox, Execution, LocalSandbox, skill_text

SKILLS = Path("/opt/skills")


def test_local_backend_runs_in_the_workspace(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path, SKILLS)
    done = sandbox.execute("echo hello; echo oops >&2; echo made > out.txt", timeout=30)
    assert (done.exit_code, done.stdout, done.stderr, done.timed_out) == (
        0,
        "hello\n",
        "oops\n",
        False,
    )
    assert (tmp_path / "out.txt").read_text() == "made\n"


def test_local_backend_reports_a_nonzero_exit_code(tmp_path: Path) -> None:
    done = LocalSandbox(tmp_path, SKILLS).execute("exit 3", timeout=30)
    assert (done.exit_code, done.timed_out) == (3, False)


def test_a_command_past_its_timeout_is_killed_with_its_output_so_far(tmp_path: Path) -> None:
    started = time.monotonic()
    # The background sleep is in the command's process group, so it is killed too.
    done = LocalSandbox(tmp_path, SKILLS).execute(
        "echo before; sleep 30 & sleep 30; echo after", timeout=1
    )
    assert time.monotonic() - started < 10
    assert (done.timed_out, done.exit_code, done.stdout) == (True, None, "before\n")


def test_a_timeout_returns_though_a_process_outside_the_group_keeps_the_output_open(
    tmp_path: Path,
) -> None:
    started = time.monotonic()
    command = "python3 -c 'import os, time; os.setsid(); time.sleep(20)' & echo before; sleep 20"
    done = LocalSandbox(tmp_path, SKILLS).execute(command, timeout=1)
    assert time.monotonic() - started < 15
    assert done.timed_out


def test_local_backend_keeps_credentials_out_of_the_command_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-key-marker")
    monkeypatch.setenv("SHOP_URL", "postgresql://u:pw-marker@db/shop")
    done = LocalSandbox(tmp_path, SKILLS).execute("env", timeout=30)
    assert done.exit_code == 0 and "marker" not in done.stdout and "PATH=" in done.stdout


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    return install(monkeypatch)


def flag_values(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, arg in enumerate(argv) if arg == flag]


def test_docker_container_mounts_only_the_workspace_and_the_skills_read_only(
    tmp_path: Path, docker: FakeDocker
) -> None:
    DockerSandbox(tmp_path, SKILLS, "run-1").execute("true", timeout=30)
    [run] = docker.verb("run")
    assert flag_values(run, "--mount") == [
        f"type=bind,source={tmp_path.resolve()},target=/workspace",
        f"type=bind,source={SKILLS},target=/skills,readonly",
    ]
    assert not {"-v", "--volume", "--volumes-from"} & set(run)
    assert flag_values(run, "-w") == ["/workspace"]


def test_docker_network_is_off_unless_turned_on(tmp_path: Path, docker: FakeDocker) -> None:
    DockerSandbox(tmp_path, SKILLS, "off").execute("true", timeout=30)
    DockerSandbox(tmp_path, SKILLS, "on", network=True).execute("true", timeout=30)
    off, on = docker.verb("run")
    assert flag_values(off, "--network") == ["none"]
    assert not any(arg.startswith("--network") or arg == "--net" for arg in on)


def test_docker_commands_pass_no_environment_and_limit_cpu_and_memory(
    tmp_path: Path, docker: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-key-marker")
    monkeypatch.setenv("SHOP_URL", "postgresql://u:pw-marker@db/shop")
    monkeypatch.setenv("DBT_PROFILES_DIR", "/secret/profiles-marker")
    DockerSandbox(tmp_path, SKILLS, "run-1").execute("true", timeout=30)
    [run] = docker.verb("run")
    assert flag_values(run, "--cpus") and flag_values(run, "--memory")
    for argv in docker.calls:
        assert not {"-e", "--env", "--env-file"} & set(argv)
        assert not any(arg.startswith(("-e=", "--env=", "--env-file=")) for arg in argv)
        text = " ".join(argv)
        assert "marker" not in text and "DATA_AGENT" not in text


def test_container_name_and_label_carry_the_run_id(tmp_path: Path, docker: FakeDocker) -> None:
    DockerSandbox(tmp_path, SKILLS, "2026-11-17T10:00:00-ab12").execute("true", timeout=30)
    [name] = docker.containers
    # A container name allows only [a-zA-Z0-9_.-].
    assert name.endswith("2026-11-17T10-00-00-ab12")
    assert docker.containers[name][RUN_LABEL] == "2026-11-17T10:00:00-ab12"


def test_two_calls_of_a_run_share_one_container(tmp_path: Path, docker: FakeDocker) -> None:
    sandbox = DockerSandbox(tmp_path, SKILLS, "run-1")
    sandbox.execute("pip install x", timeout=30)
    sandbox.execute("python -c 'import x'", timeout=30)
    assert len(docker.verb("run")) == 1
    [name] = docker.containers
    first, second = docker.verb("exec")
    assert container_of(first) == container_of(second) == name
    assert second[-3:] == ["bash", "-c", "python -c 'import x'"]


def test_docker_timeout_kills_the_command_inside_the_container(
    tmp_path: Path, docker: FakeDocker
) -> None:
    # Killing the docker client would leave the command running in the container, so the
    # container's own `timeout` runs it; 124 is its exit code for a command it stopped.
    docker.replies.append(Execution(124, "partial\n", "", False))
    done = DockerSandbox(tmp_path, SKILLS, "run-1").execute("sleep 999", timeout=7)
    [exec_] = docker.verb("exec")
    start = exec_.index("timeout")
    assert exec_[start:] == ["timeout", "-k", "5", "7", "bash", "-c", "sleep 999"]
    assert (done.timed_out, done.exit_code, done.stdout) == (True, None, "partial\n")


def test_skill_directory_is_the_mount_in_docker_and_the_real_path_locally(tmp_path: Path) -> None:
    text = 'python3 "${CLAUDE_SKILL_DIR}/scan_project.py" .'
    docker_sandbox = DockerSandbox(tmp_path, SKILLS, "run-1")
    local_sandbox = LocalSandbox(tmp_path, SKILLS)
    assert skill_text(docker_sandbox, "dbt-workflow", text) == (
        'python3 "/skills/dbt-workflow/scan_project.py" .'
    )
    assert skill_text(local_sandbox, "dbt-workflow", text) == (
        f'python3 "{SKILLS}/dbt-workflow/scan_project.py" .'
    )
