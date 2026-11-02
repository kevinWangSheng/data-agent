"""Stand-in for DAB's QueryDBTool, written for our tests (no DAB code).

Same interface: constructed from a db config, `get_spec()` returns an OpenAI-style function spec,
`exec(args)` returns `{"success": bool, "result": ...}` and appends one JSON line per call to
`log_path`. As in DAB, each database type's module under `db_utils` runs the query: SQLite and
DuckDB databases are queried for real; "postgres" and "mongo" ones are fakes (see their modules).
"""

import json
from typing import Any

from common_scaffold.tools.db_utils import duckdb_utils, mongo_utils, postgres_utils, sqlite_utils
from common_scaffold.tools.db_utils.db_config import drop_dbs, load_db_clients, load_dbs

DESCRIPTION = "FAKE-DAB query description."
QUERY_ARG = "FAKE-DAB query argument."


def run(client: dict[str, str], query: str) -> list[dict[str, Any]]:
    match client["db_type"]:
        case "sqlite":
            return sqlite_utils.exec(client["db_path"], query)
        case "duckdb":
            return duckdb_utils.exec(client["db_path"], query)
        case "postgres":
            return postgres_utils.exec(client["db_name"], query)
        case "mongo":
            return mongo_utils.exec(client["db_name"], query)
        case other:
            raise ValueError(f"Unsupported db_type: {other}")


class QueryDBTool:
    def __init__(
        self, log_path: str, name: str, db_config_path: str, check_load: bool = True
    ) -> None:
        self.log_path = log_path
        self.name = name
        self.db_config_path = db_config_path
        self.db_clients = load_db_clients(db_config_path)
        if check_load:
            load_dbs(db_config_path, self.db_clients)

    def get_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "db_name": {"type": "string", "description": "FAKE-DAB db_name argument."},
                        "query": {"type": "string", "description": QUERY_ARG},
                    },
                    "required": ["db_name", "query"],
                },
            },
        }

    def exec(self, args: dict[str, str]) -> dict[str, Any]:
        with open(self.log_path, "a") as f:
            f.write(json.dumps({"tool_name": self.name, "args": args}) + "\n")
        client = self.db_clients.get(args["db_name"])
        if client is None:
            return {"success": False, "result": f"Unknown `db_name`: {args['db_name']}"}
        # As DAB: each type's module runs the query, and a failure becomes the error's text.
        try:
            return {"success": True, "result": run(client, args["query"])}
        except Exception as e:
            return {"success": False, "result": str(e)}

    def clean_up(self) -> None:
        drop_dbs(self.db_config_path, self.db_clients)
