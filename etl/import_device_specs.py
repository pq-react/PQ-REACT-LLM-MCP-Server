#!/usr/bin/env python3
"""Load etl/data/device_crypto_specs.yaml into PQREACT.device_crypto_specs.

Idempotent — UPSERTs by (device_type, protocol). Re-running picks up
YAML edits cleanly.

Required env (no defaults — fail loudly if missing):
    DB_HOST, DB_USER, DB_PASSWORD
Optional env:
    DB_PORT  (default 3306)
    DB_NAME  (default PQREACT)

Usage:
    python3 import_device_specs.py [path/to/device_crypto_specs.yaml]
"""
from __future__ import annotations

import json
import os
import sys

import pymysql
import yaml


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None:
        sys.exit(f"missing required env var: {name}")
    return val


def _connect():
    return pymysql.connect(
        host=_env("DB_HOST"),
        port=int(_env("DB_PORT", "3306")),
        user=_env("DB_USER"),
        password=_env("DB_PASSWORD"),
        database=_env("DB_NAME", "PQREACT"),
        charset="utf8mb4",
        autocommit=False,
    )


def _upsert(cur, d: dict) -> None:
    cur.execute(
        """
        INSERT INTO device_crypto_specs (device_type, protocol, sector,
            current_cipher_suite, current_key_exchange, current_signature,
            key_length, pqc_compatible, pqc_candidate_algorithms,
            spec_reference, source, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            sector = VALUES(sector),
            current_cipher_suite = VALUES(current_cipher_suite),
            current_key_exchange = VALUES(current_key_exchange),
            current_signature = VALUES(current_signature),
            key_length = VALUES(key_length),
            pqc_compatible = VALUES(pqc_compatible),
            pqc_candidate_algorithms = VALUES(pqc_candidate_algorithms),
            spec_reference = VALUES(spec_reference),
            source = VALUES(source),
            notes = VALUES(notes)
        """,
        (
            d["device_type"],
            d["protocol"],
            d["sector"],
            d.get("current_cipher_suite"),
            d.get("current_key_exchange"),
            d.get("current_signature"),
            d.get("key_length"),
            bool(d.get("pqc_compatible", False)),
            json.dumps(d.get("pqc_candidate_algorithms") or []),
            d.get("spec_reference"),
            d["source"],
            (d.get("notes") or "").strip() or None,
        ),
    )


def main(path: str) -> None:
    with open(path) as f:
        doc = yaml.safe_load(f)
    devs = doc.get("devices") or []
    conn = _connect()
    try:
        with conn.cursor() as cur:
            for d in devs:
                _upsert(cur, d)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"OK — imported/updated {len(devs)} device specs")


if __name__ == "__main__":
    default_path = os.path.join(os.path.dirname(__file__), "data", "device_crypto_specs.yaml")
    main(sys.argv[1] if len(sys.argv) > 1 else default_path)
