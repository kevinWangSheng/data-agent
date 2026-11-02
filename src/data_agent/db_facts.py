"""Facts the database reports about itself (ADR-0003 base layer): row estimates, columns, keys,
indexes, Postgres column statistics, and query plans. Nothing here is a judgment: each fact is read
from the database's catalog, statistics or EXPLAIN and passed on as the database states it.

The Postgres row-estimate query and the `pg_indexes` read are adapted from SignalPilot's Postgres
connector (`gateway/connectors/drivers/postgres.py`, `_get_schema_impl`; Apache-2.0, see NOTICE).
Changes: no privilege filters or schema allowlist (the gateway's connection decides what it may
read), a NULL estimate for "unknown" instead of 0, and one table's or one query's tables at a time.
Keys come from `pg_get_constraintdef`; DuckDB reads its own `duckdb_tables()`,
`duckdb_constraints()` and `duckdb_indexes()`. Every statement is a plain SELECT run through the
gateway like any other.

The catalog (`load_catalog`) and ref lookup (`find_table`) are shared with the context tools.
"""

from collections.abc import Callable
from dataclasses import dataclass

from mcp.server.mcpserver.exceptions import ToolError

from data_agent.engine._sqlglot import exp, sqlglot
from data_agent.gateway import MAX_ROWS, QueryResult

# Runs one statement through the gateway; blocks and database errors raise ToolError.
RunQuery = Callable[[str, int], QueryResult]

CATALOG_SQL = """
SELECT c.table_schema, c.table_name, t.table_type, c.column_name, c.data_type
FROM information_schema.columns AS c
JOIN information_schema.tables AS t
  ON t.table_catalog = c.table_catalog
 AND t.table_schema = c.table_schema
 AND t.table_name = c.table_name
WHERE c.table_schema NOT IN ('information_schema', 'pg_catalog')
ORDER BY c.table_schema, c.table_name, c.ordinal_position
LIMIT {limit} OFFSET {offset}
"""

# (schema, table) -> estimated rows, for each table the database has an estimate for.
ROW_ESTIMATES_SQL = {
    # n_live_tup is empty on a restored or cloned server, so reltuples (kept in the catalog) is the
    # fallback; reltuples is -1 or 0 for a table never analyzed. Neither: no estimate (NULL).
    "postgres": """
SELECT n.nspname, c.relname,
       COALESCE(NULLIF(s.n_live_tup, 0), NULLIF(GREATEST(c.reltuples, 0)::bigint, 0))
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_user_tables AS s ON s.relid = c.oid
WHERE c.relkind IN ('r', 'p', 'm', 'f')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
""",
    "duckdb": "SELECT schema_name, table_name, estimated_size FROM duckdb_tables()",
}

COLUMNS_SQL = """
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = {schema} AND table_name = {table}
ORDER BY ordinal_position
"""

# Declared primary and foreign keys, in the database's own wording; the primary key first.
KEYS_SQL = {
    "postgres": """
SELECT pg_get_constraintdef(con.oid)
FROM pg_constraint AS con
JOIN pg_class AS c ON c.oid = con.conrelid
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = {schema} AND c.relname = {table} AND con.contype IN ('p', 'f')
ORDER BY con.contype DESC, con.conname
""",
    "duckdb": """
SELECT constraint_text
FROM duckdb_constraints()
WHERE schema_name = {schema} AND table_name = {table}
  AND constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY')
ORDER BY constraint_type DESC, constraint_index
""",
}

# Index catalog per dialect: (source, database, schema, table, definition and name columns).
INDEX_CATALOG = {
    "postgres": (
        "pg_indexes",
        "current_database()",
        "schemaname",
        "tablename",
        "indexdef",
        "indexname",
    ),
    "duckdb": (
        "duckdb_indexes()",
        "database_name",
        "schema_name",
        "table_name",
        "sql",
        "index_name",
    ),
}

# What a timeout report says about where its indexes come from.
INDEX_SOURCE = {
    "postgres": "pg_indexes",
    "duckdb": "duckdb_indexes(), which leaves out the indexes behind keys",
}

# Postgres only: pg_stats has a row per analyzed column; DuckDB keeps no such statistics.
COLUMN_STATS_SQL = """
SELECT attname, n_distinct, null_frac
FROM pg_stats
WHERE schemaname = {schema} AND tablename = {table}
"""


