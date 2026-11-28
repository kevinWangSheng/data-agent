"""Context from git (.scratch/cloud-deploy/spec.md "验收测试接缝" git 来源, "上下文从 git";
.scratch/product-config/decisions.md Q8 git 来源, and "改票后主会话再定的" for a `data-agent run` on
its own).

Written by ticket 151. The tests marked TICKET_154 are xfail(strict) until ticket 154 makes them
pass and removes the marker, changing nothing else. The test named `test_guard_...` passes now and
must keep passing: a plain local path in the config stays as it is, and nothing is cloned.

Seam: no function does this today, so this ticket names one (contract items 5 and 6); the tests
reach it through `cloud_support.module_attr`, which asserts the module and the name are there
first, so today's failure is an AssertionError, not an ImportError. A local repository made in
`tmp_path` stands in for the remote one (`git+file://`, contract item 6); git runs through a
wrapper first on `PATH` that records its arguments and hands them to the real git.

The contract fixed here (ticket 151 contract items 5 and 6; for the human review):

5. `data_agent.git_sources.GitSources(root, environ, token=None)`:
   - `root` (`Path`): the directory the clones go under. The server passes
     `<runs_dir>/.cache/git` (Q8; a hidden directory, so no run in the list); a `data-agent run`
     on its own pulls once when it starts, into `~/.data-agent/cache/git`, and never refreshes.
   - `environ` (`Mapping[str, str]`): the environment git runs with; git is the `git` its `PATH`
     finds.
   - `token` (`str | None`): the git token's value. The config writes it as a `${VAR}` reference
     at `[server].git_token`, which ticket 154 adds to the config format beside
     `[server].refresh`, and the loader expands. It reaches git only through `GIT_ASKPASS`: never
     in a command's arguments, a URL, `.git/config` or any other file under `root`, or the log.
   - `resolve(value: str) -> str`: a `git+<url>@<branch or commit>#<subdirectory>` value is the
     absolute path of that subdirectory in a shallow clone (`git rev-list --count HEAD` is 1)
     under `root`, cloned on the first call for that value; later calls give the current path
     without pulling. Any other value comes back unchanged and nothing is cloned.
   - `refresh() -> None`: pulls every value resolved so far again, each into a new directory,
     then switches: `resolve` gives the new path. A path given out before is a directory of its
     own that keeps its content (not a link that moves), so a run going on keeps reading what it
     started with; removing old directories once no run uses them is ticket 154's. A pull that
     fails leaves the value at its previous path and content, logs an ERROR record naming the
     value's repository, and raises nothing.
   What a pull that fails on the first `resolve` does (refuse to start, most likely) is not
   asserted here.
6. `git+file://` is taken like `git+https://` (the tests' stand-in for a remote repository).
"""

import logging
import os
import shutil
import stat
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from cloud_support import isolated, module_attr, served, toml_path, write_file

from data_agent.config import ConfigFileError, load_effective_config

TICKET_154 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 154: git sources are not resolved yet"
)

pytestmark = pytest.mark.usefixtures(isolated.__name__)

MODULE = "data_agent.git_sources"
GIT_TOKEN_ENV = "DATA_AGENT_GIT_TOKEN"
# Made up here; a file:// repository asks for no token, so this only shows where it never goes.
GIT_TOKEN = "gtk-" + "Hs6m" + "-made-up-token"
SKILL = Path("demo") / "SKILL.md"


# --- The stand-in repository ----------------------------------------------------------------


def git(*args: str, cwd: Path | None = None) -> str:
    """The real git, for the tests' own repository; RuntimeError when it fails (a fixture that
    does not come up never hides as an expected failure)."""
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


@dataclass
class Repo:
    path: Path
    # The commits so far, oldest first.
    commits: list[str]

    def commit(self, content: str) -> str:
        write_file(self.path / "skills" / SKILL, content)
        git("add", "-A", cwd=self.path)
        git("commit", "-q", "-m", content, cwd=self.path)
        self.commits.append(git("rev-parse", "HEAD", cwd=self.path))
        return self.commits[-1]

    def value(self, ref: str = "main") -> str:
        return f"git+file://{self.path}@{ref}#skills"


@dataclass
class Git:
    repo: Repo
    # The clones' root, as a server gives it: <runs_dir>/.cache/git.
    root: Path
    # Every argument list git was run with, through the wrapper on PATH.
    argv_log: Path

    def argv(self) -> str:
        return self.argv_log.read_text() if self.argv_log.is_file() else ""


