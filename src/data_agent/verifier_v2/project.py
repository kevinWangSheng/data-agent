"""Reading a dbt project's files for the v2 tools: the projects under a root, each project's
model files, the per-model configuration in `dbt_project.yml`, and what the project's YML
declares about a model (columns, tests, unique combinations).

Files are read as text; YML goes through `yaml_lite`. Nothing here touches the database.

Two things are cached for the process (ticket 52), each behind a lock because MCP runs the
synchronous tools in a thread pool: the snapshot's YML documents (the snapshot does not change
after it is taken), and each workspace project's model-name -> file map, which is walked again
when the mtime of a directory it walked, or of one on the way to a model-paths directory,
changes (adding, removing or renaming a file changes its directory's mtime), or when one of
them had changed just before the walk (`RACY_NS`). File contents in the workspace are not
cached.
"""

import os
import re
import threading
import time
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
    """dbt_project.yml as a dict; `{}` when it is missing or cannot be read (the diff then
    falls back to dbt's default paths and sees no project-level configuration)."""
    path = project / PROJECT_FILE
    if not path.is_file():
        return {}
    try:
        data = yaml_lite.load(read_text(path))
    except yaml_lite.YamlLiteError:
        return {}
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
    return _walk_files(project, directory, suffixes, None)


@dataclass(frozen=True)
class _Watch:
    """What a walk saw that a later change would alter: the mtime of each directory, taken
    before it was listed (None when missing), and for each symlinked file whether it was
    taken; deleting or moving a symlink's target changes no directory the walk listed."""

    mtimes: dict[Path, int | None]
    links: dict[Path, bool]


def _walk_files(
    project: Path,
    directory: str,
    suffixes: tuple[str, ...] | None,
    watch: _Watch | None,
) -> Iterator[Path]:
    """`files_under`, recording into `watch` (when given) every directory it lists, each
    directory from `project` down to the start, and every symlinked file it considers."""
    base = project / directory
    if watch is not None and base.is_relative_to(project):
        for step in (base, *base.parents):
            watch.mtimes[step] = _mtime(step)
            if step == project:
                break
    root = project.resolve()
    if not base.is_dir() or not base.resolve().is_relative_to(root):
        return
    # os.walk does not descend into symlinked directories, so only a symlinked file can lead
    # out; resolving every file (an lstat per path component) made a 2377-file project cost
    # seconds per walk (ticket 50).
    for current, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        if watch is not None:
            # os.walk lists a subdirectory after yielding its parent.
            for name in dirnames:
                watch.mtimes[Path(current) / name] = _mtime(Path(current) / name)
        for name in sorted(filenames):
            path = Path(current) / name
            if suffixes and path.suffix.lower() not in suffixes:
                continue
            link = path.is_symlink()
            kept = _kept(path, link, root)
            if link and watch is not None:
                watch.links[path] = kept
            if kept:
                yield path


def _kept(path: Path, link: bool, root: Path) -> bool:
    """Whether the walk yields `path`: a file, and inside the project when it is a symlink."""
    return path.is_file() and (not link or path.resolve().is_relative_to(root))


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


# File systems stamp mtimes with a coarse clock (a few ms on Linux, up to 2 s on FAT), so a file
# added within the same tick as a walk can leave its directory's mtime as the walk recorded it.
# A walk that saw a directory changed less than this before it started is not trusted, as git
# treats a "racily clean" index entry.
RACY_NS = 2 * 10**9


@dataclass(frozen=True)
class _ModelFiles:
    walked_at: int  # time.time_ns() before the walk
    root: Path  # the project, resolved
    watch: _Watch
    models: dict[str, Path]

    def still_valid(self) -> bool:
        return all(
            _mtime(path) == mtime and (mtime is None or mtime < self.walked_at - RACY_NS)
            for path, mtime in self.watch.mtimes.items()
        ) and all(
            path.is_symlink() and _kept(path, True, self.root) == kept
            for path, kept in self.watch.links.items()
        )


_model_files_lock = threading.Lock()
_model_files: dict[tuple[Path, tuple[str, ...]], _ModelFiles] = {}


