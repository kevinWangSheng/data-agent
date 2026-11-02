"""Stand-in for DAB's Postgres query module, written for our tests (no DAB code).

Same shape as DAB's: `exec` creates an engine through the module's `sqlalchemy` name on every
call, with a URL whose `options` make the session read-only. Here that name is `fake_sqlalchemy`,
whose "server" follows the settings the connection was opened with.
"""

from typing import Any

from common_scaffold.tools.db_utils import fake_sqlalchemy as sqlalchemy

READ_ONLY = "options=-c default_transaction_read_only=on"


def exec(db_name: str, sql: str) -> list[dict[str, Any]]:
    url = f"postgresql+psycopg2://fake@localhost/{db_name}?{READ_ONLY}"
    with sqlalchemy.create_engine(url).connect() as conn:
        try:
            return conn.run(sql)
        except Exception as e:
            raise ValueError(f"Postgres error ({type(e).__name__}): {e}") from e
