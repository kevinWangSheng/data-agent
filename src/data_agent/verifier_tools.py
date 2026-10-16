"""The verifier subagents' tools (ADR-0002): map_columns, check_model_schema, audit_model_sources,
verify_model_values. list_tables and query_database, the other two, are base tools.

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
- verify_model_values takes the model's upstreams as `source_tables`, read by the verifier from
  the model's SQL. Without them it compares against the nearest finer-grained table that has the
  slice column, instead of upstream's three largest tables, which on a project with a fanned-out
  sibling model put that sibling's doubled counts next to a correct model. The slice is every
  text or date column of the top row (upstream: the first), and a join to reach it goes only
  through a named upstream whose join column is unique (upstream: any table sharing a column);
  a candidate it cannot slice on every column is marked inconclusive, and the output says the
  baselines do not apply the model's own filters.
  It marks a COUNT(*) match or mismatch only for integer metrics; upstream also held sums up
  against row counts.
- A table profiled over a sample says so, its NULL shares are of the sample, and a column that
  looks unique in the sample is counted over the whole table before it is called the grain.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

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
NAME_LINE_RE = re.compile(r"-\s*name:\s*['\"]?([^'\"\s]+)")
SQL_KEYWORDS = frozenset(
    {"THEN", "ELSE", "END", "WHEN", "CASE", "AND", "OR", "NOT", "NULL", "TRUE", "FALSE", "AS"}
)


@dataclass(frozen=True)
class Profile:
    """Counts over `total` rows: the whole table, or its first SAMPLE_ROWS when `sampled`."""

    total: int
    nulls: int
    distinct: int
    sampled: bool


def base_type(data_type: str) -> str:
    return data_type.upper().split("(")[0].strip()


def is_numeric(data_type: str) -> bool:
    return base_type(data_type) in NUMERIC_TYPES


def is_dimension(data_type: str) -> bool:
    return is_text(data_type) or base_type(data_type) == "DATE"


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


def profile(run: RunQuery, table: Table, columns: Sequence[str], rows: int) -> dict[str, Profile]:
    """NULL and distinct counts per column, a chunk of columns per statement. A table with more
    than SAMPLE_CELLS cells is profiled over its first SAMPLE_ROWS rows."""
    result: dict[str, Profile] = {}
    for start in range(0, len(columns), DISTINCTS_PER_STATEMENT):
        chunk = columns[start : start + DISTINCTS_PER_STATEMENT]
        source = table.sql_name
        sampled = rows * len(columns) > SAMPLE_CELLS
        if sampled:
            picked = ", ".join(quote(column) for column in chunk)
            source = f"(SELECT {picked} FROM {table.sql_name} LIMIT {SAMPLE_ROWS}) AS s"
        parts = ["count(*)"]
        for column in chunk:
            parts += [f"count({quote(column)})", f"count(DISTINCT {quote(column)})"]
        values = run(f"SELECT {', '.join(parts)} FROM {source}", 1).rows[0]
        total = int(values[0])
        for index, column in enumerate(chunk):
            non_null, distinct = values[1 + 2 * index], values[2 + 2 * index]
            result[column] = Profile(total, total - int(non_null), int(distinct), sampled)
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


def model_sql(project: Path, model_name: str) -> Path | None:
    return next((p for p in project_files(project, "*.sql") if p.stem == model_name), None)


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
        in_sources = False
        source: str | None = None
        schema: str | None = None
        table: str | None = None
        source_indent = table_indent = 0
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
                source, schema, table = name.group(1), None, None
                source_indent = indent
            elif source is None:
                continue
            elif name and (table is None or indent == table_indent):
                # Deeper `- name:` lines under a table are its columns.
                table, table_indent = str(name.group(1)), indent
                relations[(source, table)] = (schema or source, table)
            elif stripped.startswith("schema:") and table is None:
                schema = value
            elif table and indent > table_indent and stripped.startswith("identifier:"):
                relations[(source, table)] = (relations[(source, table)][0], value)
    return relations


def sql_columns(sql: str) -> list[str]:
    """Output column names of the last SELECT, for a model not materialized yet."""
    clean = re.sub(r"\{\{.*?\}\}", "___REF___", sql)
    clean = re.sub(r"\{%.*?%\}", "", clean)
    clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.DOTALL)
    clean = re.sub(r"--.*$", "", clean, flags=re.MULTILINE)
    selects = list(re.finditer(r"SELECT\s+(.*?)\s+FROM\b", clean, re.IGNORECASE | re.DOTALL))
    if not selects or selects[-1].group(1).strip() == "*":
        return []
    parts: list[str] = []
    depth, current = 0, ""
    for char in selects[-1].group(1):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    parts.append(current.strip())
    names: list[str] = []
    for part in parts:
        alias = re.search(r"\bAS\s+(\w+)\s*$", part, re.IGNORECASE)
        words = part.split()
        name = alias.group(1) if alias else (words[-1].split(".")[-1] if words else "")
        if name and name not in ("*", "___REF___") and name.upper() not in SQL_KEYWORDS:
            names.append(name)
    return names


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
        stats = profile(run, table, [c for c, _ in columns], row_count(run, table))
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
    sources = source_relations(project)
    upstreams: list[tuple[str, list[Table], str]] = []  # (label, catalog hits, name)
    for ref in sorted(set(REF_RE.findall(sql))):
        upstreams.append((f"ref('{ref}')", find(catalog, ref), ref))
    for source, table in sorted(set(SOURCE_RE.findall(sql))):
        schema, identifier = sources.get((source, table), (source, table))
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
    """Thresholds from the verifier's CHECK 3: above 1.0 possible fan-out, below 0.9 possible
    over-filtering."""
    if source_rows == 0:
        return "ratio N/A      OBSERVATION: source has 0 rows"
    ratio = model_rows / source_rows
    if ratio > 1.0:
        note = "OBSERVATION: more model rows than source rows (possible fan-out)"
    elif ratio < 0.9:
        note = "OBSERVATION: fewer model rows than source rows (possible over-filtering)"
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

    columns = [column for column, _ in model.columns]
    scanned = columns[:SCANNED_COLUMNS]
    stats = profile(run, model, scanned, model_rows)
    shown = f"first {len(scanned)} of {len(columns)}" if len(columns) > len(scanned) else "all"
    sampled = any(s.sampled for s in stats.values())
    over = f", over a sample of the first {SAMPLE_ROWS} rows" if sampled else ""
    lines += ["", f"Column scan ({shown} columns{over}):"]
    for column in scanned:
        s = stats[column]
        share = f" ({100 * s.nulls / s.total:.1f}%)" if s.nulls else ""
        lines.append(f"  [OBS] {column}: {s.distinct} distinct, {s.nulls} nulls{share}")
    low = [c for c in scanned if 2 <= stats[c].distinct <= LOW_CARDINALITY][:LISTED_COLUMNS]
    if low:
        lines.append("Low-cardinality column values:")
        for column in low:
            sql = (
                f"SELECT DISTINCT {quote(column)} FROM {model.sql_name} "
                f"ORDER BY 1 LIMIT {LOW_CARDINALITY}"
            )
            values = [str(row[0]) for row in run(sql, LOW_CARDINALITY).rows]
            lines.append(f"  {column}: [{', '.join(values)}]")
    grain = next((c for c in scanned[:5] if is_unique(run, model, c, stats[c], model_rows)), None)
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
    """A column the candidate shares with `bridge` that is unique in `bridge`, so joining on it
    cannot multiply the candidate's rows."""
    bridge_keys = column_map(bridge)
    for key in column_map(candidate):
        if key in bridge_keys:
            column = quote(bridge_keys[key])
            sql = f"SELECT count(*), count(DISTINCT {column}) FROM {bridge.sql_name}"
            rows, distinct = run(sql, 1).rows[0]
            if rows == distinct:
                return key
    return None


