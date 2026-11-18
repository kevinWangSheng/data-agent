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
import signal
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

from data_agent.gateway import QueryResult

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
# The container's `timeout` sends TERM at the limit and KILL this many seconds later. It exits
# 124 for a command TERM stopped, 137 (128 + SIGKILL) for one it had to kill; 137 is a timeout
# only once the limit has passed (the kernel's OOM killer also gives 137).
KILL_GRACE_S = 5
TIMEOUT_EXIT = 124
KILLED_EXIT = 137
# How long the docker client itself may take past a command's limit before it is killed too.
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


class SandboxError(Exception):
    """The sandbox could not be reached or started."""


class Execution(NamedTuple):
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    # The bytes of stdout and stderr past KEEP_BYTES, read and dropped.
    dropped: tuple[int, int] = (0, 0)


# What the host keeps of each stream of a command, at most: past it the output is read and
# dropped, so a command that prints without end cannot fill the server's memory.
KEEP_BYTES = 10_000_000


class Capture:
    """Reads a pipe to its end in a thread of its own, keeping its first KEEP_BYTES."""

    def __init__(self, pipe: IO[bytes]) -> None:
        self.kept = bytearray()
        self.dropped = 0
        self.thread = threading.Thread(target=self.read, args=(pipe.fileno(),), daemon=True)
        self.thread.start()

    def read(self, fd: int) -> None:
        # A pipe closed under a blocked read (`run_process` gave up on it) ends the reading.
        with contextlib.suppress(OSError):
            while chunk := os.read(fd, 65536):
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
    """Run `argv` to its end or for `timeout` seconds, until its output closes too. Past that its
    whole process group is killed and the output it wrote so far comes back with `timed_out`. A
    process that left the group may hold the output open: the reading then stops KILL_GRACE_S
    after the kill."""
    deadline = time.monotonic() + timeout
    proc = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=None if env is None else dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert proc.stdout is not None and proc.stderr is not None
    captures = (Capture(proc.stdout), Capture(proc.stderr))
    try:
        proc.wait(timeout=timeout)
        for capture in captures:
            capture.thread.join(max(0.0, deadline - time.monotonic()))
        timed_out = any(capture.thread.is_alive() for capture in captures)
    except subprocess.TimeoutExpired:
        timed_out = True
    if timed_out:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        for capture in captures:
            capture.thread.join(KILL_GRACE_S)
    out, err = captures
    done = Execution(
        None if timed_out else proc.returncode,
        out.text(),
        err.text(),
        timed_out,
        (out.dropped, err.dropped),
    )
    # A reader still blocked (on a process outside the group) holds its own descriptor.
    if not any(capture.thread.is_alive() for capture in captures):
        proc.stdout.close()
        proc.stderr.close()
    return done


def local_env(env: Mapping[str, str]) -> dict[str, str]:
    return {name: env[name] for name in LOCAL_ENV if name in env}


@dataclass
class LocalSandbox:
    """Commands run on this machine, in the workspace, with a scrubbed environment."""

    workspace: Path
    skills_dir: Path

    @property
    def workdir(self) -> str:
        return str(self.workspace.resolve())

    @property
    def skills_root(self) -> str:
        return str(self.skills_dir)

    def execute(self, command: str, timeout: float) -> Execution:
        argv = ["bash", "-c", command]
        return run_process(argv, timeout, cwd=self.workspace, env=local_env(os.environ))


def run_docker(args: Sequence[str], timeout: float = DOCKER_TIMEOUT_S) -> Execution:
    """One call of the docker CLI. Every docker call goes through here (tests replace it)."""
    try:
        return run_process(["docker", *args], timeout)
    except FileNotFoundError as exc:
        raise SandboxError("docker is not installed here (no `docker` on PATH)") from exc


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
    image: str = SANDBOX_IMAGE
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
            *("--cpus", CPUS, "--memory", MEMORY),
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
        limit = f"{timeout:g}"
        argv = [
            *("exec", "-w", WORKDIR, self.container),
            *("timeout", "-k", str(KILL_GRACE_S), limit, "bash", "-c", command),
        ]
        started = time.monotonic()
        done = run_docker(argv, timeout + KILL_GRACE_S + CLIENT_MARGIN_S)
        killed = done.exit_code == KILLED_EXIT and time.monotonic() - started >= timeout
        if done.timed_out or done.exit_code == TIMEOUT_EXIT or killed:
            return done._replace(exit_code=None, timed_out=True)
        return done


