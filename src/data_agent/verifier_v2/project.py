"""Reading a dbt project's files for the v2 tools: the projects under a root, each project's
model files, the per-model configuration in `dbt_project.yml`, and what the project's YML
declares about a model (columns, tests, unique combinations).

Files are read as text; YML goes through `yaml_lite`. Nothing here touches the database.
"""

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_agent.verifier_v2 import yaml_lite

# Never snapshotted or scanned: packages, build output and logs (spec "快照": 不包括).
SKIP_DIRS = frozenset({"dbt_packages", "target", "logs"})
PROJECT_FILE = "dbt_project.yml"
# dbt_project.yml keys naming resource directories, with dbt's defaults.
RESOURCE_PATHS = {
    "model-paths": ["models"],
    "macro-paths": ["macros"],
    "snapshot-paths": ["snapshots"],
    "test-paths": ["tests"],
}
SEED_PATHS_KEY, SEED_PATHS_DEFAULT = "seed-paths", ["seeds"]
LOGIC_SUFFIXES = (".sql", ".yml", ".yaml")
# Per-model configuration the diff reports (spec "工具": model_diff, config).
CONFIG_KEYS = ("materialized", "where")
UNIQUE_COMBINATION = "unique_combination_of_columns"


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig", errors="replace")


def dbt_projects(root: Path) -> list[Path]:
    """Directories under `root` holding a dbt_project.yml, skipping SKIP_DIRS and hidden
    directories; symlinked directories are not followed."""
    found: list[Path] = []
    if not root.is_dir():
        return found
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in SKIP_DIRS
            and not name.startswith(".")
            and not (Path(directory) / name).is_symlink()
        )
        if PROJECT_FILE in filenames:
            found.append(Path(directory))
    return found


def project_config(project: Path) -> dict[str, Any]:
    path = project / PROJECT_FILE
    data = yaml_lite.load(read_text(path)) if path.is_file() else {}
    return data if isinstance(data, dict) else {}


def resource_dirs(config: dict[str, Any], key: str, default: list[str]) -> list[str]:
    value = config.get(key, default)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return default
    return [str(item) for item in value if isinstance(item, str | int)] or default


def logic_dirs(config: dict[str, Any]) -> list[str]:
    """The directories whose .sql and .yml files are the project's logic."""
    seen: dict[str, None] = {}
    for key, default in RESOURCE_PATHS.items():
        for name in resource_dirs(config, key, default):
            seen[name] = None
    return list(seen)


def files_under(project: Path, directory: str, suffixes: tuple[str, ...] | None) -> Iterator[Path]:
    """Files under `project/directory` (SKIP_DIRS pruned), with one of `suffixes` or any when
    None; files a symlink would lead out of the project are skipped."""
    base = project / directory
    root = project.resolve()
    if not base.is_dir() or not base.resolve().is_relative_to(root):
        return
    for current, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(current) / name
            if suffixes and path.suffix.lower() not in suffixes:
                continue
            if path.is_file() and path.resolve().is_relative_to(root):
                yield path


def model_files(project: Path, config: dict[str, Any] | None = None) -> dict[str, Path]:
    """model name -> SQL file, for the models under the project's model-paths."""
    config = project_config(project) if config is None else config
    models: dict[str, Path] = {}
    for directory in resource_dirs(config, "model-paths", RESOURCE_PATHS["model-paths"]):
        for path in files_under(project, directory, (".sql",)):
            models.setdefault(path.stem, path)
    return models


def yml_files(project: Path, config: dict[str, Any] | None = None) -> list[Path]:
    config = project_config(project) if config is None else config
    return [
        path
        for directory in logic_dirs(config)
        for path in files_under(project, directory, (".yml", ".yaml"))
    ]


@dataclass(frozen=True)
class ModelConfig:
    """What dbt_project.yml says about one model: the resolved per-key values (project-wide
    defaults overridden down the directory path) and the project's vars."""

    values: dict[str, Any]
    vars: dict[str, Any]

    def lines(self) -> dict[str, str]:
        """key -> `key: value` text, one per set key, for the diff."""
        shown = {key: f"{key}: {value}" for key, value in self.values.items() if value is not None}
        if self.vars:
            shown["vars"] = "vars: " + ", ".join(f"{k}={v!r}" for k, v in sorted(self.vars.items()))
        return shown