def model_files(project: Path, config: dict[str, Any] | None = None) -> dict[str, Path]:
    """model name -> SQL file, for the models under the project's model-paths. Walked again
    only when a directory the last walk listed changed (see the module docstring)."""
    config = project_config(project) if config is None else config
    directories = tuple(resource_dirs(config, "model-paths", RESOURCE_PATHS["model-paths"]))
    key = (project, directories)
    with _model_files_lock:
        cached = _model_files.get(key)
        if cached is None or not cached.still_valid():
            walked_at = time.time_ns()
            watch = _Watch({}, {})
            models: dict[str, Path] = {}
            for directory in directories:
                for path in _walk_files(project, directory, (".sql",), watch):
                    models.setdefault(path.stem, path)
            cached = _ModelFiles(walked_at, project.resolve(), watch, models)
            _model_files[key] = cached
        return dict(cached.models)


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
    defaults overridden down the directory path) and the project vars the model's SQL reads."""

    values: dict[str, Any]
    vars: dict[str, Any]

    def lines(self) -> dict[str, str]:
        """key -> `key: value` text, one per set key, for the diff."""
        shown = {key: f"{key}: {value}" for key, value in self.values.items() if value is not None}
        if self.vars:
            shown["vars"] = "vars: " + ", ".join(f"{k}={v!r}" for k, v in sorted(self.vars.items()))
        return shown


def model_config(
    config: dict[str, Any], project: Path, model_path: Path, sql: str | None = None
) -> ModelConfig:
    """The configuration dbt_project.yml gives the model at `model_path`: `models:` -> project
    name -> each directory on the way -> the model's own name, with `+key` and `key` both read.
    Of the project's `vars:` only those the model's SQL (`sql`, read from `model_path` when not
    given) calls `var()` on count as the model's, so a var edit shows only on models using it."""
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
    if sql is None:
        sql = read_text(model_path) if model_path.is_file() else ""
    used = referenced_vars(sql)
    raw_vars = config.get("vars")
    project_vars: dict[str, Any] = {}
    if isinstance(raw_vars, dict) and used:
        for key, value in raw_vars.items():
            if key == name and isinstance(value, dict):
                project_vars.update({k: v for k, v in value.items() if k in used})
            elif not isinstance(value, dict) and key in used:
                project_vars[key] = value
    return ModelConfig(values, project_vars)


VAR_CALL_RE = re.compile(r"""\bvar\s*\(\s*['"]([^'"]+)['"]""")


def referenced_vars(sql: str) -> set[str]:
    """The names a model's Jinja passes to `var()`."""
    return set(VAR_CALL_RE.findall(sql))


# `ref('m')`, `ref("m")`, `ref('package', 'm')`; a version keyword (`ref('m', v=2)`) is not a
# second quoted argument.
REF_CALL_RE = re.compile(r"""\bref\s*\(\s*['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+)['"])?""")
JINJA_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)
JINJA_BLOCK_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
_STRING = r"""(?:'[^']*'|"[^"]*")"""
_ARGS = r"(?:[^()]|\([^()]*\))*"  # one level of nested parentheses
# Calls that cannot reach another model: literal ref/source/var, config, is_incremental.
LITERAL_CALL_RE = re.compile(
    rf"""\b(?:ref|source|var)\s*\(\s*{_STRING}(?:\s*,\s*{_STRING})?(?:\s*,\s*\w+\s*=\s*[^()]*?)?\s*\)"""
    rf"|\bconfig\s*\({_ARGS}\)|\bis_incremental\s*\(\s*\)"
)


def referenced_models(sql: str, pure: frozenset[str] = frozenset()) -> set[str] | None:
    """The model names a model's Jinja passes to `ref()`; None when its Jinja calls anything
    else than those and the project macros in `pure` (`ProjectMacros.pure`): another macro,
    `ref(var('x'))`, `adapter.dispatch(...)` could reach a model this cannot see."""
    sql = JINJA_COMMENT_RE.sub("", sql)
    called, all_bare = _bare_calls(sql)
    if not all_bare or not called <= pure:
        return None
    return {second or first for first, second in REF_CALL_RE.findall(sql)}


# A call by bare name; `pkg.m(` and `x.upper(` are not (the name follows a dot).
BARE_CALL_RE = re.compile(r"(?<![\w.])(\w+)\s*\(")
MACRO_RE = re.compile(
    rf"\{{%-?\s*macro\s+(\w+)\s*\(({_ARGS})\)\s*-?%\}}(.*?)\{{%-?\s*endmacro\s*-?%\}}", re.DOTALL
)
# Words in a macro body that can reach a model or the database.
REACHING_RE = re.compile(r"\b(?:ref|adapter|dispatch|run_query|graph)\b")


