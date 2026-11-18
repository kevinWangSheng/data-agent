"""Runs one sandbox_exec command, and at its time limit ends every process it started (ticket 97).

Run as `python3 -I -c <this file's text> LIMIT GRACE COMMAND`, in the container (docker backend)
or on this machine (local backend): the standard library only, nothing of data_agent.

On Linux it is a child subreaper: a process the command starts in a session or process group of
its own (`setsid`, `start_new_session=True`) becomes this process's child once its parent ends,
so /proc names everything the command left, whatever group it is in. At the limit each gets TERM,
then, GRACE seconds later, KILL. Without /proc (macOS, where the local backend is only for
development) the command's process group gets the signals.

Exits with the command's exit code (128 + n for a command signal n ended), or 124 past the limit.
What the command left running when it ended in time stays: processes outlive a call.
"""

import contextlib
import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PR_SET_CHILD_SUBREAPER = 36
# sandbox.py reads the exit code from here.
TIMEOUT_EXIT = 124
PROC = Path("/proc")


def become_subreaper() -> None:
    if sys.platform == "linux":
        with contextlib.suppress(OSError, AttributeError):
            ctypes.CDLL(None, use_errno=True).prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)


def descendants(root: int) -> list[int]:
    """The live processes under `root`, from /proc."""
    children: dict[int, list[int]] = {}
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        # `pid (comm) state ppid ...`; comm may hold spaces and parentheses.
        state, ppid = stat[stat.rfind(")") + 2 :].split()[:2]
        if state != "Z":
            children.setdefault(int(ppid), []).append(int(entry.name))
    found: list[int] = []
    todo = [root]
    while todo:
        for pid in children.get(todo.pop(), []):
            found.append(pid)
            todo.append(pid)
    return found


def signal_all(command: subprocess.Popen[bytes], sig: int) -> bool:
    """Send `sig` (0 only checks) to what the command started; whether anything was there."""
    if not PROC.is_dir():
        try:
            os.killpg(command.pid, sig)
        except (ProcessLookupError, PermissionError):
            return False
        return True
    pids = descendants(os.getpid())
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, sig)
    return bool(pids)


def reap() -> None:
    """Collect the ended children, the command and the processes that came to this one."""
    with contextlib.suppress(ChildProcessError):
        while os.waitpid(-1, os.WNOHANG)[0]:
            pass


def kill_all(command: subprocess.Popen[bytes], grace: float) -> None:
    """TERM what the command started, wait up to `grace` for it to end, then KILL until nothing
    is left: a process may fork before its KILL, and its children come to this one. A process
    KILL cannot end (stuck in the kernel) is given up after another `grace`."""
    signal_all(command, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while signal_all(command, 0) and time.monotonic() < deadline:
        time.sleep(0.05)
        reap()
    deadline = time.monotonic() + grace
    while signal_all(command, signal.SIGKILL) and time.monotonic() < deadline:
        time.sleep(0.01)
        reap()


def main() -> int:
    limit, grace, command_text = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
    become_subreaper()
    # A session of its own (not `process_group`, Python 3.11+): an image may have an older Python.
    command = subprocess.Popen(["bash", "-c", command_text], start_new_session=True)
    try:
        code = command.wait(limit)
    except subprocess.TimeoutExpired:
        kill_all(command, grace)
        return TIMEOUT_EXIT
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main())