def slice_filter(
    run: RunQuery, candidate: Table, slice_values: dict[str, Any], bridges: list[Table]
) -> tuple[str, str, list[str]] | None:
    """(FROM-clause tail limiting `candidate`, aliased c, to the slice; what it slices on; the
    slice columns it could not apply). Slice columns the candidate has apply directly; the rest
    through one named upstream that has them and a unique join column. None when no slice column
    applies at all."""
    keys = column_map(candidate)
    applied = [column for column in slice_values if column.lower() in keys]
    tests = [f"c.{quote(keys[c.lower()])} = {literal(str(slice_values[c]))}" for c in applied]
    described = [", ".join(applied)] if applied else []
    missing = [column for column in slice_values if column not in applied]
    join = ""
    for bridge in bridges if missing else []:
        bridge_keys = column_map(bridge)
        covered = [column for column in missing if column.lower() in bridge_keys]
        if bridge == candidate or not covered:
            continue
        key = unique_key(run, candidate, bridge)
        if key is None:
            continue
        join = f"JOIN {bridge.sql_name} AS d ON c.{quote(keys[key])} = d.{quote(bridge_keys[key])} "
        tests += [
            f"d.{quote(bridge_keys[c.lower()])} = {literal(str(slice_values[c]))}" for c in covered
        ]
        described.append(f"{', '.join(covered)} via {bridge.ref}.{bridge_keys[key]}")
        missing = [column for column in missing if column not in covered]
        break
    if not tests:
        return None
    return f"{join}WHERE {' AND '.join(tests)}", "; ".join(described), missing


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
    run: RunQuery, candidate: Table, tail: str
) -> tuple[int, list[tuple[str, int]]]:
    """COUNT(*) and COUNT(DISTINCT) of the first `_id` columns over the slice."""
    ids = [name for name, _ in candidate.columns if name.lower().endswith("_id") or name == "id"][
        :ID_COLUMNS
    ]
    parts = ["count(*)", *(f"count(DISTINCT c.{quote(name)})" for name in ids)]
    values = run(f"SELECT {', '.join(parts)} FROM {candidate.sql_name} AS c {tail}", 1).rows[0]
    return int(values[0]), [(name, int(v)) for name, v in zip(ids, values[1:], strict=True)]


