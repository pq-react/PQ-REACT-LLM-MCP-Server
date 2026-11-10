#!/usr/bin/env python3
"""
Import QUJATA test_runs into performance_test.

Source: the QUJATA stack's MySQL (qujata.test_runs).
Each successful iperf run becomes one performance_test row.

Idempotent via uniq_natural key.

Env (same names as the MCP server's QUJATA_DB_* settings):
    QUJATA_DB_HOST     = QUJATA MySQL host (default localhost)
    QUJATA_DB_PORT     = 3306
    QUJATA_DB_USER     = root
    QUJATA_DB_PASSWORD = QUJATA MySQL password (QUJATA_DB_PASS also accepted)
    QUJATA_DB_NAME     = qujata
    MCP_DB_PASSWORD    = (required for target DB)
"""
from __future__ import annotations
import json, os, sys
import pymysql, pymysql.cursors
from _db import connect as connect_target, upsert, make_row

QUJATA_DB_HOST = os.environ.get('QUJATA_DB_HOST', 'localhost')
QUJATA_DB_PORT = int(os.environ.get('QUJATA_DB_PORT', '3306'))
QUJATA_DB_USER = os.environ.get('QUJATA_DB_USER', 'root')
QUJATA_DB_PASS = os.environ.get('QUJATA_DB_PASSWORD', os.environ.get('QUJATA_DB_PASS', ''))
QUJATA_DB_NAME = os.environ.get('QUJATA_DB_NAME', 'qujata')


def fetch_runs():
    src = pymysql.connect(
        host=QUJATA_DB_HOST, port=QUJATA_DB_PORT,
        user=QUJATA_DB_USER, password=QUJATA_DB_PASS,
        db=QUJATA_DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )
    with src.cursor() as cur:
        cur.execute("""
            SELECT id, algorithm, message_size, status, status_message
            FROM test_runs
            WHERE status='SUCCESS' AND status_message LIKE '%ipsec_algorithm%'
        """)
        rows = list(cur.fetchall())
    src.close()
    return rows


def parse_status_message(blob: str) -> dict:
    """status_message is JSON-shaped (we wrote it that way in analyze_service.py)."""
    try:
        return json.loads(blob) if blob else {}
    except Exception:
        return {}


def main():
    try:
        runs = fetch_runs()
    except Exception as e:
        print(f'ERROR fetching from qujata MySQL: {e}', file=sys.stderr)
        sys.exit(3)
    print(f'got {len(runs)} qujata runs')

    target = connect_target()
    inserted = skipped = 0
    with target.cursor() as cur:
        for tr in runs:
            sm = parse_status_message(tr.get('status_message', ''))
            kem_total = sm.get('kem_total_ms')
            mbps = sm.get('mbps')
            jitter = sm.get('jitter_ms')

            # Use kem_total_ms as duration when available (the cryptographic cost),
            # else mbps converted to a notional duration field is meaningless — skip.
            duration_ms = kem_total
            if duration_ms is None:
                # for classical runs we still record with duration=0 so they appear
                # in queries; mbps lives implicitly in the throughput/jitter context
                duration_ms = 0.0

            row = make_row(
                alg=tr['algorithm'],
                message_size=int(tr['message_size'] or 0),
                duration_ms=float(duration_ms),
                source='campaign-b-qujata',
                size_kind='payload',
            )
            try:
                upsert(cur, row)
                inserted += 1
            except Exception as e:
                print(f'  skip run id={tr["id"]} alg={row.get("algorithm_name")}: {e}', file=sys.stderr)
                skipped += 1
    target.commit()
    target.close()
    print(f'imported: {inserted}  skipped: {skipped}')


if __name__ == '__main__':
    main()
