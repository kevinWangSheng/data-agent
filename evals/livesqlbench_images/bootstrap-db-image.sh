#!/bin/bash
# From LiveSQLBench @ e15cd22, LiveSQLBench-CLI/adapters/livesqlbench/template/environment/
# bootstrap-db-image.sh (Apache License 2.0, LiveSQLBench-CLI/LICENSE; see NOTICE). Modified for
# data-agent (ticket 128, .scratch/livesqlbench/spec.md U6):
# - no SUPERUSER with upstream's public password (root/123123): the verifier's write role
#   lsb_writer gets its password from the build secret lsb_write_password, read inside psql so it
#   is on no command line; `root` exists without LOGIN only, for dumps that hand tables to it;
# - the agent's role lsb_reader can connect and SELECT, nothing else: PUBLIC loses CREATE on
#   every schema and CONNECT/TEMPORARY on `postgres`, `template1` and the template database,
#   and the template's grants and default
#   privileges (copied with each clone) give the reader SELECT on tables now and later;
# - the template's table and column counts (upstream evaluation/check_db_metadata.py's) go to
#   /opt/livesqlbench/counts.txt for `evals livesqlbench prepare` to check.
set -euo pipefail

export PGDATA="${PGDATA:-/var/lib/postgresql/livesqlbench-data}"
export SELECTED_DB_FILE="${SELECTED_DB_FILE:-/opt/livesqlbench/db_seed/db_name.txt}"
export DB_DUMP_DIR="${DB_DUMP_DIR:-/opt/livesqlbench/db_seed/db_dump}"
export PREPROCESS_SQL_FILE="${PREPROCESS_SQL_FILE:-/opt/livesqlbench/db_seed/preprocess.sql}"
export TABLE_ORDER_FILE="${TABLE_ORDER_FILE:-/opt/livesqlbench/db_seed/table_order.txt}"
SECRET_FILE=/run/secrets/lsb_write_password

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=init-db.sh
source "${SCRIPT_DIR}/livesqlbench-init-db.sh"

selected_database="$(read_selected_database)"

if ! grep -qE '^[A-Za-z0-9]{24,}$' "${SECRET_FILE}"; then
    echo "[bootstrap-db-image.sh] ERROR: the build secret lsb_write_password is missing or not 24+ alphanumerics" >&2
    exit 1
fi

mkdir -p "${PGDATA}"
chmod 700 "${PGDATA}"

initdb -D "${PGDATA}" --username=postgres --auth-local=trust --auth-host=scram-sha-256
{
    echo "listen_addresses = '*'"
    echo "port = 5432"
} >> "${PGDATA}/postgresql.conf"
echo "host all all all scram-sha-256" >> "${PGDATA}/pg_hba.conf"

pg_ctl -D "${PGDATA}" -o "-c listen_addresses=''" -w start
cleanup() {
    pg_ctl -D "${PGDATA}" -m fast -w stop >/dev/null 2>&1 || true
}
trap cleanup EXIT

psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres <<EOSQL
\\set writer_password \`cat ${SECRET_FILE}\`
CREATE ROLE lsb_writer WITH LOGIN SUPERUSER PASSWORD :'writer_password';
CREATE ROLE lsb_reader WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD 'lsb_reader';
ALTER ROLE lsb_reader SET default_transaction_read_only = on;
CREATE ROLE root WITH NOLOGIN;
REVOKE CONNECT, TEMPORARY ON DATABASE postgres FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
EOSQL
psql -v ON_ERROR_STOP=1 --username postgres --dbname template1 \
    -c "REVOKE CREATE ON SCHEMA public FROM PUBLIC;"

create_template_database "${selected_database}"
# Only the agent's clone opens CONNECT to lsb_reader (db-entrypoint.sh); the templates stay shut.
psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres <<EOSQL
REVOKE CONNECT, TEMPORARY ON DATABASE template1 FROM PUBLIC;
REVOKE CONNECT, TEMPORARY ON DATABASE "${selected_database}_template" FROM PUBLIC;
EOSQL

psql -v ON_ERROR_STOP=1 --username postgres --dbname "${selected_database}_template" <<'EOSQL'
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
        EXECUTE format('GRANT SELECT ON ALL SEQUENCES IN SCHEMA %I TO lsb_reader', s);
        EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE postgres, root IN SCHEMA %I GRANT SELECT ON TABLES TO lsb_reader', s);
    END LOOP;
END
$$;
EOSQL

base_tables="SELECT table_schema, table_name FROM information_schema.tables
    WHERE table_schema NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
      AND table_type = 'BASE TABLE'"
tables="$(psql -tA -v ON_ERROR_STOP=1 --username postgres --dbname "${selected_database}_template" \
    -c "SELECT count(*) FROM (${base_tables}) t")"
columns="$(psql -tA -v ON_ERROR_STOP=1 --username postgres --dbname "${selected_database}_template" \
    -c "SELECT count(*) FROM information_schema.columns c JOIN (${base_tables}) t USING (table_schema, table_name)")"
echo "${tables} ${columns}" > /opt/livesqlbench/counts.txt

trap - EXIT
cleanup

echo "[bootstrap-db-image.sh] Preloaded PostgreSQL template ready for ${selected_database}: ${tables} tables, ${columns} columns"
