"""The data sources the run form offers (spec "本地运行的数据源", Q38).

They are the config's `[sources.<name>]` tables (data_agent.config, ticket 143; `config_sources`).
One table per source, keyed by its name; relative paths count from the directory of the config
file that sets them:

    [sources.shop]
    kind = "duckdb"
    path = "shop.duckdb"

    [sources.warehouse]
    kind = "postgres"
    env = "WAREHOUSE_URL"          # the variable holding the connection string, not the string
    dbt_project = "warehouse_dbt"  # optional; dbt runs copy it

The connection string stays in the server's environment; the config, responses, run arguments and
events carry only the variable's name.

The runs directory and a dbt project do not contain each other (`check_layout`): each dbt run
works on its own copy of the project and leaves the original alone. The DuckDB file may be inside
the project or anywhere else: a dbt run gets its own copy of it beside the workspace and a profile
of its own (ticket 99, `data_agent.web.launcher.prepare_workspace`).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from data_agent.config import EffectiveConfig

KEYS = {"duckdb": {"kind", "path", "dbt_project"}, "postgres": {"kind", "env", "dbt_project"}}
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class DataSource:
    """A data source by name. Exactly one of `duckdb` and `postgres_env` is set; a Postgres
    connection string stays in the server's environment under that variable and never reaches a
    browser."""

    name: str
    duckdb: Path | None = None
    postgres_env: str | None = None
    # dbt runs copy this project into their own working directory (spec Q39).
    dbt_project: Path | None = None


# The `[sources.<name>]` keys a source is built from; the config's other keys of a source
# (`description`, `query_timeout_seconds`, ...) act elsewhere (tickets 144, 148).
SOURCE_KEYS = {"kind", "path", "env", "dbt_project"}


def config_sources(config: "EffectiveConfig") -> list[DataSource]:
    """The config's `[sources.<name>]` tables, in order (paths already absolute); ValueError naming
    the first problem."""
    from data_agent.config import Key

    sources: list[DataSource] = []
    for name in config.names("sources"):
        entry = config.value(Key(("sources", name))) or {}
        known = {key: str(value) for key, value in entry.items() if key in SOURCE_KEYS}
        sources.append(parse_source(name, known, Path("/")))
    return sources


def source_leaves(sources: Sequence[DataSource]) -> dict[str, str]:
    """The sources as the config's dotted keys (`sources.shop.kind`, ...), DuckDB paths absolute:
    the file or the variable's name, never a connection string; `dbt_project` too when set."""
    from data_agent.config import Key

    leaves: dict[str, str] = {}
    for source in sources:

        def key(name: str, source: DataSource = source) -> Key:
            return Key(("sources", source.name, name))

        if source.duckdb is not None:
            leaves[key("kind")] = "duckdb"
            leaves[key("path")] = str(source.duckdb.resolve())
        else:
            assert source.postgres_env is not None
            leaves[key("kind")] = "postgres"
            leaves[key("env")] = source.postgres_env
        if source.dbt_project is not None:
            leaves[key("dbt_project")] = str(source.dbt_project.resolve())
    return leaves


def parse_source(name: str, entry: Any, base: Path) -> DataSource:
    where = f"[sources.{name}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be a table")
    kind = entry.get("kind")
    if not isinstance(kind, str) or kind not in KEYS:
        raise ValueError(f"{where}: kind must be one of {sorted(KEYS)}, got {kind!r}")
    unknown = set(entry) - KEYS[kind]
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)} for kind {kind!r}")
    dbt_project = None
    if "dbt_project" in entry:
        dbt_project = resolve(base, entry["dbt_project"], f"{where} dbt_project")
        if not dbt_project.is_dir():
            raise ValueError(f"{where}: dbt_project directory not found: {dbt_project}")
    if kind == "duckdb":
        if "path" not in entry:
            raise ValueError(f"{where}: a duckdb source needs path, the DuckDB file")
        duckdb = resolve(base, entry["path"], f"{where} path")
        return DataSource(name=name, duckdb=duckdb, dbt_project=dbt_project)
    env = entry.get("env")
    if not isinstance(env, str) or not env:
        raise ValueError(f"{where}: a postgres source needs env, the environment variable's name")
    if not ENV_NAME.fullmatch(env):
        # Most likely the connection string itself; say so without repeating it.
        raise ValueError(
            f"{where}: env must be an environment variable name, not a connection string"
        )
    return DataSource(name=name, postgres_env=env, dbt_project=dbt_project)


def resolve(base: Path, value: Any, what: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{what} must be a non-empty path")
    return base / Path(value).expanduser()


def check_layout(runs_dir: Path, sources: Sequence[DataSource]) -> None:
    """ValueError naming the first source a dbt run could not copy into its own working directory:
    a runs directory and a dbt project inside one another would copy the workspace into itself."""
    runs = runs_dir.resolve()
    for source in sources:
        if source.dbt_project is None:
            continue
        project = source.dbt_project.resolve()
        where = f"[sources.{source.name}]"
        if overlap(runs, project):
            raise ValueError(
                f"{where}: dbt_project {project} and the runs directory {runs} contain one "
                "another; dbt runs copy the project into the runs directory, so keep them apart"
            )


def overlap(a: Path, b: Path) -> bool:
    """Whether one of two resolved directories is, or is inside, the other."""
    return a.is_relative_to(b) or b.is_relative_to(a)
