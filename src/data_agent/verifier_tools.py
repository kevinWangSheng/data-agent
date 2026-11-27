"""The verifier subagents' tools (ADR-0002): map_columns, check_model_schema, audit_model_sources,
and the verifier v2 tools model_diff, contract_check, grain_facts, compare_by_key (ADR-0009,
ticket 45; implemented in `verifier_v2/`). list_tables and query_database, the other two, are
base tools.

Ported from SignalPilot's gateway (`gateway/mcp/tools/model_map.py` and `model_verify.py`, see
NOTICE). Like upstream they report observations and leave the verdict to the verifier. Changes:

- No connection store, audit wrapper or connection_name argument: every statement goes through
  this server's read-only gateway, and tables are found in the catalog (information_schema) and
  quoted from there, so names are never pasted into SQL unchecked.
- Portable SQL instead of DuckDB-only forms: no `SHOW TABLES` or `PRAGMA table_info`, no
  `COUNT(*) FILTER`; one profiling statement per table instead of one per column.
- map_columns reads only inside the run's workspace when the server is given one. It keeps
  upstream's lineage mode and its MAPPED / UNMAPPED-INCLUDE / UNMAPPED-EXCLUDE labels (the ones
  the copied Skill and verifier prompts use). Dropped: the pre-write `upstream_tables` mode, the
  obt_/dim_ lookup and system-column rules, and the domain-prefix list (vendor names tuned to one
  benchmark's projects). Sources resolve through their `schema:`; the YAML scan follows
  indentation instead of upstream's fixed 1-4 spaces for model names.
  A model's SQL is looked up only under the project's model-paths, so an analysis or snapshot
  with the model's name is not taken for it. ref() and source() resolve to the relations dbt
  builds and reads (a model's `alias`, a source's `schema` and `identifier`; from
  target/manifest.json when dbt has compiled the project).
- check_model_schema also takes `name:data_type` pairs and compares the types by kind, DuckDB
  and Postgres aliases folded, nested types by their element and field types too.
- A table profiled over a sample says so, its NULL shares are of the sample, and a column that
  looks unique in the sample is counted over the whole table before it is called the grain; the
  grain key is looked for in every scanned column.
"""

import contextlib
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from sqlglot import exp
from sqlglot.errors import SqlglotError

from data_agent import verifier_v2 as v2
from data_agent.context_tools import RunQuery, Table, is_text, quote
from data_agent.db_facts import LoadCatalog
from data_agent.tool_annotations import READ_ONLY
from data_agent.verifier_v2.db import RunQueryable
from data_agent.verifier_v2.render import (
    render_compare,
    render_contract,
    render_grain,
    render_model_diff,
)

# Set by `data-agent run` for the MCP server: the directory `verifier_v2.take_snapshot` filled.
SNAPSHOT_ENV = "DATA_AGENT_SNAPSHOT_DIR"

SKIP_DIRS = frozenset({".claude", "dbt_packages", "target", "__pycache__", "logs"})
BINARY_TYPES = frozenset({"BLOB", "BYTEA", "BINARY", "VARBINARY", "IMAGE"})
# Types without equality on Postgres (json, xml, geometry) or whose values are opaque bytes:
# count(DISTINCT) on one fails or says nothing, so their distinct counts are skipped.
UNCOMPARABLE_TYPES = BINARY_TYPES | {
    *("JSON", "XML", "POINT", "LINE", "LSEG", "BOX", "PATH", "POLYGON", "CIRCLE"),
}
MAX_SOURCES = 10
# Upstream's profiling bounds: aggregates per statement, and cells (rows x columns) above which a
# table is profiled over a sample.
DISTINCTS_PER_STATEMENT = 40
SAMPLE_CELLS = 8_000_000
SAMPLE_ROWS = 200_000
# audit_model_sources: columns scanned, columns whose values are listed, values per column.
SCANNED_COLUMNS = 20
LOW_CARDINALITY = 15
LISTED_COLUMNS = 8

REF_RE = re.compile(r"\{\{\s*ref\(\s*['\"](\w+)['\"]\s*\)\s*\}\}")
SOURCE_RE = re.compile(r"\{\{\s*source\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*\)\s*\}\}")
CONFIG_ALIAS_RE = re.compile(
    r"\{\{\s*config\((?:[^()]|\([^()]*\))*?\balias\s*=\s*['\"](\w+)['\"]", re.DOTALL
)
NAME_LINE_RE = re.compile(r"-\s*name:\s*['\"]?([^'\"\s]+)")
# Declared types are read in the gateway's dialect; DuckDB's where a caller names none.
DIALECT = "duckdb"
SQL_KEYWORDS = frozenset(
    {"THEN", "ELSE", "END", "WHEN", "CASE", "AND", "OR", "NOT", "NULL", "TRUE", "FALSE", "AS"}
)


