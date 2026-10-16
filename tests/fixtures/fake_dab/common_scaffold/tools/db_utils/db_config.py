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
