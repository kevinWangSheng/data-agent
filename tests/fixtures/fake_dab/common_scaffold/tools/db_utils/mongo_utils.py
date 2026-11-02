"""Stand-in for DAB's MongoDB query module, written for our tests (no DAB code).

Same shape as DAB's: `exec` opens a new client through the module's `MongoClient` name on every
call. Here that name is `fake_pymongo`'s client, whose "server" echoes the query back.
"""

from typing import Any

from common_scaffold.tools.db_utils.fake_pymongo import MongoClient


def exec(db_name: str, query: str) -> list[dict[str, Any]]:
    with MongoClient("mongodb://localhost") as client:
        try:
            return client.find(db_name, query)
        except Exception as e:
            raise ValueError(f"MongoDB query execution error ({type(e).__name__}): {e}") from e
