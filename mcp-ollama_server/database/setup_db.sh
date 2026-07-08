#!/bin/bash
# Idempotent DB provisioning for PQ-REACT MCP server.
# Reads MARIADB_ROOT_PASSWORD + MARIADB_PASSWORD from env (no defaults).
# Skips dump load if PQREACT.performance_test already has rows.
set -e

ROOT_PASS="${MARIADB_ROOT_PASSWORD:-${1:-}}"
DB_NAME="${MARIADB_DATABASE:-PQREACT}"
DB_USER="${MARIADB_USER:-pqreact}"
DB_PASS="${MARIADB_PASSWORD:-}"
DUMP_FILE="${DUMP_FILE:-$(dirname "$0")/pqreact_export.sql}"

if [ -z "$ROOT_PASS" ]; then
    echo "Error: MARIADB_ROOT_PASSWORD env var (or arg 1) required" >&2
    exit 1
fi
if [ -z "$DB_PASS" ]; then
    echo "Error: MARIADB_PASSWORD env var required" >&2
    exit 1
fi

CLIENT="$(command -v mariadb || command -v mysql)"
if [ -z "$CLIENT" ]; then
    echo "Error: neither mariadb nor mysql CLI found in PATH" >&2
    exit 2
fi

echo "Provisioning database $DB_NAME and user $DB_USER..."
"$CLIENT" -u root -p"$ROOT_PASS" <<SQL
CREATE DATABASE IF NOT EXISTS $DB_NAME CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'%' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON $DB_NAME.* TO '$DB_USER'@'%';
FLUSH PRIVILEGES;
SQL

EXISTING=$("$CLIENT" -u root -p"$ROOT_PASS" -N -e \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB_NAME' AND table_name='performance_test'" 2>/dev/null || echo 0)

if [ "$EXISTING" -gt 0 ]; then
    echo "performance_test table already exists. Skipping dump load (idempotent)."
else
    echo "Importing dump $DUMP_FILE..."
    "$CLIENT" -u root -p"$ROOT_PASS" "$DB_NAME" < "$DUMP_FILE"
    echo "Dump imported."
fi

# Ensure extension columns + unique key for idempotent ETL
"$CLIENT" -u root -p"$ROOT_PASS" "$DB_NAME" <<SQL
ALTER TABLE performance_test
  ADD COLUMN IF NOT EXISTS source VARCHAR(64) DEFAULT 'upstream',
  ADD COLUMN IF NOT EXISTS size_kind VARCHAR(16) DEFAULT 'payload';
UPDATE performance_test SET source='upstream' WHERE source IS NULL;
-- Unique key on the natural composite so ETL upserts are idempotent.
-- IF NOT EXISTS not supported for ADD KEY in MariaDB 10.6, so guard manually.
SQL

# Add unique key only if it doesn't already exist (idempotent)
HAS_KEY=$("$CLIENT" -u root -p"$ROOT_PASS" -N -e \
    "SELECT COUNT(*) FROM information_schema.statistics
     WHERE table_schema='$DB_NAME'
       AND table_name='performance_test'
       AND index_name='uniq_natural'" 2>/dev/null || echo 0)
if [ "$HAS_KEY" -eq 0 ]; then
    echo "Adding uniq_natural unique key..."
    "$CLIENT" -u root -p"$ROOT_PASS" "$DB_NAME" <<SQL
ALTER TABLE performance_test
  ADD UNIQUE KEY uniq_natural (algorithm_name, message_size, size_kind, source);
SQL
fi

echo "Done."