def _bare_calls(text: str) -> tuple[set[str], bool]:
    """The names the Jinja in `text` (comments removed) calls by bare name, besides the
    literal calls `LITERAL_CALL_RE` matches, and whether every call is one of those (not
    `pkg.m(...)`, `x[0](...)`, a parenthesis that is no call)."""
    names: set[str] = set()
    all_bare = True
    for block in JINJA_BLOCK_RE.findall(text):
        rest = LITERAL_CALL_RE.sub("", block)
        found = BARE_CALL_RE.findall(rest)
        all_bare = all_bare and rest.count("(") == len(found)
        names.update(found)
    return names, all_bare


@dataclass(frozen=True)
class ProjectMacros:
    """The macros under a project's own macro-paths (never in `dbt_packages/`), as read."""

    definitions: dict[str, list[str]]  # name -> each definition's text, in file order
    calls: dict[str, set[str]]  # name -> the names its definitions call by bare name
    opaque: frozenset[str]  # names a definition of which could reach a model by itself

    def pure(self) -> frozenset[str]:
        """The macros that cannot reach a model: no definition names `ref`, `adapter`,
        `dispatch`, `run_query` or `graph` (Jinja comments removed) in its arguments or body,
        calls anything in its argument defaults, or calls other than by bare name in its body,
        and every macro they call is one of these (the largest such set)."""
        pure = set(self.definitions) - self.opaque
        while True:
            kept = {name for name in pure if self.calls[name] <= pure}
            if kept == pure:
                return frozenset(pure)
            pure = kept

    def used_by(self, sql: str) -> list[str]:
        """The project macros the model's Jinja calls, directly or through another, sorted."""
        found: set[str] = set()
        todo = _bare_calls(JINJA_COMMENT_RE.sub("", sql))[0]
        while todo:
            name = todo.pop()
            if name in self.definitions and name not in found:
                found.add(name)
                todo |= self.calls[name]
        return sorted(found)


def project_macros(project: Path, config: dict[str, Any] | None = None) -> ProjectMacros:
    """The project's own macros, read from the files on each call. A macro-paths entry that
    resolves into `SKIP_DIRS` (`dbt_packages/...`) is not read."""
    config = project_config(project) if config is None else config
    root = project.resolve()
    definitions: dict[str, list[str]] = {}
    calls: dict[str, set[str]] = {}
    opaque: set[str] = set()
    for directory in resource_dirs(config, "macro-paths", RESOURCE_PATHS["macro-paths"]):
        base = (project / directory).resolve()
        if base.is_relative_to(root) and SKIP_DIRS & set(base.relative_to(root).parts):
            continue
        for path in files_under(project, directory, (".sql",)):
            text = JINJA_COMMENT_RE.sub("", read_text(path))
            for match in MACRO_RE.finditer(text):
                name, arguments, body = match.groups()
                names, all_bare = _bare_calls(body)
                definitions.setdefault(name, []).append(match.group(0))
                calls.setdefault(name, set()).update(names)
                # A default value is evaluated like the body; one with a call is not read.
                reaching = REACHING_RE.search(arguments + "\n" + body)
                if reaching or not all_bare or "(" in arguments:
                    opaque.add(name)
    return ProjectMacros(definitions, calls, frozenset(opaque))


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


@dataclass(frozen=True)
class YmlFile:
    """One of a project's YML files as read: its document, or why it could not be read."""

    path: Path
    data: dict[str, Any]
    error: str | None = None

    def entries(self, model: str) -> list[ModelEntry]:
        return [
            ModelEntry(self.path, entry)
            for entry in _list(self.data.get("models"))
            if isinstance(entry, dict) and str(entry.get("name")) == model
        ]


def yml_documents(project: Path, config: dict[str, Any] | None = None) -> list[YmlFile]:
    """The project's YML files read once, in file order; a file outside the YAML subset or that
    does not parse keeps its error and an empty document, so the caller can say so instead of
    treating it as declaring nothing."""
    documents: list[YmlFile] = []
    for path in yml_files(project, config):
        try:
            data = yaml_lite.load(read_text(path))
        except yaml_lite.YamlLiteError as exc:
            documents.append(YmlFile(path, {}, str(exc)))
            continue
        documents.append(YmlFile(path, data if isinstance(data, dict) else {}))
    return documents


