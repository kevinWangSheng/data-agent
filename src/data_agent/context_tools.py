"""Context-strategy tools (ADR-0003): discover → hydrate, dictionary search, `_id` join candidates.

Contracts copied from ktx (ideas only, no code): `discover` returns references plus a summary of at
most 200 characters; `hydrate` reads one object in full; `dictionary_search` finds a keyword among
the sampled values of categorical columns. Join candidates follow SignalPilot `analyze_project_db`'s
`_id` matching, capped at ktx's 25 candidates per column. All SQL goes through the gateway.

The value dictionary (ADR-0003 update 2026-11-04) is built once before the agent starts, over one
read-only connection that is closed as soon as the build ends, and searched from memory: a search
never touches the database. The sampling rules and their limits are ktx's defaults
(`packages/cli/src/context/scan/data-dictionary.ts`, ktx@49a4ae6, Apache-2.0; see NOTICE): base
tables only, categorical column types, ID-like column names excluded, the first 10,000 rows of each
table, columns with more than 200 distinct sampled values skipped, the 100 most frequent values
kept per column. The build has a total time limit and a per-statement timeout; what is built when
the limit runs out is kept, and the search reports the coverage and why a value was not found
(ktx `dictionary-search.ts`: `searched`, `misses`).
"""

import json
import re
import signal
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from data_agent.db_facts import (
    LoadCatalog,
    RunQuery,
    Table,
    column_line,
    find_table,
    key_lines,
    literal,
    load_catalog,
    quote,
    table_facts,
)
from data_agent.gateway import Gateway, QueryBlocked, QueryFailed, QueryInterrupted, Session

# `literal` is re-exported for verifier_tools, with the other db_facts names it imports here.
__all__ = ["Table", "find_table", "literal", "register_context_tools"]

SUMMARY_LIMIT = 200
JOIN_CANDIDATES_PER_COLUMN = 25
# dictionary_search output bounds: values per column, characters per value, columns in all.
VALUES_PER_COLUMN = 20
VALUE_CHARS = 80
COLUMNS_SHOWN = 50
KEYWORD_CHARS = 200

# Dictionary build rules: ktx `defaultKtxDataDictionarySettings` and `categoricalCandidateTypes`.
CARDINALITY_THRESHOLD = 200
VALUES_STORED = 100
SAMPLE_ROWS = 10_000
EXCLUDE_PATTERNS = (
    "_id$",
    "_uuid$",
    "_key$",
    "_hash$",
    "_token$",
    "^id$",
    "^uuid$",
    "_at$",
    "_date$",
    "_time$",
    "description$",
    "comment$",
    "notes?$",
    "message$",
    "body$",
    "content$",
    "_url$",
    "_path$",
    "email$",
    "^phone",
    "address$",
)
CANDIDATE_TYPES = re.compile(r"^(n?varchar|n?char|n?text|string|character|enum|bool(ean)?)", re.I)
EXCLUDED_NAME = re.compile("|".join(f"(?:{pattern})" for pattern in EXCLUDE_PATTERNS), re.I)
# Build time limits (ticket 42): the whole build, and one statement.
TIME_LIMIT_SECONDS = 60.0
STATEMENT_TIMEOUT_SECONDS = 10.0
NOT_PROFILED_SHOWN = 10


def is_text(data_type: str) -> bool:
    upper = data_type.upper()
    return "CHAR" in upper or "TEXT" in upper or upper == "STRING"


def terms_of(text: str) -> list[str]:
    return [term for term in re.split(r"[\W_]+", text.lower()) if term]


def singular(term: str) -> str:
    return term[:-1] if len(term) > 3 and term.endswith("s") else term


def matches(term: str, identifier: str) -> bool:
    """Substring match, also on the singular, so "events" finds event_name."""
    name = identifier.lower()
    return term in name or singular(term) in name


def score(table: Table, terms: list[str]) -> tuple[int, list[str]]:
    """Per term: 3 if the table name holds it whole, 2 if only its singular, 1 if a column does."""
    total = 0
    matched_columns: list[str] = []
    name = table.name.lower()
    for term in terms:
        if term in name:
            total += 3
        elif singular(term) in name:
            total += 2
        hits = [column for column, _ in table.columns if matches(term, column)]
        if hits:
            total += 1
            matched_columns.extend(column for column in hits if column not in matched_columns)
    return total, matched_columns


