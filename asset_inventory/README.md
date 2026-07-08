# asset_inventory — CBOM ingestion service

Phase 3 of the PQ-REACT recommendation engine. Tiny FastAPI service that
bridges CycloneDX 1.6 CBOMs (typically from CBOMkit-theia scans) into
the PQREACT MariaDB tables that back the four `[INVENTORY]` MCP tools on
the chat agent.

```
   CBOMkit-theia  ─scan→  CycloneDX JSON  ──POST──►  asset_inventory:8082
                                                         ↓
                                                    cyclonedx_parser
                                                         ↓
                                                    policy evaluator
                                                         ↓
                                                    PQREACT.cbom_documents
                                                    PQREACT.crypto_assets
                                                    PQREACT.compliance_results
                                                         ↓
                                                    MCP tools surface real data
                                                    (query_crypto_inventory,
                                                     get_quantum_risk_summary,
                                                     get_compliance_report,
                                                     recommend_migration_priority)
```

## Endpoints

| Verb | Path | Purpose |
|---|---|---|
| GET  | `/health` | DB readiness check |
| POST | `/api/cbom?sector=<s>&source_system=<n>` | Ingest a CycloneDX 1.6 JSON document |
| GET  | `/api/cbom?limit=N` | List ingested CBOMs (newest first) |
| GET  | `/api/cbom/{id}` | One CBOM with asset counts by type + risk |
| POST | `/api/compliance/run/{id}` | Re-run the policy on an existing CBOM (after policy tightening) |

Optional ingest query parameters:

- `sector` — `energy` / `telco` / `health` / `financial` / `cross-sector` / `public`. Tags every asset with the sector so MCP filters work.
- `source_system` — overrides whatever's in the CBOM's `metadata.component.name`. Useful when scanning a pile of containers and you want a stable label.

## Quantum-safe policy

`policy.py` classifies each crypto asset as `vulnerable` / `partial` / `safe` / `unknown`:

- **safe** — pure PQC (ML-KEM, ML-DSA, SLH-DSA, …) or symmetric primitives at adequate length (AES-256, SHA-256+, HMAC-SHA-256+). Also any algorithm with `cryptoProperties.algorithmProperties.nistQuantumSecurityLevel >= 1`.
- **partial** — hybrid PQC (e.g. `X25519+ML-KEM-768`). Counts as transitional under the EU PQC Roadmap (acceptable to 2030, mandatory pure PQC by 2035). AES-128 also lands here (Grover ⇒ effective 64-bit).
- **vulnerable** — RSA, DSA, DH, ECDH, ECDSA, EdDSA without PQC fallback. SHA-1, MD5, 3DES, RC4 also flagged.
- **unknown** — anything else; surfaces as a manual-review finding.

Policy version: `pqreact-quantum-safe-v1`. To tighten without redeploying, edit `policy.py`, restart the container, then `POST /api/compliance/run/{id}` for each CBOM you want re-scored.

## Deployment

```bash
# In the docker-compose stack on <cbomkit-host>:
docker compose up -d asset-inventory

# Standalone:
docker build -t pqreact-asset-inventory .
docker run -d --name pqreact-asset-inventory \
    -e DB_HOST=<mcp-server-host> -e DB_USER=pqreact -e DB_PASSWORD=... \
    -p 8082:8082 pqreact-asset-inventory
```

## End-to-end demo

```bash
# 1. Scan a testbed container with CBOMkit-theia (Go binary):
cbomkit-theia image pqreact-mcp-server > /tmp/cbom.json

# 2. POST it to the inventory service:
curl -sS -X POST 'http://<cbomkit-host>:8082/api/cbom?sector=cross-sector&source_system=pqreact-mcp-server' \
     -H 'Content-Type: application/json' \
     --data @/tmp/cbom.json

# Returns: {"cbom_doc_id": 1, "n_assets": 17, "compliance": {"pass": 6, "fail": 9, ...}}

# 3. Ask the chat agent — it now sees the real inventory:
curl -sS -X POST http://<gpu-vm-host>:8081/chat \
     -H 'Content-Type: application/json' \
     -d '{"query": "What crypto is deployed in the testbed?"}'
```
