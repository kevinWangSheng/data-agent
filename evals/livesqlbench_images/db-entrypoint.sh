#!/bin/bash
# From LiveSQLBench @ e15cd22, LiveSQLBench-CLI/adapters/livesqlbench/template/environment/
# db-entrypoint.sh (Apache License 2.0, LiveSQLBench-CLI/LICENSE; see NOTICE). Modified for
# data-agent (ticket 128, .scratch/livesqlbench/spec.md U6): after the clone and the preprocess
# SQL, the agent's database is closed to PUBLIC (a clone does not copy database-level grants) and
# open to the read-only role lsb_reader for CONNECT, which also gets SELECT on tables the
# preprocess SQL made.
set -euo pipefail

if [[ $# -eq 0 ]]; then
    set -- postgres
fi

export PGDATA="${PGDATA:-/var/lib/postgresql/livesqlbench-data}"
export SELECTED_DB_FILE="${SELECTED_DB_FILE:-/docker-entrypoint-initdb.d/db_assets/db_name.txt}"
export PREPROCESS_SQL_FILE="${PREPROCESS_SQL_FILE:-/docker-entrypoint-initdb.d/db_assets/preprocess.sql}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=init-db.sh
source "${SCRIPT_DIR}/livesqlbench-init-db.sh"

if [[ "$1" != "postgres" ]]; then
    exec "$@"
fi

if [[ ! -s "${PGDATA}/PG_VERSION" ]]; then
    echo "[db-entrypoint.sh] ERROR: Expected preloaded PGDATA at ${PGDATA}" >&2
    exit 1
fi

selected_database="$(read_selected_database)"
pg_ctl -D "${PGDATA}" -o "-c listen_addresses=''" -w start
cleanup() {
    pg_ctl -D "${PGDATA}" -m fast -w stop >/dev/null 2>&1 || true
}
trap cleanup EXIT

if ! template_database_exists "${selected_database}"; then
    echo "[db-entrypoint.sh] ERROR: Missing template database ${selected_database}_template in preloaded image" >&2
    exit 1
fi

clone_template_to_agent_database "${selected_database}"
execute_preprocess_sqls "${selected_database}"

psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres <<EOSQL
REVOKE CONNECT, TEMPORARY ON DATABASE "${selected_database}" FROM PUBLIC;
GRANT CONNECT ON DATABASE "${selected_database}" TO lsb_reader;
EOSQL
psql -v ON_ERROR_STOP=1 --username postgres --dbname "${selected_database}" <<'EOSQL'
DO $$
DECLARE s text;
BEGIN
    FOR s IN
        SELECT nspname FROM pg_namespace
        WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
          AND nspname NOT LIKE 'pg_temp_%' AND nspname NOT LIKE 'pg_toast_temp_%'
    LOOP
        EXECUTE format('REVOKE CREATE ON SCHEMA %I FROM PUBLIC', s);
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO lsb_reader', s);
        EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO lsb_reader', s);
    END LOOP;
END
$$;
EOSQL

trap - EXIT
cleanup

exec "$@"
