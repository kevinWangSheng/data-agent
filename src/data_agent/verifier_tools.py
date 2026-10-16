"""The verifier subagents' tools (ADR-0002): map_columns, check_model_schema, audit_model_sources,
verify_model_values. list_tables and query_database, the other two, are base tools.

Ported from SignalPilot's gateway (`gateway/mcp/tools/model_map.py` and `model_verify.py`, see
NOTICE). Like upstream they report observations and leave the verdict to the verifier. Changes:

- No connection store, audit wrapper or connection_name argument: every statement goes through
  this server's read-only gateway, and tables are found in the catalog (information_schema) and
  quoted from there, so names are never pasted into SQL unchecked.
- Portable SQL instead of DuckDB-only forms: no `SHOW TABLES` or `PRAGMA table_info`, no
  `COUNT(*) FILTER` (except in a model's own count, rewritten in the gateway's dialect); one
  profiling statement per table instead of one per column.
- map_columns reads only inside the run's workspace when the server is given one. It keeps
  upstream's lineage mode and its MAPPED / UNMAPPED-INCLUDE / UNMAPPED-EXCLUDE labels (the ones
  the copied Skill and verifier prompts use). Dropped: the pre-write `upstream_tables` mode, the
  obt_/dim_ lookup and system-column rules, and the domain-prefix list (vendor names tuned to one
  benchmark's projects). Sources resolve through their `schema:`; the YAML scan follows
  indentation instead of upstream's fixed 1-4 spaces for model names.
  A model's SQL is looked up only under the project's model-paths, so an analysis or snapshot
  with the model's name is not taken for it.
- verify_model_values takes the model's upstreams as `source_tables`, read by the verifier from
  the model's SQL. Without them it compares against the nearest finer-grained table that has the
  slice column, instead of upstream's three largest tables, which on a project with a fanned-out
  sibling model put that sibling's doubled counts next to a correct model. The slice is every
  text, date, timestamp or `id` / `*_id` column of the top row, NULLs matched with IS NULL
  (upstream: the first), and a join to reach it goes only through a named upstream on a shared
  `*_id` column unique there (upstream: any table sharing a column); a candidate it cannot
  slice on every grouping column is marked inconclusive, a one-row model without dimensions is
  compared with whole upstreams, and the output says the baselines do not apply the model's
  own filters.
  Given `project_dir` it reads the model's SQL with sqlglot (outermost SELECT, through CTEs and
  subqueries): the slice is the GROUP BY outputs, numeric ones too, and never an aggregate such
  as MAX(ordered_at); a metric is a count only when its outermost aggregate is COUNT under
  CAST / COALESCE, and is held exactly against the same COUNT rewritten over each candidate, so
  COUNT(DISTINCT x) meets COUNT(DISTINCT x) and a conditional count keeps its predicate. The
  count's columns are traced to the upstream columns they read (sqlglot qualify against the
  catalog); one the SQL computes, renames out of reach or takes from another table, or rows of
  an aggregated CTE, make it inconclusive, as does a computed GROUP BY expression for the slice.
  ref() and source() resolve to the relations dbt builds and reads (a model's `alias`, a
  source's `schema` and `identifier`; from target/manifest.json when dbt has compiled the
  project), and an aliased model's SQL is found under its model name.
  Without the SQL, or when it does not
  parse (the output says so), dimensions come from column types and an integer named as a
  count (n_*, num_*, count_*, *_count) is held exactly against COUNT(*). Upstream also held
  sums up against row counts.
- check_model_schema also takes `name:data_type` pairs and compares the types by kind, DuckDB
  and Postgres aliases folded, nested types by their element and field types too.
- A table profiled over a sample says so, its NULL shares are of the sample, and a column that
  looks unique in the sample is counted over the whole table before it is called the grain; the
  grain key is looked for in every scanned column.
"""

import contextlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlglot
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, build_scope

from data_agent.context_tools import RunQuery, Table, is_text, literal, load_catalog, quote

