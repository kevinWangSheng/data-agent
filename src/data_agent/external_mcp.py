"""External MCP servers from the user's config file (`--mcp-servers`, .scratch/mcp/spec.md,
ticket 116).

The file is a Claude Code / Cursor `mcpServers` JSON plus an `allowedTools` list per server.
`load` reads and checks it before a session starts and gives what the session needs: the SDK's
server configs (`sdk_servers`), the allow-list entries (`allowed_tools`), the variables the stdio
wrapper reads (`env`), the `version` event's record (`summary`) and the values the event log
masks (`secrets`).

`${VAR}` is expanded only in `env` and `headers` values, only from `DATA_AGENT_MCP_*` variables.
Anywhere else it is refused: the Claude Code CLI expands `${VAR}` in `--mcp-config` by itself,
from its whole environment (checked against CLI 2.1.301, ticket 116 Comments), so a reference
left in an argument would hand the server any variable the CLI has.

An stdio server is started through the wrapper (`data-agent mcp-launch`, `launch`): the CLI passes
its whole environment, model key included, to the servers it starts, so the wrapper starts from
`BASE_ENV` alone, adds the server's `env` and execs the config's command. The wrapper's arguments
name the file and the server, never a value: it reads the file again and expands the server's
`env` from the environment the CLI gives it (`env` puts the variables there).
"""

import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, NoReturn
from urllib.parse import urlsplit

from claude_agent_sdk.types import McpHttpServerConfig, McpServerConfig, McpStdioServerConfig

# Our own server's name (`agent.MCP_SERVER`; a test keeps the two equal).
RESERVED_NAME = "data"
PREFIX = "DATA_AGENT_MCP_"
NAME = re.compile(r"[A-Za-z0-9_-]+")
# The CLI splits `mcp__<server>__<tool>` on `__`: a server named `data_` or `data__x` would have
# its tools read as tools of `data` (allowed whole, `mcp__data`), `a__b` as tools of `a`.
NAME_SEPARATOR_RISK = re.compile(r"__|^_|_$")
VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
REFERENCE = re.compile(r"\$\{([^}]*)\}")
WILDCARD = "*"
# What an allowed tool's name may not hold: the SDK joins `allowed_tools` with commas, and the
# CLI reads parentheses as a rule's argument.
TOOL_NAME_FORBIDDEN = re.compile(r"[\s,()*]")
# The fields each transport takes; any other is refused (it might change how the server is
# reached or authenticated, and dropping it silently would hide that).
FIELDS: dict[str, frozenset[str]] = {
    "stdio": frozenset({"type", "command", "args", "env", "allowedTools"}),
    "http": frozenset({"type", "url", "headers", "allowedTools"}),
}
OAUTH_FIELDS = frozenset({"oauth", "auth"})
# The environment an stdio server starts from, before its config `env`: the spec's "PATH、HOME、
# LANG 等基本变量". tests/acceptance/test_mcp_launch.py's BASE_ENV is the upper bound.
BASE_ENV = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "TZ",
)
LAUNCH_COMMAND = "mcp-launch"
# A command the wrapper cannot find or start.
EXIT_NOT_FOUND = 127
EXIT_CONFIG_ERROR = 2


class McpConfigError(ValueError):
    """The config file cannot be used; the message names the server (and the variable)."""


@dataclass(frozen=True)
class ExternalServer:
    """One mounted server, its `${VAR}` references expanded."""

    name: str
    type: Literal["stdio", "http"]
    # As the file has it (`["*"]` stays `["*"]`).
    allowed_tools: tuple[str, ...]
    command: str = ""
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    url: str = ""
    headers: Mapping[str, str] = field(default_factory=dict[str, str])
    # The DATA_AGENT_MCP_* variables the server's `env` or `headers` reference, with their values.
    variables: Mapping[str, str] = field(default_factory=dict[str, str])

    def allow_entries(self) -> list[str]:
        if self.allowed_tools == (WILDCARD,):
            return [f"mcp__{self.name}__*"]
        return [f"mcp__{self.name}__{tool}" for tool in self.allowed_tools]


