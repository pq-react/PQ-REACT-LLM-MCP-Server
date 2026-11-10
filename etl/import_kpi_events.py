#!/usr/bin/env python3
"""
Import KPI collector events into performance_test.

Source: HTTP GET <KPI_URL>/api/events?limit=N (the Katana PQC KPI collector,
same endpoint as the server's KATANA_KPI_BASE).
Both Campaign A (UC3-01..09 KPIs) and Campaign B (Demo 5 fusion bench) emit here.

Idempotent: re-running upserts in place via the uniq_natural key.

Env:
    KPI_URL         = collector base URL, e.g. http://<katana-kpi-host>:8080
                      (falls back to KATANA_KPI_BASE; required)
    KPI_LIMIT       = 5000
    MCP_DB_PASSWORD = (required, see _db.py)
"""
from __future__ import annotations
import json, os, sys, urllib.request
from _db import connect, canon_alg, security_level_for, upsert, make_row

KPI_URL = os.environ.get('KPI_URL', os.environ.get('KATANA_KPI_BASE', ''))
KPI_LIMIT = int(os.environ.get('KPI_LIMIT', '5000'))


def fetch_events() -> list[dict]:
    if not KPI_URL:
        raise RuntimeError('KPI_URL (or KATANA_KPI_BASE) is not set')
    url = f'{KPI_URL.rstrip("/")}/api/events?limit={KPI_LIMIT}'
    print(f'fetching {url}')
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())


def event_to_row(ev: dict) -> dict | None:
    """Map one collector event into a performance_test row dict, or None to skip."""
    kpi_id = ev.get('kpi_id') or ''
    metrics = ev.get('metrics') or {}
    alg = ev.get('algorithm') or ''
    src_field = ev.get('source') or ''

    # Decide source tag
    if src_field == 'demo5_bench':
        source = 'campaign-b-demo5-bench'
    elif src_field == 'besu_metrics':
        # Besu chain metrics — not algorithm-specific in our sense, skip
        return None
    else:
        source = 'campaign-a-kpi'

    # Extract the right "duration" depending on KPI id
    duration_ms = None
    message_size = None
    size_kind = 'key'

    if kpi_id == 'KPI-UC3-01':
        # Key sizes — record pk_bytes as message_size
        message_size = metrics.get('pk_bytes')
        # No latency measurement here; skip rows without size
        if message_size is None:
            return None
    elif kpi_id == 'KPI-UC3-02':
        # Wire footprint + fragmentation
        message_size = metrics.get('wire_bytes')
        if message_size is None:
            return None
    elif kpi_id == 'KPI-UC3-03':
        # Processing latency — could be sign+verify or encap+decap
        for k in ('total_kem_p50_ms', 'total_p50_ms'):
            if k in metrics:
                duration_ms = metrics[k]; break
        if duration_ms is None and 'sign_p50_ms' in metrics and 'verify_p50_ms' in metrics:
            duration_ms = metrics['sign_p50_ms'] + metrics['verify_p50_ms']
        if duration_ms is None and 'encap_p50_ms' in metrics and 'decap_p50_ms' in metrics:
            duration_ms = metrics['encap_p50_ms'] + metrics['decap_p50_ms']
        # Use pk_bytes if available, else 0 (the latency belongs to the KEM/sig op)
        message_size = metrics.get('pk_bytes', 0)
    elif kpi_id == 'KPI-UC3-07':
        duration_ms = metrics.get('total_p50_ms')
        message_size = 0  # dual sig — message-size-agnostic
    else:
        return None

    return make_row(
        alg=alg,
        message_size=message_size,
        duration_ms=duration_ms,
        source=source,
        size_kind=size_kind,
    )


def main():
    try:
        events = fetch_events()
    except Exception as e:
        print(f'ERROR fetching events: {e}', file=sys.stderr)
        sys.exit(3)
    print(f'got {len(events)} events')

    conn = connect()
    inserted = skipped = 0
    with conn.cursor() as cur:
        for ev in events:
            row = event_to_row(ev)
            if row is None:
                skipped += 1
                continue
            try:
                upsert(cur, row)
                inserted += 1
            except Exception as e:
                print(f'  skip row (alg={row.get("algorithm_name")}): {e}', file=sys.stderr)
                skipped += 1
    conn.commit()
    conn.close()
    print(f'imported: {inserted}  skipped: {skipped}')


if __name__ == '__main__':
    main()
