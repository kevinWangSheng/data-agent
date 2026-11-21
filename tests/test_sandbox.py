"""`sandbox_exec`'s backends (ticket 97). The local backend runs for real in a temp directory; the
docker backend is checked by the docker commands it issues, against a stand-in for the docker CLI
(`FakeDocker`). Real Docker is in tests/test_sandbox_docker.py (integration)."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fake_docker import FakeDocker, container_of, install

from data_agent.sandbox import (
    KEEP_BYTES,
    KILL_GRACE_S,
    RUN_LABEL,
    SUPERVISOR,
    TIMEOUT_EXIT,
    DockerSandbox,
    Execution,
    LocalSandbox,
    skill_text,
    supervised,
)

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


def test_a_command_that_ends_in_time_returns_though_its_background_process_holds_the_output(
    tmp_path: Path,
) -> None:
    """As `docker exec` returns once its process ends: the exit code, not a timeout, and the
    output read until then."""
    started = time.monotonic()
    done = LocalSandbox(tmp_path, SKILLS).execute("echo hi; sleep 30 & exit 2", timeout=60)
    assert time.monotonic() - started < KILL_GRACE_S + 5
    assert (done.exit_code, done.timed_out, done.stdout) == (2, False, "hi\n")


def test_a_reader_left_on_a_background_process_takes_no_output_of_the_next_command(
    tmp_path: Path,
) -> None:
    """Codex PR review, round 2 return: the reader still waiting on the first command's output
    lost its pipe once `run_process` returned, and read the next command's pipe under the same
    descriptor number once the background process wrote again. It fails so only on Linux: on
    macOS closing the descriptor ends the blocked read."""
    sandbox = LocalSandbox(tmp_path, SKILLS)
    first = sandbox.execute("(sleep 6; echo late) & echo first", timeout=60)
    assert (first.exit_code, first.stdout) == (0, "first\n")
    # Printing from before the background `echo late` until after it.
    done = sandbox.execute("for i in $(seq 100); do echo $i; sleep 0.03; done", timeout=60)
    assert done.stdout.split() == [str(i) for i in range(1, 101)]


def living(pid: int) -> bool:
    """Whether `pid` runs (a zombie has ended)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    return stat[stat.rfind(")") + 2] != "Z"


@pytest.mark.skipif(sys.platform != "linux", reason="needs /proc and a child subreaper (Linux)")
def test_processes_that_left_the_commands_process_group_are_killed_at_the_timeout(
    tmp_path: Path,
) -> None:
    """Codex PR review, round 1 return: the kill reached only the command's process group."""
    started = (
        "import os, subprocess; "
        "p = subprocess.Popen(['sleep', '601'], start_new_session=True); "
        "open('popen.pid', 'w').write(str(p.pid))"
    )
    command = f'setsid sleep 600 & echo $! > setsid.pid; python3 -c "{started}"; sleep 60'
    done = LocalSandbox(tmp_path, SKILLS).execute(command, timeout=2)
    assert done.timed_out
    for name in ("setsid.pid", "popen.pid"):
        assert not living(int((tmp_path / name).read_text())), name


def test_output_kept_in_memory_is_bounded_per_stream(tmp_path: Path) -> None:
    # Codex review, round 1: the host buffered everything a command printed.
    done = LocalSandbox(tmp_path, SKILLS).execute(
        f"head -c {KEEP_BYTES + 5_000_000} /dev/zero | tr '\\0' o; echo err >&2", timeout=60
    )
    assert (done.exit_code, done.stderr) == (0, "err\n")
    assert len(done.stdout) == KEEP_BYTES
    assert done.dropped == (5_000_000, 0)


def test_local_backend_keeps_credentials_out_of_the_command_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-key-marker")
    monkeypatch.setenv("SHOP_URL", "postgresql://u:pw-marker@db/shop")
    done = LocalSandbox(tmp_path, SKILLS).execute("env", timeout=30)
    assert done.exit_code == 0 and "marker" not in done.stdout and "PATH=" in done.stdout


