"""The isolated environment `sandbox_exec` runs shell commands in (ticket 97, ADR-0010).

After SignalPilot's `sandbox_exec` (Apache-2.0, see NOTICE; `gateway/mcp/tools/sandbox_vm.py`):
one sandbox per run, created on first use, holding no database credentials. Two backends:

- `docker`: a container per run, its working directory the workspace (a bind mount).
- `local`: this machine, for a run that is itself inside an isolated container (a Harbor task).
"""

import contextlib
import csv
import io
import itertools
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Literal, NamedTuple, Protocol

from data_agent import sandbox_supervisor
from data_agent.gateway import QueryResult
from data_agent.sandbox_supervisor import TIMEOUT_EXIT

type Backend = Literal["docker", "local"]
BACKENDS: tuple[Backend, ...] = ("docker", "local")

# The docker backend's image (sandbox-image/Dockerfile, `make sandbox-image`). Not `docker/`: a
# directory of that name at the repo root would be imported as the Docker SDK (evals/dab.py).
SANDBOX_IMAGE = "data-agent-sandbox"
# Where the container sees the workspace and the run's Skill directory (read-only).
WORKDIR = "/workspace"
SKILLS_MOUNT = "/skills"
# Limits on each container, after a data-eng-bench task's 2 cores (plan §5); memory is a cap.
CPUS = "2"
MEMORY = "4g"
# sandbox_exec's limits: SignalPilot's default command timeout, and its execution and dbt limit
# (900 s) as the most a call may ask for (`[sandbox].exec_max_seconds` sets that, ticket 144).
DEFAULT_TIMEOUT_S = 300
MAX_TIMEOUT_S = 900
# A command runs under sandbox_supervisor.py (as `python -I -c`, in the container or here): at
# its limit, every process it started gets TERM, and KILL this many seconds later, whatever
# process group it is in. The supervisor then exits 124, which is a timeout only once the limit
# has passed: a command can exit 124 itself.
SUPERVISOR = Path(sandbox_supervisor.__file__).read_text()
KILL_GRACE_S = 5
# How long the docker client, or the supervisor here, may take past a command's limit before it
# is killed too; the supervisor itself needs up to twice KILL_GRACE_S.
CLIENT_MARGIN_S = 30
# Any other docker call (start, list, remove).
DOCKER_TIMEOUT_S = 120
# Every sandbox container carries its run's id and the pid of the MCP server that started it.
RUN_LABEL = "data-agent.sandbox.run"
OWNER_LABEL = "data-agent.sandbox.owner"
NAME_PREFIX = "data-agent-sandbox-"

# The variables a local command keeps from the server's environment. The rest (the model key, the
# Postgres URL, every DATA_AGENT_* setting) stay out of reach of the sandbox.
LOCAL_ENV = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "TERM",
)
# What a local command also keeps: the variables a Harbor task's image and its tasks use (a task
# asks to print DB_TYPE; dbt and the task's profile read DBT_* and DUCKDB_PATH; VIRTUAL_ENV is
# the image's Python). None of them holds a credential, except dbt's DBT_ENV_SECRET_*: dbt keeps
# secrets there (a profile's password), so they stay out; dbt_execute's own environment has them.
LOCAL_TASK_ENV = ("DB_TYPE", "DUCKDB_PATH", "VIRTUAL_ENV")
LOCAL_TASK_PREFIXES = ("DBT_",)
LOCAL_SECRET_PREFIXES = ("DBT_ENV_SECRET_",)


class SandboxError(Exception):
    """The sandbox could not be reached or started."""


class Execution(NamedTuple):
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    # The bytes of stdout and stderr past KEEP_BYTES, read and dropped.
    dropped: tuple[int, int] = (0, 0)
    # How long the process ran, to its end: its output may be read for longer.
    ran_seconds: float = 0.0


# What the host keeps of each stream of a command, at most: past it the output is read and
# dropped, so a command that prints without end cannot fill the server's memory.
KEEP_BYTES = 10_000_000


