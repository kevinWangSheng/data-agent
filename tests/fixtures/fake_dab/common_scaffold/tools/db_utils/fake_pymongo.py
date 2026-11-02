"""A fake MongoDB client and server for the fake DAB (neither DAB's code nor pymongo's).

`find` echoes the query back. A query with a `"sleep"` field takes that many seconds; a shorter
client `timeoutMS` stops it, as the server does with the `maxTimeMS` pymongo sends.
"""

import json
import time
from types import TracebackType
from typing import Any


class ExecutionTimeout(Exception):
    pass


class MongoClient:
    def __init__(self, uri: str, **options: Any) -> None:
        self.uri = uri
        self.options = options

    def __enter__(self) -> "MongoClient":
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def find(self, db_name: str, query: str) -> list[dict[str, Any]]:
        seconds = float(json.loads(query).get("sleep", 0))
        limit = self.options.get("timeoutMS", 0) / 1000
        if limit and seconds > limit:
            time.sleep(limit)
            raise ExecutionTimeout("operation exceeded time limit")
        time.sleep(seconds)
        return [{"echo": query}]
