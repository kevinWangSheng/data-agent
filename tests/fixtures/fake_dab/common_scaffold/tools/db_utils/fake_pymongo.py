"""A fake MongoDB client and server for the fake DAB (neither DAB's code nor pymongo's).

`find` echoes the query back. A query with a `"sleep"` field takes that many seconds; a shorter
client `timeoutMS` stops it, as the server does with the `maxTimeMS` pymongo sends (with
`"client_side": true`, the client's socket gives up first, as pymongo's does). A query with
`"unreachable"` fails after that many seconds with a server-selection error, whatever the timeout.
"""

import json
import time
from types import TracebackType
from typing import Any


class ExecutionTimeout(Exception):
    pass


class NetworkTimeout(Exception):
    pass


class ServerSelectionTimeoutError(Exception):
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
        request = json.loads(query)
        if "unreachable" in request:
            time.sleep(float(request["unreachable"]))
            raise ServerSelectionTimeoutError("localhost:27017: [Errno 61] Connection refused")
        seconds = float(request.get("sleep", 0))
        limit = self.options.get("timeoutMS", 0) / 1000
        if limit and seconds > limit:
            time.sleep(limit)
            if request.get("client_side"):
                raise NetworkTimeout("localhost:27017: timed out")
            raise ExecutionTimeout("operation exceeded time limit")
        time.sleep(seconds)
        return [{"echo": query}]
