# Schema Mapping — Our Measurements → `performance_test`

How each existing data source populates the upstream `performance_test` table after the patched schema (`source` + `size_kind` columns) is in place.

> Captured from the testbed deploy bundle (2026-11-10). The extension columns and the `uniq_natural` key are applied by [`schema/performance_test.sql`](schema/performance_test.sql) (docker compose) or `setup_db.sh` (bare host). On the testbed DB the upstream `cpu_frequency`, `power_core` and `power_system` columns had been dropped by hand; `_db.upsert()` writes only the columns the live table has, so the importers work with either shape.
>
> Two further `source` values exist in the testbed data whose producers are not part of this repository: `cam-context-agility` (written by the CAM / Context Agility Manager hooks) and `campaign-b-qujata-live` (written by the locally modified QUJATA API `analyze_service`).

## Target schema (post-patch)

```sql
CREATE TABLE performance_test (
    algorithm_name   VARCHAR(255),
    message_size     INT,                 -- semantics depends on size_kind
    duration         DOUBLE,              -- ms
    cpu_util_pct     DOUBLE,
    delta_cpu_util   DOUBLE,
    mem_used         DOUBLE,
    energy_joules    DOUBLE,              -- NULL for our data (no power meter)
    power_watts      DOUBLE,              -- NULL for our data
    cpu_temperature  DOUBLE,
    cpu_frequency    DOUBLE,
    power_core       DOUBLE,              -- NULL for our data
    power_system     DOUBLE,              -- NULL for our data
    security_level   TINYINT,
    -- patch-04 extensions:
    source           VARCHAR(64),         -- origin tag, see §2
    size_kind        VARCHAR(16)          -- 'key' | 'payload'
);
```

## §1. Algorithm naming — canonical form

We collapse every alias to a single canonical lowercase form:

| Canonical | Aliases that map to it |
|-----------|------------------------|
| `classical` | `ECDSA-P256`, `ecdsa-p256`, `classical`, `none` |
| `mlkem512` | `Kyber512`, `Kyber-512`, `ML-KEM-512`, `mlkem512`, `kyber512` |
| `mlkem768` | `Kyber768`, `Kyber-768`, `ML-KEM-768`, `mlkem768`, `kyber768` |
| `mlkem1024` | `Kyber1024`, `Kyber-1024`, `ML-KEM-1024`, `mlkem1024`, `kyber1024` |
| `mldsa44` | `ML-DSA-44`, `mldsa44` |
| `mldsa65` | `ML-DSA-65`, `mldsa65` |
| `mldsa87` | `ML-DSA-87`, `mldsa87` |
| `falcon512` | `Falcon-512`, `falcon-512`, `falcon512` |
| `falcon1024` | `Falcon-1024`, `falcon-1024`, `falcon1024` |
| `dual-ecdsa-mldsa65` | `ECDSA+ML-DSA-65`, `dual` |

The ETL importers all call `_canon(alg)` from `_db.py` to apply this normalisation before INSERT.

## §2. `source` column values

| Value | Origin | Importer |
|-------|--------|----------|
| `upstream` | `pqreact_export.sql` (default for pre-existing rows) | none — set by patched `setup_db.sh` |
| `campaign-a-kpi` | `katana-pqc-kpi` collector events with `source != 'demo5_bench'` | `import_kpi_events.py` |
| `campaign-b-demo5-bench` | `katana-pqc-kpi` events with `source='demo5_bench'` | `import_kpi_events.py` |
| `campaign-b-qujata` | QUJATA MySQL `qujata.test_runs` | `import_qujata_runs.py` |
| `campaign-b-demo4` | `/var/lib/qujata/demo4_matrix.json` | `import_demo4_matrix.py` |
| `campaign-b-demo5` | `/var/lib/qujata/demo5_crypto.json` | `import_demo5_crypto.py` |

Demo NL queries can be filtered via `WHERE source LIKE 'campaign-%'` for ours-only views, or compared across sources.

## §3. `size_kind` semantics

| Value | Meaning | Used by |
|-------|---------|---------|
| `payload` | UDP datagram payload size in bytes (e.g., 512, 1200) | `import_demo4_matrix`, `import_qujata_runs` |
| `key` | KEM public key + ciphertext wire bytes (1568 / 2272 / 3136) | `import_demo5_crypto`, `import_kpi_events` |

