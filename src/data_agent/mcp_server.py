"""The data agent's MCP server (stdio). Every tool reaches the database through the gateway.

The display bounds follow SignalPilot (Apache-2.0, see NOTICE): `render_table_listing` is adapted
from `gateway/mcp/tools/schema/catalog.py` (`_render_table_listing`; no header lines, this server's
table line, and the row estimate from `db_facts`), and the row display cap in `format_rows` from
`gateway/mcp/tools/query.py` (`query_database`; the same floor, budget, maximum and notice).
"""

import itertools
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from data_agent.charts import DESCRIPTION as CHART_DESCRIPTION
from data_agent.charts import ChartError, ChartSeries, ChartType, chart_payload
from data_agent.classify import (
    check_categories,
    classify,
    description,
    fetch_sql,
    report,
    resume_of,
    setup_from_env,
)
from data_agent.context_tools import (
    ContextDatabase,
    Dictionary,
    DictionaryBuild,
    build_interruptible,
    dictionary_timeout,
    other_runs,
    register_context_tools,
)
from data_agent.db_facts import (
    CatalogCache,
    HiddenSchemas,
    LoadCatalog,
    describe,
    plan_text,
    row_estimates,
    rows_label,
    timeout_report,
)
from data_agent.dbt_execute import DbtExecuteError, DbtExecutor
from data_agent.dbt_execute import tool_description as dbt_tool_description
from data_agent.gateway import (
    BLOCKED_PREFIX,
    MAX_ROWS,
    Gateway,
    QueryBlocked,
    QueryFailed,
    QueryResult,
    QueryTimedOut,
    SourceLimits,
)
from data_agent.label_tables import LABELS_DIR, LabelTables
from data_agent.run_config import RunConfig
from data_agent.sandbox import (
    QueryResults,
    Sandbox,
    SandboxError,
    default_timeout,
    execute_tool,
    tool_description,
)
from data_agent.tool_annotations import CHAT_ENV, READ_ONLY, SANDBOX, database_tools
from data_agent.verifier_tools import register_verifier_tools

# One row per table, column names in order; plain SELECTs, so they pass the gateway like any other.
# The hidden schemas (`Database.hidden`) are left out in the SQL, so they take none of the MAX_ROWS.
LIST_TABLES_SQL = """
SELECT t.table_schema, t.table_name, t.table_type,
       string_agg(c.column_name, ', ' ORDER BY c.ordinal_position) AS columns,
       count(*) AS column_count
FROM information_schema.tables AS t
JOIN information_schema.columns AS c
  ON c.table_catalog = t.table_catalog
 AND c.table_schema = t.table_schema
 AND c.table_name = t.table_name
WHERE t.table_schema NOT IN ('information_schema', 'pg_catalog') AND {shown}
GROUP BY t.table_schema, t.table_name, t.table_type
ORDER BY t.table_schema, t.table_name
"""


MIN_OPTIONS = 2
MAX_OPTIONS = 5
CLARIFICATION_SENT = (
    "The question and its options are shown to the user. This turn ends here; the option they "
    "pick, or their own reply, comes as the next message."
)


def check_clarification(question: str, options: list[str]) -> None:
    """A question to show the user and 2-5 distinct, non-blank options to pick from (nao's
    `clarification` input, `apps/shared/src/tools/clarification.ts`, options required here)."""
    if not question.strip():
        raise ToolError("question must not be blank")
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise ToolError(
            f"give {MIN_OPTIONS} to {MAX_OPTIONS} options, got {len(options)}; when the question "
            "has no short set of answers, answer instead and state your assumption"
        )
    if any(not option.strip() for option in options):
        raise ToolError("options must not be blank")
    # The picked option comes back as the next message, so two equal options cannot be told apart.
    if len({option.strip() for option in options}) < len(options):
        raise ToolError("options must differ from each other")


def tool_error(exc: QueryBlocked | QueryFailed) -> ToolError:
    if isinstance(exc, QueryBlocked):
        return ToolError(f"{BLOCKED_PREFIX}: {exc}")
    return ToolError(f"Query error: {exc}")


