"""The IM platforms and the environment a run gets (.scratch/im/spec.md "配置"; contract items 4
and 8 of ticket 130). The platforms are the config's `[im.<platform>]` tables (`load_section`,
ticket 143); `load_config` still reads the old `--im` file's format.

The file is TOML, outside the repository, given by `data-agent web --im FILE` (no default path).
One table per platform; the bot starts on each platform the file has a table for:

    [slack]
    bot_token_env = "DATA_AGENT_SLACK_BOT_TOKEN"
    app_token_env = "DATA_AGENT_SLACK_APP_TOKEN"
    allowed_users = ["U012ABCDEF"]

    [telegram]
    bot_token_env = "DATA_AGENT_TELEGRAM_BOT_TOKEN"
    allowed_users = [123456789]

    [discord]
    bot_token_env = "DATA_AGENT_DISCORD_BOT_TOKEN"
    allowed_users = ["123456789012345678"]

`load_config` raises `ImConfigError`, its text naming the platform (the table), when:

- a table is not one of `PLATFORMS`, or the file has no table at all;
- a key of `TOKEN_KEYS[platform]` is missing, or names a variable that is not set (or is blank);
- a variable's name does not start with the platform's own prefix, `DATA_AGENT_<PLATFORM>_`
  (`PREFIXES`): neither another secret (`DATA_AGENT_API_KEY`) nor another platform's token;
- `allowed_users` is missing or empty;
- any other key is in the table.

No error text, log line or `repr` carries a token's value. Without `--data-sources` the file is
still accepted; each turn is then refused (`Refused(400)`) and the bot says so in the thread.
"""

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLATFORMS = ("slack", "telegram", "discord")
# The keys naming a token's variable, per platform.
TOKEN_KEYS = {
    "slack": ("bot_token_env", "app_token_env"),
    "telegram": ("bot_token_env",),
    "discord": ("bot_token_env",),
}
PREFIXES = {platform: f"DATA_AGENT_{platform.upper()}_" for platform in PLATFORMS}


class ImConfigError(ValueError):
    """The `--im` file is wrong; the text names the platform."""


@dataclass(frozen=True)
class PlatformConfig:
    platform: str
    # User ids as strings (Telegram's numbers too), as `Incoming.user_id` has them.
    allowed_users: frozenset[str]
    # The token keys of the file (`bot_token_env`, ...) and the variables they name.
    token_envs: Mapping[str, str]
    # The same keys and the variables' values, for the platform implementation only.
    tokens: Mapping[str, str] = field(repr=False)


def load_config(path: Path, environ: Mapping[str, str]) -> dict[str, PlatformConfig]:
    """Each platform the file has a table for, by name; the tokens read from `environ`."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ImConfigError(f"--im file {path}: cannot read it: {exc.strerror}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ImConfigError(f"--im file {path}: not valid TOML: {exc}") from None
    return load_tables(data, environ, f"--im file {path}")


def load_section(
    section: Mapping[str, Any], environ: Mapping[str, str]
) -> dict[str, PlatformConfig]:
    """As `load_config`, for the config's `[im]` section (data_agent.config, ticket 143): its
    `[im.<platform>]` tables, keys and checks as the file's."""
    return load_tables(section, environ, "config", "im.")


def load_tables(
    data: Mapping[str, Any], environ: Mapping[str, str], where: str, prefix: str = ""
) -> dict[str, PlatformConfig]:
    configs: dict[str, PlatformConfig] = {}
    for name, table in data.items():
        if name not in PLATFORMS:
            raise ImConfigError(
                f"{where}: [{prefix}{name}] is no platform; the platforms are "
                + ", ".join(PLATFORMS)
            )
        if not isinstance(table, dict):
            raise ImConfigError(f"{where}: {name} must be a table ([{prefix}{name}])")
        configs[name] = read_table(f"{where}, [{prefix}{name}]", name, table, environ)
    if not configs:
        raise ImConfigError(f"{where}: no platform table; give one of " + ", ".join(PLATFORMS))
    return configs


def read_table(
    where: str, platform: str, table: dict[str, Any], environ: Mapping[str, str]
) -> PlatformConfig:
    keys = TOKEN_KEYS[platform]
    unknown = sorted(set(table) - {*keys, "allowed_users"})
    if unknown:
        raise ImConfigError(f"{where}: unknown keys {', '.join(unknown)}")
    prefix = PREFIXES[platform]
    token_envs: dict[str, str] = {}
    tokens: dict[str, str] = {}
    for key in keys:
        variable = table.get(key)
        if not isinstance(variable, str) or not variable:
            raise ImConfigError(f"{where}: {key} must name the variable holding the token")
        if not variable.startswith(prefix) or variable == prefix:
            # Not echoed: it may be a token written where its variable's name goes.
            raise ImConfigError(
                f"{where}: {key} must name a variable starting with {prefix} (the name, "
                "never the token)"
            )
        value = environ.get(variable, "")
        if not value.strip():
            raise ImConfigError(f"{where}: the variable {variable} ({key}) is not set")
        token_envs[key] = variable
        tokens[key] = value
    users = table.get("allowed_users")
    if not isinstance(users, list) or not users:
        raise ImConfigError(f"{where}: allowed_users must list at least one user id")
    allowed: set[str] = set()
    for user in users:
        # A bool is an int to Python, never a user id.
        if isinstance(user, bool) or not isinstance(user, str | int) or not str(user).strip():
            raise ImConfigError(f"{where}: allowed_users has {user!r}, not a user id")
        allowed.add(str(user).strip())
    return PlatformConfig(platform, frozenset(allowed), token_envs, tokens)


def run_env(environ: Mapping[str, str], configs: Iterable[PlatformConfig]) -> dict[str, str]:
    """The environment a run starts with: `environ` without the variables the configs name for
    their tokens, everything else as it is. `serve_web`'s `start_run` gives it to `Popen` as
    `env=` when `--im` is given, so no run, and no event file, ever holds an IM token."""
    names = {variable for config in configs for variable in config.token_envs.values()}
    return {name: value for name, value in environ.items() if name not in names}