@dataclass(frozen=True)
class Profile:
    """Counts over `total` rows: the whole table, or its first SAMPLE_ROWS when `sampled`."""

    total: int
    nulls: int
    distinct: int | None  # None: not counted, the type cannot be compared
    sampled: bool


def base_type(data_type: str) -> str:
    return data_type.upper().split("(")[0].strip()


def find(catalog: list[Table], name: str) -> list[Table]:
    """Tables matching `schema.table` or a bare table name, ignoring case."""
    wanted = name.strip().lower()
    return [
        table
        for table in catalog
        if wanted in (table.name.lower(), f"{table.schema}.{table.name}".lower(), table.ref.lower())
    ]


def resolve(catalog: list[Table], name: str) -> Table:
    hits = find(catalog, name)
    if len(hits) == 1:
        return hits[0]
    if hits:
        options = ", ".join(table.ref for table in hits)
        raise ToolError(f"Error: {name!r} is ambiguous; qualify it with its schema: {options}")
    raise ToolError(f"Error: no table or view {name!r}; has the model been built with dbt run?")


def split_names(text: str) -> list[str]:
    return [name.strip() for name in text.split(",") if name.strip()]


def parse_task_columns(text: str) -> dict[str, list[str]]:
    """`model: col1, col2; other: col3` -> {model: [col1, col2], other: [col3]}."""
    columns: dict[str, list[str]] = {}
    for part in text.split(";"):
        if ":" not in part:
            continue
        model, names = part.split(":", 1)
        if model.strip():
            columns[model.strip()] = split_names(names)
    return columns


def row_count(run: RunQuery, table: Table) -> int:
    return int(run(f"SELECT count(*) FROM {table.sql_name}", 1).rows[0][0])


def comparable(data_type: str) -> bool:
    return base_type(data_type) not in UNCOMPARABLE_TYPES


def profile(
    run: RunQuery, table: Table, columns: Sequence[tuple[str, str]], rows: int
) -> dict[str, Profile]:
    """NULL and distinct counts per (column, type), a chunk of columns per statement; a type
    that cannot be compared gets no distinct count. A table with more than SAMPLE_CELLS cells is
    profiled over its first SAMPLE_ROWS rows."""
    result: dict[str, Profile] = {}
    for start in range(0, len(columns), DISTINCTS_PER_STATEMENT):
        chunk = columns[start : start + DISTINCTS_PER_STATEMENT]
        source = table.sql_name
        sampled = rows * len(columns) > SAMPLE_CELLS
        if sampled:
            picked = ", ".join(quote(column) for column, _ in chunk)
            source = f"(SELECT {picked} FROM {table.sql_name} LIMIT {SAMPLE_ROWS}) AS s"
        parts = ["count(*)"]
        for column, data_type in chunk:
            distinct = f"count(DISTINCT {quote(column)})" if comparable(data_type) else "NULL"
            parts += [f"count({quote(column)})", distinct]
        values = run(f"SELECT {', '.join(parts)} FROM {source}", 1).rows[0]
        total = int(values[0])
        for index, (column, _) in enumerate(chunk):
            non_null, distinct = values[1 + 2 * index], values[2 + 2 * index]
            counted = None if distinct is None else int(distinct)
            result[column] = Profile(total, total - int(non_null), counted, sampled)
    return result


# --- map_columns: parsing the dbt project (ported from model_map.py) -------------------------


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig", errors="replace")


def project_files(project: Path, *patterns: str) -> list[Path]:
    """Project files matching the patterns; a symlink that leads out of the project is skipped."""
    root = project.resolve()
    return sorted(
        path
        for pattern in patterns
        for path in project.rglob(pattern)
        if not SKIP_DIRS & set(path.relative_to(project).parts)
        and path.resolve().is_relative_to(root)
    )


def model_paths(project: Path) -> list[str]:
    """`model-paths` (or the older `source-paths`) from dbt_project.yml, as a flow list, a block
    list or a scalar; default `models`. The same rule as the dbt-workflow Skill's scan_project."""
    config = project / "dbt_project.yml"
    text = read_text(config) if config.is_file() else ""
    match = re.search(r"^model-paths:[ \t]*(.*)$", text, re.MULTILINE) or re.search(
        r"^source-paths:[ \t]*(.*)$", text, re.MULTILINE
    )
    names: list[str] = []
    if match:
        rest = text[match.end() :].splitlines()[1:]
        inline = match.group(1).split("#", 1)[0].strip()
        if inline.startswith("["):
            for line in rest:
                if "]" in inline:
                    break
                inline += line.split("#", 1)[0].strip()
            names = inline.strip("[]").split(",")
        elif inline:
            names = [inline]
        else:
            for line in rest:
                item = re.match(r"\s*-\s*(.+)", line)
                if item:
                    names.append(item.group(1).split("#", 1)[0])
                elif line.strip() and not line.lstrip().startswith("#"):
                    break
    names = [name.strip().strip("'\"") for name in names]
    return [name for name in names if name] or ["models"]