def run_query(gateway: Gateway, sql: str, row_limit: int) -> QueryResult:
    """Run through the gateway; blocks and database errors become readable tool errors."""
    try:
        return gateway.query(sql, row_limit)
    except (QueryBlocked, QueryFailed) as exc:
        raise tool_error(exc) from exc


def run_explain(gateway: Gateway, sql: str, row_limit: int = MAX_ROWS) -> str:
    """The plan for `sql`, which passes the gateway's check first; errors as in run_query."""
    try:
        return plan_text(gateway.explain(sql, row_limit))
    except (QueryBlocked, QueryFailed) as exc:
        raise tool_error(exc) from exc


# Display caps on query_database (SignalPilot query.py): the first DISPLAY_FLOOR rows always show;
# after that, rows keep printing within DISPLAY_CHAR_BUDGET characters, up to DISPLAY_MAX_ROWS.
# `row_limit` still bounds what the database returns; this bounds what the model reads.
DISPLAY_FLOOR = 50
DISPLAY_CHAR_BUDGET = 12_000
DISPLAY_MAX_ROWS = 400

# Rendered size above which list_tables drops the column lists, then tables (SignalPilot
# catalog.py). It sits under Claude Code's 25,000-token MCP output cap.
LIST_TABLES_CHAR_BUDGET = 36_000
LIST_TABLES_DEFAULT_MAX = 200


def capped_note(result: QueryResult, asked: int, max_rows: int) -> str:
    """What a result cut off at the data source's row cap says of it; nothing otherwise (the
    gateway's own cap, MAX_ROWS, says nothing, as before)."""
    if max_rows >= MAX_ROWS or asked <= max_rows or len(result.rows) < max_rows:
        return ""
    return (
        f"\n[ROW CAP] This data source returns at most {max_rows} rows per query (its max_rows); "
        "there may be more. Narrow the query or aggregate."
    )


def format_rows(result: QueryResult) -> str:
    if not result.rows:
        return "Query returned 0 rows"
    lines = [" | ".join(result.columns)]
    lines.append("-" * len(lines[0]))
    shown = 0
    chars = 0
    for row in result.rows:
        if shown >= DISPLAY_MAX_ROWS:
            break
        line = " | ".join(str(value) for value in row)
        if shown >= DISPLAY_FLOOR and chars + len(line) > DISPLAY_CHAR_BUDGET:
            break
        lines.append(line)
        shown += 1
        chars += len(line) + 1
    total = len(result.rows)
    lines.append(f"({total} rows)")
    if total > shown:
        lines.append(
            f"[INCOMPLETE DISPLAY] {total} rows total; only the first {shown} are shown above. "
            f"The remaining {total - shown} rows exist but are not displayed. "
            f"Do not treat the list above as complete: re-run the query with a WHERE filter or "
            f"OFFSET {shown} to see every remaining row before concluding anything about what the "
            "full result does or does not contain."
        )
    return "\n".join(lines)


def missing_line(missing: int) -> str:
    return f"{missing} more tables not shown; narrow with schema= or name_contains="


def render_table_listing(
    tables: list[tuple[str, str]], max_tables: int, columns: bool, budget: int
) -> str:
    """`tables` are (line with columns, line without) in display order. The first `max_tables`
    render within `budget` characters: when the lines with columns do not fit, every line loses
    its columns; when that still does not fit, tables drop from the end. Whenever a table is not
    shown, the last line says how many and how to narrow the call."""
    selected = tables[:max_tables]
    body = [full if columns else compact for full, compact in selected]
    if columns and sum(len(line) + 1 for line in body) > budget:
        body = [compact for _, compact in selected]
    tail_reserve = len(missing_line(len(tables))) + 1
    used = 0
    shown = 0
    for line in body:
        if used + len(line) + 1 > budget - tail_reserve:
            break
        used += len(line) + 1
        shown += 1
    body = body[:shown]
    missing = len(tables) - shown
    if missing > 0:
        body.append(missing_line(missing))
    return "\n".join(body)


def table_kind(kind: str, estimate: int | None) -> str:
    """`base table, ~N rows`; a view, which has no rows of its own, stays `view`."""
    kind = kind.lower()
    return kind if kind == "view" else f"{kind}, {rows_label(estimate)}"


