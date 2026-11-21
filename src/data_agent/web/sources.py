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

The runs directory and a dbt project do not contain each other (`check_layout`): each dbt run
works on its own copy of the project and leaves the original alone. The DuckDB file may be inside
the project or anywhere else: a dbt run gets its own copy of it beside the workspace and a profile
of its own (ticket 99, `data_agent.web.launcher.prepare_workspace`).
"""

import json
import re
import tomllib
from collections.abc import Sequence
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
        data = tomllib.loads(path.read_text(encoding="utf-8"))
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


def dump_data_sources(sources: Sequence[DataSource]) -> str:
    """The sources as `load_data_sources` reads them, with absolute DuckDB paths: name, kind and
    the file or the variable's name, never a connection string; `dbt_project` is left out."""
    lines: list[str] = []
    for source in sources:
        lines.append(f"[sources.{toml_string(source.name)}]")
        if source.duckdb is not None:
            lines += ['kind = "duckdb"', f"path = {toml_string(str(source.duckdb.resolve()))}"]
        else:
            assert source.postgres_env is not None
            lines += ['kind = "postgres"', f"env = {toml_string(source.postgres_env)}"]
        lines.append("")
    return "\n".join(lines)


def toml_string(value: str) -> str:
    """A TOML basic string. A JSON string with its non-ASCII characters left as they are is one
    (`\\"`, `\\\\`, `\\n`, `\\u00XX` mean the same in both), once DEL, which TOML does not allow
    unescaped, is escaped too."""
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


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
