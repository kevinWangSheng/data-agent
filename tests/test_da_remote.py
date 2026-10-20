"""`scripts/da-remote` without a server: a fake `ssh` runs the remote script locally with a fake
server home, a fake `rsync` records its arguments, and a fake `uv` on the server records its own."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DA_REMOTE = REPO_ROOT / "scripts" / "da-remote"

FAKE_SSH = """#!/usr/bin/env bash
printf '%s\\n' "$@" >> "$FAKE_LOG/ssh.args"
echo --- >> "$FAKE_LOG/ssh.args"
if [ -n "${FAKE_SSH_FAIL:-}" ]; then
  echo "ssh: connect to host $FAKE_SSH_FAIL port 22: Connection refused" >&2
  exit 255
fi
cd "$FAKE_SERVER_HOME" && HOME="$FAKE_SERVER_HOME" exec bash -c "${@: -1}"
"""

FAKE_RSYNC = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$FAKE_LOG/rsync.args"
"""

# The server's uv: `sync` succeeds; `run --no-sync python -m evals spend ...` reports a spend line;
# any other evals command prints whether the env file was loaded, then waits for `release`.
FAKE_UV = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$HOME/uv.last"
[ "$1" = sync ] && { echo "fake uv sync"; exit 0; }
[ "$6" = spend ] && { echo "spend: \\$0.1200 over 1 event file(s); budget \\$$9"; exit 0; }
echo "env loaded: ${DATA_AGENT_SMOKE:-no}"
while [ ! -e "$HOME/release" ]; do sleep 0.05; done
exit "$(cat "$HOME/release")"
"""


def write_exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.log = tmp_path / "log"
        self.log.mkdir()
        self.server = tmp_path / "server-home"
        self.repo = self.server / "data-agent"
        self.local = tmp_path / "mac-checkout"
        self.local.mkdir()
        git("init", "-q", cwd=self.local)
        bin_dir = tmp_path / "bin"
        write_exe(bin_dir / "ssh", FAKE_SSH)
        write_exe(bin_dir / "rsync", FAKE_RSYNC)
        write_exe(self.server / ".local" / "bin" / "uv", FAKE_UV)
        envf = self.server / ".config" / "data-agent" / "env"
        envf.parent.mkdir(parents=True)
        envf.write_text("DATA_AGENT_SMOKE=yes\n")
        self.origin = self.make_origin()
        self.vars = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp_path / "mac-home"),
            "FAKE_LOG": str(self.log),
            "FAKE_SERVER_HOME": str(self.server),
            "DA_REMOTE_DIR": str(self.repo),
            "DA_REMOTE_REPO": str(self.origin),
        }

    def make_origin(self) -> Path:
        work = self.tmp / "upstream"
        work.mkdir()
        git("init", "-q", "-b", "main", cwd=work)
        git("config", "user.email", "t@example.com", cwd=work)
        git("config", "user.name", "t", cwd=work)
        (work / ".gitignore").write_text("runs/\n.venv/\n")
        git("add", ".", cwd=work)
        git("commit", "-q", "-m", "first", cwd=work)
        origin = self.tmp / "origin.git"
        git("clone", "-q", "--bare", str(work), str(origin), cwd=self.tmp)
        self.upstream = work
        return origin

    def commit_upstream(self, message: str) -> str:
        (self.upstream / f"{message}.txt").write_text(message)
        git("add", ".", cwd=self.upstream)
        git("commit", "-q", "-m", message, cwd=self.upstream)
        git("push", "-q", str(self.origin), "main", cwd=self.upstream)
        return git("rev-parse", "HEAD", cwd=self.upstream)

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(DA_REMOTE), *args],
            cwd=self.local,
            env=self.vars | extra,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def ssh_calls(self) -> int:
        path = self.log / "ssh.args"
        return path.read_text().count("---\n") if path.exists() else 0

    def wait_for(self, path: Path) -> None:
        deadline = time.monotonic() + 20
        while not path.exists():
            assert time.monotonic() < deadline, f"{path} never appeared"
            time.sleep(0.05)

    def wait_for_evals(self) -> None:
        """Until the server's uv was last called for the evals run (setup calls it for `sync`)."""
        last = self.server / "uv.last"
        deadline = time.monotonic() + 20
        while not (last.exists() and last.read_text().startswith("run\n")):
            assert time.monotonic() < deadline, "the evals run never started"
            time.sleep(0.05)

    def finish(self, code: int = 0) -> None:
        """Let the fake evals run exit with `code` and wait until da-remote's runner noticed."""
        (self.server / "release").write_text(str(code))
        self.wait_for(self.repo / "runs" / "smoke" / "exit")