def test_local_backend_keeps_the_task_containers_own_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticket 100: a Harbor task asks to print DB_TYPE, and its dbt reads DBT_* (DBT_TARGET is the
    adapter's) and DUCKDB_PATH; the image's venv is VIRTUAL_ENV. None holds a credential."""
    for name, value in {
        "DB_TYPE": "duckdb",
        "DBT_TARGET": "dev",
        "DBT_PROFILES_DIR": "/app",
        "DUCKDB_PATH": "/app/db.duckdb",
        "VIRTUAL_ENV": "/opt/venv",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-key-marker")
    done = LocalSandbox(tmp_path, SKILLS).execute("env", timeout=30)
    lines = set(done.stdout.splitlines())
    assert {
        "DB_TYPE=duckdb",
        "DBT_TARGET=dev",
        "DBT_PROFILES_DIR=/app",
        "DUCKDB_PATH=/app/db.duckdb",
        "VIRTUAL_ENV=/opt/venv",
    } <= lines
    assert "marker" not in done.stdout


def test_local_backend_keeps_dbts_secret_variables_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticket 100 return round 1 (Codex P1): DBT_ENV_SECRET_* is where dbt keeps secrets (the
    jaffle profile's Postgres password); a sandbox command could print them or reach the database
    past dbt_execute. dbt_execute's own environment still gets them."""
    monkeypatch.setenv("DBT_TARGET", "dev")
    monkeypatch.setenv("DBT_ENV_SECRET_DA_JAFFLE_PASSWORD", "pw-marker")
    done = LocalSandbox(tmp_path, SKILLS).execute("env", timeout=30)
    assert done.exit_code == 0 and "DBT_TARGET=dev" in done.stdout.splitlines()
    assert "marker" not in done.stdout and "DBT_ENV_SECRET" not in done.stdout


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
    assert second[-1] == "python -c 'import x'"


def test_docker_timeout_kills_the_command_inside_the_container(
    tmp_path: Path, docker: FakeDocker
) -> None:
    # Killing the docker client would leave the command running in the container, so the
    # supervisor runs it there; 124 is its exit code for a command it stopped.
    docker.replies.append(Execution(124, "partial\n", "", False, ran_seconds=1.1))
    done = DockerSandbox(tmp_path, SKILLS, "run-1").execute("sleep 999", timeout=1)
    [exec_] = docker.verb("exec")
    after_container = exec_[exec_.index("-w") + 3 :]
    assert after_container == ["python3", "-I", "-c", SUPERVISOR, "1", "5", "sleep 999"]
    assert (done.timed_out, done.exit_code, done.stdout) == (True, None, "partial\n")


def test_a_commands_own_exit_code_124_is_no_timeout(tmp_path: Path, docker: FakeDocker) -> None:
    # Review round 2: `timeout 1 x` inside the command, or a tool that exits 124 itself.
    docker.replies.append(Execution(124, "", "", False))
    done = DockerSandbox(tmp_path, SKILLS, "run-1").execute("timeout 1 sleep 5", timeout=30)
    assert (done.timed_out, done.exit_code) == (False, 124)


def test_a_commands_own_124_in_time_is_no_timeout_though_its_output_is_read_past_the_limit(
    tmp_path: Path,
) -> None:
    """da-review, round 2 return: the limit was checked once the output was read, which a
    background process holding it kept open past the limit."""
    done = LocalSandbox(tmp_path, SKILLS).execute("sleep 3 & echo ended; exit 124", timeout=1)
    assert (done.timed_out, done.exit_code, done.stdout) == (False, 124, "ended\n")


def run_supervisor(command: str, limit: float, cwd: Path) -> tuple[int, float]:
    """sandbox_supervisor.py run here as the backends run it: its exit code and seconds taken."""
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, *supervised(command, limit)], cwd=cwd, timeout=60, check=False
    )
    return done.returncode, time.monotonic() - started


def test_supervisor_passes_the_commands_exit_code_and_signal(tmp_path: Path) -> None:
    assert run_supervisor("exit 3", 30, tmp_path)[0] == 3
    assert run_supervisor("kill -9 $$", 30, tmp_path)[0] == 137


def test_supervisor_kills_a_command_that_ignores_term_and_exits_124(tmp_path: Path) -> None:
    # Codex review, round 1: a command that ignores TERM is killed after the grace period, and
    # that too is a timeout.
    code, seconds = run_supervisor("trap '' TERM; sleep 999 & wait; sleep 999", 1, tmp_path)
    assert code == TIMEOUT_EXIT and seconds < 1 + 2 * KILL_GRACE_S + 2


def test_a_kill_before_the_limit_is_an_exit_code(tmp_path: Path, docker: FakeDocker) -> None:
    docker.replies.append(Execution(137, "", "", False))
    done = DockerSandbox(tmp_path, SKILLS, "run-1").execute("kill -9 $$", timeout=30)
    assert (done.timed_out, done.exit_code) == (False, 137)


def test_docker_commands_run_as_the_hosts_user(tmp_path: Path, docker: FakeDocker) -> None:
    # Codex review, round 1: root in the container made root-owned files in the workspace,
    # which the host could no longer write to (and root could make device nodes there).
    DockerSandbox(tmp_path, SKILLS, "run-1").execute("true", timeout=30)
    [run] = docker.verb("run")
    assert flag_values(run, "--user") == [f"{os.getuid()}:{os.getgid()}"]


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