def summarize(table: Table, matched_columns: list[str]) -> str:
    """Kind and column count, then matching columns, then as many other columns as fit."""
    text = f"{table.kind}, {len(table.columns)} columns"
    if matched_columns:
        text += "; matching: " + ", ".join(matched_columns)
    rest = [column for column, _ in table.columns if column not in matched_columns]
    if rest:
        text += "; columns: " + ", ".join(rest)
    if len(text) > SUMMARY_LIMIT:
        text = text[: SUMMARY_LIMIT - 1].rstrip(", ") + "…"
    return text


def named_after(prefix: str) -> set[str]:
    """Table names a `<prefix>_id` column points at, as in SignalPilot (customers, stg_customer)."""
    plurals = {f"{prefix}s"} | ({f"{prefix[:-1]}ies"} if prefix.endswith("y") else set())
    bases = {prefix, *plurals}
    return bases | {f"stg_{base}" for base in bases} | {f"dim_{base}" for base in bases}


def join_candidates(catalog: list[Table], table: Table) -> list[str]:
    """For each `<x>_id` column: tables named after x (joined on the same column or their `id`)
    first, then other tables that have the same `_id` column."""
    lines: list[str] = []
    for column, _ in table.columns:
        low = column.lower()
        if not low.endswith("_id") or low == "_id":
            continue
        names = named_after(low.removesuffix("_id"))
        lookups: list[str] = []
        shared: list[str] = []
        for other in catalog:
            if other is table:
                continue
            keys = {name.lower(): name for name, _ in other.columns}
            if other.name.lower() in names and (key := keys.get(low) or keys.get("id")):
                lookups.append(f"{column} -> {other.ref}.{key}")
            elif low in keys:
                shared.append(f"{column} -> {other.ref}.{keys[low]}")
        lines.extend([*lookups, *shared][:JOIN_CANDIDATES_PER_COLUMN])
    return lines


def sample_sql(table: Table, columns: list[str]) -> str:
    """One statement per table: the first SAMPLE_ROWS rows of its candidate columns. A plain
    LIMIT, never USING SAMPLE or TABLESAMPLE, which may read the whole table."""
    return (
        f"SELECT {', '.join(quote(c) for c in columns)} FROM {table.sql_name} LIMIT {SAMPLE_ROWS}"
    )


@dataclass(frozen=True)
class Entry:
    """One sampled column: its most frequent values with their counts in the sample, most frequent
    first, and the number of distinct values the sample held."""

    ref: str
    column: str
    distinct: int
    values: list[tuple[str, int]]


@dataclass(frozen=True)
class Coverage:
    """What the dictionary covers (ktx `searched.coverage`), reported with every search."""

    tables_total: int  # base tables in the catalog
    tables_profiled: int  # read in full or with no candidate columns; the rest are not_profiled
    columns_profiled: int
    sample_rows: int
    elapsed_seconds: float
    timed_out: bool
    not_profiled: list[str] = field(default_factory=list)  # refs, in catalog order
    # Skipped columns of the sampled tables, by reason: a categorical type with an ID-like name,
    # and more than CARDINALITY_THRESHOLD distinct values in the sample.
    columns_excluded: int = 0
    columns_high_cardinality: int = 0
    error: str | None = None  # the build could not read the catalog: nothing was sampled

    def line(self) -> str:
        if self.error is not None:
            return (
                f"Coverage: no dictionary; the build failed after {self.elapsed_seconds:.1f} s "
                f"({self.error})"
            )
        text = (
            f"Coverage: {self.tables_profiled} of {self.tables_total} base tables sampled "
            f"(up to {self.sample_rows} rows each), {self.columns_profiled} columns kept, "
            f"{self.columns_excluded} skipped by name (ID-like), {self.columns_high_cardinality} "
            f"over {CARDINALITY_THRESHOLD} distinct values; built in {self.elapsed_seconds:.1f} s"
        )
        if self.timed_out:
            text += f"; stopped by the {TIME_LIMIT_SECONDS:.0f} s time limit"
        if self.not_profiled:
            text += f"; {len(self.not_profiled)} tables not sampled"
        return text