@dataclass(frozen=True)
class ExternalServers:
    """The servers a config file mounts, in the file's order."""

    path: Path
    servers: tuple[ExternalServer, ...]
    warnings: tuple[str, ...] = ()

    def sdk_servers(self) -> dict[str, McpServerConfig]:
        """The SDK's configs: http as written, headers expanded; stdio through the wrapper."""
        configs: dict[str, McpServerConfig] = {}
        for server in self.servers:
            if server.type == "http":
                configs[server.name] = McpHttpServerConfig(
                    type="http", url=server.url, headers=dict(server.headers)
                )
            else:
                configs[server.name] = McpStdioServerConfig(
                    type="stdio",
                    command=sys.executable,
                    args=launch_args(self.path, server.name),
                )
        return configs

    def allowed_tools(self) -> list[str]:
        return [entry for server in self.servers for entry in server.allow_entries()]

    def env(self) -> dict[str, str]:
        """The variables the stdio servers' `env` reference, for the CLI's environment: the
        wrapper reads them from there."""
        return {
            name: value
            for server in self.servers
            if server.type == "stdio"
            for name, value in server.variables.items()
        }

    def summary(self) -> list[dict[str, Any]]:
        """The `version` event's `external_mcp_servers`: names and allow lists only. Recording
        more (the type, a URL without its query) is a change here alone."""
        return [{"name": s.name, "allowed_tools": list(s.allowed_tools)} for s in self.servers]

    def secrets(self) -> list[str]:
        """What the event log masks: the referenced variables' values, the expanded strings
        that hold them, and of a value that is a URL its password and its dotted host (a
        server's error may quote them alone). Literal values are not secrets."""
        found: list[str] = []
        for server in self.servers:
            values = list(server.variables.values())
            found += values
            for value in values:
                password, host = url_parts(value)
                found += [password, host if dotted(host) else ""]
            expanded = (*server.headers.values(), *server.env.values())
            found += [v for v in expanded if any(s in v for s in values)]
        return sorted({value for value in found if value})

    def names(self) -> list[str]:
        """Single-label URL hosts (`mongo`, `db-1`), masked only as whole words
        (`events.mask`), as the Postgres URL's are."""
        found = {
            url_parts(value)[1] for server in self.servers for value in server.variables.values()
        }
        return sorted(host for host in found if host and not dotted(host))


def dotted(host: str) -> bool:
    """A domain name or address, masked anywhere; a single-label host is masked as a word."""
    return "." in host or ":" in host


def url_parts(value: str) -> tuple[str, str]:
    """The password and host of a `scheme://` URL; empty strings otherwise."""
    if "://" not in value:
        return "", ""
    try:
        parts = urlsplit(value)
        return parts.password or "", parts.hostname or ""
    except ValueError:
        return "", ""


def launch_args(path: Path, name: str) -> list[str]:
    # -P: the CLI starts servers in the agent's workspace; a module planted there must not be
    # imported by the wrapper, which still holds the CLI's whole environment.
    return ["-P", "-m", "data_agent", LAUNCH_COMMAND, "--mcp-servers", str(path), "--server", name]


def load(path: Path, env: Mapping[str, str]) -> ExternalServers:
    """Reads and checks the file, expanding `${VAR}` from `env`. McpConfigError on any error."""
    path = path.absolute()
    raw = read_servers(path)
    servers: list[ExternalServer] = []
    warnings: list[str] = []
    for name, spec in raw.items():
        server = check_server(name, spec, env)
        if server.allowed_tools:
            servers.append(server)
        else:
            warnings.append(
                f"warning: MCP server '{name}' has no allowedTools; it is not mounted and none of "
                "its tools is allowed"
            )
    return ExternalServers(path, tuple(servers), tuple(warnings))