class Capture:
    """Reads a pipe to its end in a thread of its own, keeping its first KEEP_BYTES. The thread
    owns the pipe and closes it at the end: a reader `run_process` stopped waiting for (a process
    the command left running holds the pipe open) keeps its descriptor until then, so the number
    is not handed to the next command's pipe while the reader may still read it."""

    def __init__(self, pipe: IO[bytes]) -> None:
        self.kept = bytearray()
        self.dropped = 0
        self.thread = threading.Thread(target=self.read, args=(pipe,), daemon=True)
        self.thread.start()

    def read(self, pipe: IO[bytes]) -> None:
        with pipe, contextlib.suppress(OSError):
            while chunk := os.read(pipe.fileno(), 65536):
                room = KEEP_BYTES - len(self.kept)
                self.kept += chunk[:room]
                self.dropped += max(0, len(chunk) - room)

    def text(self) -> str:
        return bytes(self.kept).decode(errors="replace")


def run_process(
    argv: Sequence[str],
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Execution:
    """Run `argv` to its end or for `timeout` seconds; past that its whole process group is killed
    and the output it wrote so far comes back with `timed_out`. The output is read until it
    closes, for KILL_GRACE_S at most once the process has ended: a process it left running may
    hold the output open (and `docker exec`, too, returns once its process ends). The kill
    reaches the process group only: the local backend's supervisor puts the command in a
    session of its own, so a supervisor that hung would leave the command running."""
    proc = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=None if env is None else dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    began = time.monotonic()
    assert proc.stdout is not None and proc.stderr is not None
    captures = (Capture(proc.stdout), Capture(proc.stderr))
    try:
        proc.wait(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    ended = time.monotonic()
    for capture in captures:
        capture.thread.join(max(0.0, ended + KILL_GRACE_S - time.monotonic()))
    out, err = captures
    return Execution(
        None if timed_out else proc.returncode,
        out.text(),
        err.text(),
        timed_out,
        (out.dropped, err.dropped),
        ended - began,
    )


def supervised(command: str, timeout: float) -> list[str]:
    """The arguments of a Python interpreter that runs `command` under sandbox_supervisor.py."""
    return ["-I", "-c", SUPERVISOR, f"{timeout:g}", f"{KILL_GRACE_S:g}", command]


def timeout_past_limit(done: Execution, timeout: float) -> Execution:
    """A supervised command's outcome: the supervisor's 124 past the limit is a timeout. The limit
    is checked against the time the supervisor ran, not the time its output was read."""
    stopped = done.exit_code == TIMEOUT_EXIT and done.ran_seconds >= timeout
    if done.timed_out or stopped:
        return done._replace(exit_code=None, timed_out=True)
    return done


def local_env(env: Mapping[str, str]) -> dict[str, str]:
    return {name: env[name] for name in LOCAL_ENV if name in env}


def reaches_local_commands(name: str) -> bool:
    """Whether a local sandbox command keeps the variable `name` (`local_command_env`)."""
    if name.startswith(LOCAL_SECRET_PREFIXES):
        return False
    return name in LOCAL_ENV or name in LOCAL_TASK_ENV or name.startswith(LOCAL_TASK_PREFIXES)


def local_command_env(env: Mapping[str, str]) -> dict[str, str]:
    """A local sandbox command's environment: `local_env` and the task's own variables."""
    return {name: value for name, value in env.items() if reaches_local_commands(name)}


@dataclass
class LocalSandbox:
    """Commands run on this machine, in the workspace, with a scrubbed environment."""

    workspace: Path
    skills_dir: Path
    # The longest a sandbox_exec call may ask for (`[sandbox].exec_max_seconds`, ticket 144).
    max_seconds: float = MAX_TIMEOUT_S

    @property
    def workdir(self) -> str:
        return str(self.workspace.resolve())

    @property
    def skills_root(self) -> str:
        return str(self.skills_dir)

    def execute(self, command: str, timeout: float) -> Execution:
        argv = [sys.executable, *supervised(command, timeout)]
        env = local_command_env(os.environ)
        done = run_process(argv, timeout + CLIENT_MARGIN_S, cwd=self.workspace, env=env)
        return timeout_past_limit(done, timeout)


def run_docker(args: Sequence[str], timeout: float = DOCKER_TIMEOUT_S) -> Execution:
    """One call of the docker CLI. Every docker call goes through here (tests replace it)."""
    try:
        return run_process(["docker", *args], timeout)
    except FileNotFoundError as exc:
        raise SandboxError("docker is not installed here (no `docker` on PATH)") from exc


def docker_on_path() -> bool:
    """Whether this machine has the docker CLI, which every docker call runs."""
    return shutil.which("docker") is not None


def container_name(run_id: str) -> str:
    """A container name allows only [a-zA-Z0-9_.-]; anything else in the run id becomes `-`."""
    return NAME_PREFIX + re.sub(r"[^a-zA-Z0-9_.-]", "-", run_id)


@dataclass
class DockerSandbox:
    """A container per run, started on the first command and kept for the rest (packages and
    files stay); the run removes it (`docker_lifecycle`). The container gets the workspace as its
    working directory and the Skill directory read-only, no environment variable of the server's,
    and no network unless `network`."""

    workspace: Path
    skills_dir: Path
    run_id: str
    network: bool = False
    # `[sandbox]`'s `image`, `cpus`, `memory` and `exec_max_seconds` (ticket 144).
    image: str = SANDBOX_IMAGE
    cpus: str = CPUS
    memory: str = MEMORY
    max_seconds: float = MAX_TIMEOUT_S
    container: str | None = field(default=None, init=False)
    lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def workdir(self) -> str:
        return WORKDIR

    @property
    def skills_root(self) -> str:
        return SKILLS_MOUNT

    def start(self) -> str:
        name = container_name(self.run_id)
        argv = [
            *("run", "-d", "--init", "--pull", "never", "--name", name),
            *("--label", f"{RUN_LABEL}={self.run_id}", "--label", f"{OWNER_LABEL}={os.getpid()}"),
            # As the host's user, so the files it makes in the workspace stay the host's to change
            # (on a Linux host; Docker Desktop and colima map ownership anyway).
            *("--user", f"{os.getuid()}:{os.getgid()}"),
            *(() if self.network else ("--network", "none")),
            *("--cpus", self.cpus, "--memory", self.memory),
            *("--mount", f"type=bind,source={self.workspace.resolve()},target={WORKDIR}"),
            *("--mount", f"type=bind,source={self.skills_dir},target={SKILLS_MOUNT},readonly"),
            *("-w", WORKDIR, self.image, "sleep", "infinity"),
        ]
        done = run_docker(argv)
        if done.exit_code != 0:
            hint = (
                "; build the image with `make sandbox-image`"
                if self.image == SANDBOX_IMAGE and "No such image" in done.stderr
                else ""
            )
            raise SandboxError(
                f"could not start the sandbox container ({self.image}): {done.stderr.strip()}{hint}"
            )
        return name

    def execute(self, command: str, timeout: float) -> Execution:
        with self.lock:
            if self.container is None:
                self.container = self.start()
        # Killing the docker client would leave the command running in the container, so the
        # supervisor runs in the container.
        argv = ["exec", "-w", WORKDIR, self.container, "python3", *supervised(command, timeout)]
        done = run_docker(argv, timeout + CLIENT_MARGIN_S)
        return timeout_past_limit(done, timeout)


class Sandbox(Protocol):
    workspace: Path
    skills_dir: Path
    max_seconds: float

    @property
    def workdir(self) -> str: ...

    @property
    def skills_root(self) -> str: ...

    def execute(self, command: str, timeout: float) -> Execution: ...


def skill_text(sandbox: Sandbox, name: str, text: str) -> str:
    """A Skill's text with `${CLAUDE_SKILL_DIR}` as commands in `sandbox` see that directory."""
    return text.replace("${CLAUDE_SKILL_DIR}", f"{sandbox.skills_root}/{name}")


class SandboxRun(NamedTuple):
    """A run's `--sandbox` choice, its id and its `--network` switch, as the run passes them to
    its MCP server."""

    backend: Backend
    run_id: str
    network: bool = False

    @classmethod
    def of_run(cls, backend: Backend, events_dir: Path, network: bool = False) -> "SandboxRun":
        """The id is the `--events-dir` directory's name, which names the run (the web UI's run
        id), and a part of its own: runs in other worktrees may use the same name at once, and
        each removes only its own container."""
        return cls(backend, f"{events_dir.resolve().name}-{uuid.uuid4().hex[:8]}", network)

    def server_args(self) -> tuple[str, ...]:
        network = ("--network",) if self.network else ()
        return ("--sandbox", self.backend, "--run-id", self.run_id, *network)


def labelled(label: str) -> list[tuple[str, str]]:
    """The containers carrying `label` (`key` or `key=value`): each one's name and owner pid."""
    done = run_docker(
        [
            "ps",
            "-a",
            "--filter",
            f"label={label}",
            "--format",
            '{{.Names}}\t{{.Label "' + OWNER_LABEL + '"}}',
        ]
    )
    if done.exit_code != 0:
        raise SandboxError(f"could not list the sandbox containers: {done.stderr.strip()}")
    return [
        (name, owner)
        for line in done.stdout.splitlines()
        if line.strip()
        for name, _, owner in [line.partition("\t")]
    ]


def remove(names: Sequence[str]) -> None:
    if names:
        done = run_docker(["rm", "-f", *names])
        if done.exit_code != 0:
            raise SandboxError(f"could not remove {', '.join(names)}: {done.stderr.strip()}")


def alive(pid: str) -> bool:
    """Whether a process with this pid runs here; one this user cannot signal still runs."""
    try:
        number = int(pid)
    except ValueError:
        return False
    if number <= 0:
        return False
    try:
        os.kill(number, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def remove_run_containers(run_id: str) -> None:
    remove([name for name, _ in labelled(f"{RUN_LABEL}={run_id}")])


def remove_orphaned_containers() -> None:
    """Containers whose MCP server is gone: no call can reach them any more. The SDK kills the
    server at a session's end, so a run removes its own container; this is for a run that was
    killed before it could (the web UI's cancel signals the run's whole process group)."""
    remove([name for name, owner in labelled(OWNER_LABEL) if not alive(owner)])


def clean_up(step: str, clean: Callable[[], None]) -> None:
    """A failed clean-up is reported, never a failed run (as `evals/dab.py` removes its
    containers)."""
    try:
        clean()
    except SandboxError as exc:
        print(f"sandbox: {step}: {exc}", file=sys.stderr)


@contextmanager
def docker_lifecycle(run_id: str) -> Iterator[None]:
    """Around a run with the docker backend: first remove the containers left by killed runs,
    and at the end the run's own. The run process does it, not the MCP server, which the SDK
    kills when the session ends."""
    clean_up("removing the containers of killed runs", remove_orphaned_containers)
    try:
        yield
    finally:
        clean_up("removing the run's container", lambda: remove_run_containers(run_id))


# sandbox_exec's output cap, on stdout and stderr together.
OUTPUT_CHARS = 30_000
# Cap on the inline `output_files:` manifest; past either limit the full list is saved in LOGS_DIR.
MAX_OUTPUT_FILES = 100
OUTPUT_FILES_CHARS = 4_000
# Workspace directories: query results for the command (nao's `data_files`), the whole output of
# a reply that was cut, and the files the agent makes for the user.
DATA_DIR = "data"
LOGS_DIR = "sandbox_logs"
OUTPUTS_DIR = "outputs"


class QueryResults:
    """The run's successful query_database results by id (`query_1`, `query_2`, ...), for
    sandbox_exec's `data_files` and display_chart's `query_id` (ticket 119). One numbering across
    the server's databases. `data_files` and `charts` say which of the two tools the server
    serves, so the id line names only those."""

    def __init__(self, data_files: bool = True, charts: bool = False) -> None:
        self.results: dict[str, QueryResult] = {}
        self.numbers = itertools.count(1)
        self.data_files = data_files
        self.charts = charts

    def add(self, result: QueryResult) -> str:
        result_id = f"query_{next(self.numbers)}"
        self.results[result_id] = result
        return result_id

    def note(self, result_id: str) -> str:
        """The line query_database adds to a result's text."""
        rows = len(self.results[result_id].rows)
        uses: list[str] = []
        if self.data_files:
            uses.append(
                f"list it in sandbox_exec's data_files to read all {rows} rows as "
                f"{DATA_DIR}/{result_id}.csv"
            )
        if self.charts:
            uses.append("give it to display_chart as query_id to chart it")
        return f"[result id: {result_id}; {', or '.join(uses)}]"

    def picked(self, ids: Sequence[str]) -> dict[str, QueryResult]:
        unknown = [result_id for result_id in ids if result_id not in self.results]
        if unknown:
            known = ", ".join(self.results) or "none yet"
            raise SandboxError(
                f"unknown result id {', '.join(unknown)}: data_files takes the ids query_database "
                f"gave in this run ({known})"
            )
        return {result_id: self.results[result_id] for result_id in ids}


def csv_text(result: QueryResult) -> str:
    """The result as CSV: a header row, then every row; NULL is an empty field."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(result.columns)
    writer.writerows(result.rows)
    return out.getvalue()


def write_file(workspace: Path, directory: str, name: str, data: bytes, replace: bool) -> str:
    """Write `directory/name` in the workspace; returns its workspace-relative path. The sandbox
    can change the workspace, so the write opens nothing that is there already: the directory
    may not be a link (as `evals/dab_server.py` saves results), and the file is always a new
    one, which, when `replace`, then takes the place of whatever has the name (a file, a link, a
    FIFO), never opened."""
    try:
        root = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with contextlib.suppress(FileExistsError):
                os.mkdir(directory, dir_fd=root)
            folder = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        finally:
            os.close(root)
        try:
            new = f".{name}.{uuid.uuid4().hex[:8]}" if replace else name
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            fd = os.open(new, flags, 0o644, dir_fd=folder)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            if replace:
                try:
                    os.rename(new, name, src_dir_fd=folder, dst_dir_fd=folder)
                except OSError:
                    os.unlink(new, dir_fd=folder)
                    raise
        finally:
            os.close(folder)
    except OSError as exc:
        raise SandboxError(
            f"could not write {directory}/{name} in the workspace ({exc.strerror}); "
            f"{directory}/ must be a directory, not a link, and {name} not a directory"
        ) from exc
    return f"{directory}/{name}"


def clipped(stdout: str, stderr: str, limit: int) -> tuple[str, str]:
    """At most `limit` characters of the two together: stdout's start and stderr's end (where a
    traceback ends), each keeping at least half the room when it needs it."""
    err_room = min(len(stderr), limit // 2)
    out_room = min(len(stdout), limit - err_room)
    err_room = min(len(stderr), limit - out_room)
    return stdout[:out_room], stderr[len(stderr) - err_room :]


def _walk_outputs_fd(dir_fd: int, prefix: str, found: dict[str, tuple[int, int, int]]) -> None:
    try:
        names = os.listdir(dir_fd)
    except OSError:
        return
    for name in names:
        try:
            st = os.lstat(name, dir_fd=dir_fd)
        except OSError:
            continue
        rel = f"{prefix}/{name}"
        if stat.S_ISREG(st.st_mode):
            found[rel] = (st.st_mtime_ns, st.st_size, st.st_ino)
        elif stat.S_ISDIR(st.st_mode):
            try:
                child_fd = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd
                )
            except OSError:
                continue
            try:
                _walk_outputs_fd(child_fd, rel, found)
            finally:
                os.close(child_fd)


def outputs_snapshot(workspace: Path) -> dict[str, tuple[int, int, int]]:
    """Regular files under `workspace/outputs/`, keyed by workspace-relative POSIX path with
    `(mtime_ns, size, inode)`. Every directory (including `outputs/` itself) is opened with
    `O_NOFOLLOW` relative to its parent descriptor so symlink swaps cannot escape the workspace."""
    try:
        root_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return {}
    try:
        try:
            outputs_fd = os.open(
                OUTPUTS_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
            )
        except OSError:
            return {}
    finally:
        os.close(root_fd)
    found: dict[str, tuple[int, int, int]] = {}
    try:
        _walk_outputs_fd(outputs_fd, OUTPUTS_DIR, found)
    finally:
        os.close(outputs_fd)
    return found


def _format_output_files(output_files: Sequence[str], workspace: Path) -> str:
    """Format the `output_files:` section, bounding its inline size and saving the full list under
    `LOGS_DIR` when truncated."""
    shown: list[str] = []
    chars = 0
    for path in output_files:
        line = f"- {path}"
        if len(shown) >= MAX_OUTPUT_FILES or chars + len(line) + 1 > OUTPUT_FILES_CHARS:
            break
        shown.append(line)
        chars += len(line) + 1
    lines = ["output_files:", *shown]
    if len(shown) < len(output_files):
        manifest = ("\n".join(output_files) + "\n").encode()
        saved = write_file(
            workspace, LOGS_DIR, f"{uuid.uuid4().hex[:12]}.output_files.txt", manifest, False
        )
        lines.append(
            f"[OUTPUT FILES CUT] showing {len(shown):,} of {len(output_files):,} output files; "
            f"all of them are listed in {saved}."
        )
    return "\n".join(lines)


def reply_text(done: Execution, workspace: Path, output_files: Sequence[str] = ()) -> str:
    """SignalPilot's reply (`exit_code`, then each stream that has output). Past OUTPUT_CHARS the
    streams are cut, each cut stream is saved whole under LOGS_DIR, and the last line names the
    files. When the command created or updated files under OUTPUTS_DIR, an `output_files:` section
    lists their workspace-relative POSIX paths at the end (bounded and saved to LOGS_DIR if long).
    """
    stdout, stderr = done.stdout, done.stderr
    notes: list[str] = []
    if len(stdout) + len(stderr) > OUTPUT_CHARS:
        stdout, stderr = clipped(done.stdout, done.stderr, OUTPUT_CHARS)
        stem = uuid.uuid4().hex[:12]
        for stream, whole, shown, part, dropped in (
            ("stdout", done.stdout, stdout, "first", done.dropped[0]),
            ("stderr", done.stderr, stderr, "last", done.dropped[1]),
        ):
            if len(shown) < len(whole):
                saved = whole.encode()
                path = write_file(workspace, LOGS_DIR, f"{stem}.{stream}.txt", saved, False)
                kept = (
                    f"the first {len(saved):,} bytes of it are in {path} ({dropped:,} more "
                    "were not kept)"
                    if dropped
                    else f"all of it is in {path}"
                )
                notes.append(
                    f"{stream} is {len(whole):,} characters; the {part} {len(shown):,} are "
                    f"shown above, {kept}"
                )
    sections = [] if done.exit_code is None else [f"exit_code: {done.exit_code}"]
    if stdout:
        sections.append(f"stdout:\n{stdout}")
    if stderr:
        sections.append(f"stderr:\n{stderr}")
    if notes:
        sections.append(f"[OUTPUT CUT] {'; '.join(notes)}.")
    if output_files:
        sections.append(_format_output_files(output_files, workspace))
    return "\n".join(sections)


def execute_tool(
    sandbox: Sandbox,
    results: QueryResults,
    command: str,
    timeout_seconds: int,
    data_files: Sequence[str],
) -> str:
    """sandbox_exec: write the `data_files` results, run `command`, and reply."""
    if not command.strip():
        raise SandboxError("command must not be blank")
    if not 1 <= timeout_seconds <= sandbox.max_seconds:
        raise SandboxError(
            f"timeout_seconds must be 1 to {sandbox.max_seconds:g}, got {timeout_seconds}"
        )
    for result_id, result in results.picked(data_files).items():
        data = csv_text(result).encode()
        write_file(sandbox.workspace, DATA_DIR, f"{result_id}.csv", data, True)
    before = outputs_snapshot(sandbox.workspace)
    done = sandbox.execute(command, timeout_seconds)
    after = outputs_snapshot(sandbox.workspace)
    output_files = sorted(rel for rel, meta in after.items() if before.get(rel) != meta)
    text = reply_text(done, sandbox.workspace, output_files)
    if done.timed_out:
        raise SandboxError(
            f"The command timed out after {timeout_seconds} seconds and was killed. Its output "
            f"until then:\n{text}"
        )
    return text


def default_timeout(sandbox: Sandbox) -> int:
    """sandbox_exec's default `timeout_seconds`: DEFAULT_TIMEOUT_S, or the sandbox's longest
    when that is shorter."""
    return min(DEFAULT_TIMEOUT_S, max(1, int(sandbox.max_seconds)))


def tool_description(sandbox: Sandbox) -> str:
    """sandbox_exec's description, as this sandbox runs commands."""
    if isinstance(sandbox, DockerSandbox):
        where = "an isolated container kept for the whole run"
        net = (
            " It can reach the network (pip install works)."
            if sandbox.network
            else " It has no network: use the packages it has."
        )
    else:
        where, net = "this machine, itself an isolated container", ""
    default = default_timeout(sandbox)
    return f"""\
Run a shell command (`bash -c`) in this run's sandbox and return its exit code, stdout and stderr.

The sandbox is {where}: files, installed packages and running processes stay between calls. Its \
working directory is the workspace ({sandbox.workdir}), so the files it writes are the run's \
files; put the files you make for the user under `{OUTPUTS_DIR}/`. The Skill directories are \
under {sandbox.skills_root} (read-only).{net}

The sandbox holds no database connection or credentials. To work on query results, list their \
ids (query_database gives each successful result one) in `data_files`: each is written to \
`{DATA_DIR}/<id>.csv` (a header row, every row the query returned, NULL as an empty field) \
before the command runs.

If the command runs past `timeout_seconds` (default {default}, at most \
{sandbox.max_seconds:g}), it and every process it started are killed. Past \
{OUTPUT_CHARS:,} characters of output in all, the reply shows the start of stdout and the end of \
stderr, and names the files under `{LOGS_DIR}/` that hold all of it."""
