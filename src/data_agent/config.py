"""The layered product config (`.scratch/product-config/spec.md`, ADR-0011).

An empty shell written by ticket 141 so its acceptance tests import: `load_effective_config`
returns an empty `EffectiveConfig` and reads no file. Ticket 143 builds the loader; until then no
module imports this one. The contract the tests hold it to is in
tests/acceptance/test_config_load.py.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigFileError(ValueError):
    """A config file that cannot be loaded. `path` is the file, `key` the dotted key (or the
    `${VAR}` variable's key) at fault; the message names both and never holds a secret's value."""

    def __init__(self, message: str, path: Path | None = None, key: str | None = None) -> None:
        super().__init__(message)
        self.path = path
        self.key = key


@dataclass(frozen=True)
class EffectiveConfig:
    """The merged config of a run. Keys are dotted (`limits.max_turns.dbt`)."""

    # The files merged, in merge order.
    files: tuple[Path, ...] = ()
    # The effective config as TOML, `${VAR}` references as written, and its sha256 (hex).
    text: str = ""
    sha256: str = ""
    values: Mapping[str, Any] = field(default_factory=dict[str, Any])
    sources: Mapping[str, str] = field(default_factory=dict[str, str])

    def value(self, key: str) -> Any | None:
        """The key's effective value; None when unset."""
        return None

    def source(self, key: str) -> str | None:
        """Where the key's value came from: `default`, a file's path, `env:<VAR>`, `cli` or
        `policy`; None when unset."""
        return None


def load_effective_config(
    paths: Sequence[Path],
    environ: Mapping[str, str],
    cli_overrides: Mapping[str, Any],
    workspace: Path | None = None,
) -> EffectiveConfig:
    """The effective config: code defaults < `~/.data-agent/config.toml` (under
    `environ["HOME"]`) < `<workspace>/.data-agent/config.toml` < environment < `cli_overrides`,
    with `[policy]` over all; or, given `paths` (or `DATA_AGENT_CONFIG`), those files only. The
    shell returns an empty config."""
    return EffectiveConfig()