SKIP_DIRS = frozenset({".claude", "dbt_packages", "target", "__pycache__", "logs"})
BINARY_TYPES = frozenset({"BLOB", "BYTEA", "BINARY", "VARBINARY", "IMAGE"})
INTEGER_TYPES = frozenset(
    {
        *("TINYINT", "SMALLINT", "INTEGER", "INT", "BIGINT", "HUGEINT"),
        *("UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT", "INT2", "INT4", "INT8"),
    }
)
NUMERIC_TYPES = INTEGER_TYPES | {
    *("DECIMAL", "NUMERIC", "DOUBLE", "DOUBLE PRECISION", "FLOAT", "REAL", "FLOAT4", "FLOAT8"),
}
SLICE_TYPES = frozenset({"DATE", "TIME", "TIMETZ", "DATETIME", "BOOLEAN", "BOOL", "UUID"})
# Types without equality on Postgres (json, xml, geometry) or whose values are opaque bytes:
# count(DISTINCT) on one fails or says nothing, so their distinct counts are skipped.
UNCOMPARABLE_TYPES = BINARY_TYPES | {
    *("JSON", "XML", "POINT", "LINE", "LSEG", "BOX", "PATH", "POLYGON", "CIRCLE"),
}
# Names that say a metric counts rows: n_orders, order_count, num_orders, count_orders.
COUNT_NAME_RE = re.compile(r"^(n|num|count)_\w+$|^\w+_count$", re.IGNORECASE)
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
# verify_model_values: `_id` columns counted per candidate; tables considered without
# source_tables.
ID_COLUMNS = 3
SLICE_TABLES = 50

REF_RE = re.compile(r"\{\{\s*ref\(\s*['\"](\w+)['\"]\s*\)\s*\}\}")
SOURCE_RE = re.compile(r"\{\{\s*source\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*\)\s*\}\}")
CONFIG_ALIAS_RE = re.compile(
    r"\{\{\s*config\((?:[^()]|\([^()]*\))*?\balias\s*=\s*['\"](\w+)['\"]", re.DOTALL
)
NAME_LINE_RE = re.compile(r"-\s*name:\s*['\"]?([^'\"\s]+)")
# The gateway's dialect: model SQL is parsed in it and rewritten counts are run in it.
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


def is_numeric(data_type: str) -> bool:
    return base_type(data_type) in NUMERIC_TYPES


def is_identifier(column: str) -> bool:
    low = column.lower()
    return low == "id" or low.endswith("_id")


def is_dimension(column: str, data_type: str) -> bool:
    """A grouping column the slice can match: text, a date or time, a boolean, a UUID, or an
    identifier such as an integer customer_id."""
    kind = base_type(data_type)
    return (
        is_text(data_type)
        or kind in SLICE_TYPES
        or kind.startswith(("TIMESTAMP", "TIME "))
        or is_identifier(column)
    )


def equals(column_sql: str, value: Any) -> str:
    """A predicate matching `value`; NULL matches through IS NULL, numbers stay unquoted."""
    if value is None:
        return f"{column_sql} IS NULL"
    if isinstance(value, int | float | Decimal) and not isinstance(value, bool):
        return f"{column_sql} = {value}"
    return f"{column_sql} = {literal(str(value))}"


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

    def models_named(self, relation: str) -> list[str]:
        """The models built as `relation`, the model of that name first."""
        aliased = [m for m, alias in self.models.items() if alias.lower() == relation.lower()]
        return [relation, *(m for m in aliased if m != relation)]


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
    run: RunQuery, project: Path, model_name: str, sql_path: Path, exclude: set[str]
) -> list[str]:
    sql = read_text(sql_path)
    contract = yml_columns(project, model_name)
    mapped = {column.lower() for column in contract}
    catalog = load_catalog(run)
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


def audit(run: RunQuery, model: Table, sources: list[str], sample_nulls: bool) -> list[str]:
    catalog = load_catalog(run)
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


# --- verify_model_values (ported from model_verify.py) ---------------------------------------


def column_map(table: Table) -> dict[str, str]:
    return {name.lower(): name for name, _ in table.columns}


def unique_key(run: RunQuery, candidate: Table, bridge: Table) -> str | None:
    """A `*_id` column the candidate shares with `bridge` that is unique in `bridge`, so joining
    on it cannot multiply the candidate's rows. A shared bare `id` is each table's own key, not a
    link between them, and other shared columns need not be keys at all."""
    bridge_keys = column_map(bridge)
    for key in column_map(candidate):
        if key in bridge_keys and key.endswith("_id"):
            column = quote(bridge_keys[key])
            sql = f"SELECT count(*), count(DISTINCT {column}) FROM {bridge.sql_name}"
            rows, distinct = run(sql, 1).rows[0]
            if rows == distinct:
                return key
    return None


@dataclass(frozen=True)
class Slice:
    tail: str  # FROM-clause tail limiting the candidate, aliased c, to the slice
    sliced_on: str
    missing: list[str]  # slice columns it could not apply
    bridge: Table | None  # the upstream joined as d to reach the other slice columns