@dataclass
class Table:
    schema: str
    name: str
    kind: str
    columns: list[tuple[str, str]]  # (name, data type), in table order

    @property
    def ref(self) -> str:
        return f"{ref_part(self.schema)}.{ref_part(self.name)}"

    @property
    def sql_name(self) -> str:
        return f"{quote(self.schema)}.{quote(self.name)}"


@dataclass
class Column:
    name: str
    data_type: str
    nullable: bool


@dataclass
class TableFacts:
    columns: list[Column]
    keys: list[str]  # constraint definitions, as the database prints them
    indexes: list[str]  # index definitions, as the database prints them
    # Postgres: column -> (n_distinct, null_frac) from pg_stats; None for DuckDB.
    stats: dict[str, tuple[float, float]] | None


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def ref_part(identifier: str) -> str:
    """Plain unless a dot or quote would make the ref ambiguous: a."b.c" and "a.b".c stay apart."""
    return quote(identifier) if "." in identifier or '"' in identifier else identifier


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def load_catalog(run: RunQuery) -> list[Table]:
    """Every column of every table, read in pages: one gateway query returns at most MAX_ROWS."""
    tables: dict[tuple[str, str], Table] = {}
    offset = 0
    while True:
        rows = run(CATALOG_SQL.format(limit=MAX_ROWS, offset=offset), MAX_ROWS).rows
        for schema, name, kind, column, data_type in rows:
            table = tables.setdefault(
                (schema, name), Table(schema, name, kind.lower().removeprefix("base "), [])
            )
            table.columns.append((column, data_type))
        if len(rows) < MAX_ROWS:
            return list(tables.values())
        offset += MAX_ROWS


def find_table(
    catalog: list[Table], ref: str, hint: str = "use a ref returned by discover"
) -> Table:
    """The exact ref first, so quoted names with spaces or case-only differences resolve; then a
    case-folded match on the ref or the bare name."""
    for table in catalog:
        if table.ref == ref:
            return table
    wanted = ref.strip().lower()
    hits = [table for table in catalog if wanted in (table.ref.lower(), table.name.lower())]
    if len(hits) == 1:
        return hits[0]
    if hits:
        options = ", ".join(table.ref for table in hits)
        raise ToolError(f"Error: {ref!r} is ambiguous; use one of: {options}")
    raise ToolError(f"Error: no table or view {ref!r}; {hint}")


def row_estimates(run: RunQuery, dialect: str) -> dict[tuple[str, str], int | None]:
    return {
        (schema, name): None if estimate is None else int(estimate)
        for schema, name, estimate in run(ROW_ESTIMATES_SQL[dialect], MAX_ROWS).rows
    }


def rows_label(estimate: int | None) -> str:
    return "rows unknown" if estimate is None else f"~{estimate} rows"


def index_sql(dialect: str, condition: str) -> str:
    """(database, schema, table, definition) of each index whose row meets `condition`, which
    names the columns `{database}`, `{schema}` and `{table}`."""
    source, database, schema, table, definition, name = INDEX_CATALOG[dialect]
    where = condition.format(database=database, schema=schema, table=table)
    return (
        f"SELECT {database}, {schema}, {table}, {definition} FROM {source} WHERE {where} "
        f"ORDER BY {schema}, {table}, {name}"
    )


def table_facts(run: RunQuery, dialect: str, table: Table) -> TableFacts:
    names = {"schema": literal(table.schema), "table": literal(table.name)}
    columns = [
        Column(name, data_type, nullable.upper() == "YES")
        for name, data_type, nullable in run(COLUMNS_SQL.format(**names), MAX_ROWS).rows
    ]
    keys = [row[0] for row in run(KEYS_SQL[dialect].format(**names), MAX_ROWS).rows]
    condition = f"{{schema}} = {names['schema']} AND {{table}} = {names['table']}"
    indexes = [row[3] for row in run(index_sql(dialect, condition), MAX_ROWS).rows]
    stats = None
    if dialect == "postgres":
        rows = run(COLUMN_STATS_SQL.format(**names), MAX_ROWS).rows
        stats = {
            name: (float(n_distinct), float(null_frac)) for name, n_distinct, null_frac in rows
        }
    return TableFacts(columns, keys, indexes, stats)