def table_lines(
    schema: str, name: str, kind: str, columns: str, column_count: int, estimate: int | None
) -> tuple[str, str]:
    """The table's line with its column names and its compact line with the column count."""
    head = f"{schema}.{name} ({table_kind(kind, estimate)}"
    return f"{head}): {columns}", f"{head}, {column_count} cols)"


@dataclass(frozen=True)
class Database:
    """One database the server reaches, with what its tools keep of it: a catalog cached per
    database, shared by every tool that looks one of its tables up, and its label tables. The
    listing and the catalog leave out the `hidden` schemas: other runs' scratch schemas
    (`context_tools.other_runs`)."""

    gateway: Gateway
    catalog: LoadCatalog
    labels: LabelTables
    hidden: HiddenSchemas
    # The most rows query_database returns (`[sources.<name>].max_rows`, ticket 144).
    max_rows: int = MAX_ROWS

    def run(self, sql: str, row_limit: int) -> QueryResult:
        return run_query(self.gateway, sql, row_limit)


def open_database(
    gateway: Gateway, labels: LabelTables, hidden: HiddenSchemas, max_rows: int = MAX_ROWS
) -> Database:
    catalog = CatalogCache(partial(run_query, gateway), gateway.catalog_key, hidden=hidden).get
    return Database(gateway, catalog, labels, hidden, max_rows)


def build_server(
    gateway: Gateway,
    config: RunConfig,
    workspace: Path | None = None,
    verifier_tools: bool = True,
    dictionary: Dictionary | None = None,
    sandbox: Sandbox | None = None,
    dbt: DbtExecutor | None = None,
    charts: bool = False,
    own_schema: str | None = None,
    limits: SourceLimits | None = None,
) -> MCPServer:
    """The base tools are served in every config. A layer's tools are registered only when
    `config` turns that layer on, so a run with it off cannot see them; `verifier_tools=False`
    also leaves out the verifier tools for a run whose task type has no verifiers. `workspace`
    bounds the files a tool may read (map_columns). `dictionary` is the prebuilt value
    dictionary the context layer searches (`data-agent run` builds it before the agent starts);
    a server with that layer on and no dictionary builds one now, before it serves. With one
    database the tools take no `database` argument (`database_tools`). With `sandbox` the server
    also serves sandbox_exec (`register_sandbox_tool`); without it the tools are as before. With
    `dbt` (a dbt run with a sandbox) it serves dbt_execute too (`register_dbt_tool`). With
    `charts` (ticket 119) it serves display_chart (`register_chart_tool`). `own_schema` is the
    run's own scratch schema (ticket 140): it stays in the tools' view, and no other run's does
    (`context_tools.other_runs`). `limits` are the data source's (`[sources.<name>]`, ticket 144):
    its row cap and schema choice; None, the code's."""
    dictionaries = {"": dictionary} if dictionary is not None else {}
    server, databases = data_server(
        {"": gateway},
        config,
        workspace,
        dictionaries,
        sandbox,
        charts,
        own_schema,
        {} if limits is None else {"": limits},
    )
    if config.verifiers and verifier_tools:
        db = databases[""]
        register_verifier_tools(server, db.run, db.catalog, workspace, gateway.dialect)
    if dbt is not None:
        register_dbt_tool(server, dbt)
    return server


def build_multi_server(
    gateways: Mapping[str, Gateway],
    config: RunConfig,
    workspace: Path | None = None,
    dictionaries: Mapping[str, Dictionary] | None = None,
    sandbox: Sandbox | None = None,
    charts: bool = False,
    own_schema: str | None = None,
    limits: Mapping[str, SourceLimits] | None = None,
) -> MCPServer:
    """A server over several databases by name (ticket 89): every query and context tool takes
    a required `database` argument, one of the names, and works on that database alone; each
    database has its own catalog, facts, dictionary and label tables. As `build_server`
    otherwise, with a dictionary built now for a database `dictionaries` has none for. No
    verifier tools: they check dbt runs, which have one database. `limits` by name, as
    `build_server`'s."""
    server, _ = data_server(
        gateways, config, workspace, dictionaries or {}, sandbox, charts, own_schema, limits or {}
    )
    return server