def slice_filter(
    run: RunQuery, candidate: Table, slice_values: dict[str, Any], bridges: list[Table]
) -> Slice | None:
    """Slice columns the candidate has apply directly; the rest through one named upstream that
    has them and a unique join column. None when no slice column applies at all."""
    keys = column_map(candidate)
    applied = [column for column in slice_values if column.lower() in keys]
    tests = [equals(f"c.{quote(keys[c.lower()])}", slice_values[c]) for c in applied]
    described = [", ".join(applied)] if applied else []
    missing = [column for column in slice_values if column not in applied]
    join, joined = "", None
    for bridge in bridges if missing else []:
        bridge_keys = column_map(bridge)
        covered = [column for column in missing if column.lower() in bridge_keys]
        if bridge == candidate or not covered:
            continue
        key = unique_key(run, candidate, bridge)
        if key is None:
            continue
        join = f"JOIN {bridge.sql_name} AS d ON c.{quote(keys[key])} = d.{quote(bridge_keys[key])} "
        tests += [equals(f"d.{quote(bridge_keys[c.lower()])}", slice_values[c]) for c in covered]
        described.append(f"{', '.join(covered)} via {bridge.ref}.{bridge_keys[key]}")
        missing = [column for column in missing if column not in covered]
        joined = bridge
        break
    if not tests:
        return None
    return Slice(f"{join}WHERE {' AND '.join(tests)}", "; ".join(described), missing, joined)


def nearest_finer_table(
    run: RunQuery, catalog: list[Table], model: Table, column: str
) -> list[Table]:
    """The table with the slice column whose row count is the smallest above the model's."""
    model_rows = row_count(run, model)
    having = [table for table in catalog if table != model and column.lower() in column_map(table)][
        :SLICE_TABLES
    ]
    finer = [(rows, table) for table in having if (rows := row_count(run, table)) > model_rows]
    return [min(finer, key=lambda item: item[0])[1]] if finer else []


def candidate_counts(
    run: RunQuery, candidate: Table, tail: str, baselines: list[str]
) -> tuple[int, list[tuple[str, int]], list[int]]:
    """COUNT(*), COUNT(DISTINCT) of the first `_id` columns and the `baselines` over the slice."""
    ids = [name for name, _ in candidate.columns if is_identifier(name)][:ID_COLUMNS]
    parts = ["count(*)", *(f"count(DISTINCT c.{quote(name)})" for name in ids), *baselines]
    values = run(f"SELECT {', '.join(parts)} FROM {candidate.sql_name} AS c {tail}", 1).rows[0]
    distinct = [(name, int(v)) for name, v in zip(ids, values[1 : 1 + len(ids)], strict=True)]
    return int(values[0]), distinct, [int(v or 0) for v in values[1 + len(ids) :]]


# --- reading a model's SQL for verify_model_values (sqlglot) ---------------------------------


@dataclass(frozen=True)
class Output:
    """How the model's SQL computes one output column, traced through CTEs and subqueries to the
    SELECT that computes it."""

    expression: exp.Expr
    grouped: bool  # a GROUP BY expression of that SELECT: a dimension, whatever its type
    aggregate: bool  # an aggregate outside a window: a metric, never a dimension
    select: exp.Select


@dataclass(frozen=True)
class Parsed:
    select: exp.Select  # the outermost SELECT
    ctes: dict[str, exp.Expr]  # by lowercase name
    root: Scope

    def scope(self, query: exp.Expr) -> Scope | None:
        return next((s for s in self.root.traverse() if s.expression is query), None)


def parse_model(
    sql: str, catalog: list[Table], relations: Relations | None = None
) -> Parsed | None:
    """The model's SQL with Jinja rendered away (ref and source become the relations dbt builds
    and reads, by `relations`), its columns qualified with their tables from the catalog where
    it can tell; None when it does not parse to one SELECT."""
    relations = relations or Relations({}, {})
    plain = re.sub(r"\{#.*?#\}", "", sql, flags=re.DOTALL)
    plain = REF_RE.sub(lambda match: relations.model(match.group(1)), plain)
    plain = SOURCE_RE.sub(lambda match: ".".join(relations.source(*match.groups())), plain)
    plain = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", "", plain, flags=re.DOTALL)
    try:
        tree = sqlglot.parse_one(plain, dialect=DIALECT)
    except SqlglotError:
        return None
    tables: dict[str, dict[str, dict[str, str]]] = {}
    for table in catalog:
        # Only the names matter for qualifying; the types are not read.
        columns = {column: "TEXT" for column, _ in table.columns}
        tables.setdefault(table.schema, {})[table.name] = columns
    schema: dict[str, object] = {**tables}
    # Left unqualified on failure: then only a single-table SELECT's columns have a known table.
    with contextlib.suppress(SqlglotError):
        tree = qualify(
            tree.copy(),
            schema=schema,
            dialect=DIALECT,
            validate_qualify_columns=False,
            identify=False,
        )
    root = build_scope(tree)
    if not isinstance(tree, exp.Select) or root is None:
        return None
    return Parsed(tree, {cte.alias.lower(): cte.this for cte in tree.find_all(exp.CTE)}, root)


