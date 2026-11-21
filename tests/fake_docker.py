"""A stand-in for the docker CLI as `data_agent.sandbox.run_docker` reaches it (ticket 97)."""

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from data_agent import sandbox as sandbox_module
from data_agent.sandbox import OWNER_LABEL, Execution


@dataclass
class FakeDocker:
    """The docker CLI as `run_docker` reaches it: `run` adds a container, `exec` answers from
    `replies` (or a plain success), `ps --filter label=...` lists, `rm -f` removes."""

    replies: list[Execution] = field(default_factory=list)
    containers: dict[str, dict[str, str]] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, args: Sequence[str], timeout: float = 60) -> Execution:
        args = list(args)
        self.calls.append(args)
        verb = args[0]
        if verb == "info" or args[:2] == ["image", "inspect"]:
            return Execution(0, "", "", False)
        if verb == "run":
            name = args[args.index("--name") + 1]
            labels = [args[i + 1] for i, arg in enumerate(args) if arg == "--label"]
            self.containers[name] = dict(label.split("=", 1) for label in labels)
            return Execution(0, f"{name}\n", "", False)
        if verb == "exec":
            if container_of(args) not in self.containers:
                return Execution(1, "", "Error response from daemon: No such container\n", False)
            return self.replies.pop(0) if self.replies else Execution(0, "ok\n", "", False)
        if verb == "ps":
            wanted = args[args.index("--filter") + 1].removeprefix("label=")
            key, _, value = wanted.partition("=")
            lines = [
                f"{name}\t{labels.get(OWNER_LABEL, '')}\n"
                for name, labels in self.containers.items()
                if key in labels and (not value or labels[key] == value)
            ]
            return Execution(0, "".join(lines), "", False)
        if verb == "rm":
            for name in args[2:]:
                self.containers.pop(name, None)
            return Execution(0, "", "", False)
        raise AssertionError(f"unexpected docker call: {args}")

    def verb(self, verb: str) -> list[list[str]]:
        return [call for call in self.calls if call[0] == verb]


def container_of(exec_args: list[str]) -> str:
    """`exec -w DIR CONTAINER ...`"""
    return exec_args[exec_args.index("-w") + 2]


def install(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    """Every docker call of the test goes to the returned stand-in, and the docker CLI counts as
    installed (`docker_on_path`)."""
    fake = FakeDocker()
    monkeypatch.setattr(sandbox_module, "run_docker", fake)
    monkeypatch.setattr(sandbox_module, "docker_on_path", lambda: True)
    return fake
