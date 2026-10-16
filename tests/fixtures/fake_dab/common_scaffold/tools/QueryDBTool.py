"""Stand-in for DAB's QueryDBTool, written for our tests (no DAB code).

Same interface: constructed from a db config, `get_spec()` returns an OpenAI-style function spec,
`exec(args)` returns `{"success": bool, "result": ...}` and appends one JSON line per call to
`log_path`. SQLite databases are queried for real; a "mongo" database echoes the query back.
"""

import json
import sqlite3
from typing import Any

from common_scaffold.tools.db_utils.db_config import load_db_clients

DESCRIPTION = "FAKE-DAB query description."
QUERY_ARG = "FAKE-DAB query argument."


class QueryDBTool:
    def __init__(
        self, log_path: str, name: str, db_config_path: str, check_load: bool = True
    ) -> None:
        self.log_path = log_path
        self.name = name
        self.db_clients = load_db_clients(db_config_path)

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
        if client["db_type"] == "mongo":
            return {"success": True, "result": [{"echo": args["query"]}]}
        try:
            with sqlite3.connect(f"file:{client['db_path']}?mode=ro", uri=True) as conn:
                cursor = conn.execute(args["query"])
                columns = [c[0] for c in cursor.description]
                rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            return {"success": False, "result": f"SQLite error: {e}"}
        return {"success": True, "result": rows}

    def clean_up(self) -> None:
        pass
