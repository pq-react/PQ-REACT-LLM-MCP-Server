#!/usr/bin/env python3
"""
Import Demo 4 algorithm × slice × msg-size matrix into performance_test.

Source: /var/lib/qujata/demo4_matrix.json on the Core VM — fetched via
SSH (sudo cat) since the file is root-owned.

Idempotent via uniq_natural key.

Env:
    DEMO4_JSON_PATH = local path to a copy of demo4_matrix.json (preferred)
    DEMO4_SSH_HOST  = Core VM host to fetch demo4_matrix.json from when no local
                      copy is given (fallback; needs sudo on that host)
    SSH_USER        = remote user (optional; ssh config / current user otherwise)
    SSH_PASSWORD    = optional; enables sshpass + `sudo -S`. Without it, key
                      auth and passwordless `sudo -n` are assumed.
    MCP_DB_PASSWORD = (required for target DB)
"""
from __future__ import annotations
import json, os, sys, subprocess
from _db import connect, upsert, make_row

DEMO4_PATH = os.environ.get('DEMO4_JSON_PATH', '')
SSH_HOST = os.environ.get('DEMO4_SSH_HOST', '')
SSH_USER = os.environ.get('SSH_USER', '')
SSH_PASS = os.environ.get('SSH_PASSWORD', '')
REMOTE_PATH = '/var/lib/qujata/demo4_matrix.json'


def fetch_demo4_json() -> dict:
    """Either read local file (DEMO4_JSON_PATH) or SSH-fetch from Core VM."""
    if DEMO4_PATH and os.path.exists(DEMO4_PATH):
        with open(DEMO4_PATH) as f:
            return json.load(f)
    if not SSH_HOST:
        raise RuntimeError('set DEMO4_JSON_PATH (preferred) or DEMO4_SSH_HOST')
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


def main():
    try:
        data = fetch_demo4_json()
    except Exception as e:
        print(f'ERROR fetching demo4_matrix.json: {e}', file=sys.stderr)
        sys.exit(3)
    matrix = data.get('matrix', [])
    print(f'got {len(matrix)} demo-4 cells')

    conn = connect()
    inserted = skipped = 0
    with conn.cursor() as cur:
        for r in matrix:
            if 'error' in r:
                skipped += 1
                continue
            alg = r.get('alg')
            msg = r.get('msg')                      # 512 or 1200 (datagram)
            kem_ms = r.get('kem_total_ms') or 0.0  # 0 for classical
            row = make_row(
                alg=alg,
                message_size=int(msg) if msg is not None else 0,
                duration_ms=float(kem_ms),
                source='campaign-b-demo4',
                size_kind='payload',
            )
            try:
                upsert(cur, row)
                inserted += 1
            except Exception as e:
                print(f'  skip cell alg={alg} slice={r.get("slice")} msg={msg}: {e}', file=sys.stderr)
                skipped += 1
    conn.commit()
    conn.close()
    print(f'imported: {inserted}  skipped: {skipped}')


if __name__ == '__main__':
    main()