def model_sql(project: Path, model_name: str) -> Path | None:
    """The model's SQL file under the project's model-paths; an analysis, data test or snapshot
    with the same name is not the model."""
    root = project.resolve()
    files = [path for path in project_files(project, "*.sql") if path.stem == model_name]
    for name in model_paths(project):
        model_dir = project / name
        if not model_dir.is_dir() or not model_dir.resolve().is_relative_to(root):
            continue
        found = next((path for path in files if path.is_relative_to(model_dir)), None)
        if found:
            return found
    return None


def yml_columns(project: Path, model_name: str) -> list[str]:
    """Column names the project's `models:` blocks declare for the model, in order. A line scan
    (no YAML dependency) that follows indentation: models are the `- name:` entries at the first
    entry's indent, columns the `- name:` entries at the first column's indent under `columns:`,
    which YAML allows at the same indent as `columns:` itself."""
    columns: list[str] = []
    for path in project_files(project, "*.yml", "*.yaml"):
        in_models = False
        model: str | None = None
        model_indent: int | None = None
        columns_indent: int | None = None  # of the `columns:` key while inside its list
        column_indent: int | None = None
        for line in read_text(path).splitlines():
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if not stripped or stripped.startswith("#"):
                continue
            if indent == 0 and not stripped.startswith("-"):
                in_models, model, model_indent = stripped.startswith("models:"), None, None
                continue
            name = NAME_LINE_RE.match(stripped)
            if not in_models:
                continue
            if name and indent == (model_indent if model_indent is not None else indent):
                model, model_indent, columns_indent = name.group(1), indent, None
            elif model != model_name:
                continue
            elif stripped.startswith("columns:"):
                columns_indent, column_indent = indent, None
            elif columns_indent is None:
                continue
            elif indent <= columns_indent and not stripped.startswith("-"):
                columns_indent = None
            elif name and indent == (column_indent if column_indent is not None else indent):
                column_indent = indent
                columns.append(name.group(1))
    return columns


def source_relations(project: Path) -> dict[tuple[str, str], tuple[str, str]]:
    """(source, table) -> (schema, identifier) from the project's `sources:` blocks. The schema
    defaults to the source name and the identifier to the table name, as in dbt."""
    relations: dict[tuple[str, str], tuple[str, str]] = {}
    for path in project_files(project, "*.yml", "*.yaml"):
        identifiers: dict[tuple[str, str], str] = {}
        schemas: dict[str, str] = {}  # a source's `schema:`, before or after its `tables:`
        in_sources = False
        source: str | None = None
        table: str | None = None
        source_indent = table_indent = key_indent = 0
        for line in read_text(path).splitlines():
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if not stripped or stripped.startswith("#"):
                continue
            if indent == 0 and not stripped.startswith("-"):
                in_sources, source, table = stripped.startswith("sources:"), None, None
                continue
            if not in_sources:
                continue
            name = NAME_LINE_RE.match(stripped)
            value = stripped.split(":", 1)[-1].strip().strip("'\"")
            if name and (source is None or indent <= source_indent):
                source, table = name.group(1), None
                # The source's own keys line up with `name` after the `- `.
                source_indent = indent
                key_indent = indent + len(stripped) - len(stripped[1:].lstrip())
            elif source is None:
                continue
            elif stripped.startswith("schema:") and indent == key_indent:
                schemas[source] = value
            elif name and (table is None or indent == table_indent):
                # Deeper `- name:` lines under a table are its columns.
                table, table_indent = str(name.group(1)), indent
                identifiers[(source, table)] = table
            elif table and indent > table_indent and stripped.startswith("identifier:"):
                identifiers[(source, table)] = value
        for (source_name, table_name), identifier in identifiers.items():
            schema = schemas.get(source_name, source_name)
            relations[(source_name, table_name)] = (schema, identifier)
    return relations


def model_aliases(project: Path) -> dict[str, str]:
    """model -> alias from the `models:` blocks' `alias:` (under `config:` or not), then from the
    model SQL's `{{ config(alias=...) }}`, which dbt lets override the YAML."""
    aliases: dict[str, str] = {}
    for path in project_files(project, "*.yml", "*.yaml"):
        in_models = False
        model: str | None = None
        model_indent: int | None = None
        for line in read_text(path).splitlines():
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if not stripped or stripped.startswith("#"):
                continue
            if indent == 0 and not stripped.startswith("-"):
                in_models, model, model_indent = stripped.startswith("models:"), None, None
                continue
            name = NAME_LINE_RE.match(stripped)
            if not in_models:
                continue
            if name and indent == (model_indent if model_indent is not None else indent):
                model, model_indent = name.group(1), indent
            elif model and stripped.startswith("alias:"):
                aliases[model] = stripped.split(":", 1)[1].split("#", 1)[0].strip().strip("'\"")
    model_dirs = [project / name for name in model_paths(project)]
    for path in project_files(project, "*.sql"):
        if any(path.is_relative_to(model_dir) for model_dir in model_dirs):
            config = CONFIG_ALIAS_RE.search(read_text(path))
            if config:
                aliases[path.stem] = config.group(1)
    return aliases