@dataclass(frozen=True)
class Dictionary:
    coverage: Coverage
    entries: list[Entry]

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), ensure_ascii=False))

    @classmethod
    def load(cls, path: Path) -> "Dictionary":
        data = json.loads(path.read_text())
        entries = [
            Entry(e["ref"], e["column"], e["distinct"], [(v, n) for v, n in e["values"]])
            for e in data["entries"]
        ]
        return cls(Coverage(**data["coverage"]), entries)

    def search(self, keyword: str) -> str:
        """Hits as `schema.table.column: 'value' (count), …`, then why there were none, then the
        coverage line. Case-insensitive substring match over the sampled values."""
        needle = keyword.lower()
        lines: list[str] = []
        for entry in self.entries:
            hits = [(value, n) for value, n in entry.values if needle in value.lower()]
            if not hits:
                continue
            shown = ", ".join(f"{short(value)} ({n})" for value, n in hits[:VALUES_PER_COLUMN])
            extra = len(hits) - VALUES_PER_COLUMN
            more = f", … {extra} more" if extra > 0 else ""
            lines.append(f"{entry.ref}.{entry.column}: {shown}{more}")
            if len(lines) > COLUMNS_SHOWN:
                lines = lines[:COLUMNS_SHOWN]
                lines.append(f"… more columns match {keyword!r}; use a more specific keyword")
                break
        if not lines:
            lines.extend(self.misses(keyword))
        lines.append(self.coverage.line())
        return "\n".join(lines)

    def misses(self, keyword: str) -> list[str]:
        """ktx `misses`: `value_not_in_sample` for the tables that were sampled, `not_profiled`
        for those that were not. Either way the agent is told to confirm with query_database."""
        coverage = self.coverage
        lines: list[str] = []
        if coverage.tables_profiled > 0:
            lines.append(
                f"No sampled value contains {keyword!r} (value_not_in_sample). The dictionary "
                f"holds the {VALUES_STORED} most frequent values of each categorical column in "
                f"the first {coverage.sample_rows} rows of a table, and never ID-like columns "
                "(_id, _key, _uuid, email, …): confirm with query_database on the column you "
                "expect, e.g. SELECT DISTINCT col FROM t WHERE lower(col) LIKE '%…%'."
            )
        if coverage.error is not None:
            lines.append(
                f"No table was sampled (not_profiled): {coverage.error}. Use query_database on "
                "the column you expect."
            )
        elif coverage.not_profiled:
            shown = ", ".join(coverage.not_profiled[:NOT_PROFILED_SHOWN])
            rest = len(coverage.not_profiled) - NOT_PROFILED_SHOWN
            more = f", … {rest} more" if rest > 0 else ""
            lines.append(
                f"{len(coverage.not_profiled)} base tables were not sampled (not_profiled): "
                f"{shown}{more}. Use query_database on them directly."
            )
        return lines


def short(value: object) -> str:
    text = str(value)
    return repr(text if len(text) <= VALUE_CHARS else text[:VALUE_CHARS] + "…")


@dataclass
class TableSample:
    """One table's sample: its dictionary entries and the columns skipped, by reason."""

    entries: list[Entry] = field(default_factory=list)
    excluded: int = 0  # categorical type, ID-like name
    high_cardinality: int = 0


def sample_table(session: Session, table: Table) -> TableSample:
    sampled = TableSample()
    columns: list[str] = []
    for column, data_type in table.columns:
        if not CANDIDATE_TYPES.match(data_type):
            continue
        if EXCLUDED_NAME.search(column):
            sampled.excluded += 1
        else:
            columns.append(column)
    if not columns:
        return sampled
    rows = session.query(sample_sql(table, columns), SAMPLE_ROWS).rows
    for index, column in enumerate(columns):
        counts = Counter(str(row[index]) for row in rows if row[index] is not None)
        if len(counts) > CARDINALITY_THRESHOLD:
            sampled.high_cardinality += 1
        elif counts:
            entry = Entry(table.ref, column, len(counts), counts.most_common(VALUES_STORED))
            sampled.entries.append(entry)
    return sampled


