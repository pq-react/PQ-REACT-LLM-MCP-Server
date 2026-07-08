#!/usr/bin/env python3
"""Load etl/data/regulations.yaml into the PQREACT regulatory tables.

Idempotent. Every regulation is upserted by id; every regulation's
obligations are atomically replaced (DELETE + INSERT inside a single
transaction). Re-running is safe and reflects the current YAML.

Required env (no defaults — fail loudly if missing):
    DB_HOST, DB_USER, DB_PASSWORD
Optional env:
    DB_PORT  (default 3306)
    DB_NAME  (default PQREACT)

Usage:
    python3 import_regulations.py [path/to/regulations.yaml]

If no path is given, defaults to ../etl/data/regulations.yaml relative
to the script.
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


def _upsert_regulation(cur, r: dict) -> None:
    cur.execute(
        """
        INSERT INTO regulations (id, name, long_name, kind, body, jurisdiction,
            effective_date, pqc_deadline, sector_scope, summary, source_url)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            long_name = VALUES(long_name),
            kind = VALUES(kind),
            body = VALUES(body),
            jurisdiction = VALUES(jurisdiction),
            effective_date = VALUES(effective_date),
            pqc_deadline = VALUES(pqc_deadline),
            sector_scope = VALUES(sector_scope),
            summary = VALUES(summary),
            source_url = VALUES(source_url)
        """,
        (
            r["id"],
            r["name"],
            r.get("long_name"),
            r["kind"],
            r.get("body"),
            r.get("jurisdiction"),
            r.get("effective_date"),
            r.get("pqc_deadline"),
            json.dumps(r.get("sector_scope") or []),
            (r.get("summary") or "").strip() or None,
            r.get("source_url"),
        ),
    )


def _replace_obligations(cur, regulation_id: str, obligations: list[dict]) -> int:
    cur.execute(
        "DELETE FROM obligations WHERE regulation_id = %s",
        (regulation_id,),
    )
    for o in obligations:
        am = o.get("algorithm_map") or {}
        rec_fams = am.get("recommended_families") if am else None
        cur.execute(
            """
            INSERT INTO obligations (regulation_id, article_ref, obligation_text,
                obligation_type, affected_sectors, deadline, crypto_relevance,
                nist_level_min, recommended_families, hybrid_required,
                algorithm_notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                regulation_id,
                o["article_ref"],
                (o["obligation_text"] or "").strip(),
                o["obligation_type"],
                json.dumps(o.get("affected_sectors") or []),
                o.get("deadline"),
                o["crypto_relevance"],
                am.get("nist_level_min") if am else None,
                json.dumps(rec_fams) if rec_fams else None,
                am.get("hybrid_required") if am else None,
                (am.get("notes") or "").strip() if am and am.get("notes") else None,
            ),
        )
    return len(obligations)


def _upsert_sector_profile(cur, p: dict) -> None:
    cur.execute(
        """
        INSERT INTO sector_profiles (sector_id, applicable_regulations,
            priority_assets, typical_crypto_surface, migration_complexity,
            long_lived_data, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            applicable_regulations = VALUES(applicable_regulations),
            priority_assets = VALUES(priority_assets),
            typical_crypto_surface = VALUES(typical_crypto_surface),
            migration_complexity = VALUES(migration_complexity),
            long_lived_data = VALUES(long_lived_data),
            notes = VALUES(notes)
        """,
        (
            p["sector_id"],
            json.dumps(p.get("applicable_regulations") or []),
            json.dumps(p.get("priority_assets") or []),
            (p.get("typical_crypto_surface") or "").strip() or None,
            p.get("migration_complexity"),
            p.get("long_lived_data"),
            (p.get("notes") or "").strip() or None,
        ),
    )


def main(path: str) -> None:
    with open(path) as f:
        doc = yaml.safe_load(f)
    regs = doc.get("regulations") or []
    profs = doc.get("sector_profiles") or []

    conn = _connect()
    n_obl = 0
    try:
        with conn.cursor() as cur:
            for r in regs:
                _upsert_regulation(cur, r)
                n_obl += _replace_obligations(cur, r["id"], r.get("obligations") or [])
            for p in profs:
                _upsert_sector_profile(cur, p)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(
        f"OK — imported {len(regs)} regulations, {n_obl} obligations, "
        f"{len(profs)} sector profiles"
    )


if __name__ == "__main__":
    default_path = os.path.join(os.path.dirname(__file__), "data", "regulations.yaml")
    main(sys.argv[1] if len(sys.argv) > 1 else default_path)
