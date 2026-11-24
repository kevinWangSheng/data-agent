"""The `--im` file and the environment a run gets (.scratch/im/spec.md "配置"; contract items 4
and 8 of ticket 130).

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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

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
    raise NotImplementedError("ticket 131")


def run_env(environ: Mapping[str, str], configs: Iterable[PlatformConfig]) -> dict[str, str]:
    """The environment a run starts with: `environ` without the variables the configs name for
    their tokens, everything else as it is. `serve_web`'s `start_run` gives it to `Popen` as
    `env=` when `--im` is given, so no run, and no event file, ever holds an IM token."""
    raise NotImplementedError("ticket 131")
