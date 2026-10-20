"""Stand-in for DAB's `db_config.load_db_clients`, written for our tests (no DAB code).

Same contract: read `db_clients` from the config file and resolve each file path against the
config's directory. Tests write the config as JSON, which is also valid YAML.
"""

import json
import os


def load_db_clients(config_file: str) -> dict[str, dict[str, str]]:
    with open(config_file) as f:
        db_clients = json.load(f)["db_clients"]
    for client in db_clients.values():
        for key in ("db_path", "dump_folder", "sql_file"):
            if key in client:
                client[key] = os.path.join(os.path.dirname(config_file), client[key])
    return db_clients


# Not in DAB: what the fake tools load and drop is written next to the config, so tests can see
# who loaded a database and when.
LOAD_LOG = "loads.jsonl"
LOADED_TYPES = ("mongo", "postgres")  # the types DAB loads from a dump; files are used in place


def record(config_file: str, action: str, db_name: str) -> None:
    with open(os.path.join(os.path.dirname(config_file), LOAD_LOG), "a") as f:
        f.write(json.dumps({"action": action, "db_name": db_name}) + "\n")


def load_dbs(config_file: str, db_clients: dict[str, dict[str, str]]) -> None:
    """As DAB's query tool with `check_load`: load each dump. A client with `load_error` fails to
    load with that message."""
    for client in db_clients.values():
        if client["db_type"] in LOADED_TYPES:
            if "load_error" in client:
                raise RuntimeError(client["load_error"])
            record(config_file, "load", client["db_name"])


def drop_dbs(config_file: str, db_clients: dict[str, dict[str, str]]) -> None:
    """As DAB's query and list tools' `clean_up`: drop each loaded database. A client with
    `drop_error` fails to drop with that message."""
    for client in db_clients.values():
        if client["db_type"] in LOADED_TYPES:
            if "drop_error" in client:
                raise RuntimeError(client["drop_error"])
            record(config_file, "drop", client["db_name"])
