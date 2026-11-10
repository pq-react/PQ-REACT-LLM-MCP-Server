-- performance_test — extension columns + natural key.
--
-- The base table and its sample rows come from
-- mcp-ollama_server/database/pqreact_export.sql (upstream shape, no
-- `source` column). The MCP server groups and splits every PQC metric by
-- `source` (cam-context-agility, campaign-b-*, upstream, ...) and the
-- measurement importers (etl/import_*.py) UPSERT on `uniq_natural`, so
-- the table must be brought to this shape before either is used.
-- setup_db.sh applies the same change on the non-docker path; this file
-- is the docker-compose equivalent (mounted into initdb after the seed).
-- Idempotent: safe to re-run.
--
-- `source`     origin tag of each row; see etl/schema_map.md §2
-- `size_kind`  'payload' (datagram/message bytes) | 'key' (KEM pk+ct
--              bytes); disambiguates message_size, see schema_map.md §3
--
-- Live shape on the PQ-REACT testbed (MariaDB 10.6, captured 2026-11-10)
-- for reference. The upstream power/frequency columns cpu_frequency,
-- power_core and power_system had been dropped there by hand; they are
-- kept here because the seed rows populate them and the importers write
-- them only when present.
--
--   algorithm_name varchar(255), message_size int, duration double,
--   cpu_util_pct double, delta_cpu_util double, mem_used double,
--   energy_joules double, power_watts double, cpu_temperature double,
--   security_level tinyint, source varchar(64) DEFAULT 'upstream',
--   size_kind varchar(16) DEFAULT 'payload',
--   UNIQUE KEY uniq_natural (algorithm_name, message_size, size_kind, source)

USE PQREACT;

ALTER TABLE performance_test
  ADD COLUMN IF NOT EXISTS source    VARCHAR(64) DEFAULT 'upstream',
  ADD COLUMN IF NOT EXISTS size_kind VARCHAR(16) DEFAULT 'payload';

UPDATE performance_test SET source = 'upstream' WHERE source IS NULL;
UPDATE performance_test SET size_kind = 'payload' WHERE size_kind IS NULL;

ALTER TABLE performance_test
  ADD UNIQUE KEY IF NOT EXISTS uniq_natural (algorithm_name, message_size, size_kind, source);
