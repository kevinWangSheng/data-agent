"""Helpers shared by the product-config acceptance tests (.scratch/product-config/spec.md "Testing
Decisions", ticket 141): test_config_*.py and the earlier acceptance files ticket 141 moved to the
new interface.

The seam (ticket 141 Context "截停接缝"): the session is stopped at `ClaudeSDKClient`. Its
`connect` does nothing and its `query` records the prompt it was given and the client's options,
then raises `CLIConnectionError`. Stopping at the transport (tests/acceptance/
test_chat_eval_unchanged.py) sees the options but not the task prompt, which the client sends
only after it has connected (`agent.query`), and the snapshots compare the task prompt too
(decisions.md Q8). Replacing `data_agent.agent.query` would miss DAB, which imports the function
by name (`evals/dab.py`), and would tie the tests to a name in agent.py; the SDK's client is what
every entry uses. No model is called and no Claude Code CLI starts. The `version` event is written
before the session starts (`agent.run`), so the event file is there when the session is stopped.

Taking a value out of something today's code does not produce goes through the helpers below,
which `assert` it is there first: a test marked `xfail(strict=True, raises=AssertionError)` then
fails on the assertion (the expected failure), never on a `KeyError`.

Recording the snapshots (tests/fixtures/product_config/snapshots/): from main, with no
`~/.data-agent/` in play (the tests give every run an empty fake HOME),

    DATA_AGENT_RECORD_SNAPSHOTS=1 uv run pytest tests/acceptance/test_config_snapshot.py -k guard

writes one JSON file per entry and layer instead of comparing; then run the file again without
the variable. What the guard proves (decisions.md "调研后定" 4): that the first step of the
migration (tickets 143-148) leaves the evaluation entries' run parameters as they were, the old
and the new mechanism on the same Skill files. No ticket agent records them again. Once ticket 146
is merged the guard has done its job; after that the main session records them again when the
second step of the migration or a deliberate Skill change moves them, reviews the diff and notes
it in that ticket. Until ticket 146 is merged no commit that changes `skills/` is merged.
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from collections.abc import AsyncIterable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, CLIConnectionError
from seam_support import DBT_SHOP, FIXTURES, REPO_ROOT, build_shop_duckdb, make_dbt_workspace

from data_agent import cli

PRODUCT_FIXTURES = FIXTURES / "product_config"
SNAPSHOTS = PRODUCT_FIXTURES / "snapshots"
FAKE_DAB = FIXTURES / "fake_dab"
RECORD_ENV = "DATA_AGENT_RECORD_SNAPSHOTS"
# Placeholders the tests put in the environment. Built from parts so no scanner takes them for real
# keys (as test_chat_api.py does); the session is stopped before anything is sent.
API_KEY = "unused-test-key-" + "Hq3v"
PG_PASSWORD = "hunter2-" + "t6Yd"
PG_URL = f"postgresql://agent:{PG_PASSWORD}@localhost:5432/shop"
# Our own MCP server's name (`agent.MCP_SERVER`).
DATA_SERVER = "data"
# Config errors exit 2 (seam A, tests/acceptance/test_seam_a_run_cli.py).
EXIT_CONFIG_ERROR = 2
# The config files' names (spec Q1).
CONFIG_DIR = ".data-agent"
CONFIG_FILE = "config.toml"
# Variables that would change what a run is given, cleared for every test: the model's, the
# config file's, the CLI's effort and the Langfuse export's.
AMBIENT_ENV = (
    "DATA_AGENT_BASE_URL",
    "DATA_AGENT_MODEL",
    "DATA_AGENT_CONFIG",
    "CLAUDE_CODE_EFFORT_LEVEL",
    "DATA_AGENT_LANGFUSE_PUBLIC_KEY",
    "DATA_AGENT_LANGFUSE_SECRET_KEY",
    "DATA_AGENT_LANGFUSE_BASE_URL",
)


# --- The stopped session ------------------------------------------------------------------


@dataclass
class Session:
    options: ClaudeAgentOptions
    # The task prompt as sent; a conversation turn's stream-json message gives its text.
    prompt: str
    # What the capture's probe returned, run while the session's files (a run's temp dirs) exist.
    probed: Any = None


@dataclass
class Capture:
    sessions: list[Session] = field(default_factory=list[Session])
    # Run on the options when a session starts, while the run's temp files still exist. An
    # exception it raises is kept as its result, not raised: raised there, the run would take it
    # for an infrastructure error and hide it.
    probe: Callable[[ClaudeAgentOptions], Any] | None = None

    def only(self) -> Session:
        assert len(self.sessions) == 1, f"expected one session, got {len(self.sessions)}"
        return self.sessions[0]


async def prompt_text(prompt: str | AsyncIterable[dict[str, Any]]) -> str:
    if isinstance(prompt, str):
        return prompt
    texts: list[str] = []
    async for message in prompt:
        content = message.get("message", {}).get("content")
        texts.append(content if isinstance(content, str) else json.dumps(content))
    return "\n".join(texts)


def stop_sessions(monkeypatch: pytest.MonkeyPatch, home: Path | None = None) -> Capture:
    """Stops every session the code under test starts (see the module docstring) and records it.
    Also sets the model key the run needs, clears AMBIENT_ENV, and points HOME at `home` (an empty
    fake home when not given is the caller's: `fake_home`)."""
    capture = Capture()

    async def connect(self: ClaudeSDKClient, prompt: Any = None) -> None:
        return None

    async def query(self: ClaudeSDKClient, prompt: Any, session_id: str = "default") -> None:
        probed: Any = None
        if capture.probe is not None:
            try:
                probed = capture.probe(self.options)
            except Exception as exc:
                probed = exc
        capture.sessions.append(Session(self.options, await prompt_text(prompt), probed))
        raise CLIConnectionError("stopped by the product-config acceptance tests")

    monkeypatch.setattr(ClaudeSDKClient, "connect", connect)
    monkeypatch.setattr(ClaudeSDKClient, "query", query)
    monkeypatch.setenv("DATA_AGENT_API_KEY", API_KEY)
    for name in AMBIENT_ENV:
        monkeypatch.delenv(name, raising=False)
    if home is not None:
        monkeypatch.setenv("HOME", str(home))
    return capture


def no_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every docker call succeeds with no output (`sandbox.run_docker`, which its docstring says
    tests replace): the session is stopped before a container would start, and the docker
    backend's clean-ups and DAB's image check need no daemon."""
    from data_agent import sandbox

    def run_docker(args: Sequence[str], timeout: float = 0) -> sandbox.Execution:
        return sandbox.Execution(0, "", "", False)

    monkeypatch.setattr(sandbox, "run_docker", run_docker)


def dictionary_from_duckdb(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """The value dictionary of a run on Postgres is built from the shop DuckDB instead: `make
    check` has no Postgres, and the dictionary's content is in no snapshot (only the path of its
    file, which the MCP server reads). Replaced where `agent.run` looks it up and in
    context_tools, so a build reached either way is caught; nothing else connects before the
    session (a Postgres gateway connects on first use)."""
    from data_agent import agent, context_tools
    from data_agent.gateway import DuckDBGateway

    shop = build_shop_duckdb(root / "dictionary-shop.duckdb")
    real = context_tools.build_interruptible

    def build(_: Any) -> Any:
        return real(context_tools.DictionaryBuild(DuckDBGateway(shop)))

    monkeypatch.setattr(context_tools, "build_interruptible", build)
    monkeypatch.setattr(agent, "build_interruptible", build, raising=False)


# --- Fake home and workspace --------------------------------------------------------------


def fake_home(root: Path) -> Path:
    """An empty home directory: no `~/.data-agent/`, no Claude Code config."""
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    return home


def write_config(directory: Path, text: str) -> Path:
    """`<directory>/.data-agent/config.toml` (user level under a home, project level under a
    workspace)."""
    path = directory / CONFIG_DIR / CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_file(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def toml_path(path: Path) -> str:
    """A path as a TOML basic string."""
    return json.dumps(str(path))


# --- Running the CLI ----------------------------------------------------------------------


@dataclass
class CliRun:
    code: int | str | None
    stdout: str
    stderr: str


def run_cli(capsys: pytest.CaptureFixture[str], argv: Sequence[str]) -> CliRun:
    """`data-agent ...` in-process; argparse errors and the run's own exit both end in
    SystemExit."""
    capsys.readouterr()
    try:
        cli.main(list(argv))
    except SystemExit as exc:
        code = exc.code
    else:
        code = 0
    out = capsys.readouterr()
    return CliRun(code, out.out, out.err)


def query_args(root: Path, *extra: str, database: bool = True) -> tuple[list[str], Path]:
    """A query run's arguments under `root`: its own workspace and events dir, `--sandbox local`
    (the session stops before any sandbox starts; local needs no Docker), and a fresh shop DuckDB
    as `--duckdb` unless `database` is off (the config names the source)."""
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    events_dir = root / "events"
    db = ("--duckdb", str(build_shop_duckdb(root / "shop.duckdb"))) if database else ()
    argv = [
        "run",
        *("--task", "How many orders are there?", "--type", "query"),
        *("--workspace", str(workspace), *db),
        *("--events-dir", str(events_dir), "--sandbox", "local"),
        *extra,
    ]
    return argv, events_dir


def dbt_args(root: Path, *extra: str, database: bool = True) -> tuple[list[str], Path]:
    """As `query_args`, a dbt run on a copy of the mini dbt project."""
    project, db = make_dbt_workspace(root / "trial")
    events_dir = root / "events"
    argv = [
        "run",
        *("--task", "Add a dbt model named customer_ltv with one row per customer.", "--type"),
        *("dbt", "--workspace", str(project), *(("--duckdb", str(db)) if database else ())),
        *("--events-dir", str(events_dir), "--sandbox", "local"),
        *extra,
    ]
    return argv, events_dir


def assert_refused_before_session(run: CliRun, capture: Capture, *words: str) -> None:
    """A config error (ticket 141 Context "预期失败的标记"): stderr names each of `words`, no
    session started, exit code 2. Exit code 2 alone proves nothing: argparse already exits 2 on an
    argument it does not know."""
    for word in words:
        assert word in run.stderr, f"stderr does not name {word!r}: {run.stderr!r}"
    assert capture.sessions == [], "a session started despite the config error"
    assert run.code == EXIT_CONFIG_ERROR, f"exit code {run.code!r}, expected 2"


def assert_session(run: CliRun, capture: Capture) -> Session:
    """The run got as far as starting its session (and was stopped there)."""
    assert capture.sessions, f"no session started (exit {run.code!r}): {run.stderr[-2000:]!r}"
    return capture.only()


# --- Reading values -----------------------------------------------------------------------


def key_of(mapping: Any, key: str) -> Any:
    assert isinstance(mapping, Mapping), f"not a mapping: {mapping!r}"
    assert key in mapping, f"no {key!r} in {sorted(map(str, mapping))}"
    return mapping[key]


def attr_of(obj: Any, name: str) -> Any:
    assert hasattr(obj, name), f"{type(obj).__name__} has no {name!r}"
    return getattr(obj, name)


def flag_value(args: Sequence[str], name: str) -> str:
    assert name in args, f"{name} not in the arguments: {list(args)}"
    index = list(args).index(name)
    assert index + 1 < len(args), f"{name} has no value"
    return args[index + 1]


def flag_values(args: Sequence[str], name: str) -> list[str]:
    """Every value of a flag given several times, in order."""
    args = list(args)
    return [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == name]


def servers_of(options: ClaudeAgentOptions) -> dict[str, Any]:
    servers = options.mcp_servers
    assert isinstance(servers, dict), f"mcp_servers is not a dict: {servers!r}"
    return dict(servers)


def server_args(options: ClaudeAgentOptions, name: str = DATA_SERVER) -> list[str]:
    server = key_of(servers_of(options), name)
    return [str(arg) for arg in key_of(server, "args")]


def system_prompt_of(options: ClaudeAgentOptions) -> str:
    prompt = options.system_prompt
    assert isinstance(prompt, str), f"the system prompt is not text: {prompt!r}"
    return prompt


def read_events(events_dir: Path) -> list[dict[str, Any]]:
    files = sorted(events_dir.glob("*.jsonl"))
    assert len(files) == 1, f"expected one event file in {events_dir}, found {files}"
    return [json.loads(line) for line in files[0].read_text().splitlines() if line.strip()]


def events_text(events_dir: Path) -> str:
    files = sorted(events_dir.glob("*.jsonl"))
    assert len(files) == 1, f"expected one event file in {events_dir}, found {files}"
    return files[0].read_text()


def event_at(events: Sequence[Mapping[str, Any]], index: int, kind: str) -> dict[str, Any]:
    assert len(events) > index, f"only {len(events)} events"
    event = dict(events[index])
    assert event.get("type") == kind, f"event {index} is {event.get('type')!r}, not {kind!r}"
    return event


def version_event(events_dir: Path) -> dict[str, Any]:
    return event_at(read_events(events_dir), 0, "version")


def layers_of(events_dir: Path) -> dict[str, bool]:
    return dict(key_of(version_event(events_dir), "layers"))


FULL_LAYERS = {"skill": True, "verifiers": True, "context": True}


# --- Replaying a recorded `data-agent run` ------------------------------------------------


def replay(capsys: pytest.CaptureFixture[str], args: Sequence[str], events_dir: Path) -> CliRun:
    """The arguments a web or IM entry gave its run process (`run` excluded), run in-process to
    the stopped session, with `--events-dir` swapped for `events_dir` (the fake run the web
    started writes its own event file in the run's directory). `--sandbox docker` needs
    `no_docker`."""
    args = list(args)
    flag_value(args, "--events-dir")
    args[args.index("--events-dir") + 1] = str(events_dir)
    return run_cli(capsys, ["run", *args])


# --- Snapshots ----------------------------------------------------------------------------

# The `version` fields a snapshot keeps (decisions.md Q5).
VERSION_FIELDS = ("skill_version", "skills", "config", "layers")
# What `mkdtemp` adds to a prefix of ours (`data-agent-task-`, ...): 8 characters.
MKDTEMP = re.compile(r"((?:data-agent|da)-[a-z-]*-)[A-Za-z0-9_]{8}\b")
# An event file's name (`agent.events_file`): stamp and random part.
EVENT_FILE = re.compile(r"run-\d{8}T\d{6}Z-[0-9a-f]{8}")


def snapshot(session: Session, version: Mapping[str, Any]) -> dict[str, Any]:
    """The run parameters compared (decisions.md Q5): system and task prompts, tools and
    permissions, MCP servers with their commands and arguments, Skills, plugins, setting sources,
    the turn and spend caps, the CLI's environment (keys and values), and the `version` event's
    Skill version, Skills, config and layers. Not yet normalized."""
    options = session.options
    servers = options.mcp_servers if isinstance(options.mcp_servers, dict) else {}
    return {
        "system_prompt": options.system_prompt,
        "prompt": session.prompt,
        "tools": options.tools,
        "allowed_tools": options.allowed_tools,
        "disallowed_tools": options.disallowed_tools,
        "permission_mode": options.permission_mode,
        "mcp_servers": {
            name: {key: server.get(key) for key in ("type", "command", "args", "env", "url")}
            for name, server in servers.items()
        },
        "skills": options.skills,
        "plugins": options.plugins,
        "setting_sources": options.setting_sources,
        "strict_mcp_config": options.strict_mcp_config,
        "max_turns": options.max_turns,
        "max_budget_usd": options.max_budget_usd,
        "env": dict(sorted(options.env.items())),
        "version": {name: version[name] for name in VERSION_FIELDS if name in version},
    }


def assert_server_configs(
    values: Sequence[str], version_config: Any, home: Path, workspace: Path | None
) -> None:
    """What the data server's `--config` values may be, beside the snapshot that collapses them
    (`config_arg_rule`): each is the ablation name the `version` event's `config` names, or a path
    under the checkout's `evals/profiles/` or the temp dir (a file the run wrote), never under the
    fake home or the workspace (a user's or a project's config file read in)."""
    profiles = (REPO_ROOT / "evals" / "profiles").resolve()
    temp = Path(tempfile.gettempdir()).resolve()
    assert values, "the data server is given no --config"
    for value in values:
        if value == version_config and "/" not in value:
            continue
        path = Path(value)
        assert path.is_absolute(), (
            f"the data server's --config {value!r} is neither the version's config nor a path"
        )
        path = path.resolve()
        for kept_out in (home, workspace):
            if kept_out is not None:
                assert not path.is_relative_to(kept_out.resolve()), (
                    f"the data server's --config {value} is under {kept_out}"
                )
        assert path.is_relative_to(profiles) or path.is_relative_to(temp), (
            f"the data server's --config {value} is under neither evals/profiles/ nor the temp dir"
        )


def config_arg_rule(args: list[str]) -> list[str]:
    """The data server's `--config` values (ticket 141 "spec 里的解读" 1). Today one ablation
    name, after the migration one or more config file paths (a temp file of the run's, maybe).
    Each `--config VALUE` pair becomes one `--config <config>`, repeats collapsed: what the
    server's layers are is pinned by the `version` event's `config` and `layers` and by
    `--no-verifier-tools`, which stay compared as they are; where the values may point is
    asserted apart (`assert_server_configs`)."""
    out: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--config" and i + 1 < len(args):
            if out[-2:] != ["--config", "<config>"]:
                out += ["--config", "<config>"]
            i += 2
            continue
        out.append(args[i])
        i += 1
    return out


def text_rules(roots: Sequence[Path]) -> list[tuple[str, str]]:
    """Literal replacements, longest first, each for a part that differs on every run or
    machine; a path only where it is a whole path or a path's first part (`normalize_text`):
    - the test's own directories (`roots`: tmp_path and the like), as given and resolved (macOS
      resolves /var to /private/var): `<tmp>`;
    - the temp dir (`tempfile.gettempdir()`), where a run keeps its task file, snapshot, ledger
      and dictionary: `<tempdir>`;
    - this interpreter (`sys.executable`, the MCP server's command): `<python>`;
    - the checkout (Skill directories, task files of the jaffle cases): `<repo>`;
    - the sandbox run id of the data server's `--run-id` (`normalized_snapshot`): `<run-id>`;
    - the placeholders the tests set (model key, Postgres password and URL): `<api-key>`,
      `<pg-url>`, `<pg-password>`.
    Nothing else: prompt text, tool lists, variable names and cap values stay as they are."""
    pairs: dict[str, str] = {
        PG_URL: "<pg-url>",
        PG_PASSWORD: "<pg-password>",
        API_KEY: "<api-key>",
        sys.executable: "<python>",
    }
    for root in roots:
        for form in {str(root), str(root.resolve())}:
            pairs[form] = "<tmp>"
    temp = Path(tempfile.gettempdir())
    for form in {str(temp), str(temp.resolve())}:
        pairs.setdefault(form, "<tempdir>")
    for form in {str(REPO_ROOT), str(REPO_ROOT.resolve())}:
        pairs.setdefault(form, "<repo>")
    return sorted(pairs.items(), key=lambda pair: len(pair[0]), reverse=True)


# A path replaced must not run on into more of a name (`/work` in `/workspace`, `/work-tree`,
# `/work.d`; a full stop ending a sentence is not one), nor be the tail of a longer word or path
# (`/tmp` in `/private/tmp`).
PATH_BEFORE = r"(?<![\w.-])"
PATH_AFTER = r"(?![\w-]|\.[\w-])"


def is_path(text: str) -> bool:
    return text.startswith("/")


def normalize_text(text: str, rules: Sequence[tuple[str, str]]) -> str:
    for old, new in rules:
        if is_path(old):
            pattern = PATH_BEFORE + re.escape(old) + PATH_AFTER
            text = re.sub(pattern, lambda _, new=new: new, text)
        else:
            text = text.replace(old, new)
    # mkdtemp's random suffix and the run's ids: different on every run.
    text = MKDTEMP.sub(r"\1<random>", text)
    return EVENT_FILE.sub("run-<stamp>", text)


def normalize(value: Any, rules: Sequence[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        return normalize_text(value, rules)
    if isinstance(value, Mapping):
        return {normalize_text(str(k), rules): normalize(v, rules) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [normalize(item, rules) for item in value]
    return value


def normalized_snapshot(
    session: Session, version: Mapping[str, Any], roots: Sequence[Path]
) -> dict[str, Any]:
    raw = snapshot(session, version)
    rules = text_rules(roots)
    for name, server in raw["mcp_servers"].items():
        if isinstance(server.get("args"), list):
            args = [str(arg) for arg in server["args"]]
            # The sandbox's run id: the events dir's name and a random part (`SandboxRun.of_run`).
            rules += [(run_id, "<run-id>") for run_id in flag_values(args, "--run-id")]
            # Our own server; DAB's (`evals.dab_server`) takes no `--config`.
            if name == DATA_SERVER and "mcp-server" in args:
                cwd = session.options.cwd
                assert_server_configs(
                    flag_values(args, "--config"),
                    version.get("config"),
                    Path(os.environ["HOME"]),
                    Path(str(cwd)) if cwd is not None else None,
                )
            server["args"] = config_arg_rule(args)
    if "skill_version" in raw["version"]:
        raw["version"]["skill_version"] = skill_fingerprint(raw["version"]["skill_version"])
    return normalize(raw, rules)


# What a content hash leaves out (Harbor's `Packager.DEFAULT_IGNORES`, harbor
# `publisher/packager.py`), with the checkout's `.gitignore` besides.
CONTENT_IGNORES = ("__pycache__/", "*.pyc", ".DS_Store", "*.swp", "*.swo", "*~")


def content_files(directory: Path) -> list[Path]:
    """The files under `directory` a content hash takes in, sorted: every file but those
    `.gitignore` or CONTENT_IGNORES match (Harbor's `Packager.collect_files`), so a
    `__pycache__/` a test leaves when it imports a Skill's script counts for nothing."""
    import pathspec

    gitignore = REPO_ROOT / ".gitignore"
    lines = [*CONTENT_IGNORES, *(gitignore.read_text().splitlines() if gitignore.is_file() else [])]
    spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
    root = REPO_ROOT.resolve()
    files = [
        path
        for path in directory.rglob("*")
        if path.is_file() and not spec.match_file(path.resolve().relative_to(root).as_posix())
    ]
    return sorted(files, key=lambda path: path.relative_to(directory).as_posix())


# Where a Claude Code plugin keeps its manifest (`plugin.json`). Ticket 147 adds one to `skills/`;
# it is no Skill, and the Skill version leaves it out (decisions.md Q8 人定 2).
PLUGIN_MANIFEST_DIR = ".claude-plugin"


def content_sha256(directory: Path) -> str:
    """Harbor's content hash (`Packager.compute_content_hash`): sha256 over each file's path
    under `directory` and its own sha256. The plugin manifest (PLUGIN_MANIFEST_DIR) is left
    out, as the Skill version leaves it out; the `config` event's file hashes keep it."""
    outer = hashlib.sha256()
    for path in content_files(directory):
        if PLUGIN_MANIFEST_DIR in path.relative_to(directory).parts:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        outer.update(f"{path.relative_to(directory).as_posix()}\0{digest}\n".encode())
    return outer.hexdigest()


def skill_fingerprint(value: str) -> Any:
    """The `version` event's Skill version as the Skill directory it is the hash of
    (`skills/`, `skills-frozen/<version>/`, found with the agent's own `skill_version` now) and
    that directory's content hash (`content_sha256`); `none` as it is; any other value as it is
    (so it differs). The agent's hash itself is not recorded: it takes in every file under the
    directory, ignored ones too, so two checkouts with the same Skill files may give different
    ones (`agent.skill_version`). The content hash changes with any Skill file, scripts too."""
    from data_agent.agent import skill_version

    if value == "none":
        return value
    frozen = sorted(
        p for p in (REPO_ROOT / "skills-frozen").iterdir() if not p.name.startswith(".")
    )
    for directory in [REPO_ROOT / "skills", *frozen]:
        if directory.is_dir() and skill_version(directory) == value:
            return {
                "directory": f"{directory.relative_to(REPO_ROOT).as_posix()}/",
                "content_sha256": content_sha256(directory),
            }
    return value


def compare_or_record(name: str, got: dict[str, Any]) -> None:
    """Equal to the recorded snapshot, key by key; with RECORD_ENV set, records it instead."""
    path = SNAPSHOTS / f"{name}.json"
    if os.environ.get(RECORD_ENV):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(got, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        return
    assert path.is_file(), f"no recorded snapshot {path}"
    recorded = json.loads(path.read_text())
    differing = sorted(key for key in {*recorded, *got} if recorded.get(key) != got.get(key))
    assert not differing, f"{name}: differs from the recorded snapshot in {differing}"
    assert got == recorded


# --- The evaluation entries, each driven its own way --------------------------------------

# The entries and layers recorded (ticket 141 Context "快照", "层"): every benchmark at the layer
# it runs (`full`; DAB's tool server has no context tools, so DAB runs `no-context`, the config
# its tests use: `evals.dab.run_query` refuses `full`), and data-eng-bench, the benchmark with
# every layer in play, also at the two extremes, `baseline` (every layer off) and
# `full-old-skill` (Skill directory swapped for skills-frozen/).
SNAPSHOT_CASES = (
    ("data-eng-bench", "full"),
    ("data-eng-bench", "baseline"),
    ("data-eng-bench", "full-old-skill"),
    ("livesqlbench", "full"),
    ("dab", "no-context"),
    ("jaffle", "full"),
)
DBT_TASK = "Add a dbt model named customer_ltv with one row per customer."
LSB_TASK = "List the three customers with the most orders."
LSB_URL_ENV = "LSB_DATABASE_URL"
JAFFLE_CASE = "b1_monthly_revenue"
PG_URL_ENV = "DA_TEST_JAFFLE_URL"


@dataclass
class EntryRun:
    """One evaluation entry's run, stopped at its session."""

    session: Session
    version: dict[str, Any]
    events: list[dict[str, Any]]
    # The `data-agent run` arguments the entry gave (`run` excluded); DAB runs no such process.
    run_args: list[str] | None
    roots: list[Path]


def harbor_command_args(command: str) -> list[str]:
    """The `data-agent run` arguments in a Harbor trial's shell command (`run` excluded): the
    words after `.../bin/data-agent run`, up to the redirect of its output."""
    words = shlex.split(command)
    starts = [i for i, word in enumerate(words) if word.endswith("/bin/data-agent")]
    assert starts, f"no data-agent command in {command!r}"
    start = starts[0] + 1
    assert words[start] == "run", f"not `data-agent run`: {words[start : start + 3]}"
    end = words.index("2>&1", start)
    return words[start + 1 : end]


def profile_path(value: str) -> str:
    """A `--config` value as this checkout has it. A trial container's path to an evaluation
    config file (`evals/profiles/...`, wherever ticket 146 installs it in the container) is
    mapped to the checkout's by its part from `profiles/` on; a name or a path that exists is
    kept."""
    if Path(value).exists() or "/" not in value:
        return value
    parts = PurePosixPath(value).parts
    assert "profiles" in parts, f"a --config path outside profiles/: {value}"
    at = len(parts) - 1 - parts[::-1].index("profiles")
    return str(REPO_ROOT / "evals" / Path(*parts[at:]))


def substitute(args: list[str], values: Mapping[str, str]) -> list[str]:
    """`args` with the value of each flag in `values` replaced (by flag name, so the order and any
    repeated `--config` stay), and each `--config` value mapped by `profile_path`."""
    out = list(args)
    for i, arg in enumerate(out[:-1]):
        if arg in values:
            out[i + 1] = values[arg]
        elif arg == "--config":
            out[i + 1] = profile_path(out[i + 1])
    return out


def harbor_agent(root: Path, layer: str, benchmark: str) -> Any:
    from evals.harbor_agent import DataAgent

    logs = root / "logs" / "agent"
    logs.mkdir(parents=True)
    kwargs: dict[str, Any] = {"run_config": layer}
    if benchmark == "livesqlbench":
        kwargs["benchmark"] = "livesqlbench"
    return DataAgent(
        logs_dir=logs,
        model_name="deepseek/deepseek-flash",
        environment_logs_dir=PurePosixPath(logs),
        extra_env={"DATA_AGENT_API_KEY": API_KEY},
        **kwargs,
    )


def run_entry_cli(capsys: pytest.CaptureFixture[str], args: list[str], capture: Capture) -> None:
    run = run_cli(capsys, ["run", *args])
    assert capture.sessions, f"no session (exit {run.code!r}): {run.stderr[-3000:]}"


def harbor_entry(
    root: Path,
    layer: str,
    capsys: pytest.CaptureFixture[str],
    capture: Capture,
    project_config: str | None = None,
) -> EntryRun:
    """data-eng-bench: the command `evals/harbor_agent.py` `_run_command` builds (the agent built
    directly, as tests/acceptance/test_mcp_eval_unchanged.py does), split by shell rules, the
    trial container's workspace, database and events dir swapped for the test's, run in-process."""
    agent = harbor_agent(root, layer, "data-eng-bench")
    command = agent._run_command(DBT_TASK)  # pyright: ignore[reportPrivateUsage]
    args = harbor_command_args(command)
    project, db = make_dbt_workspace(root / "trial")
    if project_config is not None:
        write_config(project, project_config)
    events_dir = root / "events"
    args = substitute(
        args,
        {"--workspace": str(project), "--duckdb": str(db), "--events-dir": str(events_dir)},
    )
    run_entry_cli(capsys, args, capture)
    events = read_events(events_dir)
    return EntryRun(capture.only(), event_at(events, 0, "version"), events, args, [root])


def livesqlbench_entry(
    root: Path,
    layer: str,
    capsys: pytest.CaptureFixture[str],
    capture: Capture,
    monkeypatch: pytest.MonkeyPatch,
    project_config: str | None = None,
) -> EntryRun:
    """LiveSQLBench: `_query_command` (through `_run_command`), as data-eng-bench. Its Postgres
    is a placeholder URL in `LSB_DATABASE_URL`: nothing connects before the session but the
    dictionary build, which `dictionary_from_duckdb` serves."""
    dictionary_from_duckdb(monkeypatch, root)
    monkeypatch.setenv(LSB_URL_ENV, PG_URL)
    agent = harbor_agent(root, layer, "livesqlbench")
    command = agent._run_command(LSB_TASK)  # pyright: ignore[reportPrivateUsage]
    args = harbor_command_args(command)
    workspace = root / "app"
    workspace.mkdir()
    if project_config is not None:
        write_config(workspace, project_config)
    events_dir = root / "events"
    args = substitute(args, {"--workspace": str(workspace), "--events-dir": str(events_dir)})
    run_entry_cli(capsys, args, capture)
    events = read_events(events_dir)
    return EntryRun(capture.only(), event_at(events, 0, "version"), events, args, [root])


def make_dab(root: Path) -> Path:
    """A DAB checkout with one dataset: a SQLite and a mongo database, one query (as
    tests/acceptance/test_chat_eval_unchanged.py builds it; copied, not imported)."""
    import sqlite3

    shutil.copytree(FAKE_DAB, root)
    dataset = root / "query_toyset"
    (dataset / "query_dataset").mkdir(parents=True)
    with sqlite3.connect(dataset / "query_dataset" / "prices.db") as conn:
        conn.execute("CREATE TABLE prices (ticker TEXT, close REAL)")
        conn.executemany("INSERT INTO prices VALUES (?, ?)", [("T001", 1.0), ("T002", 2.0)])
    (dataset / "dump").mkdir()
    (dataset / "dump" / "articles.bson").write_bytes(b"x" * 10)
    clients = {
        "prices_db": {"db_type": "sqlite", "db_path": "query_dataset/prices.db"},
        "articles_db": {"db_type": "mongo", "db_name": "articles", "dump_folder": "dump"},
    }
    (dataset / "db_config.yaml").write_text(json.dumps({"db_clients": clients}))
    (dataset / "db_description.txt").write_text("prices_db holds daily closes.\n")
    query = dataset / "query1"
    query.mkdir()
    (query / "query.json").write_text(json.dumps("Which ticker closed highest?"))
    (query / "ground_truth.csv").write_text("T002")
    (query / "validate.py").write_text("GT = 'T002'\n")
    return root


def dab_entry(
    root: Path,
    layer: str,
    capsys: pytest.CaptureFixture[str],
    capture: Capture,
    monkeypatch: pytest.MonkeyPatch,
) -> EntryRun:
    """DAB: its own command line, `python -m evals dab ... --config <layer>`, in-process, on a
    stand-in DAB checkout (tests/fixtures/fake_dab). Through the command line rather than
    `run_query`, whose config argument ticket 146 changes; the ablation name stays the entry's
    (spec Q4). DAB's own system prompt and tool server (`dab.py` `run_once`)."""
    from evals import __main__ as evals_main

    no_docker(monkeypatch)
    dab_root = make_dab(root / "dab")
    out = root / "out"
    argv = ["dab", "--dab-root", str(dab_root), "--dataset", "toyset", "--query", "1"]
    argv += ["--runs", "1", "--config", layer, "--out", str(out), "--no-hints"]
    capsys.readouterr()
    code = evals_main.main(argv)
    assert capture.sessions, f"no session (exit {code!r}): {capsys.readouterr().err[-3000:]}"
    [events_file] = sorted(out.rglob("events.jsonl"))
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
    return EntryRun(capture.only(), event_at(events, 0, "version"), events, None, [root])


class JaffleStopped(Exception):
    """Raised in place of the jaffle case's `data-agent run` process, once its command is read."""


def jaffle_entry(
    root: Path,
    layer: str,
    capsys: pytest.CaptureFixture[str],
    capture: Capture,
    monkeypatch: pytest.MonkeyPatch,
    project_config: str | None = None,
) -> EntryRun:
    """jaffle: its own command line, `python -m evals jaffle --postgres-env VAR --config <layer>
    --case b1_monthly_revenue` (a dbt build case without setup edits), in-process. What needs
    Postgres, git or dbt is replaced: the clone of jaffle_shop by a copy of the mini dbt project,
    the schema reset and `dbt seed`/`dbt run` by nothing. The `data-agent run` process it starts
    is not started: its command and environment are read (`subprocess.run` as `evals.jaffle`
    sees it), and the command is run in-process in that environment. Postgres is a placeholder
    URL; the dictionary build is served by `dictionary_from_duckdb`; no `--sandbox`, so the
    docker backend, whose calls `no_docker` answers."""
    import subprocess

    from evals import __main__ as evals_main
    from evals import jaffle

    no_docker(monkeypatch)
    dictionary_from_duckdb(monkeypatch, root)
    monkeypatch.setenv(PG_URL_ENV, PG_URL)
    project = root / "jaffle_project"
    shutil.copytree(DBT_SHOP, project)
    if project_config is not None:
        # Copied with the project into the case's workspace (`prepare_case_dir`).
        write_config(project, project_config)
    monkeypatch.setattr(jaffle, "clone_project", lambda cases, dest: project)
    monkeypatch.setattr(jaffle, "reset_schemas", lambda url: None)
    monkeypatch.setattr(jaffle, "dbt", lambda workspace, env, *args: None)
    started: list[tuple[list[str], dict[str, str]]] = []

    def run(command: Sequence[str], env: Mapping[str, str] | None = None, **_: Any) -> Any:
        started.append(([str(word) for word in command], dict(env or os.environ)))
        raise JaffleStopped

    monkeypatch.setattr(
        jaffle,
        "subprocess",
        SimpleNamespace(
            run=run,
            TimeoutExpired=subprocess.TimeoutExpired,
            CalledProcessError=subprocess.CalledProcessError,
        ),
    )
    out = root / "out"
    argv = ["jaffle", "--postgres-env", PG_URL_ENV, "--out", str(out), "--config", layer]
    argv += ["--case", JAFFLE_CASE]
    with pytest.raises(JaffleStopped):
        evals_main.main(argv)
    assert len(started) == 1, f"expected one data-agent process, got {len(started)}"
    command, env = started[0]
    assert command[1] == "run", f"not `data-agent run`: {command[:3]}"
    args = substitute(command[2:], {})
    with environment(monkeypatch, env):
        run_entry_cli(capsys, args, capture)
    events_dir = Path(flag_value(args, "--events-dir"))
    events = read_events(events_dir)
    return EntryRun(capture.only(), event_at(events, 0, "version"), events, args, [root])


@contextmanager
def environment(monkeypatch: pytest.MonkeyPatch, env: Mapping[str, str]) -> Iterator[None]:
    """os.environ as `env`, for the in-process run of a command an entry would have started with
    it; the test's own environment afterwards."""
    with monkeypatch.context() as scoped:
        for name in list(os.environ):
            if name not in env:
                scoped.delenv(name)
        for name, value in env.items():
            scoped.setenv(name, value)
        yield


def drive_entry(
    entry: str,
    layer: str,
    root: Path,
    capsys: pytest.CaptureFixture[str],
    capture: Capture,
    monkeypatch: pytest.MonkeyPatch,
    project_config: str | None = None,
) -> EntryRun:
    """Runs one entry (`SNAPSHOT_CASES`). With `project_config`, a project-level config file is
    in the run's workspace (DAB makes its workspace itself, so there only in the working
    directory) and in the working directory the entry is run from."""
    root.mkdir(parents=True, exist_ok=True)
    if project_config is not None:
        cwd = root / "cwd"
        write_config(cwd, project_config)
        monkeypatch.chdir(cwd)
    if entry == "data-eng-bench":
        return harbor_entry(root, layer, capsys, capture, project_config)
    if entry == "livesqlbench":
        return livesqlbench_entry(root, layer, capsys, capture, monkeypatch, project_config)
    if entry == "dab":
        return dab_entry(root, layer, capsys, capture, monkeypatch)
    assert entry == "jaffle", entry
    return jaffle_entry(root, layer, capsys, capture, monkeypatch, project_config)


# --- A user's own config (the isolation guard; the Skill, MCP, plugin and prompt tests) -----

USER_SKILLS = PRODUCT_FIXTURES / "user_skills"
USER_SKILL = "acme-sql"
USER_SKILL_MARKER = "ACME-SKILL-MARKER"
USER_PLUGIN = PRODUCT_FIXTURES / "user_plugin"
USER_PLUGIN_SKILL_MARKER = "ACME-PLUGIN-MARKER"
INSTRUCTIONS = PRODUCT_FIXTURES / "instructions.md"
INSTRUCTIONS_MARKER = "ACME-INSTRUCTIONS-MARKER"
SOURCE_DOCS = PRODUCT_FIXTURES / "source_docs"
SOURCE_DOCS_FILE = "glossary.md"
SOURCE_DOCS_MARKER = "ACME-DOCS-MARKER"
# The variable a user's MCP server's header names, and its placeholder value.
MCP_TOKEN_ENV = "DATA_AGENT_MCP_ACME_TOKEN"
MCP_TOKEN = "acme-" + "Wz81" + "-fake"
USER_MCP_SERVER = "acme"
CLAUDE_CODE_MCP_SERVER = "cc-notes"
USER_ANSWER_RULE = "USER-ANSWER-RULE: answer in one word."


def user_config_text() -> str:
    """What a user might keep in `~/.data-agent/config.toml`: every layer off, other limits and
    prompts, an MCP server, Skill and plugin directories, an instructions file, and the user's
    Claude Code MCP config turned on. Its one secret is a `${VAR}` reference."""
    header = "Bearer ${" + MCP_TOKEN_ENV + "}"
    return "\n".join(
        [
            "[layers]",
            "skill = false",
            "verifiers = false",
            "context = false",
            "",
            "[limits]",
            "max_budget_usd = 9.5",
            "agent_timeout_sec = 60",
            'effort = "low"',
            "",
            "[limits.max_turns]",
            "dbt = 7",
            "query = 5",
            "",
            "[prompts]",
            f"answer_rule = {json.dumps(USER_ANSWER_RULE)}",
            "",
            "[skills]",
            f"dirs = [{toml_path(USER_SKILLS)}]",
            f"preload = [{json.dumps(USER_SKILL)}]",
            "",
            "[plugins]",
            f"dirs = [{toml_path(USER_PLUGIN)}]",
            "",
            "[instructions]",
            f"files = [{toml_path(INSTRUCTIONS)}]",
            "",
            "[mcp]",
            "claude_code_config = true",
            "",
            f"[mcp.servers.{USER_MCP_SERVER}]",
            'type = "http"',
            'url = "https://mcp.example/acme"',
            f"headers = {{ Authorization = {json.dumps(header)} }}",
            'allowedTools = ["*"]',
            "",
        ]
    )


def project_config_text() -> str:
    """A project-level `.data-agent/config.toml`: other layers and limits again."""
    return "\n".join(
        [
            "[layers]",
            "skill = false",
            "verifiers = true",
            "context = false",
            "",
            "[limits]",
            "max_budget_usd = 0.25",
            "",
            "[limits.max_turns]",
            "dbt = 3",
            "query = 2",
            "",
        ]
    )


def claude_code_mcp(home: Path) -> Path:
    """A user's Claude Code MCP config in `home` (`~/.claude.json`, `mcpServers`)."""
    path = home / ".claude.json"
    server = {"type": "http", "url": "https://mcp.example/notes"}
    path.write_text(json.dumps({"mcpServers": {CLAUDE_CODE_MCP_SERVER: server}}), encoding="utf-8")
    return path


# --- The new interface, for the acceptance files written before it (ticket 141) ------------
#
# Each old option becomes a config file given with `--config` (repeatable, later files win;
# tests/acceptance/test_config_cli.py):
#   `--config <ablation name>` (a product entry)  -> `--config layer_config(<name>)`
#   `--data-sources FILE`                         -> `--config FILE` (its `[sources.<name>]`
#                                                    tables are the config's, unchanged)
#   `--mcp-servers FILE.json`                     -> `--config mcp_config(dir, FILE.json)`
#   `--im FILE` (`[slack]`, ...)                  -> `--config im_config(dir, FILE)` (`[im.slack]`)
#   `run_config.load_config(<name>)` (in-process) -> `run_config_of(<name>)`
#   `run_config.CONFIG_NAMES`                     -> `RUN_CONFIG_NAMES`

LAYERS = PRODUCT_FIXTURES / "layers"
# The six old run configs (`src/data_agent/configs/`, which ticket 146 deletes), in the order
# `CONFIG_NAMES` had them (sorted).
RUN_CONFIG_NAMES = ("baseline", "full", "full-old-skill", "no-context", "no-skill", "no-verifiers")
# The one run config only evaluations have (the Skill directory swapped): no product layer file,
# but ticket 146's evaluation layer file.
EVAL_ONLY_LAYER = "full-old-skill"
EVAL_LAYERS = REPO_ROOT / "evals" / "profiles" / "layers"


def layer_config(name: str) -> Path:
    """A product config file holding only the `[layers]` of the old run config `name` (`full`,
    `baseline`, `no-skill`, `no-verifiers`, `no-context`); its name without `.toml` is `name`, so
    the `version` event's `config` stays `name` (test_config_cli.py contract item 3)."""
    path = LAYERS / f"{name}.toml"
    assert path.is_file(), f"no layer file for {name!r}"
    return path


def layer_file(name: str) -> Path:
    """The config file of the old run config `name`: its product layer file, or for
    `full-old-skill` ticket 146's `evals/profiles/layers/full-old-skill.toml`, asserted to be
    there first."""
    if name == EVAL_ONLY_LAYER:
        path = EVAL_LAYERS / f"{name}.toml"
        assert path.is_file(), f"no evaluation layer file {path} yet (ticket 146)"
        return path
    return layer_config(name)


def run_config_of(name: str) -> Any:
    """The `RunConfig` an in-process `agent.run`/`build_options` is given for the old run config
    `name`, read the new way: `run_config.load_config` given the `EffectiveConfig` of the layer
    file (ticket 143 Authority: `load_config` derives the `RunConfig` from the effective config;
    ticket 146 removes `load_config(<name>)`). The file is asserted to be merged first, so today's
    empty loader fails on an assertion, not on `load_config`'s `ValueError`."""
    from data_agent import run_config
    from data_agent.config import load_effective_config

    path = layer_file(name)
    effective = load_effective_config([path], dict(os.environ), {})
    assert path in effective.files, f"the config loader did not merge {path}"
    # By an Any: today's `load_config` takes a name, ticket 143's the effective config.
    load: Any = run_config.load_config
    return load(effective)


def mcp_config(directory: Path, servers_file: Path) -> Path:
    """A config file whose `[mcp]` names a Claude Code / Cursor `mcpServers` JSON file (with an
    `allowedTools` list per server, ticket 116's rules): spec Q2 `[mcp].file`."""
    return write_file(
        directory / "mcp-config.toml", f"[mcp]\nfile = {toml_path(servers_file.resolve())}\n"
    )


def im_config(directory: Path, im_file: Path) -> Path:
    """The old `--im` file's platform tables (`[slack]`, `[telegram]`, `[discord]`) as the
    config's `[im.<platform>]` tables, keys and values unchanged."""
    text = re.sub(r"^\[(slack|telegram|discord)\]", r"[im.\1]", im_file.read_text(), flags=re.M)
    return write_file(directory / "im-config.toml", text)


# --- The web entry --------------------------------------------------------------------------

# tests/fixtures/chat/fake_chat_run.py (reused read-only, as test_mcp_web.py does): it ignores
# arguments it does not know and waits on a gate file, so a test reads a run's arguments while it
# is going, and the run's directory (with whatever the server wrote there) is still in place.
FAKE_CHAT_RUN = FIXTURES / "chat" / "fake_chat_run.py"
WEB_TIMEOUT = 60
FORM = {"task": "Count orders", "task_type": "query", "data_source": "shop"}


@dataclass
class Started:
    args: list[str]
    process: Any


@dataclass
class Web:
    """An app built by `create_app`, its runs recorded by the `start_run` it was given."""

    app: Any
    started: list[Started]
    gates: Path

    def only_args(self) -> list[str]:
        assert len(self.started) == 1, f"expected one run, got {len(self.started)}"
        return self.started[0].args


def recording_start_run(gates: Path, started: list[Started]) -> Any:
    import subprocess

    def start_run(args: Sequence[str], cwd: Path) -> Any:
        process = subprocess.Popen(
            [sys.executable, str(FAKE_CHAT_RUN), "--gate-dir", str(gates), *args],
            cwd=cwd,
            stdin=subprocess.PIPE,
        )
        started.append(Started(list(args), process))
        return process

    return start_run


def stop_runs(started: Sequence[Started]) -> None:
    """Only the fake runs this test started, by their own process handles."""
    for run in started:
        if run.process.poll() is None:
            run.process.terminate()
        run.process.wait(timeout=WEB_TIMEOUT)


def shop_sources(root: Path) -> list[Any]:
    from data_agent.web.sources import DataSource

    return [DataSource(name="shop", duckdb=build_shop_duckdb(root / "shop.duckdb"))]


@contextmanager
def web_app(root: Path, **create_kwargs: Any) -> Iterator[Web]:
    """`create_app(runs_dir, [shop], start_run, **create_kwargs)` with a recording `start_run`."""
    from data_agent.web.app import create_app

    runs_dir = root / "runs"
    runs_dir.mkdir(parents=True)
    gates = root / "gates"
    gates.mkdir()
    started: list[Started] = []
    app = create_app(
        runs_dir, shop_sources(root), recording_start_run(gates, started), **create_kwargs
    )
    try:
        yield Web(app, started, gates)
    finally:
        stop_runs(started)


@dataclass
class Served:
    """`data-agent web` run in-process with `uvicorn.run` stood in for: the app the real
    `create_app` built (its `start_run` swapped for a recording one), `create_app`'s arguments
    bound to its signature, and `uvicorn.run`'s keyword arguments."""

    code: int | str | None
    web: Web | None
    created: dict[str, Any] | None
    uvicorn: dict[str, Any] | None


@contextmanager
def served(root: Path, monkeypatch: pytest.MonkeyPatch, argv: Sequence[str]) -> Iterator[Served]:
    """`data-agent web <argv>`. `create_app` keeps a `start_run` parameter (ticket 141 contract
    item 4); everything else it is given is the command's choice."""
    import inspect

    import uvicorn

    from data_agent.web import app as web_module

    real = web_module.create_app
    signature = inspect.signature(real)
    gates = root / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    started: list[Started] = []
    built: dict[str, Any] = {}

    def create_app(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        built["created"] = dict(bound.arguments)
        bound.arguments["start_run"] = recording_start_run(gates, started)
        built["app"] = real(*bound.args, **bound.kwargs)
        return built["app"]

    def run(app: Any, **kwargs: Any) -> None:
        built["uvicorn"] = kwargs

    # Patched for this call only: a test may serve twice, and a second call must wrap the real
    # `create_app`, not the first call's stand-in.
    with monkeypatch.context() as patch:
        patch.setattr(web_module, "create_app", create_app)
        patch.setattr(uvicorn, "run", run)
        try:
            cli.main(["web", *argv])
        except SystemExit as exc:
            code = exc.code
        else:
            code = None
    web = Web(built["app"], started, gates) if "app" in built else None
    try:
        yield Served(code, web, built.get("created"), built.get("uvicorn"))
    finally:
        stop_runs(started)


def submit_form(web: Web, **fields: Any) -> list[str]:
    """`POST /api/runs` with the form's fields (no `config`: the form picks no ablation, ticket
    141 contract item 5); the run's arguments."""
    from fastapi.testclient import TestClient

    with TestClient(web.app) as client:
        response = client.post("/api/runs", json={**FORM, **fields})
        assert response.status_code == 201, response.text
    return web.only_args()


def start_turn(web: Web) -> list[str]:
    """A conversation's first message (`POST /api/conversations/{id}/messages`); the turn's
    arguments."""
    from fastapi.testclient import TestClient

    with TestClient(web.app) as client:
        response = client.post("/api/conversations", json={})
        assert response.status_code == 201, response.text
        conversation = response.json()["id"]
        response = client.post(
            f"/api/conversations/{conversation}/messages", json={"text": "How many orders?"}
        )
        assert response.status_code == 201, response.text
    return web.only_args()


def im_turn(web: Web) -> list[str]:
    """A conversation turn the way the IM adapter starts one (`im/adapter.py` `send_turn`):
    `Conversations.send(conversation, text)`, with no `network` and no config of its own."""
    from data_agent.web.launcher import Refused

    conversations = web.app.state.conversations
    conversation = conversations.create()
    try:
        sent = conversations.send(conversation, "How many orders?")
    except Refused as exc:
        raise AssertionError(f"the turn was refused: {exc.status} {exc.detail}") from exc
    assert sent.get("kind") == "turn", sent
    return web.only_args()