def has_aggregate(expression: exp.Expr) -> bool:
    """An aggregate outside windows and subqueries."""
    nodes = expression.walk(prune=lambda node: isinstance(node, exp.Window | exp.Subquery))
    return any(isinstance(node, exp.AggFunc) for node in nodes)


def unqualified(expression: exp.Expr) -> exp.Expr:
    return expression.transform(
        lambda node: exp.column(node.this.copy()) if isinstance(node, exp.Column) else node
    )


def in_group_by(select: exp.Select, projection: exp.Expr) -> bool:
    """Whether the projection is grouped on: GROUP BY ALL, its ordinal, its alias, or the same
    expression with table qualifiers ignored."""
    group = select.args.get("group")
    if not group:
        return False
    expression = projection.unalias()
    if group.args.get("all"):
        return not has_aggregate(expression)
    position = select.expressions.index(projection) + 1
    target = unqualified(expression)
    for item in group.expressions:
        if isinstance(item, exp.Literal) and item.is_int and int(item.name) == position:
            return True
        alias = projection.alias_or_name.lower()
        if isinstance(item, exp.Column) and not item.table and item.name.lower() == alias:
            return True
        if unqualified(item) == target:
            return True
    return False


def derived(query: exp.Expr, parsed: Parsed) -> dict[str, exp.Expr | None]:
    """The query's FROM and JOIN sources by lowercase alias, as sqlglot scopes them (so a CTE
    named after the table it reads is not taken for itself): a CTE's or subquery's query (a
    SELECT, or a set operation), or None for a table."""
    scope = parsed.scope(query)
    if scope is None:
        return {}
    return {
        alias.lower(): source.expression if isinstance(source, Scope) else None
        for alias, (_, source) in scope.selected_sources.items()
    }


def trace(parsed: Parsed, select: exp.Select, name: str, depth: int = 0) -> Output | None:
    """How `select` computes output column `name`: a column taken from a CTE or subquery is
    followed into it (under CAST, COALESCE or parentheses too, when it is grouped or aggregated
    there), a star expanded through them. None when the column comes from a table's star or the
    SQL nests deeper than the CTEs plausibly do."""
    if depth > len(parsed.ctes) + 10:
        return None
    sources = derived(select, parsed)
    for projection in select.expressions:
        star = projection if isinstance(projection, exp.Star) else None
        if isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
            star = projection
        if star is not None:
            table = star.table.lower() if isinstance(star, exp.Column) else ""
            for alias, body in sources.items():
                if isinstance(body, exp.Select) and table in ("", alias):
                    found = trace(parsed, body, name, depth + 1)
                    if found:
                        return found
            continue
        if projection.alias_or_name.lower() != name.lower():
            continue
        expression = projection.unalias()
        grouped = in_group_by(select, projection)
        aggregate = not grouped and has_aggregate(expression)
        inner = expression
        while isinstance(inner, exp.Paren | exp.Cast | exp.Coalesce):
            inner = inner.this
        if not grouped and not aggregate and isinstance(inner, exp.Column):
            table = inner.table.lower()
            for alias, body in sources.items():
                if isinstance(body, exp.Select) and table in ("", alias):
                    found = trace(parsed, body, inner.name, depth + 1)
                    # A wrapped pass-through column is computed here, not taken from there.
                    if found and (inner is expression or found.grouped or found.aggregate):
                        return found
        return Output(expression, grouped, aggregate, select)
    return None


def count_of(output: Output) -> exp.Expr | None:
    """The COUNT(...) (with its FILTER) when it is the outermost aggregate, under CAST, COALESCE
    or parentheses only; None for any other metric, such as CAST(SUM(a) / COUNT(*) AS BIGINT)."""
    node = output.expression if output.aggregate else None
    while isinstance(node, exp.Paren | exp.Cast | exp.Coalesce):
        node = node.this
    if isinstance(node, exp.Filter) and isinstance(node.this, exp.Count):
        return node
    return node if isinstance(node, exp.Count) else None


