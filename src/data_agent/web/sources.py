"""The data sources the run form offers (spec "本地运行的数据源", Q38).

`data-agent web --data-sources FILE` reads them from a TOML file, and only when that option is
given: there is no default path. One table per source, keyed by its name; relative paths count from
the file's directory:

    [sources.shop]
    kind = "duckdb"
    path = "shop.duckdb"

    [sources.warehouse]
    kind = "postgres"
    env = "WAREHOUSE_URL"          # the variable holding the connection string, not the string
    dbt_project = "warehouse_dbt"  # optional; dbt runs copy it

The connection string stays in the server's environment; the file, responses, run arguments and
events carry only the variable's name.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def load_data_sources(path: Path) -> list[DataSource]:
    """The sources in the file, in file order; ValueError naming the first problem."""
    if not path.is_file():
        raise ValueError(f"data-source file not found: {path}")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path} is not valid TOML: {exc}") from exc
    other = set(data) - {"sources"}
    if other:
        raise ValueError(f"{path}: unknown tables {sorted(other)}; only [sources.<name>] is read")
    sources = data.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ValueError(f"{path} names no data source; add a [sources.<name>] table")
    # Absolute: runs start in their own working directory.
    base = path.resolve().parent
    return [parse_source(name, entry, base) for name, entry in sources.items()]


def parse_source(name: str, entry: Any, base: Path) -> DataSource:
    where = f"[sources.{name}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be a table")
    kind = entry.get("kind")
    if kind not in KEYS:
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