def model_config(config: dict[str, Any], project: Path, model_path: Path) -> ModelConfig:
    """The configuration dbt_project.yml gives the model at `model_path`: `models:` -> project
    name -> each directory on the way -> the model's own name, with `+key` and `key` both read."""
    name = str(config.get("name", ""))
    models = config.get("models")
    values: dict[str, Any] = dict.fromkeys(CONFIG_KEYS)
    node: Any = models.get(name) if isinstance(models, dict) else None
    if node is None and isinstance(models, dict):
        node = models
    _collect(models if isinstance(models, dict) else {}, values)
    try:
        relative = model_path.relative_to(project)
    except ValueError:
        relative = Path(model_path.name)
    directories = relative.parts[1:-1]  # under the model-paths directory
    for part in (*directories, model_path.stem):
        if not isinstance(node, dict):
            break
        _collect(node, values)
        node = node.get(part)
    if isinstance(node, dict):
        _collect(node, values)
    raw_vars = config.get("vars")
    project_vars: dict[str, Any] = {}
    if isinstance(raw_vars, dict):
        for key, value in raw_vars.items():
            if key == name and isinstance(value, dict):
                project_vars.update(value)
            elif not isinstance(value, dict):
                project_vars[key] = value
    return ModelConfig(values, project_vars)


def _collect(node: dict[str, Any], values: dict[str, Any]) -> None:
    for key in CONFIG_KEYS:
        for form in (f"+{key}", key):
            if form in node and not isinstance(node[form], dict):
                values[key] = node[form]


@dataclass(frozen=True)
class ModelEntry:
    """One `models:` entry about a model in a project YML file."""

    path: Path
    data: dict[str, Any]

    def columns(self) -> list[str]:
        return [
            str(column["name"])
            for column in _list(self.data.get("columns"))
            if isinstance(column, dict) and column.get("name") is not None
        ]

    def column_tests(self) -> dict[str, list[str]]:
        """column -> names of its tests (`unique`, `not_null`, ...)."""
        tests: dict[str, list[str]] = {}
        for column in _list(self.data.get("columns")):
            if isinstance(column, dict) and column.get("name") is not None:
                tests[str(column["name"])] = [_test_name(t) for t in _tests_of(column)]
        return tests

    def unique_declarations(self) -> list[list[str]]:
        """Each set of columns the entry declares unique: a `unique` column test gives one
        column, a `unique_combination_of_columns` model test its combination."""
        declared: list[list[str]] = []
        for column, names in self.column_tests().items():
            if "unique" in names:
                declared.append([column])
        for test in _tests_of(self.data):
            if isinstance(test, dict):
                for name, args in test.items():
                    if str(name).endswith(UNIQUE_COMBINATION) and isinstance(args, dict):
                        combination = _list(args.get("combination_of_columns"))
                        if combination:
                            declared.append([str(c) for c in combination])
        return declared


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _tests_of(node: dict[str, Any]) -> list[Any]:
    return _list(node.get("data_tests")) + _list(node.get("tests"))


def _test_name(test: Any) -> str:
    if isinstance(test, dict) and test:
        return str(next(iter(test)))
    return str(test)


def model_entries(
    project: Path, model: str, config: dict[str, Any] | None = None
) -> list[ModelEntry]:
    """The `models:` entries named `model` in the project's YML files, in file order."""
    entries: list[ModelEntry] = []
    for path in yml_files(project, config):
        data = yaml_lite.load(read_text(path))
        if not isinstance(data, dict):
            continue
        for entry in _list(data.get("models")):
            if isinstance(entry, dict) and str(entry.get("name")) == model:
                entries.append(ModelEntry(path, entry))
    return entries


def declared_columns(entries: list[ModelEntry]) -> list[str]:
    seen: dict[str, None] = {}
    for entry in entries:
        for column in entry.columns():
            seen.setdefault(column, None)
    return list(seen)


def unique_not_null_columns(entries: list[ModelEntry]) -> list[str]:
    """Columns tested both `unique` and `not_null`: part of the grain key (spec: grain_facts)."""
    found: dict[str, None] = {}
    for entry in entries:
        for column, names in entry.column_tests().items():
            if "unique" in names and "not_null" in names:
                found.setdefault(column, None)
    return list(found)


CONFIG_CALL_RE = re.compile(r"\{\{-?\s*config\s*\((.*?)\)\s*-?\}\}", re.DOTALL)


def sql_config_call(sql: str) -> str | None:
    """The text inside the model's `{{ config(...) }}`, whitespace folded; None without one."""
    match = CONFIG_CALL_RE.search(sql)
    return " ".join(match.group(1).split()) if match else None


ALIAS_RE = re.compile(r"""alias\s*=\s*['"]([^'"]+)['"]""")


def model_alias(project: Path, model: str) -> str | None:
    """The relation name the model is built as, when the project renames it: `alias:` in its
    YML entry (directly or under `config:`), else `alias=` in its `{{ config(...) }}`, which dbt
    lets override the YML; None when the model keeps its own name."""
    path = model_files(project).get(model)
    call = sql_config_call(read_text(path)) if path else None
    in_sql = ALIAS_RE.search(call) if call else None
    if in_sql:
        return in_sql.group(1)
    for entry in model_entries(project, model):
        config = entry.data.get("config")
        alias = entry.data.get("alias") or (
            config.get("alias") if isinstance(config, dict) else None
        )
        if alias:
            return str(alias)
    return None