def origin(scope: Scope, column: exp.Column, depth: int = 0) -> tuple[str, str] | str:
    """(table, column), lowercase, of the upstream column that `column` reads unchanged through
    CTEs and subqueries, or why there is none."""
    sources = {alias: source for alias, (_, source) in scope.selected_sources.items()}
    source = sources.get(column.table) if column.table else None
    if not column.table and len(sources) == 1:
        source = next(iter(sources.values()))
    if isinstance(source, exp.Table):
        return source.name.lower(), column.name.lower()
    if isinstance(source, Scope) and isinstance(source.expression, exp.Select) and depth < 50:
        for projection in source.expression.expressions:
            if projection.alias_or_name.lower() == column.name.lower():
                inner = projection.unalias()
                if isinstance(inner, exp.Column) and not isinstance(inner.this, exp.Star):
                    return origin(source, inner, depth + 1)
                computed = inner.sql(dialect=DIALECT)
                return f"{column.name} is computed in the model's SQL ({computed})"
    return f"cannot tell which upstream table {column.name} comes from"


def portable(count: exp.Expr, scope: Scope) -> exp.Expr | str:
    """The count with each column qualified by the upstream table it reads, or why it cannot be
    counted over an upstream."""
    problems: list[str] = []

    def rewrite(node: exp.Expr) -> exp.Expr:
        if not isinstance(node, exp.Column) or isinstance(node.this, exp.Star):
            return node
        found = origin(scope, node)
        if isinstance(found, str):
            problems.append(found)
            return node
        # Unquoted, for the label; baseline_sql quotes the catalog's names.
        return exp.column(found[1], table=found[0])

    rewritten = count.transform(rewrite)
    return problems[0] if problems else rewritten


def keeps_rows(parsed: Parsed, query: exp.Expr, depth: int = 0) -> bool:
    """Whether a CTE's or subquery's rows are its tables' rows, filtered at most: a single
    SELECT without GROUP BY, aggregate or DISTINCT, down to the tables."""
    if not isinstance(query, exp.Select) or depth > len(parsed.ctes) + 10:
        return False
    if query.args.get("group") or query.args.get("distinct"):
        return False
    if any(has_aggregate(projection) for projection in query.expressions):
        return False
    sources = derived(query, parsed).values()
    return all(body is None or keeps_rows(parsed, body, depth + 1) for body in sources)


def rows_problem(parsed: Parsed, select: exp.Select) -> str:
    """Why the SELECT's rows are not upstream rows (it reads an aggregated CTE); "" when they
    are."""
    for alias, body in derived(select, parsed).items():
        if body is not None and not keeps_rows(parsed, body):
            return f"counts the rows of {alias}, which are not upstream rows"
    return ""


def counts_all_rows(count: exp.Expr) -> bool:
    """COUNT(*) or COUNT(<constant>) without FILTER: the baseline is the slice's row count."""
    return isinstance(count, exp.Count) and isinstance(count.this, exp.Star | exp.Literal)


def label(count: exp.Expr) -> str:
    return "COUNT(*)" if counts_all_rows(count) else unqualified(count).sql(dialect=DIALECT)


def baseline_sql(count: exp.Expr, candidate: Table, bridge: Table | None) -> str | list[str]:
    """The model's count rewritten over the candidate (alias c) and the bridge it is sliced
    through (alias d), or the columns neither has, which leave the count without a baseline."""
    if count.find(exp.Select):
        return [unqualified(count).sql(dialect=DIALECT)]
    keys = column_map(candidate)
    bridge_keys = column_map(bridge) if bridge else {}
    missing: list[str] = []

    def rewrite(node: exp.Expr) -> exp.Expr:
        if not isinstance(node, exp.Column) or isinstance(node.this, exp.Star):
            return node
        name, table = node.name.lower(), node.table.lower()
        if table in ("", candidate.name.lower()) and name in keys:
            return exp.column(keys[name], table="c", quoted=True)
        if bridge and table in ("", bridge.name.lower()) and name in bridge_keys:
            return exp.column(bridge_keys[name], table="d", quoted=True)
        missing.append(f"{node.table}.{node.name}" if node.table else node.name)
        return node

    rewritten = count.transform(rewrite)
    return missing or rewritten.sql(dialect=DIALECT)


ROW_COUNT = exp.Count(this=exp.Star())
NESTED_TYPES = frozenset({"STRUCT", "MAP", "LIST", "UNION", "ARRAY"})


def sliceable(data_type: str) -> bool:
    """A type whose values the slice can match with `=`."""
    return (
        comparable(data_type) and "[" not in data_type and base_type(data_type) not in NESTED_TYPES
    )