@dataclass(frozen=True)
class Relations:
    """The relation names a dbt project builds its refs and reads its sources from."""

    models: dict[str, str]  # model -> alias, the table or view name in the database
    sources: dict[tuple[str, str], tuple[str, str]]  # (source, table) -> (schema, identifier)

    def model(self, name: str) -> str:
        return self.models.get(name, name)

    def source(self, source: str, table: str) -> tuple[str, str]:
        return self.sources.get((source, table), (source, table))


def dbt_relations(project: Path) -> Relations:
    """From `target/manifest.json` when dbt has compiled the project, as it holds what dbt
    resolved; else from the project's YAML and model SQL."""
    manifest = project / "target" / "manifest.json"
    if manifest.is_file():
        with contextlib.suppress(ValueError, AttributeError, TypeError):
            data = json.loads(read_text(manifest))
            models = {
                str(node["name"]): str(node.get("alias") or node["name"])
                for node in data.get("nodes", {}).values()
                if node.get("resource_type") == "model" and node.get("name")
            }
            sources = {
                (str(node["source_name"]), str(node["name"])): (
                    str(node.get("schema") or node["source_name"]),
                    str(node.get("identifier") or node["name"]),
                )
                for node in data.get("sources", {}).values()
                if node.get("source_name") and node.get("name")
            }
            return Relations(models, sources)
    return Relations(model_aliases(project), source_relations(project))


def select_lists(sql: str) -> list[list[str]]:
    """The items of every SELECT list in the SQL, in order, Jinja and comments removed."""
    clean = re.sub(r"\{\{.*?\}\}", "___REF___", sql)
    clean = re.sub(r"\{%.*?%\}", "", clean)
    clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.DOTALL)
    clean = re.sub(r"--.*$", "", clean, flags=re.MULTILINE)
    lists: list[list[str]] = []
    for select in re.finditer(r"SELECT\s+(.*?)\s+FROM\b", clean, re.IGNORECASE | re.DOTALL):
        parts: list[str] = []
        depth, current = 0, ""
        for char in select.group(1):
            depth += {"(": 1, ")": -1}.get(char, 0)
            if char == "," and depth == 0:
                parts.append(current.strip())
                current = ""
            else:
                current += char
        parts.append(current.strip())
        lists.append(parts)
    return lists


def item_name(item: str) -> str:
    alias = re.search(r"\bAS\s+(\w+)\s*$", item, re.IGNORECASE)
    words = item.split()
    return alias.group(1) if alias else (words[-1].split(".")[-1] if words else "")


def sql_columns(sql: str) -> list[str]:
    """Output column names of the last SELECT, for a model not materialized yet."""
    lists = select_lists(sql)
    if not lists or lists[-1] == ["*"]:
        return []
    return [
        name
        for name in map(item_name, lists[-1])
        if name and name not in ("*", "___REF___") and name.upper() not in SQL_KEYWORDS
    ]


def classify(
    column: str, data_type: str, stats: Profile | None, mapped: set[str], exclude: set[str]
) -> tuple[str, str]:
    """(status, reason) for one upstream column, as upstream's `_classify_column`."""
    low = column.lower()
    if low in mapped:
        return "MAPPED", ""
    if low in exclude:
        return "UNMAPPED-EXCLUDE", "excluded"
    binary = base_type(data_type) in BINARY_TYPES
    all_null = stats is not None and stats.total > 0 and stats.nulls == stats.total
    if binary and all_null:
        return "UNMAPPED-EXCLUDE", "all_null_binary"
    if binary:
        return "UNMAPPED-INCLUDE", "binary_type"
    if all_null:
        return "UNMAPPED-INCLUDE", "all_null"
    if stats is not None and stats.total > 0 and stats.distinct == 1 and stats.nulls == 0:
        return "UNMAPPED-INCLUDE", "constant_value"
    if is_text(data_type) and low.endswith(("_date", "_on", "_at")):
        if any(word in low for word in ("modified", "updated", "changed")):
            return "UNMAPPED-INCLUDE", "varchar_audit_timestamp"
        return "UNMAPPED-INCLUDE", "varchar_event_date"
    return "UNMAPPED-INCLUDE", ""