@pytest.fixture
def env(tmp_path: Path):
    e = Env(tmp_path)
    yield e
    (e.server / "release").write_text("0")  # never leave a fake run waiting


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "../x", "a b", "$(id)", "x;y", "é"])
@pytest.mark.parametrize("command", ["start", "status", "pull", "push"])
def test_bad_run_names_are_refused_before_any_connection(env: Env, command: str, name: str):
    args = [command, name, "--", "ablate"] if command == "start" else [command, name]
    proc = env.run(*args)
    assert proc.returncode == 2
    assert "name" in proc.stderr
    assert env.ssh_calls() == 0
    assert not (env.log / "rsync.args").exists()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["nope"],
        ["start"],
        ["start", "smoke"],
        ["start", "smoke", "ablate"],
        ["start", "smoke", "--"],
        ["pull"],
        ["push"],
        ["setup", "a", "b"],
        ["setup", "--upload-pack=x"],
    ],
)
def test_usage_errors_exit_2_without_connecting(env: Env, args: list[str]):
    proc = env.run(*args)
    assert proc.returncode == 2
    assert "usage" in proc.stderr
    assert env.ssh_calls() == 0


@pytest.mark.parametrize("command", [["check"], ["setup"], ["status"], ["status", "smoke"]])
def test_server_addresses_are_masked_and_the_ssh_exit_code_kept(env: Env, command: list[str]):
    for address in ("203.0.113.7", "2001:db8:0:0:0:0:0:1", "2001:db8::1"):
        proc = env.run(*command, FAKE_SSH_FAIL=address)
        assert proc.returncode == 255
        assert address not in proc.stdout + proc.stderr
        assert "<server>" in proc.stdout


def test_ssh_goes_through_the_host_alias_in_batch_mode(env: Env):
    env.run("status", DA_REMOTE_HOST="my-alias")
    args = (env.log / "ssh.args").read_text().splitlines()
    assert args[:5] == ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "my-alias"]
    assert args[5] == "bash -s"  # the script comes on stdin, never on the remote command line


def test_setup_clones_checks_out_origin_main_and_syncs(env: Env):
    head = git("rev-parse", "HEAD", cwd=env.upstream)
    proc = env.run("setup")
    assert proc.returncode == 0, proc.stdout
    assert git("rev-parse", "HEAD", cwd=env.repo) == head
    assert head[:7] in proc.stdout
    assert "fake uv sync" in proc.stdout
    assert (env.server / "uv.last").read_text() == "sync\n"


def test_setup_fetches_and_moves_to_the_requested_commit(env: Env):
    assert env.run("setup").returncode == 0
    first = git("rev-parse", "HEAD", cwd=env.repo)
    second = env.commit_upstream("second")
    assert env.run("setup").returncode == 0
    assert git("rev-parse", "HEAD", cwd=env.repo) == second
    proc = env.run("setup", first[:10])
    assert proc.returncode == 0, proc.stdout
    assert git("rev-parse", "HEAD", cwd=env.repo) == first


def test_setup_refuses_a_dirty_worktree_and_an_unknown_commit(env: Env):
    assert env.run("setup").returncode == 0
    head = git("rev-parse", "HEAD", cwd=env.repo)
    env.commit_upstream("second")
    (env.repo / ".gitignore").write_text("changed\n")
    proc = env.run("setup")
    assert proc.returncode == 1
    assert "uncommitted" in proc.stdout
    assert git("rev-parse", "HEAD", cwd=env.repo) == head

    (env.repo / ".gitignore").unlink()
    git("checkout", "-q", "--", ".gitignore", cwd=env.repo)
    proc = env.run("setup", "no-such-commit")
    assert proc.returncode == 1
    assert "unknown commit" in proc.stdout
    assert git("rev-parse", "HEAD", cwd=env.repo) == head