@dataclass
class Roles:
    """The model's columns by role. From its SQL when it parses: a GROUP BY output is a
    dimension, a numeric aggregate a metric and a count when its outermost aggregate is COUNT;
    other columns, and every column without the SQL, by type and name (text, date and `*_id` are
    dimensions; an integer named as a count is COUNT(*))."""

    dimensions: list[str]
    unsliceable: list[str]  # grouping columns the slice cannot match: every slice is too wide
    metrics: list[str]
    counts: dict[str, exp.Expr]  # metric -> the COUNT(...) to recompute over each candidate
    opaque: dict[str, str]  # count metric -> why no upstream count is its baseline


def column_roles(
    model: Table, sql: str | None, catalog: list[Table], relations: Relations | None = None
) -> tuple[Roles, str]:
    """The roles and a note on how they were read."""
    parsed = parse_model(sql, catalog, relations) if sql is not None else None
    if sql is None:
        note = "Model SQL not read: only metrics named as counts are held against COUNT(*)"
    elif parsed is None:
        note = (
            "Model SQL could not be parsed: dimensions and counts from column types and names; "
            "only metrics named as counts are held against COUNT(*)"
        )
    else:
        note = (
            "Model SQL read: the slice is its GROUP BY columns, and a COUNT(...) metric is held "
            "against the same COUNT over each candidate"
        )
    roles = Roles([], [], [], {}, {})
    for name, data_type in model.columns:
        output = trace(parsed, parsed.select, name) if parsed else None
        scope = parsed.scope(output.select) if parsed and output else None
        if output and output.grouped:
            # A computed or renamed grouping column has no upstream column of its name to match.
            column = output.expression
            kept = (
                isinstance(column, exp.Column)
                and scope is not None
                and origin(scope, column)[1:] == (name.lower(),)
            )
            if kept and sliceable(data_type):
                roles.dimensions.append(name)
            else:
                roles.unsliceable.append(name)
        elif output and output.aggregate:
            if is_numeric(data_type):
                roles.metrics.append(name)
                count = count_of(output)
                if count is not None and parsed and scope:
                    rewritten = portable(count, scope)
                    problem = rows_problem(parsed, output.select)
                    if isinstance(rewritten, str) or problem:
                        roles.counts[name] = count
                        roles.opaque[name] = problem or str(rewritten)
                    else:
                        roles.counts[name] = rewritten
        elif is_dimension(name, data_type):
            roles.dimensions.append(name)
        elif is_numeric(data_type):
            # A sum, even of integers, or an average is compared by the verifier, not here.
            roles.metrics.append(name)
            # The name speaks only for a column the SQL does not compute (a window, arithmetic).
            named = output is None or isinstance(output.expression, exp.Column)
            if named and base_type(data_type) in INTEGER_TYPES and COUNT_NAME_RE.match(name):
                roles.counts[name] = ROW_COUNT
        else:
            roles.unsliceable.append(name)
    return roles, note


@dataclass(frozen=True)
class Measured:
    candidate: Table
    rows: int
    found: Slice | None  # None: no slice column applies
    count: int = 0
    distinct: tuple[tuple[str, int], ...] = ()
    baselines: tuple[tuple[str, int | list[str]], ...] = ()  # metric -> count, or missing columns


def measure(
    run: RunQuery, candidate: Table, found: Slice | None, counts: dict[str, exp.Expr]
) -> Measured:
    rows = row_count(run, candidate)
    if found is None:
        return Measured(candidate, rows, None)
    rewritten = {
        metric: baseline_sql(count, candidate, found.bridge)
        for metric, count in counts.items()
        if not counts_all_rows(count)
    }
    queried = [(metric, sql) for metric, sql in rewritten.items() if isinstance(sql, str)]
    count, distinct, values = candidate_counts(run, candidate, found.tail, [s for _, s in queried])
    baselines: dict[str, int | list[str]] = {
        metric: sql for metric, sql in rewritten.items() if isinstance(sql, list)
    }
    baselines.update(zip([metric for metric, _ in queried], values, strict=True))
    return Measured(candidate, rows, found, count, tuple(distinct), tuple(baselines.items()))


