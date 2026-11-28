"""The layered product config (`.scratch/product-config/spec.md` "配置文件与分层", "分节";
ADR-0011; decisions.md Q1, Q2, Q8).

`load_effective_config` merges, key by key (a later layer replaces a key whole; lists are not
concatenated):

    code defaults (`SCHEMA`)
    < `~/.data-agent/config.toml` (under `environ["HOME"]`)
    < `<workspace>/.data-agent/config.toml`
      (or, in their place, the explicit files, later ones winning; or `DATA_AGENT_CONFIG` alone)
    < the environment (`DATA_AGENT_MODEL` -> `roles.main`, `DATA_AGENT_BASE_URL` ->
      `models.<main model>.base_url`)
    < `cli_overrides`

and `[policy]` over all of them: the first file that sets a policy key holds it, and
`policy.network = false` locks `sandbox.network` to false. A command-line value against a policy
is refused, a later file's is not taken.

Every file is checked on its own against `SCHEMA` before it is merged, so an error names the file
and the dotted key (`ConfigFileError`): an unknown section or key, a value of the wrong type, a
plaintext secret, or a `${VAR}` reference to an unset variable. No message holds a value.

`${VAR}` references are expanded in `EffectiveConfig.value`; `EffectiveConfig.text` (and `raw`)
keeps them as written. The one exception is `[mcp.servers.*]`: its values stay as written in
`value` too, because `data_agent.external_mcp` expands them by its own rules (only
`DATA_AGENT_MCP_*` variables, only in `env` and `headers`; ticket 116).

Relative paths count from the directory of the file that sets them; the effective config holds
them absolute, so its text means the same wherever it is written (a web run's directory).

The sections' types are defined here (keys, types, defaults); what each section does is the
business of the code that reads it (tickets 144-148).
"""

import hashlib
import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The choices of `dbt_execute.TARGETS`, `sandbox.BACKENDS` and `tracing.TRACE_TIERS`, written out:
# those modules import `run_config`, which imports this one (tests/test_config.py keeps them equal).
TARGETS = ("scratch", "task")
BACKENDS = ("docker", "local")
TRACE_TIERS = ("full", "structure")

CONFIG_DIR = ".data-agent"
CONFIG_FILE = "config.toml"
CONFIG_ENV = "DATA_AGENT_CONFIG"
MODEL_ENV = "DATA_AGENT_MODEL"
BASE_URL_ENV = "DATA_AGENT_BASE_URL"
# The `version` event's `config` when no file is read (decisions.md Q8).
DEFAULT_NAME = "default"
DEFAULT_SOURCE = "default"
CLI = "cli"
POLICY = "policy"