def column_line(column: Column) -> str:
    return f"  {column.name} {column.data_type}, {'nullable' if column.nullable else 'not null'}"


def key_lines(facts: TableFacts) -> list[str]:
    """The declared keys and the indexes, a section each."""
    lines = ["Keys:"]
    lines.extend(f"  {key}" for key in facts.keys or ["none declared"])
    lines.append("Indexes:")
    lines.extend(f"  {index}" for index in facts.indexes or ["none"])
    return lines


def describe(run: RunQuery, dialect: str, ref: str) -> str:
    """One table or view: row estimate, columns (type, nullable, Postgres statistics), declared
    keys and indexes."""
    table = find_table(load_catalog(run), ref, hint="use a name from list_tables")
    facts = table_facts(run, dialect, table)
    head = [table.kind]
    if table.kind != "view":
        estimate = row_estimates(run, dialect).get((table.schema, table.name))
        head.append(rows_label(estimate))
    head.append(f"{len(facts.columns)} columns")
    lines = [f"{table.ref} ({', '.join(head)})", "Columns:"]
    for column in facts.columns:
        line = column_line(column)
        if facts.stats and column.name in facts.stats:
            n_distinct, null_frac = facts.stats[column.name]
            line += f"; n_distinct={n_distinct:g}, null_frac={null_frac:g}"
        lines.append(line)
    if facts.stats is not None and not facts.stats and table.kind != "view":
        lines.append("Column statistics: none in pg_stats for this table")
    lines.extend(key_lines(facts))
    return "\n".join(lines)


def plan_text(result: QueryResult) -> str:
    """EXPLAIN's output as the database prints it: Postgres gives a line per row, DuckDB the whole
    plan in the last column of a row."""
    return "\n".join(str(row[-1]) for row in result.rows)


# Resolving the names a query reads to the tables it reads. Postgres: `to_regclass` looks a name up
# exactly as the planner does (search_path, implicit pg_catalog and pg_temp; the caller has already
# folded unquoted names). DuckDB has no such function, so its lookup order is applied here.
RESOLVE_SQL = """
SELECT current_database(), n.nspname, c.relname
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.oid = to_regclass({ref})
"""
DUCKDB_PATH_SQL = "SELECT current_setting('search_path'), current_database(), current_schema()"
# Every table and view named like {name} (ignoring case, as DuckDB does), in any attached database.
DUCKDB_CANDIDATES_SQL = """
SELECT database_name, schema_name, table_name FROM duckdb_tables()
WHERE lower(table_name) = lower({name})
UNION ALL
SELECT database_name, schema_name, view_name FROM duckdb_views()
WHERE NOT internal AND lower(view_name) = lower({name})
"""

# (database, schema, table): a table as the database resolved it.
Resolved = tuple[str, str, str]


@dataclass
class Named:
    """A table name as a query writes it: catalog and schema are "" when left out."""

    catalog: str
    schema: str
    name: str

    @property
    def written(self) -> str:
        return ".".join(ref_part(part) for part in (self.catalog, self.schema, self.name) if part)


def tables_read(sql: str, dialect: str) -> list[Named]:
    """Each table `sql` names, in order and once each; CTE names and table functions are left
    out. Postgres folds unquoted names to lower case, so they are folded here too; DuckDB matches
    names ignoring case, so they are kept as written."""
    tree = sqlglot.parse_one(sql, dialect=dialect)
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables: list[Named] = []
    for node in tree.find_all(exp.Table):
        named = Named(
            *(identifier_name(node.args.get(part), dialect) for part in ("catalog", "db", "this"))
        )
        is_cte = not node.db and node.name.lower() in ctes
        if node.name and not is_cte and named not in tables:
            tables.append(named)
    return tables


def identifier_name(identifier: exp.Expr | None, dialect: str) -> str:
    """The name the database looks up: Postgres folds an unquoted identifier to lower case."""
    if not isinstance(identifier, exp.Identifier):
        return identifier.name if identifier is not None else ""
    if dialect == "postgres" and not identifier.quoted:
        return identifier.name.lower()
    return identifier.name


