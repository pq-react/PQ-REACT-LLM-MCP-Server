#!/usr/bin/env bash
# Idempotent install of the PQ-REACT MCP server stack (MariaDB + FastMCP SSE)
# onto a target VM via Docker compose.
#
# Usage:
#   ./deploy/install-server.sh <TARGET_HOST> <SSH_USER>
#   MCP_SERVER_HOST=<host> ./deploy/install-server.sh
#
# Env (all optional):
#   SSHPASS=...    enables sshpass; otherwise key auth required
#
# After bring-up, load the schema seed:
#   docker exec -i pqreact-mcp-mariadb \
#       sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" PQREACT' \
#       < mcp-ollama_server/database/pqreact_export.sql

set -euo pipefail

TARGET="${1:-${MCP_SERVER_HOST:?MCP_SERVER_HOST required (target VM IP/hostname)}}"
USER_="${2:?SSH_USER required (positional arg 2)}"
REMOTE_DIR=/opt/pqreact-mcp-server
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -n "${SSHPASS:-}" ]]; then
    SSH="sshpass -e ssh -o StrictHostKeyChecking=no"
    SCP="sshpass -e scp -o StrictHostKeyChecking=no"
    export SSHPASS
else
    SSH="ssh -o StrictHostKeyChecking=no"
    SCP="scp -o StrictHostKeyChecking=no"
fi

echo "[install-server] target=${USER_}@${TARGET} dir=${REMOTE_DIR}"

$SSH "${USER_}@${TARGET}" "sudo mkdir -p ${REMOTE_DIR} && sudo chown ${USER_}:${USER_} ${REMOTE_DIR}"

# Copy stack files
for f in docker-compose.yml Dockerfile.server serve_sse.py requirements-server.txt .env.example; do
    $SCP "${REPO_ROOT}/${f}" "${USER_}@${TARGET}:${REMOTE_DIR}/${f}"
done
$SCP -r "${REPO_ROOT}/mcp-ollama_server" "${USER_}@${TARGET}:${REMOTE_DIR}/"

# Generate .env if absent (random passwords); start stack
$SSH "${USER_}@${TARGET}" bash -se <<EOF
set -euo pipefail
cd "${REMOTE_DIR}"
if [[ ! -f .env ]]; then
  cp .env.example .env
  ROOT_PW="\$(openssl rand -hex 24)"
  USER_PW="\$(openssl rand -hex 24)"
  sed -i "s|^MCP_DB_ROOT_PW=.*|MCP_DB_ROOT_PW=\${ROOT_PW}|" .env
  sed -i "s|^MCP_DB_PW=.*|MCP_DB_PW=\${USER_PW}|" .env
  echo "Generated random DB passwords in .env"
fi
docker compose up -d --build
echo "Stack up. SSE endpoint: http://${TARGET}:5040/sse"
EOF