def verify_values(run: RunQuery, model: Table, sources: list[str]) -> list[str]:
    catalog = load_catalog(run)
    metrics = [name for name, data_type in model.columns if is_numeric(data_type)]
    if not metrics:
        return [f"No numeric columns in '{model.ref}'; nothing to verify."]
    top = run(f"SELECT * FROM {model.sql_name} ORDER BY {quote(metrics[0])} DESC NULLS LAST", 1)
    if not top.rows:
        return [f"No rows in '{model.ref}'."]
    row = dict(zip(top.columns, top.rows[0], strict=True))
    # Every text or date column of the top row: the model's full grouping grain, as far as it
    # shows in its columns.
    slice_values = {
        name: row[name]
        for name, t in model.columns
        if is_dimension(t) and row.get(name) is not None
    }
    shown = ", ".join(f"{name} = '{value}'" for name, value in slice_values.items())
    lines = [
        f"## Value Verification: {model.ref}",
        f"Top row (by {metrics[0]}) slice: {shown}"
        if slice_values
        else "No dimension column for slicing; candidates skipped",
    ]
    if not slice_values:
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
    measured: list[tuple[Table, str, list[str], int | None, list[tuple[str, int]], int]] = []
    for candidate in candidates:
        rows = row_count(run, candidate)
        found = slice_filter(run, candidate, slice_values, bridges)
        if found is None:
            measured.append((candidate, "", [], None, [], rows))
            continue
        tail, sliced_on, missing = found
        count, distinct = candidate_counts(run, candidate, tail)
        measured.append((candidate, sliced_on, missing, count, distinct, rows))
    lines += [
        "Baselines apply the slice only, not the model's own filters (WHERE, join conditions);",
        "for a model that filters rows, count with those filters before calling a mismatch.",
        "",
    ]

    types = dict(model.columns)
    for metric in metrics:
        value = row.get(metric)
        if value is None:
            continue
        lines += [f"### Metric: {metric}", f"  Model value: {value}"]
        # Only an integer can be a row count; a sum or average is compared by the verifier.
        is_count = base_type(types[metric]) in INTEGER_TYPES
        for candidate, sliced_on, missing, count, distinct, rows in measured:
            if count is None:
                lines.append(
                    f"  Candidate: {candidate.ref} ({rows} rows) — no slice column and no "
                    "safe join to one; inconclusive, skipped"
                )
                continue
            lines.append(f"  Candidate: {candidate.ref} ({rows} rows), sliced on {sliced_on}")
            if missing:
                lines.append(
                    f"    not sliced on {', '.join(missing)} (no such column or safe join): "
                    "a wider slice than the model row; inconclusive"
                )
            lines.append(f"    COUNT(*): {count}")
            lines.extend(f"    COUNT(DISTINCT {name}): {n}" for name, n in distinct)
            model_value = float(value)
            if is_count and not missing and model_value > 0 and count > 0:
                ratio = count / model_value
                lines.append(
                    "    → Model matches COUNT(*)"
                    if 0.9 <= ratio <= 1.1
                    else f"    → MISMATCH vs COUNT(*) ({ratio:.1f}x)"
                )
        lines.append("")
    return lines


