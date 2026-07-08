"""asset_inventory — Phase 3 of the PQ-REACT recommendation engine.

Tiny FastAPI service that ingests CycloneDX 1.6 CBOMs (typically produced
by CBOMkit-theia scanning testbed containers), parses them into rows for
the existing PQREACT.cbom_documents / crypto_assets / compliance_results
tables, and runs a quantum-safe policy check on each ingested document.

Once a CBOM is in MariaDB, the four [INVENTORY] MCP tools on the chat
agent (query_crypto_inventory, get_quantum_risk_summary, get_compliance_report,
recommend_migration_priority) immediately surface real data instead of
the "no CBOMs yet" placeholder.

Endpoints:
    GET  /health                — readiness
    POST /api/cbom              — ingest a CycloneDX JSON document
    GET  /api/cbom              — list ingested CBOMs
    GET  /api/cbom/{id}         — fetch one CBOM (metadata + asset count)
    POST /api/compliance/run/{id} — re-run the policy on an existing CBOM

Required env (no defaults — fail loudly):
    DB_HOST, DB_USER, DB_PASSWORD
Optional env:
    DB_PORT  (default 3306)
    DB_NAME  (default PQREACT)
    LISTEN_PORT (default 8082)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Optional

import pymysql
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

import cyclonedx_parser as cdx
import policy as pol


def _env(name: str, default: Optional[str] = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        sys.exit(f"missing required env var: {name}")
    return v


DB_CONFIG = dict(
    host=_env("DB_HOST"),
    port=int(_env("DB_PORT", "3306")),
    user=_env("DB_USER"),
    password=_env("DB_PASSWORD"),
    database=_env("DB_NAME", "PQREACT"),
    charset="utf8mb4",
    autocommit=False,
)


def _conn():
    return pymysql.connect(**DB_CONFIG)


app = FastAPI(title="PQ-REACT asset_inventory", version="0.1.0")


@app.get("/health")
def health() -> dict:
    try:
        c = _conn()
        with c.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        c.close()
        return {"status": "ok", "db": DB_CONFIG["host"], "name": DB_CONFIG["database"]}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "down", "error": str(e)[:200]},
        )


@app.post("/api/cbom")
async def ingest_cbom(request: Request) -> dict:
    """Ingest a CycloneDX 1.6 CBOM JSON document.

    Optional query parameters:
        sector         attach a sector tag to all assets in this scan
                       (energy / telco / health / financial / cross-sector / public)
        source_system  override the metadata.component.name from the CBOM
    """
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(400, f"body must be valid JSON: {e}")
    if not isinstance(body, dict) or body.get("bomFormat") != "CycloneDX":
        raise HTTPException(400, "bomFormat must be 'CycloneDX'")

    sector = request.query_params.get("sector")
    override_source = request.query_params.get("source_system")

    md = cdx.cbom_metadata(body)
    source = override_source or md["source_system"]
    tool = md["scan_tool"]
    ts_str = md.get("scan_timestamp")
    scan_dt = None
    if ts_str:
        try:
            # CycloneDX timestamp is ISO-8601 with Z; mariadb DATETIME tolerant
            scan_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            scan_dt = None

    try:
        rows = cdx.parse(body, sector=sector)
    except Exception as e:
        raise HTTPException(400, f"failed to parse CBOM: {e}")

    eval_ = pol.evaluate_cbom(rows)

    cbom_id: int
    try:
        c = _conn()
        with c.cursor() as cur:
            if scan_dt is not None:
                cur.execute(
                    "INSERT INTO cbom_documents "
                    "(source_system, scan_date, sector, scan_tool, cbom_json) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (source[:128], scan_dt, sector, tool[:64], json.dumps(body)),
                )
            else:
                cur.execute(
                    "INSERT INTO cbom_documents "
                    "(source_system, sector, scan_tool, cbom_json) "
                    "VALUES (%s, %s, %s, %s)",
                    (source[:128], sector, tool[:64], json.dumps(body)),
                )
            cbom_id = cur.lastrowid
            for r in rows:
                cur.execute(
                    "INSERT INTO crypto_assets "
                    "(cbom_doc_id, asset_type, name, version, "
                    " quantum_risk_level, component_path, sector) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (cbom_id, r["asset_type"], r["name"], r["version"],
                     r["quantum_risk_level"], r["component_path"],
                     r["sector"]),
                )
            cur.execute(
                "INSERT INTO compliance_results "
                "(cbom_doc_id, policy_name, pass_count, fail_count, findings_json) "
                "VALUES (%s, %s, %s, %s, %s)",
                (cbom_id, eval_["policy_name"], eval_["pass_count"],
                 eval_["fail_count"], json.dumps(eval_["findings"])),
            )
        c.commit()
        c.close()
    except pymysql.Error as e:
        raise HTTPException(500, f"database error: {e}")

    return {
        "cbom_doc_id":    cbom_id,
        "source_system":  source,
        "sector":         sector,
        "scan_tool":      tool,
        "n_assets":       len(rows),
        "policy_name":    eval_["policy_name"],
        "compliance":     {
            "pass": eval_["pass_count"],
            "fail": eval_["fail_count"],
            "findings": len(eval_["findings"]),
        },
    }


@app.get("/api/cbom")
def list_cboms(limit: int = 50) -> dict:
    limit = max(1, min(int(limit), 200))
    try:
        c = _conn()
        with c.cursor() as cur:
            cur.execute(
                "SELECT d.id, d.source_system, d.scan_date, d.sector, d.scan_tool, "
                "       (SELECT COUNT(*) FROM crypto_assets a "
                "        WHERE a.cbom_doc_id = d.id) AS n_assets "
                "FROM cbom_documents d ORDER BY d.id DESC LIMIT %s",
                (limit,),
            )
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                if isinstance(r.get("scan_date"), datetime):
                    r["scan_date"] = r["scan_date"].isoformat()
        c.close()
        return {"count": len(rows), "items": rows}
    except pymysql.Error as e:
        raise HTTPException(500, f"database error: {e}")


@app.get("/api/cbom/{cbom_id}")
def get_cbom(cbom_id: int) -> dict:
    try:
        c = _conn()
        with c.cursor() as cur:
            cur.execute(
                "SELECT id, source_system, scan_date, sector, scan_tool "
                "FROM cbom_documents WHERE id = %s", (cbom_id,))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, f"no CBOM with id={cbom_id}")
            cur.execute(
                "SELECT asset_type, COUNT(*), "
                "       SUM(quantum_risk_level='vulnerable'), "
                "       SUM(quantum_risk_level='partial'), "
                "       SUM(quantum_risk_level='safe'), "
                "       SUM(quantum_risk_level='unknown') "
                "FROM crypto_assets WHERE cbom_doc_id = %s "
                "GROUP BY asset_type",
                (cbom_id,),
            )
            assets_by_type = [
                {
                    "asset_type": x[0], "total": int(x[1]),
                    "vulnerable": int(x[2] or 0), "partial": int(x[3] or 0),
                    "safe": int(x[4] or 0), "unknown": int(x[5] or 0),
                }
                for x in cur.fetchall()
            ]
            cur.execute(
                "SELECT policy_name, pass_count, fail_count, scan_date "
                "FROM compliance_results WHERE cbom_doc_id = %s ORDER BY id DESC",
                (cbom_id,),
            )
            compliance = []
            for row in cur.fetchall():
                compliance.append({
                    "policy_name": row[0],
                    "pass": row[1], "fail": row[2],
                    "scan_date": row[3].isoformat() if isinstance(row[3], datetime) else str(row[3]),
                })
        c.close()
        return {
            "cbom_doc_id":  r[0],
            "source_system": r[1],
            "scan_date":    r[2].isoformat() if isinstance(r[2], datetime) else str(r[2]),
            "sector":       r[3],
            "scan_tool":    r[4],
            "assets_by_type": assets_by_type,
            "compliance":   compliance,
        }
    except pymysql.Error as e:
        raise HTTPException(500, f"database error: {e}")


@app.post("/api/compliance/run/{cbom_id}")
def rerun_compliance(cbom_id: int) -> dict:
    """Pull the stored CBOM JSON, re-parse, re-run the policy, and
    insert a fresh compliance_results row. Useful after policy.py is
    updated (tighter rules) or after a YAML refresh."""
    try:
        c = _conn()
        with c.cursor() as cur:
            cur.execute("SELECT cbom_json FROM cbom_documents WHERE id = %s",
                        (cbom_id,))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, f"no CBOM with id={cbom_id}")
            doc = json.loads(r[0])
            rows = cdx.parse(doc)
            eval_ = pol.evaluate_cbom(rows)
            cur.execute(
                "INSERT INTO compliance_results "
                "(cbom_doc_id, policy_name, pass_count, fail_count, findings_json) "
                "VALUES (%s, %s, %s, %s, %s)",
                (cbom_id, eval_["policy_name"], eval_["pass_count"],
                 eval_["fail_count"], json.dumps(eval_["findings"])),
            )
        c.commit()
        c.close()
    except pymysql.Error as e:
        raise HTTPException(500, f"database error: {e}")
    return {
        "cbom_doc_id": cbom_id,
        "policy_name": eval_["policy_name"],
        "pass": eval_["pass_count"],
        "fail": eval_["fail_count"],
        "findings": len(eval_["findings"]),
    }
