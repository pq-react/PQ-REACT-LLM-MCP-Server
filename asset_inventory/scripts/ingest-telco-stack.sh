#!/usr/bin/env bash
# ingest-telco-stack.sh — produce CycloneDX 1.6 CBOMs for the live 5G testbed
# (Open5GS core + UERANSIM RAN, both natively installed) and POST them to the
# PQ-REACT asset_inventory ingester with sector=telco.
#
# Theia binary must already be present at /tmp/theia on each scanned host (the
# cbomkit host has it; this script also pushes a copy to core+ran if missing).
#
# Required env (no defaults — fail loudly so credentials & topology never live
# in the script itself):
#   SSH_USER         shell account on each VM (must have sudo for `theia dir`)
#   SSHPASS          password for that account, consumed by sshpass
#   CBOMKIT_HOST     host running cbomkit-theia + the asset_inventory API
#   CORE_HOST        host running Open5GS NFs   (target of /etc/open5gs scan)
#   RAN_HOST         host running UERANSIM      (target of /opt/UERANSIM scan)
#
# Optional env:
#   INGEST_BASE      override URL of the ingester (default http://${CBOMKIT_HOST}:8082)
#   MCP_DB_PW        only used in the final verification hint printed at the end
#
# Usage:
#   SSH_USER=... SSHPASS=... CBOMKIT_HOST=... CORE_HOST=... RAN_HOST=... \
#     ./ingest-telco-stack.sh

set -euo pipefail

: "${SSH_USER:?SSH_USER not set}"
: "${SSHPASS:?SSHPASS not set}"
: "${CBOMKIT_HOST:?CBOMKIT_HOST not set}"
: "${CORE_HOST:?CORE_HOST not set}"
: "${RAN_HOST:?RAN_HOST not set}"
INGEST_BASE="${INGEST_BASE:-http://${CBOMKIT_HOST}:8082}"

SSH_OPTS=(-o StrictHostKeyChecking=no -o LogLevel=ERROR)

run_remote() {
  local host="$1"; shift
  sshpass -e ssh "${SSH_OPTS[@]}" "${SSH_USER}@${host}" "$@"
}

ensure_theia() {
  local host="$1"
  if ! run_remote "${host}" 'test -x /tmp/theia'; then
    echo ">> pushing /tmp/theia to ${host}"
    local cache="/tmp/theia.cache.$$"
    sshpass -e scp "${SSH_OPTS[@]}" \
      "${SSH_USER}@${CBOMKIT_HOST}:/tmp/theia" "${cache}"
    sshpass -e scp "${SSH_OPTS[@]}" \
      "${cache}" "${SSH_USER}@${host}:/tmp/theia"
    rm -f "${cache}"
    run_remote "${host}" 'chmod +x /tmp/theia'
  fi
}

scan_and_ingest() {
  local host="$1" path="$2" source_system="$3"
  local out="/tmp/cbom-${source_system}.json"
  echo ">> scanning ${path} on ${host} -> ${out}"
  run_remote "${host}" "sudo /tmp/theia dir ${path} 2>/tmp/theia.err >${out}"
  echo ">> ingesting ${out} as source_system=${source_system}"
  run_remote "${host}" "curl -s -X POST '${INGEST_BASE}/api/cbom?sector=telco&source_system=${source_system}' -H 'Content-Type: application/json' --data-binary @${out}"
  echo
}

ensure_theia "${CORE_HOST}"
ensure_theia "${RAN_HOST}"

# Open5GS — /etc/open5gs holds yaml configs + the live TLS PKI for every NF
scan_and_ingest "${CORE_HOST}" /etc/open5gs       open5gs-2.7.6-core

# UERANSIM — install tree carries gNB+UE configs and source headers
scan_and_ingest "${RAN_HOST}"  /opt/UERANSIM      ueransim-3.2.7-ran

echo ">> done. Verify with:"
echo "   docker exec <mariadb-container> mariadb -u<db-user> -p\"\$MCP_DB_PW\" PQREACT \\"
echo "     -e \"SELECT cbom_doc_id, COUNT(*) n FROM crypto_assets WHERE sector='telco' GROUP BY cbom_doc_id;\""