def register_verifier_tools(server: MCPServer, run: RunQuery, workspace: Path | None) -> None:
    """Add the verification tools to `server`; called only when the run config turns the verifier
    layer on. With a `workspace`, map_columns reads only dbt projects inside it."""
    root = workspace.resolve() if workspace else None

    @server.tool()
    def map_columns(model_name: str, project_dir: str, exclude: str = "") -> str:
        """Map a dbt model's upstream columns against its YML contract.

        Reads the model's SQL in `project_dir` for its ref() and source() upstreams, then lists
        every upstream column as MAPPED (in the YML), UNMAPPED-INCLUDE or UNMAPPED-EXCLUDE, with
        flags such as all_null or constant_value, plus columns that collide across upstreams.
        `exclude` is a comma-separated list of column names to mark UNMAPPED-EXCLUDE.
        """
        project = Path(project_dir).expanduser()
        if root and not project.resolve().is_relative_to(root):
            raise ToolError(f"Error: {project_dir!r} is outside the workspace {root}")
        if not (project / "dbt_project.yml").is_file():
            raise ToolError(f"Error: {project_dir!r} is not a dbt project (no dbt_project.yml)")
        sql_path = model_sql(project, model_name)
        if sql_path is None:
            raise ToolError(f"Error: no SQL file for model {model_name!r} in {project_dir!r}")
        excluded = {name.lower() for name in split_names(exclude)}
        return "\n".join(map_model(run, project, model_name, sql_path, excluded))

    @server.tool()
    def check_model_schema(model_name: str, yml_columns: str) -> str:
        """Compare a built model's columns with the comma-separated column list from its YML:
        matching, missing, extra and case-mismatched columns."""
        expected = split_names(yml_columns)
        if not expected:
            raise ToolError(
                "Error: yml_columns is empty; pass the YML column names, comma-separated"
            )
        model = resolve(load_catalog(run), model_name)
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
    def verify_model_values(model_name: str, source_tables: str = "") -> str:
        """Cross-check a model's metric values against its upstream data.

        Takes the model's top row by its first numeric column and slices on every text or date
        column of that row, then counts COUNT(*) and COUNT(DISTINCT <_id column>) for the same
        slice in each upstream table; a candidate it cannot slice on every column is marked
        inconclusive. The counts skip the model's own filters. Pass the model's upstreams (the
        tables in its FROM / ref() / source(), comma-separated) as `source_tables`; without them
        the nearest finer-grained table that has the first slice column stands in.
        """
        model = resolve(load_catalog(run), model_name)
        return "\n".join(verify_values(run, model, split_names(source_tables)))