def verify_values(
    run: RunQuery,
    model: Table,
    sources: list[str],
    sql: str | None = None,
    relations: Relations | None = None,
) -> list[str]:
    catalog = load_catalog(run)
    roles, note = column_roles(model, sql, catalog, relations)
    metrics = roles.metrics
    if not metrics:
        return [f"No numeric metric columns in '{model.ref}'; nothing to verify."]
    top = run(f"SELECT * FROM {model.sql_name} ORDER BY {quote(metrics[0])} DESC NULLS LAST", 1)
    if not top.rows:
        return [f"No rows in '{model.ref}'."]
    row = dict(zip(top.columns, top.rows[0], strict=True))
    # Every dimension of the top row, NULLs included: the model's full grouping grain.
    slice_values = {name: row[name] for name in roles.dimensions}
    unsliceable = roles.unsliceable
    # A one-row model without dimensions is a scalar aggregate over its whole upstreams.
    whole_table = not slice_values and bool(sources) and row_count(run, model) == 1
    shown = ", ".join(
        f"{name} IS NULL" if value is None else f"{name} = '{value}'"
        for name, value in slice_values.items()
    )
    lines = [f"## Value Verification: {model.ref}", note]
    if slice_values:
        lines.append(f"Top row (by {metrics[0]}) slice: {shown}")
    elif whole_table:
        lines.append("One-row model with no dimension column: compared with whole upstreams")
    else:
        lines.append("No dimension column for slicing; candidates skipped")
        return lines
    candidates: list[Table] = []
    if sources:
        for name in sources[:MAX_SOURCES]:
            try:
                candidates.append(resolve(catalog, name))
            except ToolError as exc:
                lines.append(f"Source {name}: {exc}")
        basis = "named upstreams"
    else:
        candidates = nearest_finer_table(run, catalog, model, next(iter(slice_values)))
        basis = "no source_tables given; nearest finer-grained table with the slice column"
    lines += [f"Candidates ({basis}): {', '.join(t.ref for t in candidates) or 'none'}", ""]

    # Bridges only between the named upstreams: a join the model's lineage does not show could
    # put unrelated rows into the count.
    bridges = candidates if sources else []
    measured = [
        measure(
            run,
            candidate,
            Slice("", "the whole table", [], None)
            if whole_table
            else slice_filter(run, candidate, slice_values, bridges),
            {m: count for m, count in roles.counts.items() if m not in roles.opaque},
        )
        for candidate in candidates
    ]
    lines += [
        "Baselines apply the slice only, not the model's own filters (WHERE, join conditions);",
        "for a model that filters rows, count with those filters before calling a mismatch.",
        "",
    ]

    for metric in metrics:
        value = row.get(metric)
        if value is None:
            continue
        lines += [f"### Metric: {metric}", f"  Model value: {value}"]
        count = roles.counts.get(metric)
        for m in measured:
            found = m.found
            if found is None:
                lines.append(
                    f"  Candidate: {m.candidate.ref} ({m.rows} rows) — no slice column and no "
                    "safe join to one; inconclusive, skipped"
                )
                continue
            lines.append(
                f"  Candidate: {m.candidate.ref} ({m.rows} rows), sliced on {found.sliced_on}"
            )
            if found.missing:
                lines.append(
                    f"    not sliced on {', '.join(found.missing)} (no such column or safe join): "
                    "a wider slice than the model row; inconclusive"
                )
            if unsliceable:
                lines.append(
                    f"    not sliced on {', '.join(unsliceable)} (computed or renamed in the "
                    "SQL, or a type the slice cannot match): a wider slice than the model row; "
                    "inconclusive"
                )
            counted = [f"    COUNT(*): {m.count}"]
            counted += [f"    COUNT(DISTINCT {name}): {n}" for name, n in m.distinct]
            lines += counted
            if count is None:
                continue
            if metric in roles.opaque:
                lines.append(
                    f"    {label(count)}: {roles.opaque[metric]}; no baseline, inconclusive"
                )
                continue
            baseline: int | list[str] = dict(m.baselines).get(metric, m.count)
            if isinstance(baseline, list):
                lines.append(
                    f"    {label(count)}: uses {', '.join(baseline)}, not in {m.candidate.ref}; "
                    "no baseline, inconclusive"
                )
                continue
            if f"    {label(count)}: {baseline}" not in counted:
                lines.append(f"    {label(count)}: {baseline}")
            if found.missing or unsliceable:
                continue
            # A count is exact: a row too many or too few is a defect.
            model_value = float(value)
            if baseline == model_value:
                lines.append(f"    → Model matches {label(count)}")
            else:
                ratio = f" ({baseline / model_value:.1f}x)" if model_value else ""
                lines.append(f"    → MISMATCH vs {label(count)}{ratio}")
        lines.append("")
    return lines


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
    pairs = [item.partition(":") for item in re.split(r",(?![^()]*\))", text)]
    return [(name.strip(), data_type.strip()) for name, _, data_type in pairs if name.strip()]


def normalized_type(data_type: str) -> str:
    """The type's kind, aliases folded (int4, int and integer are INT), with a nested type's
    element and field types folded the same way: INTEGER[] is ARRAY(INT), VARCHAR[] is
    ARRAY(VARCHAR)."""
    try:
        return folded_type(exp.DataType.build(data_type, dialect=DIALECT))
    except SqlglotError:
        return base_type(data_type)


