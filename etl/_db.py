"""
Shared MariaDB connection + algorithm-name canonicaliser for ETL importers.

Reads connection params from env (no defaults for password — fail loud).
Defaults target the docker-compose stack from the host it runs on
(MariaDB published on localhost:3307, see docker-compose.yml).

Usage:
    from _db import connect, canon_alg, security_level_for, upsert
    conn = connect()
    with conn.cursor() as cur:
        upsert(cur, dict(algorithm_name='mlkem768', message_size=1200, ...))
    conn.commit()
"""
from __future__ import annotations
import os, sys
import pymysql
import pymysql.cursors

# ── Env ────────────────────────────────────────────────────────────────────
DB_HOST = os.environ.get('MCP_DB_HOST', os.environ.get('DB_HOST', 'localhost'))
DB_PORT = int(os.environ.get('MCP_DB_PORT', os.environ.get('DB_PORT', '3307')))
DB_NAME = os.environ.get('MCP_DB_NAME', os.environ.get('DB_NAME', 'PQREACT'))
DB_USER = os.environ.get('MCP_DB_USER', os.environ.get('DB_USER', 'pqreact'))
DB_PASS = os.environ.get('MCP_DB_PASSWORD', os.environ.get('DB_PASSWORD', ''))

if not DB_PASS:
    print('ERROR: MCP_DB_PASSWORD (or DB_PASSWORD) env var is required.',
          file=sys.stderr)
    sys.exit(2)


# ── Algorithm naming canonicalisation ──────────────────────────────────────
# Collapses every alias to a single canonical form. See etl/schema_map.md §1.
_ALG_ALIASES = {
    # classical
    'classical': 'classical',
    'ecdsa-p256': 'classical',
    'ecdsa_p256': 'classical',
    'none': 'classical',
    # ML-KEM family (= Kyber after FIPS 203)
    'kyber512': 'mlkem512', 'kyber-512': 'mlkem512', 'ml-kem-512': 'mlkem512',
    'mlkem512': 'mlkem512', 'mlkem_512': 'mlkem512',
    'kyber768': 'mlkem768', 'kyber-768': 'mlkem768', 'ml-kem-768': 'mlkem768',
    'mlkem768': 'mlkem768', 'mlkem_768': 'mlkem768',
    'kyber1024': 'mlkem1024', 'kyber-1024': 'mlkem1024', 'ml-kem-1024': 'mlkem1024',
    'mlkem1024': 'mlkem1024', 'mlkem_1024': 'mlkem1024',
    # ML-DSA family
    'ml-dsa-44': 'mldsa44', 'mldsa44': 'mldsa44',
    'ml-dsa-65': 'mldsa65', 'mldsa65': 'mldsa65',
    'ml-dsa-87': 'mldsa87', 'mldsa87': 'mldsa87',
    # Falcon
    'falcon-512': 'falcon512',  'falcon512':  'falcon512',
    'falcon-1024': 'falcon1024', 'falcon1024': 'falcon1024',
    # Hybrid / dual signatures
    'ecdsa+ml-dsa-65': 'dual-ecdsa-mldsa65',
    'dual': 'dual-ecdsa-mldsa65',
    'dual-ecdsa-mldsa65': 'dual-ecdsa-mldsa65',
}

def canon_alg(name: str | None) -> str | None:
    if not name:
        return None
    key = name.strip().lower()
    return _ALG_ALIASES.get(key, key)


# ── NIST security level mapping ────────────────────────────────────────────
_SEC_LEVEL = {
    'classical': 0,
    'mlkem512': 1, 'falcon512': 1, 'mldsa44': 1,
    'mlkem768': 3, 'mldsa65': 3, 'dual-ecdsa-mldsa65': 3,
    'mlkem1024': 5, 'falcon1024': 5, 'mldsa87': 5,
}

def security_level_for(canonical_alg: str | None) -> int | None:
    return _SEC_LEVEL.get(canonical_alg)


# ── Connection ─────────────────────────────────────────────────────────────
def connect():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        db=DB_NAME, user=DB_USER, password=DB_PASS,
        cursorclass=pymysql.cursors.Cursor,
        connect_timeout=10,
    )


# ── UPSERT helper ──────────────────────────────────────────────────────────
COLS = [
    'algorithm_name', 'message_size', 'duration',
    'cpu_util_pct', 'delta_cpu_util', 'mem_used',
    'energy_joules', 'power_watts', 'cpu_temperature', 'cpu_frequency',
    'power_core', 'power_system', 'security_level',
    'source', 'size_kind',
]

# Columns overwritten unconditionally on re-import; every other non-key
# column keeps its stored value when the new one is NULL.
_ALWAYS_UPDATE = {'duration', 'security_level'}
_KEY_COLS = {'algorithm_name', 'message_size', 'size_kind', 'source'}

# Not every deployment carries all upstream columns (the testbed DB had
# cpu_frequency / power_core / power_system dropped), so the statement is
# built from the columns actually present, looked up once per connection.
_present_cols_cache: dict[int, list[str]] = {}


def _present_cols(cur) -> list[str]:
    key = id(cur.connection)
    if key not in _present_cols_cache:
        cur.execute('SHOW COLUMNS FROM performance_test')
        have = {r[0] for r in cur.fetchall()}
        missing = _KEY_COLS - have
        if missing:
            raise RuntimeError(
                f'performance_test lacks {sorted(missing)}; apply '
                'etl/schema/performance_test.sql first')
        _present_cols_cache[key] = [c for c in COLS if c in have]
    return _present_cols_cache[key]


def _upsert_sql(cols: list[str]) -> str:
    updates = []
    for c in cols:
        if c in _KEY_COLS:
            continue
        if c in _ALWAYS_UPDATE:
            updates.append(f'  {c} = VALUES({c})')
        else:
            updates.append(f'  {c} = COALESCE(VALUES({c}), {c})')
    return (
        f'INSERT INTO performance_test ({", ".join(cols)})\n'
        f'VALUES ({", ".join(["%s"] * len(cols))})\n'
        'ON DUPLICATE KEY UPDATE\n' + ',\n'.join(updates)
    )


def upsert(cur, row: dict) -> None:
    """row keys must be a subset of COLS. Missing keys → NULL.

    UPSERTs on the uniq_natural key (algorithm_name, message_size,
    size_kind, source); columns absent from the live table are skipped.
    """
    cols = _present_cols(cur)
    cur.execute(_upsert_sql(cols), [row.get(c) for c in cols])


def make_row(*, alg: str, message_size: int | None, duration_ms: float | None,
             source: str, size_kind: str = 'payload', **extra) -> dict:
    """Build a row dict with canonical alg + derived security_level + extras."""
    canon = canon_alg(alg)
    return {
        'algorithm_name': canon,
        'message_size': message_size,
        'duration': duration_ms,
        'security_level': security_level_for(canon),
        'source': source,
        'size_kind': size_kind,
        **extra,
    }
