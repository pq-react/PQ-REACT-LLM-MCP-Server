#!/usr/bin/env python3
"""
Import Demo 5 crypto-layer benchmark (KEM + signature timings) into
performance_test.

Source: /var/lib/qujata/demo5_crypto.json on the Core VM.

For each algorithm in the JSON we record:
  - one row with `size_kind=key` and `message_size=pk_bytes`, `duration=total_kem_p50`
    (or sign+verify total for signature algorithms)

Idempotent via uniq_natural key.

Env:
    DEMO5_JSON_PATH = local path (preferred)
    DEMO5_SSH_HOST  = Core VM host to fetch demo5_crypto.json from when no local
                      copy is given (fallback; needs sudo on that host)
    SSH_USER        = remote user (optional; ssh config / current user otherwise)
    SSH_PASSWORD    = optional; enables sshpass + `sudo -S`. Without it, key
                      auth and passwordless `sudo -n` are assumed.
    MCP_DB_PASSWORD = (required for target DB)
"""
from __future__ import annotations
import json, os, sys, subprocess
from _db import connect, upsert, make_row

DEMO5_PATH = os.environ.get('DEMO5_JSON_PATH', '')
SSH_HOST = os.environ.get('DEMO5_SSH_HOST', '')
SSH_USER = os.environ.get('SSH_USER', '')
SSH_PASS = os.environ.get('SSH_PASSWORD', '')
REMOTE_PATH = '/var/lib/qujata/demo5_crypto.json'


def fetch_demo5_json() -> dict:
    if DEMO5_PATH and os.path.exists(DEMO5_PATH):
        with open(DEMO5_PATH) as f:
            return json.load(f)
    if not SSH_HOST:
        raise RuntimeError('set DEMO5_JSON_PATH (preferred) or DEMO5_SSH_HOST')
    # The file is root-owned. The password, if any, is passed via the
    # SSHPASS env var and sudo's stdin, never on a command line.
    target = f'{SSH_USER}@{SSH_HOST}' if SSH_USER else SSH_HOST
    cmd = ['ssh', '-o', 'StrictHostKeyChecking=accept-new']
    env, stdin = None, None
    if SSH_PASS:
        cmd = ['sshpass', '-e'] + cmd + [
            '-o', 'PreferredAuthentications=password',
            '-o', 'PubkeyAuthentication=no',
            target, f"sudo -S -p '' cat {REMOTE_PATH}",
        ]
        env = {**os.environ, 'SSHPASS': SSH_PASS}
        stdin = SSH_PASS + '\n'
    else:
        cmd += [target, f'sudo -n cat {REMOTE_PATH}']
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                       timeout=30, env=env)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f'ssh fetch failed: {r.stderr[:200]}')
    return json.loads(r.stdout)


def derive_row(alg_label: str, m: dict) -> dict | None:
    """Convert one entry of demo5_crypto.results into a row."""
    pk_bytes = m.get('pk_bytes', 0)

    # Pick a duration consistent with the algorithm class
    if 'total_kem_p50' in m:                       # KEM
        duration = m['total_kem_p50']
    elif 'sign_p50' in m and 'verify_p50' in m:    # signature
        duration = m['sign_p50'] + m['verify_p50']
    elif 'total_p50' in m:                         # dual signature
        duration = m['total_p50']
    else:
        return None

    return make_row(
        alg=alg_label,
        message_size=int(pk_bytes),
        duration_ms=float(duration),
        source='campaign-b-demo5',
        size_kind='key',
    )


def main():
    try:
        data = fetch_demo5_json()
    except Exception as e:
        print(f'ERROR fetching demo5_crypto.json: {e}', file=sys.stderr)
        sys.exit(3)
    results = data.get('results') or {}
    print(f'got {len(results)} algorithm entries')

    conn = connect()
    inserted = skipped = 0
    with conn.cursor() as cur:
        for alg, metrics in results.items():
            row = derive_row(alg, metrics)
            if row is None:
                skipped += 1
                continue
            try:
                upsert(cur, row)
                inserted += 1
            except Exception as e:
                print(f'  skip alg={alg}: {e}', file=sys.stderr)
                skipped += 1
    conn.commit()
    conn.close()
    print(f'imported: {inserted}  skipped: {skipped}')


if __name__ == '__main__':
    main()