def upstream_columns(
    run: RunQuery, catalog: list[Table], project: Path, name: str, candidates: list[Table]
) -> tuple[str, list[tuple[str, str]], dict[str, Profile]]:
    """(note, columns with types, profiles) for one upstream: from the database when it is
    materialized, else from its SQL file."""
    if len(candidates) == 1:
        table = candidates[0]
        columns = table.columns
        stats = profile(run, table, columns, row_count(run, table))
        return table.ref, columns, stats
    if candidates:
        options = ", ".join(table.ref for table in candidates)
        return f"AMBIGUOUS ({options}); not profiled", [], {}
    path = model_sql(project, name)
    parsed = sql_columns(read_text(path)) if path else []
    note = "not materialized; columns parsed from its SQL" if parsed else "NO COLUMNS FOUND"
    return note, [(column, "?") for column in parsed], {}


def map_model(
    run: RunQuery,
    load: LoadCatalog,
    project: Path,
    model_name: str,
    sql_path: Path,
    exclude: set[str],
) -> list[str]:
    sql = read_text(sql_path)
    contract = yml_columns(project, model_name)
    mapped = {column.lower() for column in contract}
    catalog = load()
    relations = dbt_relations(project)
    upstreams: list[tuple[str, list[Table], str]] = []  # (label, catalog hits, name)
    for ref in sorted(set(REF_RE.findall(sql))):
        upstreams.append((f"ref('{ref}')", find(catalog, relations.model(ref)), ref))
    for source, table in sorted(set(SOURCE_RE.findall(sql))):
        schema, identifier = relations.source(source, table)
        hits = find(catalog, f"{schema}.{identifier}") or find(catalog, identifier)
        upstreams.append((f"source('{source}', '{table}')", hits, identifier))

    lines = [
        f"## Column Map: {model_name}",
        f"YML contract: {len(contract)} columns ({', '.join(contract)})"
        if contract
        else "YML contract: NONE (no columns defined in YML)",
        "",
    ]
    seen: dict[str, list[str]] = {}
    counts = {"MAPPED": 0, "UNMAPPED-INCLUDE": 0, "UNMAPPED-EXCLUDE": 0}
    for label, hits, name in upstreams:
        note, columns, stats = upstream_columns(run, catalog, project, name, hits)
        lines.append(f"### Upstream: {label} = {note} — {len(columns)} columns")
        for column, data_type in columns:
            status, reason = classify(column, data_type, stats.get(column), mapped, exclude)
            counts[status] += 1
            tag = f" [{reason}]" if reason else ""
            lines.append(f"  {column} ({data_type}) → {status}{tag}: {column}")
            if status != "UNMAPPED-EXCLUDE":
                seen.setdefault(column.lower(), []).append(label)
        lines.append("")
    collisions = {column: labels for column, labels in seen.items() if len(labels) > 1}
    if collisions:
        lines.append("### COLLISIONS — duplicate column names across upstreams")
        lines.extend(f"  {c} — appears in: {', '.join(labels)}" for c, labels in collisions.items())
        lines.append("")
    total = sum(counts.values())
    lines.append(
        f"### Summary: {total} upstream columns — {counts['MAPPED']} mapped, "
        f"{counts['UNMAPPED-INCLUDE']} include, {counts['UNMAPPED-EXCLUDE']} exclude"
    )
    if collisions:
        lines.append(f"### COLLISIONS: {len(collisions)} column(s) in several upstreams — alias")
    return lines


# --- audit_model_sources (ported from model_verify.py) ---------------------------------------


def ratio_note(model_rows: int, source_rows: int) -> str:
    """Thresholds from the verifier's CHECK 3: above 1.0 possible fan-out; below 0.9 fewer rows,
    which the verifier holds against the model's grain."""
    if source_rows == 0:
        return "ratio N/A      OBSERVATION: source has 0 rows"
    ratio = model_rows / source_rows
    if ratio > 1.0:
        note = "OBSERVATION: more model rows than source rows (possible fan-out)"
    elif ratio < 0.9:
        note = (
            "OBSERVATION: fewer model rows than source rows (expected for an aggregate; "
            "over-filtering only if fewer than the model's grain)"
        )
    else:
        note = "OBSERVATION"
    return f"ratio {ratio:.2f}x  {note}"


