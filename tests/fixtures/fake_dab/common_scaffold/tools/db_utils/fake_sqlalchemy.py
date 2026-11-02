"""The little of SQLAlchemy the fake Postgres module uses, with a fake server behind it (neither
DAB's code nor SQLAlchemy's).

`create_engine(url).connect()` runs the engine's `do_connect` listeners on the connection
parameters, as SQLAlchemy does, then "connects": the server takes its settings from the `-c k=v`
pairs in `options`. It answers two kinds of statement: one reading `pg_settings` for a setting
`WHERE name = '...'`, and one reading a table `sleep_<s>`, which takes `s` seconds unless a shorter
`statement_timeout` (ms) cancels it, as Postgres does. It also answers `EXPLAIN` with a one-line
plan of what follows it, and the catalog reads of `data_agent.db_facts` (picked out by the catalog
each reads) with canned rows for one table, `public.films`.
"""

import re
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any
from urllib.parse import parse_qs, urlsplit


class OperationalError(Exception):
    pass


class Engine:
    def __init__(self, url: str) -> None:
        self.url = url
        self.listeners: list[Callable[..., None]] = []

    def connect(self) -> "Connection":
        parts = urlsplit(self.url)
        cparams: dict[str, Any] = {"dbname": parts.path.lstrip("/")}
        cparams.update({k: v[0] for k, v in parse_qs(parts.query).items()})
        for fn in self.listeners:
            fn(None, None, [], cparams)
        options = str(cparams.get("options") or "")
        return Connection(dict(re.findall(r"-c\s*([\w.]+)=(\S+)", options)))


class _Event:
    @staticmethod
    def listen(target: Engine, identifier: str, fn: Callable[..., None]) -> None:
        assert identifier == "do_connect", identifier
        target.listeners.append(fn)


event = _Event()


def create_engine(url: str) -> Engine:
    return Engine(url)


# Catalog reads -> rows, first match wins: the column catalog joins information_schema.tables too.
CATALOG: list[tuple[str, list[dict[str, Any]]]] = [
    (
        "FROM information_schema.columns AS c",
        [
            {
                "table_schema": "public",
                "table_name": "films",
                "table_type": "BASE TABLE",
                "column_name": "id",
                "data_type": "integer",
            },
        ],
    ),
    (
        "FROM information_schema.columns",
        [{"column_name": "id", "data_type": "integer", "is_nullable": "NO"}],
    ),
    (
        "FROM information_schema.tables",
        [{"table_name": "films", "table_type": "BASE TABLE"}],
    ),
    ("pg_stat_user_tables", [{"nspname": "public", "relname": "films", "coalesce": 1234}]),
    ("FROM pg_constraint", [{"pg_get_constraintdef": "PRIMARY KEY (id)"}]),
    (
        "FROM pg_indexes",
        [
            {
                "current_database": "films",
                "schemaname": "public",
                "tablename": "films",
                "indexdef": "CREATE UNIQUE INDEX films_pkey ON public.films USING btree (id)",
            }
        ],
    ),
    # The search path resolves only `films`; any other name is no table.
    (
        """TO_REGCLASS('"films"')""",
        [{"current_database": "films", "nspname": "public", "relname": "films"}],
    ),
    ("TO_REGCLASS(", []),
    ("FROM pg_stats", [{"attname": "id", "n_distinct": -1.0, "null_frac": 0.0}]),
]


class Connection:
    def __init__(self, settings: dict[str, str]) -> None:
        self.settings = settings

    def __enter__(self) -> "Connection":
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def run(self, sql: str) -> list[dict[str, Any]]:
        if sql.startswith("EXPLAIN "):
            return [{"QUERY PLAN": f"Fake Plan  (rows=42) for: {sql.removeprefix('EXPLAIN ')}"}]
        for marker, rows in CATALOG:
            if marker in sql:
                return [dict(row) for row in rows]
        if setting := re.search(r"FROM pg_settings WHERE name = '([\w.]+)'", sql):
            return [{"setting": self.settings.get(setting[1])}]
        if sleep := re.search(r"FROM sleep_(\d+)", sql):
            seconds = float(sleep[1])
            limit = int(self.settings.get("statement_timeout", "0")) / 1000
            if limit and seconds > limit:
                time.sleep(limit)
                raise OperationalError(
                    "(psycopg2.errors.QueryCanceled) canceling statement due to statement timeout"
                )
            time.sleep(seconds)
            return [{"slept": seconds}]
        raise OperationalError(f"the fake server cannot run: {sql}")