class DuckDBNames:
    """DuckDB's lookup: an unqualified name in temporary objects first, then the current database
    and schema; `x.t` in schema x of those databases or in database x's main schema (DuckDB refuses
    a query where both exist); `d.s.t` exactly. A search_path setting is not interpreted: when one
    is set, unqualified names are left unresolved and the report says why."""

    def __init__(self, run: RunQuery) -> None:
        self.run = run
        [(setting, database, schema)] = run(DUCKDB_PATH_SQL, 1).rows
        self.search_path = str(setting or "")
        self.current_database = database
        self.path = [("temp", "main"), (database, schema)]

    def resolve(self, named: Named) -> Resolved | None:
        rows = self.run(DUCKDB_CANDIDATES_SQL.format(name=literal(named.name)), MAX_ROWS).rows
        if named.catalog:
            places = [(named.catalog, named.schema)]
        elif named.schema:
            places = [(database, named.schema) for database, _ in self.path]
            places.append((named.schema, "main"))
        else:
            places = [] if self.search_path else self.path
        for database, schema in places:
            for row in rows:
                if (row[0].lower(), row[1].lower()) == (database.lower(), schema.lower()):
                    return (row[0], row[1], row[2])
        return None

    def unresolved(self, named: Named) -> str:
        if named.schema:
            return "no table or view by this name"
        if self.search_path:
            return f"not resolved: search_path is set ({self.search_path!r})"
        return "no table or view by this name on the search path"

    def label(self, table: Resolved) -> str:
        parts = table if table[0] != self.current_database else table[1:]
        return ".".join(ref_part(part) for part in parts)


class PostgresNames:
    def __init__(self, run: RunQuery) -> None:
        self.run = run

    def resolve(self, named: Named) -> Resolved | None:
        ref = ".".join(quote(part) for part in (named.catalog, named.schema, named.name) if part)
        rows = self.run(RESOLVE_SQL.format(ref=literal(ref)), 1).rows
        return (rows[0][0], rows[0][1], rows[0][2]) if rows else None

    def unresolved(self, named: Named) -> str:
        return "no table or view by this name" + ("" if named.schema else " on the search path")

    def label(self, table: Resolved) -> str:
        return f"{ref_part(table[1])}.{ref_part(table[2])}"


def index_report(run: RunQuery, dialect: str, sql: str) -> list[str]:
    """The indexes on each table `sql` reads, under the name of the table the database resolves
    it to; a name that resolves to no table says so."""
    tables = tables_read(sql, dialect)
    if not tables:
        return ["Indexes: the query reads no tables"]
    names = PostgresNames(run) if dialect == "postgres" else DuckDBNames(run)
    resolved = [(named, names.resolve(named)) for named in tables]
    found = sorted({table for _, table in resolved if table is not None})
    rows: list[tuple[str, ...]] = []
    if found:
        condition = " OR ".join(
            f"({{database}} = {literal(database)} AND {{schema}} = {literal(schema)} "
            f"AND {{table}} = {literal(name)})"
            for database, schema, name in found
        )
        rows = [tuple(row) for row in run(index_sql(dialect, condition), MAX_ROWS).rows]
    lines = [f"Indexes on the tables this query reads (from {INDEX_SOURCE[dialect]}):"]
    for named, table in resolved:
        if table is None:
            lines.append(f"  {named.written}: {names.unresolved(named)}")
            continue
        definitions = [row[3] for row in rows if row[:3] == table]
        label = names.label(table)
        lines.extend(f"  {label}: {definition}" for definition in definitions or ["none"])
    return lines


def timeout_report(
    message: str, sql: str, dialect: str, explain: Callable[[str], str], run: RunQuery
) -> str:
    """A timeout's message, then the query's plan and the indexes on the tables it reads. When
    EXPLAIN itself fails, only why."""
    try:
        plan = explain(sql)
    except ToolError as exc:
        return f"{message}\n\nEXPLAIN failed: {exc}"
    lines = [message, "", "Plan (EXPLAIN, without running the query):", plan, ""]
    try:
        lines.extend(index_report(run, dialect, sql))
    except ToolError as exc:
        lines.append(f"Indexes could not be read: {exc}")
    return "\n".join(lines)