class Sandbox(Protocol):
    workspace: Path
    skills_dir: Path

    @property
    def workdir(self) -> str: ...

    @property
    def skills_root(self) -> str: ...

    def execute(self, command: str, timeout: float) -> Execution: ...


def skill_text(sandbox: Sandbox, name: str, text: str) -> str:
    """A Skill's text with `${CLAUDE_SKILL_DIR}` as commands in `sandbox` see that directory."""
    return text.replace("${CLAUDE_SKILL_DIR}", f"{sandbox.skills_root}/{name}")


class SandboxRun(NamedTuple):
    """A run's `--sandbox` choice and its id (the `--events-dir` directory's name), as the run
    passes them to its MCP server."""

    backend: Backend
    run_id: str

    def server_args(self) -> tuple[str, ...]:
        return ("--sandbox", self.backend, "--run-id", self.run_id)


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


def report(step: str, clean: Callable[[], None]) -> None:
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
    report("removing the containers of killed runs", remove_orphaned_containers)
    try:
        yield
    finally:
        report("removing the run's container", lambda: remove_run_containers(run_id))


# sandbox_exec's limits: SignalPilot's default command timeout, and its execution and dbt limit
# (900 s) as the most a call may ask for; its output cap, on stdout and stderr together.
DEFAULT_TIMEOUT_S = 300
MAX_TIMEOUT_S = 900
OUTPUT_CHARS = 30_000
# Workspace directories: query results for the command (nao's `data_files`), the whole output of
# a reply that was cut, and the files the agent makes for the user.
DATA_DIR = "data"
LOGS_DIR = "sandbox_logs"
OUTPUTS_DIR = "outputs"


class QueryResults:
    """The run's successful query_database results by id (`query_1`, `query_2`, ...), for
    `data_files`. One numbering across the server's databases."""

    def __init__(self) -> None:
        self.results: dict[str, QueryResult] = {}
        self.numbers = itertools.count(1)

    def add(self, result: QueryResult) -> str:
        result_id = f"query_{next(self.numbers)}"
        self.results[result_id] = result
        return result_id

    def note(self, result_id: str) -> str:
        """The line query_database adds to a result's text."""
        rows = len(self.results[result_id].rows)
        return (
            f"[result id: {result_id}; list it in sandbox_exec's data_files to read all {rows} "
            f"rows as {DATA_DIR}/{result_id}.csv]"
        )

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


def reply_text(done: Execution, workspace: Path) -> str:
    """SignalPilot's reply (`exit_code`, then each stream that has output). Past OUTPUT_CHARS the
    streams are cut, each cut stream is saved whole under LOGS_DIR, and the last line names the
    files."""
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
    if not 1 <= timeout_seconds <= MAX_TIMEOUT_S:
        raise SandboxError(f"timeout_seconds must be 1 to {MAX_TIMEOUT_S}, got {timeout_seconds}")
    for result_id, result in results.picked(data_files).items():
        data = csv_text(result).encode()
        write_file(sandbox.workspace, DATA_DIR, f"{result_id}.csv", data, True)
    done = sandbox.execute(command, timeout_seconds)
    text = reply_text(done, sandbox.workspace)
    if done.timed_out:
        raise SandboxError(
            f"The command timed out after {timeout_seconds} seconds and was killed. Its output "
            f"until then:\n{text}"
        )
    return text


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

The command is killed after `timeout_seconds` (default {DEFAULT_TIMEOUT_S}, at most \
{MAX_TIMEOUT_S}). Past {OUTPUT_CHARS:,} characters of output in all, the reply shows the start \
of stdout and the end of stderr, and names the files under `{LOGS_DIR}/` that hold all of it."""