def data_server(
    gateways: Mapping[str, Gateway],
    config: RunConfig,
    workspace: Path | None,
    dictionaries: Mapping[str, Dictionary],
    sandbox: Sandbox | None,
    charts: bool = False,
    own_schema: str | None = None,
    limits: Mapping[str, SourceLimits] | None = None,
) -> tuple[MCPServer, dict[str, Database]]:
    """The base tools and, when `config` turns that layer on, the context tools, over `gateways`
    by name (`database_tools`); a database `dictionaries` has no dictionary for gets one built
    now. The label tables of all the databases share one numbering. The clarification tool is
    served in conversation mode only (spec decision 8), which `data-agent run --chat` switches
    on through the environment (`CHAT_ENV`). With `sandbox`, sandbox_exec too; with `charts`,
    display_chart. Either one keeps every query_database result under an id, one store for
    both tools. No other run's scratch schema is in any database's view; `own_schema` is. A
    database's `limits` (by name; the code's when it has none) give its row cap and the schemas
    its catalog, listing and dictionary see."""
    server = MCPServer("data-agent")
    numbers = itertools.count(1)
    each = {name: (limits or {}).get(name, SourceLimits()) for name in gateways}
    hidden = {
        name: other_runs(own_schema, limit.include_schemas, limit.exclude_schemas)
        for name, limit in each.items()
    }
    databases = {
        name: open_database(
            gateway, LabelTables(workspace, numbers), hidden[name], each[name].max_rows
        )
        for name, gateway in gateways.items()
    }
    results = (
        QueryResults(data_files=sandbox is not None, charts=charts)
        if sandbox is not None or charts
        else None
    )
    if sandbox is not None:
        register_sandbox_tool(server, sandbox, results)
    if charts and results is not None:
        register_chart_tool(server, results)
    register_base_tools(server, databases, workspace, results)
    if os.environ.get(CHAT_ENV) == "1":
        register_clarification_tool(server)
    if config.context:
        contexts: dict[str, ContextDatabase] = {}
        for name, db in databases.items():
            dictionary = dictionaries.get(name)
            if dictionary is None:
                timeout = dictionary_timeout(db.gateway)
                build = DictionaryBuild(db.gateway, statement_timeout=timeout, hidden=db.hidden)
                dictionary = build_interruptible(build)
            contexts[name] = ContextDatabase(db.run, db.catalog, db.gateway.dialect, dictionary)
        register_context_tools(server, contexts)
    return server, databases


def register_clarification_tool(server: MCPServer) -> None:
    # Asking reads nothing and writes nothing; the turn's end is the run's PostToolUse hook. One
    # tool for the whole conversation, so no `database` argument with several databases.
    @server.tool(name="clarification", annotations=READ_ONLY)
    def clarification_tool(question: str, options: list[str]) -> str:
        """Ask the user one short question, with 2 to 5 distinct options to pick from.

        Ask only when the question can be read in ways that give different results and
        neither the conversation nor the data settles which one is meant. Otherwise answer,
        and state the reading you chose among your assumptions. Calling this tool ends your
        turn: call no other tool in the same response and write nothing after it. The option
        the user picks, or their own reply, comes as the next message.
        """
        check_clarification(question, options)
        return CLARIFICATION_SENT


def register_sandbox_tool(
    server: MCPServer, sandbox: Sandbox, results: QueryResults | None = None
) -> QueryResults:
    """sandbox_exec (ticket 97, after SignalPilot's `sandbox_exec` and nao's
    `execute_sandboxed_code` `data_files`). One tool for the run, so no `database` argument.
    Returns the store of query results its `data_files` reads, which query_database fills: the
    one given, or a new one."""
    results = QueryResults() if results is None else results

    @server.tool(name="sandbox_exec", annotations=SANDBOX, description=tool_description(sandbox))
    def sandbox_exec(
        command: str,
        timeout_seconds: int = default_timeout(sandbox),
        data_files: list[str] | None = None,
    ) -> str:
        try:
            return execute_tool(sandbox, results, command, timeout_seconds, data_files or [])
        except SandboxError as exc:
            raise ToolError(str(exc)) from exc

    return results