def audit(
    run: RunQuery, load: LoadCatalog, model: Table, sources: list[str], sample_nulls: bool
) -> list[str]:
    catalog = load()
    model_rows = row_count(run, model)
    lines = [f"Source audit for model '{model.ref}' ({model_rows} rows):", "", "Sources:"]
    for name in sources[:MAX_SOURCES]:
        try:
            source = resolve(catalog, name)
            source_rows = row_count(run, source)
        except ToolError as exc:
            lines.append(f"  {name}: {exc}")
            continue
        lines.append(f"  {source.ref}: {source_rows} rows → {ratio_note(model_rows, source_rows)}")
    if model_rows == 0:
        lines += ["", "Column scan: skipped (model has 0 rows)"]
        return lines

    try:
        sample = run(f"SELECT * FROM {model.sql_name} ORDER BY 1 LIMIT 5", 5)
        lines += [
            "",
            f"Sample rows (5 of {model_rows}):",
            f"  Columns: {', '.join(sample.columns)}",
        ]
        lines += [f"  {' | '.join(str(v)[:30] for v in row[:10])}" for row in sample.rows]
    except ToolError:
        lines += ["", "Sample rows: unavailable"]
    if not sample_nulls:
        return lines

    columns = model.columns
    scanned_types = columns[:SCANNED_COLUMNS]
    scanned = [column for column, _ in scanned_types]
    stats = profile(run, model, scanned_types, model_rows)
    shown = f"first {len(scanned)} of {len(columns)}" if len(columns) > len(scanned) else "all"
    sampled = any(s.sampled for s in stats.values())
    over = f", over a sample of the first {SAMPLE_ROWS} rows" if sampled else ""
    lines += ["", f"Column scan ({shown} columns{over}):"]
    for column, data_type in scanned_types:
        s = stats[column]
        share = f" ({100 * s.nulls / s.total:.1f}%)" if s.nulls else ""
        counted = (
            f"{s.distinct} distinct"
            if s.distinct is not None
            else f"distinct not counted ({base_type(data_type)})"
        )
        lines.append(f"  [OBS] {column}: {counted}, {s.nulls} nulls{share}")
    low = [
        c for c in scanned if (n := stats[c].distinct) is not None and 2 <= n <= LOW_CARDINALITY
    ][:LISTED_COLUMNS]
    if low:
        lines.append("Low-cardinality column values:")
        for column in low:
            sql = (
                f"SELECT DISTINCT {quote(column)} FROM {model.sql_name} "
                f"ORDER BY 1 LIMIT {LOW_CARDINALITY}"
            )
            values = [str(row[0]) for row in run(sql, LOW_CARDINALITY).rows]
            lines.append(f"  {column}: [{', '.join(values)}]")
    grain = next((c for c in scanned if is_unique(run, model, c, stats[c], model_rows)), None)
    lines.append(
        f"Grain key: {grain} ({model_rows} distinct = row count, unique)"
        if grain
        else "Grain key: no single column is unique (composite key or fan-out)"
    )
    return lines


def is_unique(run: RunQuery, table: Table, column: str, stats: Profile, rows: int) -> bool:
    """Whether the column has no NULLs and a distinct value per row. A sample cannot tell, so a
    sampled column that looks unique is counted over the whole table."""
    if stats.nulls or stats.distinct != stats.total:
        return False
    if not stats.sampled:
        return True
    sql = f"SELECT count(DISTINCT {quote(column)}), count({quote(column)}) FROM {table.sql_name}"
    distinct, non_null = run(sql, 1).rows[0]
    return int(distinct) == int(non_null) == rows


# --- check_model_schema ----------------------------------------------------------------------

# Kinds the gateway's database treats as one type: DuckDB's VARCHAR is also TEXT and STRING, and
# its TIMESTAMP is Postgres' timestamp without time zone, and its LIST(INTEGER) is INTEGER[].
SAME_KIND: dict[str, str] = {
    **dict.fromkeys(("TEXT", "CHAR", "NCHAR", "NVARCHAR", "BPCHAR", "NAME"), "VARCHAR"),
    **dict.fromkeys(("TIMESTAMPNTZ", "DATETIME"), "TIMESTAMP"),
    "LIST": "ARRAY",
}


def declared_columns(text: str) -> list[tuple[str, str]]:
    """(name, data type or "") per comma-separated `name[:data_type]`; a comma inside a type's
    parentheses, as in numeric(10,2), does not split."""
    items = [""]
    depth = 0
    for char in text:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "," and depth <= 0:
            items.append("")
        else:
            items[-1] += char
    pairs = [item.partition(":") for item in items]
    return [(name.strip(), data_type.strip()) for name, _, data_type in pairs if name.strip()]


def normalized_type(data_type: str, dialect: str) -> str:
    """The type's kind, aliases folded (int4, int and integer are INT), with a nested type's
    element and field types folded the same way: INTEGER[] is ARRAY(INT), VARCHAR[] is
    ARRAY(VARCHAR)."""
    try:
        return folded_type(exp.DataType.build(data_type, dialect=dialect), dialect)
    except SqlglotError:
        return base_type(data_type)


