"""Stand-in for DAB's ListDBTool, written for our tests (no DAB code)."""

import json
import sqlite3
from typing import Any

from common_scaffold.tools.db_utils.db_config import drop_dbs, load_db_clients

DESCRIPTION = "FAKE-DAB list description."


class ListDBTool:
    def __init__(
        self, log_path: str, name: str, db_config_path: str, check_load: bool = True
    ) -> None:
        self.log_path = log_path
        self.name = name
        self.db_config_path = db_config_path
        self.db_clients = load_db_clients(db_config_path)

    def get_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {"db_name": {"type": "string"}},
                    "required": ["db_name"],
                },
            },
        }

    def exec(self, args: dict[str, str]) -> dict[str, Any]:
        with open(self.log_path, "a") as f:
            f.write(json.dumps({"tool_name": self.name, "args": args}) + "\n")
        client = self.db_clients[args["db_name"]]
        if client["db_type"] == "mongo":
            return {"success": True, "result": [client["db_name"]]}
        with sqlite3.connect(f"file:{client['db_path']}?mode=ro", uri=True) as conn:
            names = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {"success": True, "result": [name for (name,) in names]}

    def clean_up(self) -> None:
        drop_dbs(self.db_config_path, self.db_clients)