def register_chart_tool(server: MCPServer, results: QueryResults) -> None:
    """display_chart (ticket 119, after nao's `display_chart`), over the query results in
    `results`. One tool for the run, so no `database` argument; it only reads a stored result."""

    @server.tool(name="display_chart", annotations=READ_ONLY, description=CHART_DESCRIPTION)
    def display_chart(
        query_id: str,
        chart_type: ChartType,
        x: str,
        series: Annotated[list[ChartSeries], Field(min_length=1)],
        title: str,
        stacked: bool = False,
    ) -> dict[str, Any]:
        try:
            return chart_payload(results.results, query_id, chart_type, x, series, title, stacked)
        except ChartError as exc:
            raise ToolError(str(exc)) from exc


def register_dbt_tool(server: MCPServer, executor: DbtExecutor) -> None:
    """dbt_execute (ticket 99, after SignalPilot's `dbt_execute`): the run's one way to run dbt
    against the database. It writes, so its calls run one at a time (`SANDBOX`)."""

    @server.tool(
        name="dbt_execute", annotations=SANDBOX, description=dbt_tool_description(executor)
    )
    def dbt_execute(
        command: str,
        select: str = "",
        exclude: str = "",
        full_refresh: bool = False,
        threads: int | None = None,
        project_dir: str = "",
    ) -> str:
        try:
            return executor.execute(
                command,
                select=select,
                exclude=exclude,
                full_refresh=full_refresh,
                threads=threads,
                project_dir=project_dir,
            )
        except DbtExecuteError as exc:
            raise ToolError(str(exc)) from exc