def folded_type(data_type: exp.DataType, dialect: str) -> str:
    kind = str(data_type.this.name)
    if kind == "USERDEFINED":
        return base_type(data_type.sql(dialect=dialect))
    inner: list[str] = []
    for item in data_type.expressions:
        if isinstance(item, exp.DataType):
            inner.append(folded_type(item, dialect))
        elif isinstance(item, exp.ColumnDef) and isinstance(field := item.kind, exp.DataType):
            inner.append(f"{item.name.lower()} {folded_type(field, dialect)}")
    kind = SAME_KIND.get(kind, kind)
    return f"{kind}({', '.join(inner)})" if inner else kind


def same_type(declared: str, actual: str, dialect: str = DIALECT) -> bool:
    """Whether the types are of one kind; a nested type named without its element types (as
    Postgres' information_schema names every array ARRAY) matches any of its kind."""
    expected, found = normalized_type(declared, dialect), normalized_type(actual, dialect)
    if "(" not in expected or "(" not in found:
        return expected.partition("(")[0] == found.partition("(")[0]
    return expected == found


def register_verifier_tools(
    server: MCPServer, run: RunQuery, load: LoadCatalog, workspace: Path | None, dialect: str
) -> None:
    """Add the verification tools to `server`; called only when the run config turns the verifier
    layer on. `load` is the server's cached catalog, shared with the base and context tools.
    With a `workspace`, map_columns reads only dbt projects inside it. `dialect` is the
    gateway's: model SQL and declared types are read in it."""
    root = workspace.resolve() if workspace else None

    def dbt_project(project_dir: str) -> Path:
        project = Path(project_dir).expanduser()
        if root and not project.resolve().is_relative_to(root):
            raise ToolError(f"Error: {project_dir!r} is outside the workspace {root}")
        if not (project / "dbt_project.yml").is_file():
            raise ToolError(f"Error: {project_dir!r} is not a dbt project (no dbt_project.yml)")
        return project

    @server.tool(annotations=READ_ONLY)
    def map_columns(model_name: str, project_dir: str, exclude: str = "") -> str:
        """Map a dbt model's upstream columns against its YML contract.

        Reads the model's SQL in `project_dir` for its ref() and source() upstreams, then lists
        every upstream column as MAPPED (in the YML), UNMAPPED-INCLUDE or UNMAPPED-EXCLUDE, with
        flags such as all_null or constant_value, plus columns that collide across upstreams.
        `exclude` is a comma-separated list of column names to mark UNMAPPED-EXCLUDE.
        """
        project = dbt_project(project_dir)
        sql_path = model_sql(project, model_name)
        if sql_path is None:
            raise ToolError(f"Error: no SQL file for model {model_name!r} in {project_dir!r}")
        excluded = {name.lower() for name in split_names(exclude)}
        return "\n".join(map_model(run, load, project, model_name, sql_path, excluded))

    @server.tool(annotations=READ_ONLY)
    def check_model_schema(model_name: str, yml_columns: str) -> str:
        """Compare a built model's columns with the comma-separated column list from its YML:
        matching, missing, extra and case-mismatched columns, and wrong types. Give a column as
        `name:data_type` (e.g. `amount:numeric(10,2)`) to check its type, or as `name` alone.
        Types compare by kind with DuckDB and Postgres aliases folded (int4 = INTEGER,
        text = VARCHAR); precision and length are not compared."""
        declared = declared_columns(yml_columns)
        expected = [name for name, _ in declared]
        if not expected:
            raise ToolError(
                "Error: yml_columns is empty; pass the YML column names, comma-separated"
            )
        model = resolve(load(), model_name)
        actual_types = {column.lower(): data_type for column, data_type in model.columns}
        wrong_types = [
            f"{name} (expected {data_type}, actual {actual_types[name.lower()]})"
            for name, data_type in declared
            if data_type
            and name.lower() in actual_types
            and not same_type(data_type, actual_types[name.lower()], dialect)
        ]
        actual = [column for column, _ in model.columns]
        expected_lower = {column.lower(): column for column in expected}
        actual_lower = {column.lower(): column for column in actual}
        mismatched = [
            f"{expected_lower[key]} (expected) vs {actual_lower[key]} (actual)"
            for key in expected_lower
            if key in actual_lower and expected_lower[key] != actual_lower[key]
        ]

        def listed(items: list[str]) -> str:
            return ", ".join(items) if items else "(none)"

        return "\n".join(
            [
                f"Schema check for '{model.ref}':",
                f"  Expected: {len(expected)} columns | Actual: {len(actual)} columns",
                f"  [OK] Matching: {listed([c for c in expected if c in actual])}",
                f"  [X] Missing: {listed([c for c in expected if c not in actual])}",
                f"  [X] Extra: {listed([c for c in actual if c not in expected])}",
                f"  [!] Case mismatch: {listed(mismatched)}",
                f"  [X] Type mismatch: {listed(wrong_types)}",
            ]
        )

    @server.tool(annotations=READ_ONLY)
    def audit_model_sources(model_name: str, source_tables: str, sample_nulls: bool = True) -> str:
        """Row counts of a model and its upstream tables (comma-separated `source_tables`, at
        most 10) with model/source ratios, sample rows, and per-column distinct and NULL counts,
        low-cardinality values and the grain key (`sample_nulls`)."""
        sources = split_names(source_tables)
        if not sources:
            raise ToolError("Error: source_tables is empty; name at least one upstream table")
        model = resolve(load(), model_name)
        return "\n".join(audit(run, load, model, sources, sample_nulls))

    # Verifier v2 (ticket 45): the snapshot `data-agent run` took before the agent started, named
    # by the environment because the server is started by the SDK with only its arguments. Without
    # it (the server run on its own) the v2 tools say so when called.
    snapshot = os.environ.get(SNAPSHOT_ENV)
    snapshot_dir = Path(snapshot) if snapshot else None
    v2_gateway = RunQueryable(run, dialect)

    def v2_paths() -> tuple[Path, Path]:
        if root is None or snapshot_dir is None:
            raise ToolError(
                "Error: this server has no workspace or no snapshot of it; the tool needs both "
                f"(--workspace and {SNAPSHOT_ENV}, set by `data-agent run`)"
            )
        return root, snapshot_dir

    @server.tool(annotations=READ_ONLY)
    def model_diff() -> str:
        """List every dbt model created, changed or removed since the run started, and for each
        the before/after facts that differ, by category: `source` (ref/source tables), `filter`
        (WHERE and HAVING predicates, one per line), `join`, `rows` (DISTINCT, QUALIFY, UNION vs
        UNION ALL, LIMIT, row_number() = 1), `grain` (GROUP BY), `window` (window functions) and
        `config` (materialization, where, vars in dbt_project.yml; the model's config()). Facts
        only: unchanged models and unchanged logic are not shown. A model whose SQL has Jinja
        statements or does not parse is marked 未解析 and shown as a line diff instead.
        """
        workspace, snapshot_path = v2_paths()
        return render_model_diff(v2.model_diff(workspace, snapshot_path, dialect))

    @server.tool(annotations=READ_ONLY)
    def contract_check(models: str, task_columns: str = "") -> str:
        """For each model (comma-separated) report three facts: whether it exists in the
        database; whether its columns meet the contract, which is the columns the task text
        requires (`task_columns`, as `model: col1, col2; other_model: col3`) plus the columns the
        project's YML declared before the run started (YML the agent wrote or changed is not a
        contract); and, for window functions the agent added or changed, whether their ORDER BY
        is unique within each partition (an unchanged window is N/A).
        """
        workspace, snapshot_path = v2_paths()
        names = split_names(models)
        if not names:
            raise ToolError("Error: models is empty; name the models to check")
        return render_contract(
            v2.contract_check(
                workspace,
                snapshot_path,
                v2_gateway,
                names,
                parse_task_columns(task_columns),
                catalog=load,
            )
        )

    @server.tool(annotations=READ_ONLY)
    def grain_facts(model: str, key_columns: str) -> str:
        """Count the rows of `model` whose grain key is NULL and the duplicate rows per key.
        The key is `key_columns` (comma-separated) plus any column the project's original YML
        tests both unique and not_null. Also says whether the original YML declares a
        different or finer grain (with the file), the only exemption source besides the task
        text; YML or notes the agent wrote cannot exempt. NULL or duplicates above 0 is a FAIL
        unless exempted.
        """
        workspace, snapshot_path = v2_paths()
        keys = split_names(key_columns)
        if not keys:
            raise ToolError("Error: key_columns is empty; name the grain key")
        return render_grain(
            v2.grain_facts(workspace, snapshot_path, v2_gateway, model, keys, catalog=load)
        )

    @server.tool(annotations=READ_ONLY)
    def compare_by_key(model: str, key_columns: str, reference_sql: str) -> str:
        """Compare `model` with an independent `reference_sql` over every key (FULL OUTER JOIN,
        no sampling) and report how many keys are only in the model, only in the reference, or
        differ in a value, with up to 20 examples of each. The reference must be written from
        the task text and read only tables that existed before the run: one that reads the
        model or any model created or changed in this run is refused. Values compare by the
        model column's type: DECIMAL after rounding the reference to the column's scale,
        floating point within a relative 1e-6, everything else exactly; NULL equals NULL.
        """
        workspace, snapshot_path = v2_paths()
        keys = split_names(key_columns)
        if not keys:
            raise ToolError("Error: key_columns is empty; name the grain key")
        changed = [d["model"] for d in v2.model_diff(workspace, snapshot_path, dialect)]
        return render_compare(
            v2.compare_by_key(
                v2_gateway, model, keys, reference_sql, changed, catalog=load, workspace=workspace
            )
        )