class DictionaryCancelled(Exception):
    """`cancel` was called: the statement was interrupted and the connection is closed."""


class DictionaryBuild:
    """One build over one read-only session: base tables in (schema, name) order, until all are
    read or `time_limit` runs out. The limit holds mid-statement: a timer interrupts the session
    when it is reached, and what was built by then is kept. `cancel` (from any thread, before or
    during `run`) interrupts the running statement; `run` then closes the session and raises
    DictionaryCancelled."""

    def __init__(
        self,
        gateway: Gateway,
        time_limit: float = TIME_LIMIT_SECONDS,
        statement_timeout: float = STATEMENT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.gateway = gateway
        self.time_limit = time_limit
        self.statement_timeout = statement_timeout
        self.clock = clock
        self._cancel = threading.Event()
        self._expired = threading.Event()
        self._session: Session | None = None
        self._lock = threading.Lock()

    def cancel(self) -> None:
        self._cancel.set()
        self._interrupt()

    def _expire(self) -> None:
        self._expired.set()
        self._interrupt()

    def _interrupt(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.interrupt()

    def run(self) -> Dictionary:
        started = self.clock()
        if self._cancel.is_set():
            raise DictionaryCancelled
        # The timer runs from here: connecting (a Postgres connect waits up to 10 s) counts too.
        timer = threading.Timer(self.time_limit, self._expire)
        timer.start()
        try:
            session = self.gateway.session(self.statement_timeout)
        except QueryFailed as exc:
            timer.cancel()
            return self.failed(str(exc), started)
        with self._lock:
            self._session = session
        try:
            if self._expired.is_set():
                return self.failed("the time limit ran out while connecting", started)
            tables = [t for t in load_catalog(session.query) if t.kind == "table"]
            return self.sample(session, sorted(tables, key=lambda t: (t.schema, t.name)), started)
        except QueryInterrupted:
            if self._cancel.is_set():
                raise DictionaryCancelled from None
            return self.failed("the time limit ran out while reading the catalog", started)
        except (QueryBlocked, QueryFailed) as exc:
            return self.failed(str(exc), started)
        finally:
            timer.cancel()
            with self._lock:
                self._session = None
            session.close()
            if self._cancel.is_set():
                raise DictionaryCancelled

    def sample(self, session: Session, tables: list[Table], started: float) -> Dictionary:
        entries: list[Entry] = []
        not_profiled: list[str] = []
        excluded = high_cardinality = 0
        timed_out = False
        for table in tables:
            if self._cancel.is_set():
                raise DictionaryCancelled
            if timed_out or self.clock() - started >= self.time_limit:
                timed_out = True
                not_profiled.append(table.ref)
                continue
            try:
                sampled = sample_table(session, table)
            except QueryInterrupted:
                if self._cancel.is_set():
                    raise DictionaryCancelled from None
                timed_out = True  # the timer stopped this statement; the rest are not sampled
                not_profiled.append(table.ref)
                continue
            except (QueryBlocked, QueryFailed):
                # A statement that timed out or failed: the table is left out, the build goes on.
                not_profiled.append(table.ref)
                continue
            entries.extend(sampled.entries)
            excluded += sampled.excluded
            high_cardinality += sampled.high_cardinality
        coverage = Coverage(
            tables_total=len(tables),
            tables_profiled=len(tables) - len(not_profiled),
            columns_profiled=len(entries),
            sample_rows=SAMPLE_ROWS,
            elapsed_seconds=self.clock() - started,
            timed_out=timed_out,
            not_profiled=not_profiled,
            columns_excluded=excluded,
            columns_high_cardinality=high_cardinality,
        )
        return Dictionary(coverage, entries)

    def failed(self, error: str, started: float) -> Dictionary:
        elapsed = self.clock() - started
        timed_out = self._expired.is_set()
        coverage = Coverage(0, 0, 0, SAMPLE_ROWS, elapsed, timed_out, error=error)
        return Dictionary(coverage, [])


def build_interruptible(build: DictionaryBuild) -> Dictionary:
    """Run the build on a worker thread while the main thread waits. SIGINT and SIGTERM cancel
    it (the statement is interrupted and the connection closed) and are then raised as
    KeyboardInterrupt and SystemExit(143), so the process exits as it would have without the
    build. Called from the main thread; elsewhere the signals keep their current handlers."""
    done: list[Dictionary | BaseException] = []

    def work() -> None:
        try:
            done.append(build.run())
        except BaseException as exc:
            done.append(exc)

    received: list[int] = []

    def on_signal(signum: int, _frame: object) -> None:
        received.append(signum)
        build.cancel()

    previous: dict[int, Any] = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, on_signal)
    worker = threading.Thread(target=work, name="dictionary-build", daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            worker.join(0.1)
    except KeyboardInterrupt:
        build.cancel()
        worker.join()
        raise
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if received:
        if signal.SIGINT in received:
            raise KeyboardInterrupt
        raise SystemExit(128 + signal.SIGTERM)
    [result] = done
    if isinstance(result, BaseException):
        raise result
    return result


def register_context_tools(
    server: MCPServer,
    run: RunQuery,
    catalog: LoadCatalog,
    dialect: str,
    dictionary: Dictionary,
) -> None:
    """Add the context-strategy tools to `server`; called only when the run config turns the
    context layer on. `catalog` is the server's cached catalog, shared with describe_table and
    the verifier tools; `dialect` is the gateway's, for the table facts hydrate reads;
    `dictionary` is the prebuilt value dictionary dictionary_search answers from."""

    @server.tool()
    def discover(query: str, limit: int = 10) -> dict[str, list[dict[str, Any]]]:
        """Search table, view and column names; returns refs with a short summary, best first.

        Each result is `{"ref", "summary"}`; the summary has at most 200 characters, so a wide
        table's column list is cut short. Call `hydrate(ref)` for one object's full detail.
        `limit` is 1-50.
        """
        terms = terms_of(query)
        if not terms:
            raise ToolError("Error: query has no words to search for")
        scored = [(*score(table, terms), table) for table in catalog()]
        ranked = sorted(
            (item for item in scored if item[0] > 0), key=lambda item: (-item[0], item[2].ref)
        )
        return {
            "results": [
                {"ref": table.ref, "summary": summarize(table, matched)}
                for _, matched, table in ranked[: max(1, min(limit, 50))]
            ]
        }

    @server.tool()
    def hydrate(ref: str) -> str:
        """Read one table or view in full: exact row count, every column with its type and
        whether it may be null, join candidates found from `<x>_id` columns, then the declared
        keys and the indexes. `ref` is a ref from discover (schema.table) or a bare table name."""
        tables = catalog()
        table = find_table(tables, ref)
        count = run(f"SELECT count(*) FROM {table.sql_name}", 1).rows[0][0]
        facts = table_facts(run, dialect, table)
        lines = [f"{table.ref} ({table.kind}, {count} rows, {len(table.columns)} columns)"]
        lines.extend(column_line(column) for column in facts.columns)
        lines.append("Join candidates (by _id column name; check the key values before joining):")
        lines.extend(f"  {line}" for line in join_candidates(tables, table) or ["none found"])
        lines.extend(key_lines(facts))
        return "\n".join(lines)

    @server.tool()
    def dictionary_search(keyword: str) -> str:
        """Find real values of categorical columns that contain `keyword` (ignores case), with
        their counts, to match dirty keys and enum spellings before filtering or joining.

        Searches a dictionary sampled before the run (the 100 most frequent values of each
        text, enum or boolean column in the first 10,000 rows of each base table; ID-like
        columns such as `*_id`, `*_key`, `email` are never sampled), so it answers at once and
        never reads the database. Every answer ends with the coverage (tables and columns
        sampled, whether the time limit cut the build short); a miss says whether the value
        was not in the sample or its table was not sampled, and IDs or rare values must be
        confirmed with query_database. Shows up to 20 values per column and 50 columns.
        """
        if not keyword.strip():
            raise ToolError("Error: keyword is empty")
        if len(keyword) > KEYWORD_CHARS:
            raise ToolError(f"Error: keyword is longer than {KEYWORD_CHARS} characters")
        return dictionary.search(keyword)