def register_base_tools(
    server: MCPServer,
    databases: Mapping[str, Database],
    workspace: Path | None,
    results: QueryResults | None = None,
) -> None:
    """The tools every config serves, over `databases` by name (`database_tools`). With
    `results` (a run with a sandbox or charts) each successful query_database result is kept
    there and its text ends with the result's id, for sandbox_exec's `data_files` and
    display_chart's `query_id`."""
    tool = database_tools(server, databases)
    # classify_text's label tables by name, with the database's store that holds them.
    label_tables: dict[str, tuple[LabelTables, QueryResult]] = {}

    @tool(annotations=READ_ONLY)
    def query_database(db: Database, sql: str, row_limit: int = 1000) -> str:
        """Run one read-only SQL query (SELECT only) and return the rows as text.

        DDL, DML, multiple statements and file or network functions are blocked. At most
        `row_limit` rows come back (default 1000, max 10000). A query stopped by the timeout
        fails with the query's plan (EXPLAIN) and the indexes on the tables it reads.
        """
        gateway = db.gateway
        # The data source's cap (`[sources.<name>].max_rows`); the gateway's own is MAX_ROWS.
        asked, row_limit = row_limit, min(row_limit, db.max_rows)
        try:
            result = gateway.query(sql, row_limit)
        except QueryTimedOut as exc:
            explain = partial(run_explain, gateway, row_limit=row_limit)
            failure = timeout_report(str(exc), sql, gateway.dialect, explain, db.run)
            raise ToolError(failure) from exc
        except (QueryBlocked, QueryFailed) as exc:
            raise tool_error(exc) from exc
        text = format_rows(result) + capped_note(result, asked, db.max_rows)
        if results is None:
            return text
        return f"{text}\n{results.note(results.add(result))}"

    @tool(annotations=READ_ONLY)
    def list_tables(
        db: Database,
        schema: str | None = None,
        name_contains: str | None = None,
        max_tables: int = LIST_TABLES_DEFAULT_MAX,
        columns: bool = True,
    ) -> str:
        """List the database's tables and views, one per line with their column names.

        Each table shows the database's own row estimate (`~N rows`, from its statistics, not
        a count; `rows unknown` when it has none). Views have no estimate.

        Narrow a large listing. Pass `schema` to keep one schema and `name_contains` to keep
        only tables whose name contains that text (both ignore case). Set `columns=false` for a
        compact listing of names, estimates and column counts. At most `max_tables` tables
        (default 200) appear, in (schema, name) order, and the reply never exceeds about 36,000
        characters: when it would, the tool drops the column lists, then drops tables, and the
        last line says how many tables are missing.
        """
        if max_tables < 1:
            raise ToolError("max_tables must be at least 1")
        tables = db.run(
            LIST_TABLES_SQL.format(shown=db.hidden.shown("t.table_schema")), MAX_ROWS
        ).rows
        estimates = row_estimates(db.run, db.gateway.dialect)
        wanted_schema = (schema or "").strip().lower()
        needle = (name_contains or "").strip().lower()
        lines = [
            table_lines(table_schema, name, kind, cols, n, estimates.get((table_schema, name)))
            for table_schema, name, kind, cols, n in tables
            if (not wanted_schema or table_schema.lower() == wanted_schema)
            and needle in name.lower()
        ]
        if not lines:
            filters = []
            if wanted_schema:
                filters.append(f"schema {wanted_schema!r}")
            if needle:
                filters.append(f"name containing {needle!r}")
            if not filters:
                return "No tables"
            return f"No tables found with {' and '.join(filters)}"
        return render_table_listing(lines, max_tables, columns, LIST_TABLES_CHAR_BUDGET)

    @tool(annotations=READ_ONLY)
    def describe_table(db: Database, table: str) -> str:
        """One table or view in detail, as the database reports it: its row estimate, every
        column with its type and whether it may be null, declared primary and foreign keys, and
        its indexes (name and definition). `table` is schema.table or a bare table name.

        On Postgres each column also shows `n_distinct` and `null_frac` from pg_stats (a negative
        n_distinct is minus the distinct values' share of the rows); a table with no statistics
        says so. DuckDB lists only indexes made with CREATE INDEX: the index behind a primary key
        shows as the key.
        """
        return describe(db.run, db.gateway.dialect, table, db.catalog)

    @tool(annotations=READ_ONLY)
    def explain_query(db: Database, sql: str) -> str:
        """The database's plan for one read-only SQL query, without running it (EXPLAIN, never
        ANALYZE), as the database prints it: each step with its estimated rows, the join methods,
        and the join and filter conditions.

        The query passes the same read-only checks as query_database; the plan is of the query
        as query_database would run it, row cap included.
        """
        return run_explain(db.gateway, sql)

    @tool(
        annotations=READ_ONLY,
        description=description(
            source="`sql` is one read-only SQL query, run through the same gateway as "
            "query_database.",
            stored=f"a table `labels_<n>` that query_labels reads, also saved as "
            f"`{LABELS_DIR}/labels_<n>.csv` in the workspace.",
            resumed="the name of a label table this tool made in this run on the same "
            "database (`labels_<n>`)",
            env=os.environ,
        ),
    )
    def classify_text(
        db: Database,
        sql: str,
        key_column: str,
        text_column: str | list[str],
        categories: list[str],
        instructions: str = "",
        allow_unresolved: bool = False,
        resume_from: str = "",
        retry_unresolved: bool = False,
    ) -> str:
        allowed = check_categories(categories)
        made = label_tables.get(resume_from)
        found = made[1] if made is not None and made[0] is db.labels else None
        resume = resume_of(resume_from, found, retry_unresolved)
        setup = setup_from_env(os.environ)
        rows = fetch_sql(db.run, sql, db.gateway.dialect, key_column)
        classification = classify(
            rows, key_column, text_column, allowed, instructions, setup, allow_unresolved, resume
        )
        name, path = db.labels.add(classification.columns, classification.rows)
        # As made, for a later call's resume_from (the DuckDB table holds some values as text).
        label_tables[name] = (db.labels, QueryResult(classification.columns, classification.rows))
        stored = f"`{name}` (read it with query_labels)"
        if path is not None and workspace is not None:
            stored += f", also the file `{path.relative_to(workspace)}` in the workspace"
        return report(classification, stored)

    @tool(annotations=READ_ONLY)
    def query_labels(db: Database, sql: str, row_limit: int = 1000) -> str:
        """Run one read-only SQL query (DuckDB dialect) over the label tables classify_text made
        in this run (`labels_1`, `labels_2`, ...), and return the rows as text.

        Only the label tables are there, not the database's tables: to combine labels with other
        data, have the classify_text query return the columns you need. The same read-only checks
        and row cap as query_database apply.
        """
        try:
            return format_rows(db.labels.query(sql, row_limit))
        except (QueryBlocked, QueryFailed) as exc:
            raise tool_error(exc) from exc
