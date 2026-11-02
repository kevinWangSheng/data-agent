"""The little of SQLAlchemy the fake Postgres module uses, with a fake server behind it (neither
DAB's code nor SQLAlchemy's).

`create_engine(url).connect()` runs the engine's `do_connect` listeners on the connection
parameters, as SQLAlchemy does, then "connects": the server takes its settings from the `-c k=v`
pairs in `options`. It answers two kinds of statement: one reading `pg_settings` for a setting
`WHERE name = '...'`, and one reading a table `sleep_<s>`, which takes `s` seconds unless a shorter
`statement_timeout` (ms) cancels it, as Postgres does.
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