def test_setup_refuses_a_directory_that_is_not_a_repo(env: Env):
    env.repo.mkdir()
    proc = env.run("setup")
    assert proc.returncode == 1
    assert "not a git repo" in proc.stdout


def test_start_runs_evals_in_the_background_with_the_server_env(env: Env):
    assert env.run("setup").returncode == 0
    head = git("rev-parse", "--short", "HEAD", cwd=env.repo)
    odd = "a b $HOME 'q' \"d\" `id` ;x"
    proc = env.run("start", "smoke", "--", "ablate", "--budget", "1", "--note", odd)
    assert proc.returncode == 0, proc.stdout
    assert head in proc.stdout
    run = env.repo / "runs" / "smoke"
    env.wait_for_evals()
    uv_args = (env.server / "uv.last").read_text().split("\n")[:-1]
    assert uv_args == [
        *("run", "--no-sync", "python", "-m", "evals"),
        *("ablate", "--budget", "1", "--note", odd),
    ]

    env.finish(3)
    log = (run / "run.log").read_text()
    assert head in log
    assert "env loaded: yes" in log
    assert (run / "exit").read_text().strip() == "3"


def test_a_running_name_is_refused_and_a_finished_one_can_start_again(env: Env):
    assert env.run("setup").returncode == 0
    assert env.run("start", "smoke", "--", "ablate", "--budget", "1").returncode == 0
    proc = env.run("start", "smoke", "--", "ablate", "--budget", "1")
    assert proc.returncode == 1
    assert "already running" in proc.stdout
    assert env.run("setup").returncode == 1  # would change the code under the run

    env.finish(0)
    (env.server / "release").unlink()
    assert env.run("start", "smoke", "--", "ablate", "--budget", "1").returncode == 0
    assert not (env.repo / "runs" / "smoke" / "exit").exists()
    assert (env.repo / "runs" / "smoke" / "run.log").read_text().count("da-remote start") == 2


def test_start_needs_the_repo_and_the_env_file(env: Env):
    proc = env.run("start", "smoke", "--", "ablate")
    assert proc.returncode == 1
    assert "da-remote setup" in proc.stdout

    assert env.run("setup").returncode == 0
    (env.server / ".config" / "data-agent" / "env").unlink()
    proc = env.run("start", "smoke", "--", "ablate")
    assert proc.returncode == 1
    assert "env file" in proc.stdout
    assert not (env.repo / "runs" / "smoke" / "run.pid").exists()


def test_status_shows_state_log_tail_and_spend(env: Env):
    assert env.run("setup").returncode == 0
    assert env.run("start", "smoke", "--", "ablate", "--budget=2.5").returncode == 0
    env.wait_for_evals()
    for _ in range(100):  # the runner's first log line
        if "env loaded" in (env.repo / "runs" / "smoke" / "run.log").read_text():
            break
        time.sleep(0.05)
    proc = env.run("status", "smoke")
    assert proc.returncode == 0, proc.stdout
    assert "smoke: running" in proc.stdout
    assert "env loaded: yes" in proc.stdout
    assert "spend: $0.1200" in proc.stdout
    assert "budget $2.5" in proc.stdout

    env.finish(0)
    proc = env.run("status", "smoke")
    assert "smoke: finished, exit 0" in proc.stdout
    listing = env.run("status")
    assert listing.returncode == 0
    assert "smoke" in listing.stdout and "finished, exit 0" in listing.stdout


def test_a_run_samples_memory_and_status_shows_the_peak(env: Env):
    assert env.run("setup").returncode == 0
    assert env.run("start", "smoke", "--", "ablate", "--budget", "1").returncode == 0
    run = env.repo / "runs" / "smoke"
    env.wait_for(run / "memory.log")
    env.finish(0)

    (run / "memory.log").write_text(
        "2026-10-20T10:00:00Z used_mb=900 total_mb=7900\n"
        "  da-dab-postgres 50MiB / 7.7GiB\n"
        "2026-10-20T10:00:05Z used_mb=2100 total_mb=7900\n"
        "2026-10-20T10:00:10Z used_mb=1500 total_mb=7900\n"
    )
    proc = env.run("status", "smoke")
    assert "memory peak: 2100 MB used (total_mb=7900), at 2026-10-20T10:00:05Z" in proc.stdout