This disambiguates queries like `query_by_message_size(1200)` — the upstream tool treats `message_size` as a single number, but our data has two semantics. Tools can be enhanced to filter by `size_kind` explicitly later (out of scope for v2).

## §4. `security_level` mapping

Static table applied during INSERT (no manual data entry needed):

| algorithm | security_level |
|-----------|---------------|
| `classical` | 0 (sentinel for non-PQC) |
| `mlkem512`, `falcon512`, `mldsa44` | 1 (NIST L1) |
| `mlkem768`, `mldsa65` | 3 (NIST L3) |
| `mlkem1024`, `falcon1024`, `mldsa87` | 5 (NIST L5) |
| `dual-ecdsa-mldsa65` | 3 (carries level-3 ML-DSA-65) |

## §5. Column-by-column population

| Column | Campaign A KPI events | Campaign B qujata-mysql | Campaign B demo4 | Campaign B demo5 |
|--------|----------------------|-----------------------|-------------------|-------------------|
| `algorithm_name` | `event.algorithm` (canon) | `tr.algorithm` (canon) | `r['alg']` (canon) | result key (canon) |
| `message_size` | `metrics.pk_bytes` or `wire_bytes` | `tr.message_size` (datagram) | `r['msg']` | `r['pk_bytes']` |
| `size_kind` | `key` | `payload` | `payload` | `key` |
| `duration` | `metrics.encap_p50_ms+decap_p50_ms` or `total_kem_p50_ms` or `sign_p50_ms` | `kem_total_ms` (parsed JSON) | `kem_total_ms` | `total_kem_p50` or `sign_p50+verify_p50` |
| `cpu_util_pct` | NULL | NULL | NULL | NULL |
| `delta_cpu_util` | NULL | NULL | NULL | NULL |
| `mem_used` | NULL | NULL | NULL | NULL |
| `energy_joules` | NULL (deferred — no instrumentation) | NULL | NULL | NULL |
| `power_watts` | NULL | NULL | NULL | NULL |
| `cpu_temperature` | NULL | NULL | NULL | NULL |
| `cpu_frequency` | NULL (or `/proc/cpuinfo` cpu MHz at import time) | same | same | same |
| `power_core` | NULL | NULL | NULL | NULL |
| `power_system` | NULL | NULL | NULL | NULL |
| `security_level` | derived (§4) | derived | derived | derived |
| `source` | `campaign-a-kpi` or `campaign-b-demo5-bench` | `campaign-b-qujata` | `campaign-b-demo4` | `campaign-b-demo5` |

## §6. Deferred — power/energy instrumentation

The `energy_joules`, `power_watts`, `power_core`, `power_system` columns stay NULL for all our data. The MCP tools `get_lowest_power_for_size`, `get_lowest_energy_algorithms`, `get_lowest_energy_for_size` will return:
- For our `WHERE source LIKE 'campaign-%'` rows: empty result (NULL ordering puts them last)
- For `WHERE source='upstream'` rows: meaningful results (the dump has populated columns)

This is **honest**: those columns reflect measurements we don't have. A future sprint can add Linux RAPL energy counter sampling around each KEM operation in the iperf adapter — adds ~5 µs overhead per call and would populate `energy_joules` directly. See `docs/ARCHITECTURE.md §9 Long-term Evolution` in the integration plan.

## §7. UPSERT key — idempotent re-import

Each importer issues:

```sql
INSERT INTO performance_test (algorithm_name, message_size, size_kind, source, ...)
VALUES (...)
ON DUPLICATE KEY UPDATE
  duration = VALUES(duration),
  cpu_frequency = VALUES(cpu_frequency),
  ...
```

The natural key is `(algorithm_name, message_size, size_kind, source)` — but performance_test has no PK upstream. The setup_db patch adds:

```sql
ALTER TABLE performance_test
  ADD UNIQUE KEY uniq_natural (algorithm_name, message_size, size_kind, source);
```

Result: re-running any importer is a no-op (rows update in place rather than duplicate).