def read_servers(path: Path) -> dict[str, Any]:
    if "${" in str(path):
        raise McpConfigError(f"{path}: the file's path may not hold ${{")
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise McpConfigError(f"MCP servers file not found: {path}") from None
    except OSError as exc:
        raise McpConfigError(f"{path}: cannot read: {exc.strerror}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpConfigError(f"{path}: not valid JSON (line {exc.lineno})") from None
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers"), dict):
        raise McpConfigError(f'{path}: expected an object with "mcpServers", an object of servers')
    servers: dict[str, Any] = data["mcpServers"]
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            raise McpConfigError(f"{path}: MCP server '{name}' is not an object")
    return servers


def check_server(name: str, spec: Mapping[str, Any], env: Mapping[str, str]) -> ExternalServer:
    where = f"MCP server '{name}'"
    if name == RESERVED_NAME:
        raise McpConfigError(f"{where}: the name '{RESERVED_NAME}' is data-agent's own server")
    if not NAME.fullmatch(name):
        raise McpConfigError(f"{where}: a name has only letters, digits, '-' and '_'")
    if NAME_SEPARATOR_RISK.search(name):
        raise McpConfigError(
            f"{where}: a name may not hold '__' or start or end with '_' (tool names are "
            "mcp__<server>__<tool>)"
        )
    if "type" not in spec and "url" in spec:
        raise McpConfigError(f'{where}: a server with a url needs "type": "http"')
    kind = spec.get("type", "stdio")
    if not isinstance(kind, str):
        raise McpConfigError(f"{where}: type must be a string (stdio or http)")
    if kind == "sse":
        raise McpConfigError(f"{where}: type 'sse' is not supported (only stdio and http)")
    if kind not in FIELDS:
        raise McpConfigError(f"{where}: unknown type {kind!r} (only stdio and http)")
    for key in spec:
        if key in OAUTH_FIELDS or "oauth" in key.lower():
            raise McpConfigError(f"{where}: field '{key}': OAuth is not supported")
        if key not in FIELDS[kind]:
            raise McpConfigError(
                f"{where}: unsupported field '{key}' for type {kind} "
                f"(supported: {', '.join(sorted(FIELDS[kind]))})"
            )
    allowed = allow_list(where, spec.get("allowedTools"))
    variables: dict[str, str] = {}
    if kind == "http":
        url = string(where, "url", spec.get("url"))
        no_reference(where, "url", url)
        if not url.startswith(("https://", "http://")):
            raise McpConfigError(f"{where}: url must start with https:// or http://")
        headers = expanded_map(where, "headers", spec.get("headers", {}), env, variables)
        return ExternalServer(name, "http", allowed, url=url, headers=headers, variables=variables)
    command = string(where, "command", spec.get("command"))
    no_reference(where, "command", command)
    args = spec.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise McpConfigError(f"{where}: args must be a list of strings")
    for arg in (command, *args):
        no_reference(where, "args", arg)
        if "\0" in arg:
            raise McpConfigError(f"{where}: command and args may not hold a NUL character")
    server_env = expanded_map(where, "env", spec.get("env", {}), env, variables)
    return ExternalServer(
        name,
        "stdio",
        allowed,
        command=command,
        args=tuple(args),
        env=server_env,
        variables=variables,
    )


def allow_list(where: str, value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise McpConfigError(f"{where}: allowedTools must be a list of tool names")
    tools: list[str] = []
    for tool in value:
        if not isinstance(tool, str) or not tool:
            raise McpConfigError(f"{where}: allowedTools holds {tool!r}, not a tool name string")
        tools.append(tool)
    if WILDCARD in tools:
        if tools != [WILDCARD]:
            raise McpConfigError(f'{where}: allowedTools ["*"] allows every tool; list no others')
        return (WILDCARD,)
    for tool in tools:
        if TOOL_NAME_FORBIDDEN.search(tool) or tool.startswith("mcp__"):
            raise McpConfigError(
                f"{where}: allowedTools entry {tool!r} is not a tool's own name "
                "(no spaces, commas, parentheses or '*'; without the mcp__<server>__ prefix)"
            )
    return tuple(tools)


def string(where: str, key: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise McpConfigError(f"{where}: {key} must be a non-empty string")
    return value


def no_reference(where: str, key: str, value: str) -> None:
    if "${" in value:
        raise McpConfigError(
            f"{where}: {key} may not hold ${{...}}; variables are expanded only in env and "
            "headers values"
        )


def expanded_map(
    where: str, key: str, value: Any, env: Mapping[str, str], variables: dict[str, str]
) -> dict[str, str]:
    if not isinstance(value, dict):
        raise McpConfigError(f"{where}: {key} must be an object of strings")
    expanded: dict[str, str] = {}
    for name, template in value.items():
        if not isinstance(template, str):
            raise McpConfigError(f"{where}: {key} '{name}' must be a string")
        no_reference(where, f"{key} name", name)
        if not name or "=" in name or "\0" in name or "\0" in template:
            raise McpConfigError(f"{where}: {key} name {name!r} is not usable (empty, '=' or NUL)")
        expanded[name] = expand(f"{where}: {key} '{name}'", template, env, variables)
    return expanded


def expand(where: str, template: str, env: Mapping[str, str], variables: dict[str, str]) -> str:
    """`template` with each `${DATA_AGENT_MCP_*}` replaced by its value; the values used are
    added to `variables`. The message of an error never holds a value."""

    def value(match: re.Match[str]) -> str:
        name = match.group(1)
        if not VARIABLE_NAME.fullmatch(name):
            raise McpConfigError(f"{where}: ${{{name}}} is not a plain ${{VARIABLE}} reference")
        if not name.startswith(PREFIX):
            raise McpConfigError(f"{where}: variable {name} does not start with {PREFIX}")
        found = env.get(name)
        if not found:
            raise McpConfigError(f"{where}: variable {name} is not set")
        if "${" in found:
            # The CLI would expand it again, from its own environment.
            raise McpConfigError(f"{where}: variable {name}'s value holds ${{")
        variables[name] = found
        return found

    result = REFERENCE.sub(value, template)
    # What is left is a `${` with no closing brace.
    if "${" in REFERENCE.sub("", template):
        raise McpConfigError(f"{where}: an unclosed ${{ reference")
    return result


# --- The wrapper ---------------------------------------------------------------------------


def launch_env(server: ExternalServer, parent: Mapping[str, str]) -> dict[str, str]:
    """An stdio server's whole environment: the basic variables the parent has, and its `env`."""
    return {name: parent[name] for name in BASE_ENV if name in parent} | dict(server.env)


def launch(path: Path, name: str, env: Mapping[str, str]) -> NoReturn:
    """Execs server `name` of the file in a clean environment. Never writes to stdout: the
    server's stdout is its JSON-RPC channel. Errors go to stderr, exit code 2 (127 for a
    command that cannot be started)."""
    try:
        raw = read_servers(path)
        if name not in raw:
            raise McpConfigError(f"{path}: no MCP server '{name}'")
        server = check_server(name, raw[name], env)
        if server.type != "stdio" or not server.allowed_tools:
            raise McpConfigError(f"MCP server '{name}' is not an stdio server the file mounts")
    except McpConfigError as exc:
        print(f"data-agent {LAUNCH_COMMAND}: error: {exc}", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)
    try:
        os.execvpe(server.command, [server.command, *server.args], launch_env(server, env))
    except OSError as exc:
        print(
            f"data-agent {LAUNCH_COMMAND}: error: MCP server '{name}': cannot start "
            f"{server.command!r}: {exc.strerror}",
            file=sys.stderr,
        )
        sys.exit(EXIT_NOT_FOUND)