_snapshot_lock = threading.Lock()
_snapshot_documents: dict[Path, list[YmlFile]] = {}


def snapshot_documents(snapshot: Path) -> list[YmlFile]:
    """Every project YML file under the snapshot, read once per process: the snapshot does not
    change after it is taken (spec "快照")."""
    with _snapshot_lock:
        documents = _snapshot_documents.get(snapshot)
        if documents is None:
            documents = _snapshot_documents[snapshot] = [
                document
                for project in dbt_projects(snapshot)
                for document in yml_documents(project)
            ]
        return list(documents)


def entries_in(documents: list[YmlFile], model: str) -> list[ModelEntry]:
    """The `models:` entries named `model` across the documents, in file order."""
    return [entry for document in documents for entry in document.entries(model)]


def unreadable(documents: list[YmlFile], root: Path) -> list[str]:
    """`path (reason)` for each document that could not be read, path relative to `root`."""
    return [
        f"{document.path.relative_to(root)} ({document.error})"
        for document in documents
        if document.error is not None
    ]


def model_entries(
    project: Path, model: str, config: dict[str, Any] | None = None
) -> list[ModelEntry]:
    """The `models:` entries named `model` in the project's YML files, in file order."""
    return entries_in(yml_documents(project, config), model)


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


def model_name(workspace: Path, name: str, relation: str | None = None) -> str:
    """The name the project knows a model by (its file stem) for a name the verifier gives:
    the name itself, the name with its schema qualifier dropped, or, when the model is built
    under an alias (`relation`, the relation it was found as), the model that alias belongs
    to. Falls back to the unqualified name."""
    bare = bare_name(name)
    candidates = [bare] + ([relation] if relation and relation.lower() != bare.lower() else [])
    projects = dbt_projects(workspace)
    files = [model_files(project) for project in projects]
    for candidate in candidates:
        for names in files:
            if candidate in names:
                return candidate
            match = next((n for n in names if n.lower() == candidate.lower()), None)
            if match is not None:
                return match
    for candidate in candidates:
        for project, names in zip(projects, files, strict=True):
            owner = _alias_owner(project, names, candidate)
            if owner is not None:
                return owner
    return bare


def bare_name(relation: str) -> str:
    """The relation's own name without schema or database qualifiers or quotes."""
    return relation.strip().rsplit(".", 1)[-1].strip('"')


def _alias_owner(project: Path, names: dict[str, Path], alias: str) -> str | None:
    """The model the project builds as `alias`: `alias=` in a model's `{{ config(...) }}`, else
    `alias:` in a YML entry (directly or under `config:`)."""
    wanted = alias.lower()
    for model, path in names.items():
        if (_sql_alias(path) or "").lower() == wanted:
            return model
    for document in yml_documents(project):
        for entry in _list(document.data.get("models")):
            if (
                isinstance(entry, dict)
                and entry.get("name") is not None
                and (_entry_alias(entry) or "").lower() == wanted
            ):
                return str(entry["name"])
    return None


def _sql_alias(path: Path) -> str | None:
    """`alias=` in the model file's `{{ config(...) }}`."""
    call = sql_config_call(read_text(path))
    match = ALIAS_RE.search(call) if call else None
    return match.group(1) if match else None


def _entry_alias(entry: dict[str, Any]) -> str | None:
    """`alias:` in a YML model entry, directly or under `config:`."""
    config = entry.get("config")
    alias = entry.get("alias") or (config.get("alias") if isinstance(config, dict) else None)
    return str(alias) if alias else None


def model_aliases(project: Path, models: list[str]) -> dict[str, str | None]:
    """The relation name each of `models` that has a SQL file in the project is built as, when
    the project renames it (None when it keeps its own name): `alias=` in its `{{ config(...)
    }}`, which dbt lets override the YML, else `alias:` in its YML entry (directly or under
    `config:`). The project's files and YML are read once for all of them."""
    files = model_files(project)
    aliases = {model: _sql_alias(files[model]) for model in models if model in files}
    if any(alias is None for alias in aliases.values()):
        documents = yml_documents(project)
        for model, alias in aliases.items():
            if alias is None:
                aliases[model] = next(
                    (
                        found
                        for entry in entries_in(documents, model)
                        if (found := _entry_alias(entry.data))
                    ),
                    None,
                )
    return aliases