def folded_type(data_type: exp.DataType) -> str:
    kind = str(data_type.this.name)
    if kind == "USERDEFINED":
        return base_type(data_type.sql(dialect=DIALECT))
    inner: list[str] = []
    for item in data_type.expressions:
        if isinstance(item, exp.DataType):
            inner.append(folded_type(item))
        elif isinstance(item, exp.ColumnDef) and isinstance(field := item.kind, exp.DataType):
            inner.append(f"{item.name.lower()} {folded_type(field)}")
    kind = SAME_KIND.get(kind, kind)
    return f"{kind}({', '.join(inner)})" if inner else kind


def same_type(declared: str, actual: str) -> bool:
    """Whether the types are of one kind; a nested type named without its element types (as
    Postgres' information_schema names every array ARRAY) matches any of its kind."""
    expected, found = normalized_type(declared), normalized_type(actual)
    if "(" not in expected or "(" not in found:
        return expected.partition("(")[0] == found.partition("(")[0]
    return expected == found


def register_verifier_tools(server: MCPServer, run: RunQuery, workspace: Path | None) -> None:
    """Add the verification tools to `server`; called only when the run config turns the verifier
    layer on. With a `workspace`, map_columns reads only dbt projects inside it."""
    root = workspace.resolve() if workspace else None

    def dbt_project(project_dir: str) -> Path:
        project = Path(project_dir).expanduser()
        if root and not project.resolve().is_relative_to(root):
            raise ToolError(f"Error: {project_dir!r} is outside the workspace {root}")
        if not (project / "dbt_project.yml").is_file():
            raise ToolError(f"Error: {project_dir!r} is not a dbt project (no dbt_project.yml)")
        return project

    @server.tool()
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
        return "\n".join(map_model(run, project, model_name, sql_path, excluded))

    @server.tool()
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
        model = resolve(load_catalog(run), model_name)
        actual_types = {column.lower(): data_type for column, data_type in model.columns}
        wrong_types = [
            f"{name} (expected {data_type}, actual {actual_types[name.lower()]})"
            for name, data_type in declared
            if data_type
            and name.lower() in actual_types
            and not same_type(data_type, actual_types[name.lower()])
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

    @server.tool()
    def audit_model_sources(model_name: str, source_tables: str, sample_nulls: bool = True) -> str:
        """Row counts of a model and its upstream tables (comma-separated `source_tables`, at
        most 10) with model/source ratios, sample rows, and per-column distinct and NULL counts,
        low-cardinality values and the grain key (`sample_nulls`)."""
        sources = split_names(source_tables)
        if not sources:
            raise ToolError("Error: source_tables is empty; name at least one upstream table")
        model = resolve(load_catalog(run), model_name)
        return "\n".join(audit(run, model, sources, sample_nulls))

    @server.tool()
    def verify_model_values(model_name: str, source_tables: str = "", project_dir: str = "") -> str:
        """Cross-check a model's metric values against its upstream data.

        Takes the model's top row by its first metric and slices on that row's dimensions
        (NULLs included), then counts COUNT(*) and COUNT(DISTINCT <_id column>) for the same
        slice in each upstream table; a candidate it cannot slice on every dimension is marked
        inconclusive, and a one-row model with no dimension is compared with the whole
        upstreams. The counts skip the model's own filters.
        Pass the model's upstreams (the tables in its FROM / ref() / source(), comma-separated)
        as `source_tables`; without them the nearest finer-grained table that has the first
        slice column stands in. Pass the dbt project directory as `project_dir` so the model's
        SQL is read: the dimensions are its GROUP BY columns, and a metric whose outermost
        aggregate is COUNT(...) is compared exactly with the same COUNT (DISTINCT, CASE WHEN or
        FILTER included) over each upstream. Without it, or when the SQL does not parse, text,
        date and id columns are the dimensions and only integer metrics named as counts
        (n_*, num_*, count_*, *_count) are compared, exactly, with COUNT(*). Sums and averages
        get no verdict.
        """
        model = resolve(load_catalog(run), model_name)
        sql = None
        relations = None
        if project_dir:
            project = dbt_project(project_dir)
            relations = dbt_relations(project)
            # The model may be built under an alias: its SQL file has the model's name.
            paths = (model_sql(project, name) for name in relations.models_named(model.name))
            path = next((path for path in paths if path), None)
            sql = read_text(path) if path else None
        return "\n".join(verify_values(run, model, split_names(source_tables), sql, relations))