# A key whose value is a secret: it may only be a `${VAR}` reference (spec Q1). Matched on the
# key's last part, case-insensitively and with `-` read as `_`, so an MCP server's
# `env.DATA_AGENT_MCP_GH_TOKEN` and a `headers.X-Api-Key` count.
SECRET_SUFFIXES = ("api_key", "apikey", "token", "password", "secret")
# A key whose value carries a secret beside other text (`Bearer ${VAR}`): it must reference one.
CREDENTIAL_KEYS = ("authorization", "proxy_authorization", "cookie")
# What a gitleaks rule catches in any value: the repo's own (`.gitleaks.toml` `deepseek-api-key`;
# its IPv4 rule is about the repo, not secrets) and the shapes of the default rules for the
# credentials a config of ours is likeliest to hold (GitHub, Slack, Anthropic and OpenAI keys,
# AWS access keys, private keys).
SECRET_VALUES = (
    re.compile(r"\bsk-[0-9a-f]{32}\b"),
    re.compile(r"\b(?:gh[pousr]_[0-9A-Za-z]{36}|github_pat_\w{82})\b"),
    re.compile(r"\bxox[abposr]-[0-9A-Za-z-]{10,}"),
    re.compile(r"\bxapp-\d-[0-9A-Za-z-]{10,}"),
    re.compile(r"\bsk-ant-[0-9A-Za-z_-]{20,}"),
    re.compile(r"\bsk-(?:proj-)?[0-9A-Za-z_-]{40,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
REFERENCE = re.compile(r"\$\{([^}]*)\}")
WHOLE_REFERENCE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")
VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A key TOML takes unquoted.
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
# Values kept as written in `value`, for the code reading them to expand (module docstring).
UNEXPANDED = ("mcp", "servers")


class ConfigFileError(ValueError):
    """A config file that cannot be loaded. `path` is the file, `key` the dotted key (or the
    `${VAR}` variable's key) at fault; the message names both and never holds a secret's value."""

    def __init__(self, message: str, path: Path | None = None, key: str | None = None) -> None:
        super().__init__(message)
        self.path = path
        self.key = key


# --- The schema ---------------------------------------------------------------------------


class Unset:
    """No default: the key has no value until a layer sets it."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = Unset()


@dataclass(frozen=True)
class Leaf:
    """A value. `kind`: bool, int, number, str, path, enum, list[str], list[path], users (IM
    user ids: strings or integers)."""

    kind: str
    default: Any = UNSET
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Table:
    """A table with these keys only. With `entries`, each of its tables is an entry that may
    not be empty (`[im.<platform>]`), as a `Named` table's are."""

    keys: Mapping[str, "Node"]
    entries: bool = False


@dataclass(frozen=True)
class Named:
    """A table of entries by name (`[sources.<name>]`), each one `entry`. With `free_names` any
    key is a name (an MCP server's `env` variables, its `headers`)."""

    entry: "Node"
    free_names: bool = False


Node = Leaf | Table | Named

STRINGS = Named(Leaf("str"), free_names=True)
MODEL = Table(
    {
        "base_url": Leaf("str"),
        "api_key": Leaf("str"),
        # Per million tokens (spec Q2); left out, the spend is unknown (ticket 145).
        "input_price": Leaf("number"),
        "cache_read_price": Leaf("number"),
        "output_price": Leaf("number"),
        "context_window": Leaf("int"),
        "compact_window": Leaf("int"),
        "efforts": Leaf("list[str]"),
    }
)
# Today's prices (`agent.PRICES`, `agent.DEFAULT_BASE_URL`; tests/test_config.py keeps them
# equal) as the code defaults' `[models.*]` (spec Q2).
DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODELS: dict[str, dict[str, Any]] = {
    "deepseek-flash": {
        "base_url": DEFAULT_BASE_URL,
        "input_price": 0.30,
        "cache_read_price": 0.006,
        "output_price": 1.2,
    },
    "deepseek-v4-pro": {
        "base_url": DEFAULT_BASE_URL,
        "input_price": 1.32,
        "cache_read_price": 1.32,
        "output_price": 3.96,
    },
}
SOURCE = Table(
    {
        "kind": Leaf("enum", choices=("duckdb", "postgres")),
        "path": Leaf("path"),
        # The variable holding the connection string: a name, not a secret (decisions.md Q8).
        "env": Leaf("str"),
        "dbt_project": Leaf("path"),
        "description": Leaf("str"),
        "docs": Leaf("path"),
        "schemas": Table({"include": Leaf("list[str]"), "exclude": Leaf("list[str]")}),
        "query_timeout_seconds": Leaf("number"),
        "max_rows": Leaf("int"),
        "duckdb": Table({"memory_limit": Leaf("str"), "max_temp_directory_size": Leaf("str")}),
        "dbt_timeout_seconds": Leaf("number"),
    }
)
# Ticket 116's fields (`external_mcp.FIELDS`), which `external_mcp` checks further.
MCP_SERVER = Table(
    {
        "type": Leaf("str"),
        "command": Leaf("str"),
        "args": Leaf("list[str]"),
        "env": STRINGS,
        "url": Leaf("str"),
        "headers": STRINGS,
        "allowedTools": Leaf("list[str]"),
    }
)


def im_platform(*token_keys: str) -> Table:
    # The token keys hold the variable's name (`im.config.TOKEN_KEYS`), not the token.
    return Table({**{key: Leaf("str") for key in token_keys}, "allowed_users": Leaf("users")})


SCHEMA = Table(
    {
        "layers": Table(
            {
                "skill": Leaf("bool", True),
                "verifiers": Leaf("bool", True),
                "context": Leaf("bool", True),
            }
        ),
        "models": Named(MODEL),
        "roles": Table(
            {
                "main": Leaf("str", "deepseek-flash"),
                "small": Leaf("str"),
                "classify": Leaf("str"),
                "verifier": Leaf("str"),
            }
        ),
        "limits": Table(
            {
                "max_budget_usd": Leaf("number", 1.0),
                "max_turns": Table(
                    {
                        "dbt": Leaf("int", 200),
                        "query": Leaf("int", 50),
                        "large": Leaf("int", 75),
                    }
                ),
                "agent_timeout_sec": Leaf("number"),
                # Not checked against `agent.EFFORTS` here: a model's `efforts` decides (145).
                "effort": Leaf("str"),
            }
        ),
        "sandbox": Table(
            {
                "backend": Leaf("enum", choices=BACKENDS),
                "image": Leaf("str"),
                "cpus": Leaf("number"),
                "memory": Leaf("str"),
                "network": Leaf("bool"),
                "exec_max_seconds": Leaf("number"),
            }
        ),
        "sources": Named(SOURCE),
        "skills": Table(
            {
                "dirs": Leaf("list[path]", []),
                "disabled": Leaf("list[str]", []),
                # Unset: the task type's workflow Skill (ticket 147).
                "preload": Leaf("list[str]"),
                "tool": Leaf("bool", True),
            }
        ),
        "instructions": Table({"files": Leaf("list[path]", [])}),
        "mcp": Table(
            {
                "servers": Named(MCP_SERVER),
                "file": Leaf("path"),
                "claude_code_config": Leaf("bool", False),
            }
        ),
        "plugins": Table({"dirs": Leaf("list[path]", []), "enabled": Leaf("list[str]", [])}),
        "prompts": Table({"answer_rule": Leaf("str")}),
        "im": Table(
            {
                "slack": im_platform("bot_token_env", "app_token_env"),
                "telegram": im_platform("bot_token_env"),
                "discord": im_platform("bot_token_env"),
            },
            entries=True,
        ),
        "trace": Table({"base_url": Leaf("str"), "tier": Leaf("enum", choices=TRACE_TIERS)}),
        "server": Table(
            {
                "host": Leaf("str", "127.0.0.1"),
                "port": Leaf("int", 8765),
                "runs_dir": Leaf("path"),
                "mode": Leaf("enum", "local", choices=("local", "cloud")),
            }
        ),
        "policy": Table(
            {
                "disabled_models": Leaf("list[str]"),
                "network": Leaf("bool"),
                "dbt_target": Leaf("enum", choices=TARGETS),
            }
        ),
    }
)


def default_leaves() -> dict[str, Any]:
    """The code defaults, as dotted keys to values."""
    leaves: dict[str, Any] = {}

    def walk(node: Node, prefix: tuple[str, ...]) -> None:
        if isinstance(node, Leaf):
            if node.default is not UNSET:
                default = node.default
                leaves[Key(prefix)] = list(default) if isinstance(default, list) else default
        elif isinstance(node, Table):
            for key, child in node.keys.items():
                walk(child, (*prefix, key))

    walk(SCHEMA, ())
    for name, entry in DEFAULT_MODELS.items():
        for key, value in entry.items():
            leaves[Key(("models", name, key))] = value
    # In schema order: `[models.*]` after `[layers]`.
    return {key: leaves[key] for key in in_section_order(leaves)}


def in_section_order(keys: Iterable[str]) -> list[str]:
    """Dotted keys by their section's place in `SCHEMA`, in their own order within a section."""
    order = list(SCHEMA.keys)
    return sorted(keys, key=lambda key: order.index(key.split(".", 1)[0]))


class Key(str):
    """A dotted key (`sources.shop.kind`) that keeps its parts: a name may hold a dot
    (`[models."gpt-4.1"]`), so splitting the string would not give them back. Equal to, and
    found in a dict by, its dotted string."""

    parts: tuple[str, ...]

    def __new__(cls, parts: Sequence[str]) -> "Key":
        key = super().__new__(cls, ".".join(parts))
        key.parts = tuple(parts)
        return key


def parts_of(key: str) -> tuple[str, ...]:
    """A key's parts: a `Key`'s own, else the dotted string split (a key written in code)."""
    return key.parts if isinstance(key, Key) else tuple(key.split("."))


def node_at(key: str) -> Leaf | None:
    """The leaf a dotted key names, None when there is none."""
    node: Node = SCHEMA
    for part in parts_of(key):
        if isinstance(node, Table):
            if part not in node.keys:
                return None
            node = node.keys[part]
        elif isinstance(node, Named):
            node = node.entry
        else:
            return None
    return node if isinstance(node, Leaf) else None


# --- One file -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Origin:
    """Where a layer's values come from, for the messages."""

    label: str
    path: Path | None = None
    # The directory relative paths count from.
    base: Path | None = None

    def error(self, key: str | None, reason: str) -> ConfigFileError:
        where = f"{self.label}: {key}" if key else self.label
        return ConfigFileError(f"{where}: {reason}", self.path, key)


def read_file(path: Path) -> dict[str, Any]:
    import tomllib

    origin = Origin(str(path), path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise origin.error(None, "config file not found") from None
    except OSError as exc:
        raise origin.error(None, f"cannot read it: {exc.strerror}") from None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        # tomllib's message names a line and column, never a value.
        raise origin.error(None, f"not valid TOML: {exc}") from None


def file_leaves(data: Mapping[str, Any], origin: Origin, environ: Mapping[str, str]) -> dict:
    """The file's values as dotted keys to raw values (paths absolute), checked against
    `SCHEMA`; ConfigFileError on the first problem."""
    leaves: dict[str, Any] = {}
    walk_table(SCHEMA, data, (), origin, environ, leaves)
    return leaves


def walk_table(
    node: Table | Named,
    data: Any,
    prefix: tuple[str, ...],
    origin: Origin,
    environ: Mapping[str, str],
    leaves: dict[str, Any],
) -> None:
    if not isinstance(data, dict):
        raise origin.error(Key(prefix), "must be a table")
    for key, value in data.items():
        dotted = Key((*prefix, key))
        if isinstance(node, Table):
            if key not in node.keys:
                raise origin.error(dotted, "unknown key" if prefix else "unknown section")
            child = node.keys[key]
        else:
            if not key:
                raise origin.error(dotted, "a name may not be empty")
            child = node.entry
        if isinstance(child, Leaf):
            check_secret(dotted, value, origin)
            leaves[dotted] = leaf_value(child, dotted, value, origin, environ)
        else:
            # An entry by name (`[sources.shop]`, `[mcp.servers.gh]`) or an IM platform with no
            # key would leave no trace once flattened, as if it were not there.
            if value == {} and (
                isinstance(node, Named) or (isinstance(node, Table) and node.entries)
            ):
                raise origin.error(dotted, "an empty table; give its keys or leave it out")
            walk_table(child, value, dotted.parts, origin, environ, leaves)


def key_name(key: str) -> str:
    return parts_of(key)[-1].lower().replace("-", "_")


def is_secret_key(key: str) -> bool:
    return key_name(key).endswith(SECRET_SUFFIXES)


def check_secret(key: str, value: Any, origin: Origin) -> None:
    """A plaintext secret is refused before any other check, its value never repeated."""
    hint = "a secret is written as a ${VAR} reference to an environment variable"
    if is_secret_key(key) and not (isinstance(value, str) and WHOLE_REFERENCE.fullmatch(value)):
        raise origin.error(key, f"looks like a plaintext secret; {hint}")
    if key_name(key) in CREDENTIAL_KEYS and not (
        isinstance(value, str) and REFERENCE.search(value)
    ):
        raise origin.error(key, f"holds a credential without a reference; {hint}")
    for text in strings_in(value):
        if any(pattern.search(text) for pattern in SECRET_VALUES):
            raise origin.error(key, f"holds what looks like an API key; {hint}")


def strings_in(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item)


def leaf_value(leaf: Leaf, key: str, value: Any, origin: Origin, environ: Mapping[str, str]) -> Any:
    """The raw value (references as written, paths absolute); ConfigFileError naming the type
    expected, never the value."""
    kind = leaf.kind
    if kind == "bool":
        if not isinstance(value, bool):
            raise origin.error(key, "must be true or false")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise origin.error(key, "must be an integer")
        return value
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise origin.error(key, "must be a number")
        return float(value)
    if kind == "enum":
        if not isinstance(value, str) or value not in leaf.choices:
            raise origin.error(key, f"must be one of {', '.join(leaf.choices)}")
        return value
    if kind == "str":
        if not isinstance(value, str):
            raise origin.error(key, "must be a string")
        check_references(key, value, origin, environ)
        return value
    if kind == "path":
        return path_value(key, value, origin)
    if kind == "users":
        if not isinstance(value, list) or not all(
            isinstance(item, str | int) and not isinstance(item, bool) for item in value
        ):
            raise origin.error(key, "must be a list of user ids (strings or integers)")
        for item in value:
            if isinstance(item, str):
                check_references(key, item, origin, environ)
        return list(value)
    if not isinstance(value, list):
        raise origin.error(key, "must be a list")
    if kind == "list[str]":
        if not all(isinstance(item, str) for item in value):
            raise origin.error(key, "must be a list of strings")
        for item in value:
            check_references(key, item, origin, environ)
        return list(value)
    assert kind == "list[path]", kind
    return [path_value(key, item, origin) for item in value]


def path_value(key: str, value: Any, origin: Origin) -> str:
    if not isinstance(value, str) or not value:
        raise origin.error(key, "must be a non-empty path")
    if "${" in value:
        raise origin.error(key, "a path may not hold a ${VAR} reference")
    path = Path(value).expanduser()
    if not path.is_absolute():
        if origin.base is None:
            raise origin.error(key, "must be an absolute path")
        path = origin.base / path
    return str(path)


def check_references(key: str, value: str, origin: Origin, environ: Mapping[str, str]) -> None:
    for match in REFERENCE.finditer(value):
        name = match.group(1)
        if not VARIABLE_NAME.fullmatch(name):
            raise origin.error(key, "a reference is written ${VARIABLE_NAME}")
        if not environ.get(name):
            raise origin.error(key, f"the variable {name} it references is not set")
    if "${" in REFERENCE.sub("", value):
        raise origin.error(key, "an unclosed ${ reference")


def expand(key: str, value: Any, environ: Mapping[str, str]) -> Any:
    """`value` with its references expanded (checked set by `check_references`), except under
    `UNEXPANDED`; paths as `Path`."""
    if parts_of(key)[:2] == UNEXPANDED:
        return value
    leaf = node_at(key)
    kind = leaf.kind if leaf is not None else ""
    if kind == "path":
        return Path(value)
    if kind == "list[path]":
        return [Path(item) for item in value]
    if isinstance(value, str):
        return REFERENCE.sub(lambda match: environ[match.group(1)], value)
    if isinstance(value, list):
        return [expand(key, item, environ) for item in value]
    return value


# --- The effective config -----------------------------------------------------------------


@dataclass(frozen=True)
class EffectiveConfig:
    """The merged config of a run. Keys are dotted (`limits.max_turns.dbt`)."""

    # The files merged, in merge order.
    files: tuple[Path, ...] = ()
    # The effective config as TOML, `${VAR}` references as written, and its sha256 (hex).
    text: str = ""
    sha256: str = ""
    # Resolved values (references expanded) and as written, by dotted key.
    values: Mapping[str, Any] = field(default_factory=dict[str, Any], repr=False)
    sources: Mapping[str, str] = field(default_factory=dict[str, str])
    raw: Mapping[str, Any] = field(default_factory=dict[str, Any])

    def value(self, key: str) -> Any | None:
        """The key's effective value; for a table (`mcp.servers`, `sources.shop`) its keys as
        nested dicts; None when unset."""
        if key in self.values:
            return self.values[key]
        return subtree(self.values, key)

    def raw_value(self, key: str) -> Any | None:
        """As `value`, every `${VAR}` reference as written."""
        if key in self.raw:
            return self.raw[key]
        return subtree(self.raw, key)

    def source(self, key: str) -> str | None:
        """Where the key's value came from: `default`, a file's path, `env:<VAR>`, `cli` or
        `policy`; None when unset."""
        return self.sources.get(key)

    def names(self, section: str) -> list[str]:
        """The entries of a named section (`sources`, `models`, `im`), in order."""
        found: list[str] = []
        for key in self.values:
            parts = parts_of(key)
            if parts[0] == section and len(parts) > 1 and parts[1] not in found:
                found.append(parts[1])
        return found

    def name(self) -> str:
        """The `version` event's `config`: the last file's name without `.toml`, or `default`
        (decisions.md Q8)."""
        return self.files[-1].stem if self.files else DEFAULT_NAME


def subtree(leaves: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    """The leaves under `key` (a table: `sources.shop`, `mcp.servers`) as nested dicts, None when
    there are none. Matched by key parts, never a string prefix, so `sources.shop` holds none of
    `sources."shop.archive"`."""
    found: dict[str, Any] = {}
    for dotted, value in leaves.items():
        depth = table_depth(parts_of(dotted), key)
        if depth is None:
            continue
        node = found
        *parents, last = parts_of(dotted)[depth:]
        for part in parents:
            node = node.setdefault(part, {})
        node[last] = value
    return found or None


def table_depth(parts: Sequence[str], key: str) -> int | None:
    """How many of a leaf's `parts` the table `key` takes, None when the leaf is not under it. A
    `Key` matches its own parts; a dotted string, the leading parts that join to it (a name may
    hold a dot: `sources.a.b` is `[sources."a.b"]`)."""
    if isinstance(key, Key):
        depth = len(key.parts)
        return depth if len(parts) > depth and tuple(parts[:depth]) == key.parts else None
    return next((n for n in range(1, len(parts)) if ".".join(parts[:n]) == key), None)


def config_files(
    paths: Sequence[Path], environ: Mapping[str, str], workspace: Path | None
) -> list[Path]:
    """The files to merge, in order: explicit ones must exist (`read_file`), the user-level and
    project-level ones only when they do."""
    if paths:
        return list(paths)
    if environ.get(CONFIG_ENV):
        return [Path(environ[CONFIG_ENV])]
    found: list[Path] = []
    if environ.get("HOME"):
        found.append(Path(environ["HOME"]) / CONFIG_DIR / CONFIG_FILE)
    if workspace is not None:
        found.append(workspace / CONFIG_DIR / CONFIG_FILE)
    return [path for path in found if path.is_file()]


def load_effective_config(
    paths: Sequence[Path],
    environ: Mapping[str, str],
    cli_overrides: Mapping[str, Any],
    workspace: Path | None = None,
) -> EffectiveConfig:
    """The effective config (module docstring): code defaults < `~/.data-agent/config.toml`
    (under `environ["HOME"]`) < `<workspace>/.data-agent/config.toml` < environment <
    `cli_overrides`, with `[policy]` over all; or, given `paths` (or `DATA_AGENT_CONFIG`), those
    files only, later ones winning. ConfigFileError on any error."""
    files = config_files(paths, environ, workspace)
    raw: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for key, value in default_leaves().items():
        raw[key], sources[key] = value, DEFAULT_SOURCE
    # policy key -> (raw value, the file that set it)
    policy: dict[str, tuple[Any, Path]] = {}
    for path in files:
        origin = Origin(str(path), path, path.resolve().parent)
        for key, value in file_leaves(read_file(path), origin, environ).items():
            if key.startswith("policy.") and key in policy:
                continue
            if key.startswith("policy."):
                policy[key] = (value, path)
            raw[key], sources[key] = value, str(path)
    env_origin = Origin("environment")
    if environ.get(MODEL_ENV):
        raw["roles.main"] = leaf_value(
            Leaf("str"), "roles.main", environ[MODEL_ENV], env_origin, {}
        )
        sources["roles.main"] = f"env:{MODEL_ENV}"
    if environ.get(BASE_URL_ENV):
        key = Key(("models", raw["roles.main"], "base_url"))
        raw[key], sources[key] = environ[BASE_URL_ENV], f"env:{BASE_URL_ENV}"
    locked = locks(policy)
    cli_origin = Origin("command line")
    for key, value in cli_overrides.items():
        key = Key(key.split("."))
        leaf = node_at(key)
        if leaf is None:
            raise cli_origin.error(key, "unknown key")
        check_secret(key, value, cli_origin)
        value = leaf_value(leaf, key, value, cli_origin, environ)
        if key in locked and value != locked[key][0]:
            policy_key, path = locked[key][1], locked[key][2]
            raise ConfigFileError(
                f"{path}: {policy_key}: the policy does not allow {key} to be changed on the "
                "command line",
                path,
                policy_key,
            )
        raw[key], sources[key] = value, CLI
    for key, (value, _, _) in locked.items():
        raw[key], sources[key] = value, POLICY
    values = {key: expand(key, value, environ) for key, value in raw.items()}
    text = toml_text(raw)
    return EffectiveConfig(
        files=tuple(files),
        text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        values=values,
        sources=sources,
        raw=raw,
    )


def locks(policy: Mapping[str, tuple[Any, Path]]) -> dict[str, tuple[Any, str, Path]]:
    """The keys the policy holds: key -> (value, the policy key, its file)."""
    held = {key: (value, key, path) for key, (value, path) in policy.items()}
    network = policy.get("policy.network")
    if network is not None and network[0] is False:
        held["sandbox.network"] = (False, "policy.network", network[1])
    return held


def default_config() -> EffectiveConfig:
    """The code defaults alone: no file, no environment."""
    return load_effective_config((), {}, {})


# --- TOML ---------------------------------------------------------------------------------


def toml_string(value: str) -> str:
    """A TOML basic string. A JSON string with its non-ASCII characters left as they are is one
    (`\\"`, `\\\\`, `\\n`, `\\u00XX` mean the same in both), once DEL, which TOML does not allow
    unescaped, is escaped too."""
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def toml_key(part: str) -> str:
    return part if BARE_KEY.fullmatch(part) else toml_string(part)


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, Path):
        return toml_string(str(value))
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    raise TypeError(f"no TOML for {type(value).__name__}")


def toml_text(leaves: Mapping[str, Any]) -> str:
    """Dotted keys to values as a TOML document: each table's keys, then its subtables, in the
    order the keys first name them. That is the order `tomllib` reads the document back in, so
    the text of a config loaded from this text is this text."""
    tree: dict[str, Any] = {}
    for key, value in leaves.items():
        *table, last = parts_of(key)
        node = tree
        for part in table:
            node = node.setdefault(part, {})
        node[last] = TomlLeaf(value)
    blocks: list[str] = []

    def emit(node: Mapping[str, Any], path: list[str]) -> None:
        lines = [
            f"{toml_key(k)} = {toml_value(v.value)}"
            for k, v in node.items()
            if isinstance(v, TomlLeaf)
        ]
        if lines and path:
            header = ".".join(toml_key(part) for part in path)
            blocks.append(f"[{header}]\n" + "\n".join(lines) + "\n")
        for key, child in node.items():
            if not isinstance(child, TomlLeaf):
                emit(child, [*path, key])

    emit(tree, [])
    return "\n".join(blocks)


@dataclass(frozen=True)
class TomlLeaf:
    """A leaf of `toml_text`'s tree (a value may itself be a dict-free list or scalar)."""

    value: Any