def test_status_of_an_unknown_run(env: Env):
    assert env.run("setup").returncode == 0
    proc = env.run("status", "smoke")
    assert proc.returncode == 1
    assert "no run named smoke" in proc.stdout
    proc = env.run("status")
    assert proc.returncode == 0
    assert "no runs" in proc.stdout


def test_a_run_killed_without_an_exit_code_reads_as_stopped(env: Env):
    assert env.run("setup").returncode == 0
    run = env.repo / "runs" / "smoke"
    run.mkdir(parents=True)
    (run / "run.pid").write_text("999999\n")
    (run / "run.log").write_text("")
    proc = env.run("status", "smoke")
    assert "smoke: stopped without an exit code" in proc.stdout
    assert "spend" in proc.stdout


@pytest.mark.parametrize(
    ("remote_dir", "remote_path"),
    [("~/data-agent", "data-agent/runs/smoke/"), ("/srv/da", "/srv/da/runs/smoke/")],
)
def test_pull_copies_the_server_run_into_the_checkout(env: Env, remote_dir: str, remote_path: str):
    proc = env.run("pull", "smoke", DA_REMOTE_DIR=remote_dir, DA_REMOTE_HOST="my-alias")
    assert proc.returncode == 0, proc.stderr
    local = Path(git("rev-parse", "--show-toplevel", cwd=env.local))
    args = (env.log / "rsync.args").read_text().splitlines()
    assert args[:3] == ["-a", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10"]
    assert args[-2:] == [f"my-alias:{remote_path}", f"{local}/runs/smoke/"]
    assert (local / "runs").is_dir()


SUMMARY_FILTERS = [
    "--include=/reports/***",
    *(f"--include=/{f}" for f in ("run.log", "exit", "budget", "subcommand", "request.json")),
    "--exclude=*",
]


def test_pull_of_a_holdout_run_takes_the_summary_only(env: Env):
    """ADR-0004: an ablate run is on the holdout; its trial dirs and Harbor logs name tasks and stay
    on the server. The reports, the run log (aggregates only) and the control files come back."""
    assert env.run("setup").returncode == 0
    assert env.run("start", "smoke", "--", "ablate", "--budget", "1").returncode == 0
    env.finish(0)
    assert (env.repo / "runs" / "smoke" / "subcommand").read_text().strip() == "ablate"
    proc = env.run("pull", "smoke")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "summary only" in proc.stdout
    args = (env.log / "rsync.args").read_text().splitlines()
    assert args[3:-2] == SUMMARY_FILTERS


@pytest.mark.parametrize("trial_dir", ["jobs", "logs", "dropped"])
def test_a_summary_pull_refuses_a_local_dir_holding_trials(env: Env, trial_dir: str):
    """rsync's filters keep old local files, so per-task files from an earlier full pull would stay
    next to the summary; pull refuses instead of deleting anything local."""
    local = Path(git("rev-parse", "--show-toplevel", cwd=env.local))
    (local / "runs" / "smoke" / trial_dir / "full").mkdir(parents=True)
    (local / "runs" / "smoke" / "manifest.json").write_text("{}")  # a pushed input is fine
    proc = env.run("pull", "smoke")
    assert proc.returncode == 1
    assert f"runs/smoke/{trial_dir}" in proc.stderr
    assert not (env.log / "rsync.args").exists()
    assert (local / "runs" / "smoke" / trial_dir / "full").is_dir()


def test_pull_of_a_run_with_no_recorded_subcommand_takes_the_summary_only(env: Env):
    """Fail closed: a run da-remote did not start may be a holdout run."""
    (env.repo / "runs" / "smoke").mkdir(parents=True)
    proc = env.run("pull", "smoke")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    args = (env.log / "rsync.args").read_text().splitlines()
    assert args[3:-2] == SUMMARY_FILTERS


def test_pull_of_a_dev_or_dab_run_takes_the_whole_dir(env: Env):
    assert env.run("setup").returncode == 0
    (env.server / ".config" / "data-agent" / "dab.env").write_text("")
    assert env.run("start", "smoke", "--", "dab", "--budget", "1").returncode == 0
    env.finish(0)
    proc = env.run("pull", "smoke")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    args = (env.log / "rsync.args").read_text().splitlines()
    assert not any(a.startswith(("--include", "--exclude")) for a in args)


def test_push_copies_a_local_run_dir_to_the_server(env: Env):
    assert env.run("setup").returncode == 0
    local = Path(git("rev-parse", "--show-toplevel", cwd=env.local))
    proc = env.run("push", "smoke")
    assert proc.returncode == 1
    assert "no runs/smoke" in proc.stderr

    (local / "runs" / "smoke").mkdir(parents=True)
    proc = env.run("push", "smoke")
    assert proc.returncode == 0, proc.stdout
    assert (env.repo / "runs").is_dir()  # rsync creates only the last path component
    args = (env.log / "rsync.args").read_text().splitlines()
    assert args[-2:] == [f"{local}/runs/smoke/", f"da-server:{env.repo}/runs/smoke/"]


def test_push_never_touches_a_live_run_or_its_control_files(env: Env):
    assert env.run("setup").returncode == 0
    assert env.run("start", "smoke", "--", "ablate", "--budget", "1").returncode == 0
    local = Path(git("rev-parse", "--show-toplevel", cwd=env.local))
    (local / "runs" / "smoke").mkdir(parents=True)
    proc = env.run("push", "smoke")
    assert proc.returncode == 1
    assert "running" in proc.stdout
    assert not (env.log / "rsync.args").exists()

    env.finish(0)
    assert env.run("push", "smoke").returncode == 0
    args = (env.log / "rsync.args").read_text().splitlines()
    for control in ("run.pid", "run.sh", "run.log", "exit", "budget", "subcommand", "memory.log"):
        assert f"--exclude=/{control}" in args


def test_setup_clones_over_ssh_by_default():
    """The server reads the private repo with a deploy key, which GitHub accepts over SSH only."""
    text = DA_REMOTE.read_text()
    assert 'DA_REMOTE_REPO:-git@github.com:kevinWangSheng/data-agent.git}"' in text


def test_pull_outside_a_checkout_is_refused(env: Env, tmp_path: Path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    proc = subprocess.run(
        [str(DA_REMOTE), "pull", "smoke"],
        cwd=elsewhere,
        env=env.vars | {"GIT_CEILING_DIRECTORIES": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "checkout" in proc.stderr
    assert not (env.log / "rsync.args").exists()


def test_the_script_is_shellcheck_clean():
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck not installed")
    proc = subprocess.run([shellcheck, str(DA_REMOTE)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout


# `benchmarks` and the benchmark lines of `check`: fake git (clones of the public benchmark repos;
# anything else goes to the real git), docker, uv and curl on the server record their calls.
FAKE_BENCH_GIT = """#!/usr/bin/env bash
printf '%s|LFS=%s\\n' "$*" "${GIT_LFS_SKIP_SMUDGE:-}" >> "$HOME/git.calls"
if [ "$1" = clone ] && [[ $* == *github.com* ]]; then
  dir="${@: -1}"
  mkdir -p "$dir/.git"
  echo upstream-head > "$dir/.git/FAKE_HEAD"
  case $dir in
    */DataAgentBench)
      mkdir -p "$dir/query_x"
      printf 'stub' > "$dir/query_x/data.db"
      printf '# path\\tsha256\\tsize\\nquery_x/data.db\\tabc\\t5\\n' > "$dir/dataset_manifest.tsv"
      ;;
    */data-eng-bench)
      mkdir -p "$dir/tasks/t1" && touch "$dir/tasks/t1/task.toml"
      ;;
  esac
  exit 0
fi
if [ "$1" = -C ] && [[ $2 == */da-benchmarks/* ]]; then
  d=$2
  shift 2
  case $1 in
    rev-parse) exec cat "$d/.git/FAKE_HEAD" 2>/dev/null ;;
    diff) [[ $d == */DataAgentBench ]] && printf 'query_x/data.db\\n%s' "${FAKE_DAB_EDITED:-}" ;;
    cat-file) exit 1 ;;
    checkout) echo "${@: -1}" > "$d/.git/FAKE_HEAD" ;;
  esac
  exit 0
fi
exec REAL_GIT "$@"
"""

# download.sh "fetches" the data file; the probe reports both databases up unless FAKE_DB_DOWN.
FAKE_BENCH_UV = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$HOME/uv.calls"
case "$*" in
  *download.sh*)
    if [ -n "${FAKE_DOWNLOAD_FAIL:-}" ]; then echo "  [FAIL] query_x/data.db"; exit 1; fi
    echo "  [ok  ] query_y/other.db"
    echo "  [get ] query_x/data.db (0.0 MB)"
    printf '12345' > query_x/data.db
    echo "All datasets present and verified."
    ;;
  *dab-requirements.txt*)
    cat > /dev/null
    printf '%s' "$PG_PASSWORD" > "$HOME/probe.password"
    if [ -n "${FAKE_DB_DOWN:-}" ]; then
      echo "postgres FAIL OperationalError: connection refused"; echo "mongodb ok"; exit 1
    fi
    echo "postgres ok"; echo "mongodb ok"
    ;;
esac
"""

FAKE_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$HOME/docker.calls"
st="$HOME/docker-state"
mkdir -p "$st"
case $1 in
  info) exit 0 ;;
  image) [ -e "$st/image" ] ;;
  build) touch "$st/image" ;;
  inspect) [ -e "$st/${@: -1}" ] && cat "$st/${@: -1}" ;;
  start) echo true > "$st/$2" ;;
  run)
    while [ $# -gt 0 ]; do [ "$1" = --name ] && name=$2; shift; done
    echo true > "$st/$name"
    printf '%s' "${POSTGRES_PASSWORD:-}" > "$st/$name.password"
    echo 0123456789ab
    ;;
esac
"""

FAKE_CURL = """#!/usr/bin/env bash
printf 401
"""

DEB_SHA = "a3278ad102829a6084dde086244a0ef665a8011c"
DAB_SHA = "6ad9d689393ff13b51a6bdcc0b98d1d4d8209f36"


@pytest.fixture
def bench(env: Env) -> Env:
    real_git = shutil.which("git")
    assert real_git
    server_bin = env.server / ".local" / "bin"
    write_exe(server_bin / "git", FAKE_BENCH_GIT.replace("REAL_GIT", real_git))
    write_exe(server_bin / "uv", FAKE_BENCH_UV)
    write_exe(server_bin / "docker", FAKE_DOCKER)
    write_exe(server_bin / "curl", FAKE_CURL)
    (env.repo / "evals").mkdir(parents=True)
    (env.repo / "evals" / "dab-requirements.txt").write_text("pymongo\n")
    return env


def calls(env: Env, tool: str) -> list[str]:
    path = env.server / f"{tool}.calls"
    return path.read_text().splitlines() if path.exists() else []


def test_benchmarks_prepares_both_benchmarks_and_the_dab_databases(bench: Env):
    proc = bench.run("benchmarks")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    base = bench.server / "da-benchmarks"
    git_calls = calls(bench, "git")
    for url, path in [
        ("https://github.com/Snowflake-Labs/data-eng-bench.git", base / "data-eng-bench"),
        ("https://github.com/ucbepic/DataAgentBench.git", base / "DataAgentBench"),
    ]:
        assert f"clone --quiet {url} {path}|LFS=1" in git_calls
    assert (base / "data-eng-bench" / ".git" / "FAKE_HEAD").read_text().strip() == DEB_SHA
    assert (base / "DataAgentBench" / ".git" / "FAKE_HEAD").read_text().strip() == DAB_SHA

    uv_calls = calls(bench, "uv")
    assert "run -q --no-project --with huggingface_hub>=0.23 bash download.sh" in uv_calls
    assert "[get ] query_x/data.db" in proc.stdout
    assert "[ok  ]" not in proc.stdout  # matched files are not listed one by one
    for setting in ("process ", "clean ", "smudge ", "required false"):
        assert f"-C {base / 'DataAgentBench'} config filter.lfs.{setting}|LFS=" in git_calls
    assert "only the downloaded data differs from the LFS stubs" in proc.stdout

    docker = calls(bench, "docker")
    assert f"build -q -t python-data:3.12 {base / 'DataAgentBench'}" in docker
    runs = [c for c in docker if c.startswith("run ")]
    assert len(runs) == 2
    for c in runs:
        published = [a for a in c.split() if a.count(":") == 2 and not a.endswith(":ro")]
        assert published and all(p.startswith("127.0.0.1:") for p in published)
        assert f"{base / 'DataAgentBench'}:{base / 'DataAgentBench'}:ro" in c
    assert "--name da-dab-postgres" in runs[0] and "postgres:17" in runs[0]
    assert "--name da-dab-mongo" in runs[1] and "mongo:8" in runs[1]

    settings = bench.server / ".config" / "data-agent" / "dab.env"
    assert settings.stat().st_mode & 0o777 == 0o600
    password = next(
        line.split("=", 1)[1]
        for line in settings.read_text().splitlines()
        if line.startswith("PG_PASSWORD=")
    )
    assert len(password) == 32
    assert password not in proc.stdout + proc.stderr
    assert not any(password in c for c in docker)  # off the command line
    state = bench.server / "docker-state"
    assert (state / "da-dab-postgres.password").read_text() == password
    assert (bench.server / "probe.password").read_text() == password
    for tool in ("psql", "mongorestore"):
        assert os.access(base / "bin" / tool, os.X_OK)
    assert "postgres ok" in proc.stdout and "mongodb ok" in proc.stdout
    assert "--tasks-dir ../da-benchmarks/data-eng-bench/tasks" in proc.stdout


@pytest.mark.parametrize(
    ("tool", "container"), [("psql", "da-dab-postgres"), ("mongorestore", "da-dab-mongo")]
)
def test_the_loader_wrappers_leave_the_mcp_stream_alone(bench: Env, tool: str, container: str):
    """DAB's loaders run inside its MCP server; a wrapper that passed stdin to `docker exec -i`
    swallowed the MCP handshake, and the server never connected (ticket 21, bookreview run)."""
    assert bench.run("benchmarks").returncode == 0
    recorder = bench.tmp / "docker-bin" / "docker"
    write_exe(recorder, '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" > "$OUT"\ncat > "$OUT.stdin"\n')
    out = bench.tmp / f"{tool}.args"
    proc = subprocess.run(
        [str(bench.server / "da-benchmarks" / "bin" / tool), "-d", "db", "-f", "/x.sql"],
        input="initialize request",
        env=os.environ
        | {"PATH": f"{recorder.parent}{os.pathsep}{os.environ['PATH']}", "OUT": str(out)},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    args = out.read_text().split()
    assert args[0] == "exec" and "-i" not in args and container in args
    assert args[-4:] == ["-d", "db", "-f", "/x.sql"]
    assert Path(f"{out}.stdin").read_text() == ""


def test_benchmarks_skips_what_is_ready(bench: Env):
    assert bench.run("benchmarks").returncode == 0
    settings = bench.server / ".config" / "data-agent" / "dab.env"
    before = settings.read_text()
    for tool in ("git", "uv", "docker"):
        (bench.server / f"{tool}.calls").unlink()
    proc = bench.run("benchmarks")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert settings.read_text() == before  # the password stays the one the database has
    assert not any(c.startswith("clone") or " checkout " in c for c in calls(bench, "git"))
    assert not any("download.sh" in c for c in calls(bench, "uv"))
    assert not any(c.startswith(("build", "run", "start")) for c in calls(bench, "docker"))
    assert proc.stdout.count("already") == 6


def test_benchmarks_starts_stopped_containers_and_redownloads_changed_data(bench: Env):
    assert bench.run("benchmarks").returncode == 0
    (bench.server / "docker-state" / "da-dab-mongo").write_text("false\n")
    (bench.server / "da-benchmarks" / "DataAgentBench" / "query_x" / "data.db").write_text("x")
    proc = bench.run("benchmarks")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "start da-dab-mongo" in calls(bench, "docker")
    assert "da-dab-mongo: started" in proc.stdout
    assert sum("download.sh" in c for c in calls(bench, "uv")) == 2


def test_benchmarks_names_tracked_dab_changes_besides_the_data(bench: Env):
    proc = bench.run("benchmarks", FAKE_DAB_EDITED="common_scaffold/config.py\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 tracked file(s) changed besides the data" in proc.stdout
    assert "common_scaffold/config.py" in proc.stdout


def test_benchmarks_stops_when_dab_download_fails(bench: Env):
    proc = bench.run("benchmarks", FAKE_DOWNLOAD_FAIL="1")
    assert proc.returncode == 1
    assert "download.sh failed" in proc.stdout
    assert "[FAIL] query_x/data.db" in proc.stdout
    assert not any(c.startswith(("build", "run")) for c in calls(bench, "docker"))
    assert not (bench.server / "da-benchmarks" / "dab-data.ok").exists()


def test_benchmarks_needs_the_repo(bench: Env):
    shutil.rmtree(bench.repo / "evals")
    proc = bench.run("benchmarks")
    assert proc.returncode == 1
    assert "da-remote setup" in proc.stdout
    assert calls(bench, "git") == []


@pytest.mark.parametrize("args", [["benchmarks", "x"], ["check", "x"]])
def test_benchmarks_and_check_take_no_arguments(env: Env, args: list[str]):
    proc = env.run(*args)
    assert proc.returncode == 2
    assert "usage" in proc.stderr
    assert env.ssh_calls() == 0


def test_check_reports_the_benchmarks(bench: Env):
    proc = bench.run("check")
    for missing in [
        "FAIL  data-eng-bench missing",
        "FAIL  no data-eng-bench tasks",
        "FAIL  DataAgentBench missing",
        "FAIL  no image python-data:3.12",
        "FAIL  DAB database settings missing",
    ]:
        assert missing in proc.stdout, proc.stdout

    assert bench.run("benchmarks").returncode == 0
    proc = bench.run("check")
    for ready in [
        "ok    data-eng-bench at a3278ad",
        "ok    data-eng-bench tasks: 1",
        "ok    DataAgentBench at 6ad9d68",
        "ok    DAB data: all 1 files of dataset_manifest.tsv, sizes match",
        "ok    image python-data:3.12",
        "ok    DAB database settings present, mode 600",
        "ok    postgres reachable",
        "ok    mongodb reachable",
    ]:
        assert ready in proc.stdout

    (bench.server / "da-benchmarks" / "DataAgentBench" / "query_x" / "data.db").write_text("x")
    proc = bench.run("check", FAKE_DB_DOWN="1")
    assert "FAIL  DAB data: 1 of 1 files missing or of the wrong size" in proc.stdout
    assert "FAIL  postgres FAIL OperationalError" in proc.stdout
    assert "ok    mongodb reachable" in proc.stdout


def test_a_dab_run_gets_dab_packages_and_database_settings(env: Env):
    assert env.run("setup").returncode == 0
    proc = env.run("start", "smoke", "--", "dab", "--budget", "1")
    assert proc.returncode == 1
    assert "da-remote benchmarks" in proc.stdout
    assert not (env.repo / "runs" / "smoke").exists()

    (env.server / ".config" / "data-agent" / "dab.env").write_text("DATA_AGENT_SMOKE=dab\n")
    assert env.run("start", "smoke", "--", "dab", "--budget", "1").returncode == 0
    env.wait_for_evals()
    uv_args = (env.server / "uv.last").read_text().split("\n")[:-1]
    assert uv_args == [
        *("run", "--no-sync", "--with-requirements", "evals/dab-requirements.txt"),
        *("python", "-m", "evals", "dab", "--budget", "1"),
    ]
    env.finish(0)
    assert "env loaded: dab" in (env.repo / "runs" / "smoke" / "run.log").read_text()