@pytest.fixture
def stand_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Git]:
    """A local repository with two commits on `main` (so a full clone has two), git on its own
    config only, and a `git` wrapper first on PATH that records its arguments."""
    real = shutil.which("git")
    if real is None:
        raise RuntimeError("git is not installed")
    # git reads no config of this machine's: no signing, no hooks, no user from outside.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Cloud Tests")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "cloud-tests@example.invalid")
    repo = Repo(tmp_path / "remote" / "context", [])
    repo.path.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=repo.path)
    # As GitHub does: a commit reachable from a branch can be fetched by its id.
    git("config", "uploadpack.allowReachableSHA1InWant", "true", cwd=repo.path)
    repo.commit("v1")
    repo.commit("v2")
    if git("rev-list", "--count", "HEAD", cwd=repo.path) != "2":
        raise RuntimeError("the stand-in repository does not have two commits")

    bin_dir = tmp_path / "bin"
    argv_log = tmp_path / "git-argv.log"
    wrapper = write_file(
        bin_dir / "git",
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{argv_log}"\nexec "{real}" "$@"\n',
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    yield Git(repo, tmp_path / "runs" / ".cache" / "git", argv_log)


def sources(stand_in: Git, token: str | None = None) -> Any:
    """`GitSources` (contract item 5), for the server's root and this environment."""
    git_sources = module_attr(MODULE, "GitSources")
    return git_sources(stand_in.root, dict(os.environ), token=token)


def skill_text(path: str) -> str:
    file = Path(path) / SKILL
    assert file.is_file(), f"no {SKILL} under {path}"
    return file.read_text()


def commit_count(path: str) -> str:
    done = subprocess.run(
        ["git", "-C", path, "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, f"{path} is not in a git checkout: {done.stderr.strip()}"
    return done.stdout.strip()


# --- Pulling at startup ---------------------------------------------------------------------


@TICKET_154
def test_a_git_value_is_a_shallow_clone_under_the_cache(stand_in: Git) -> None:
    path = sources(stand_in).resolve(stand_in.repo.value())
    assert Path(path).is_absolute()
    assert Path(path).resolve().is_relative_to(stand_in.root.resolve())
    assert skill_text(path) == "v2"
    assert commit_count(path) == "1"


@TICKET_154
def test_a_pinned_commit_gives_that_commit(stand_in: Git) -> None:
    first = stand_in.repo.commits[0]
    path = sources(stand_in).resolve(stand_in.repo.value(first))
    assert skill_text(path) == "v1"


# --- Refreshing -----------------------------------------------------------------------------


@TICKET_154
def test_a_refresh_switches_to_the_new_commit_and_leaves_the_old_path(stand_in: Git) -> None:
    pulled = sources(stand_in)
    value = stand_in.repo.value()
    old = pulled.resolve(value)
    assert skill_text(old) == "v2"
    stand_in.repo.commit("v3")
    pulled.refresh()
    new = pulled.resolve(value)
    assert new != old
    assert skill_text(new) == "v3"
    assert skill_text(old) == "v2", "the refresh changed the directory a run may be reading"


@TICKET_154
def test_a_failed_pull_keeps_the_previous_content(
    stand_in: Git, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    pulled = sources(stand_in)
    value = stand_in.repo.value()
    old = pulled.resolve(value)
    moved = tmp_path / "moved-away"
    stand_in.repo.path.rename(moved)
    pulled.refresh()
    assert pulled.resolve(value) == old
    assert skill_text(old) == "v2"
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(str(stand_in.repo.path) in message for message in errors), (
        f"no error record names {stand_in.repo.path}: {errors}"
    )


# --- The token ------------------------------------------------------------------------------


def git_token(tmp_path: Path) -> str:
    """The token as a config gives it: `[server].git_token = "${DATA_AGENT_GIT_TOKEN}"`, loaded
    and expanded (contract item 5). A config the loader refuses fails as an assertion."""
    path = write_file(
        tmp_path / "config" / "git.toml", f'[server]\ngit_token = "${{{GIT_TOKEN_ENV}}}"\n'
    )
    try:
        config = load_effective_config([path], dict(os.environ), {})
    except ConfigFileError as exc:
        raise AssertionError(f"the config is refused: {exc}") from exc
    token = config.value("server.git_token")
    assert token == GIT_TOKEN, "the token's reference is not expanded"
    return str(token)


def files_holding(root: Path, text: str) -> list[Path]:
    needle = text.encode()
    return [p for p in root.rglob("*") if p.is_file() and needle in p.read_bytes()]


@TICKET_154
def test_the_token_is_never_written_logged_or_passed_as_an_argument(
    stand_in: Git,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv(GIT_TOKEN_ENV, GIT_TOKEN)
    token = git_token(tmp_path)
    pulled = sources(stand_in, token=token)
    value = stand_in.repo.value()
    pulled.resolve(value)
    stand_in.repo.commit("v3")
    pulled.refresh()
    assert skill_text(pulled.resolve(value)) == "v3"
    assert stand_in.argv(), "git was not run as the `git` on the environment's PATH"
    assert GIT_TOKEN not in stand_in.argv()
    # Every file under the root, the clones' `.git/config` among them.
    assert files_holding(stand_in.root, GIT_TOKEN) == []
    assert GIT_TOKEN not in caplog.text


# --- A plain local path ---------------------------------------------------------------------


@TICKET_154
def test_resolve_gives_a_plain_value_back_and_clones_nothing(stand_in: Git, tmp_path: Path) -> None:
    plain = str(tmp_path / "config" / "skills")
    assert sources(stand_in).resolve(plain) == plain
    assert stand_in.argv() == "", "git ran for a value that is not a git value"
    assert not stand_in.root.exists()


def test_guard_a_plain_local_path_stays_as_it_is_and_nothing_is_cloned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "config"
    skills = config_dir / "skills"
    (skills / "demo").mkdir(parents=True)
    write_file(skills / SKILL, "local")
    notes = write_file(config_dir / "notes.md", "# notes\n")
    docs = write_file(config_dir / "docs" / "shop.md", "# shop\n").parent
    db = write_file(config_dir / "shop.duckdb", "")
    path = write_file(
        config_dir / "config.toml",
        '[skills]\ndirs = ["skills"]\n\n[instructions]\nfiles = ["notes.md"]\n\n'
        f'[sources.shop]\nkind = "duckdb"\npath = {toml_path(db)}\ndocs = "docs"\n',
    )
    config = load_effective_config([path], dict(os.environ), {})
    assert [str(p) for p in config.value("skills.dirs") or []] == [str(skills.resolve())]
    assert [str(p) for p in config.value("instructions.files") or []] == [str(notes.resolve())]
    assert str(config.value("sources.shop.docs")) == str(docs.resolve())
    with served(tmp_path / "server", monkeypatch, capsys, ["--config", str(path)]) as server:
        assert server.ran, f"data-agent web did not start: {server.stderr[-2000:]!r}"
    assert not (tmp_path / "server" / "runs" / ".cache").exists()
