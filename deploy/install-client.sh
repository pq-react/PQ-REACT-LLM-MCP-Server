#!/usr/bin/env bash
# Idempotent install of the PQ-REACT MCP chat client (FastAPI + Ollama)
# onto a target VM (the host that runs Ollama).
#
# Usage:
#   ./deploy/install-client.sh <TARGET_HOST> <SSH_USER>
#   MCP_CLIENT_HOST=<host> ./deploy/install-client.sh
#
# Env (all optional):
#   SSHPASS=...             enables sshpass; otherwise requires key auth
#   MCP_SERVER_URL=...      MCP SSE endpoint (no default; set in .env)
#   OLLAMA_MODEL=...        default glm-4.7-flash:q4_K_M

set -euo pipefail

TARGET="${1:-${MCP_CLIENT_HOST:?MCP_CLIENT_HOST required (target VM IP/hostname)}}"
USER_="${2:?SSH_USER required (positional arg 2)}"
REMOTE_DIR=/opt/pqreact-mcp-client
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -n "${SSHPASS:-}" ]]; then
    SSH="sshpass -e ssh -o StrictHostKeyChecking=no"
    SCP="sshpass -e scp -o StrictHostKeyChecking=no"
    export SSHPASS
else
    SSH="ssh -o StrictHostKeyChecking=no"
    SCP="scp -o StrictHostKeyChecking=no"
fi

echo "[install-client] target=${USER_}@${TARGET} dir=${REMOTE_DIR}"

$SSH "${USER_}@${TARGET}" "sudo mkdir -p ${REMOTE_DIR} && sudo chown ${USER_}:${USER_} ${REMOTE_DIR}"

# Copy client_mcp/ as the working dir
$SCP -r "${REPO_ROOT}/mcp-ollama_server/client_mcp/." "${USER_}@${TARGET}:${REMOTE_DIR}/"
$SCP "${REPO_ROOT}/requirements-client.txt" "${USER_}@${TARGET}:${REMOTE_DIR}/requirements.txt"
$SCP "${REPO_ROOT}/systemd/pqreact-mcp-client.service" "${USER_}@${TARGET}:/tmp/pqreact-mcp-client.service"

# Provision venv + systemd unit on the host. Substitute __USER__ in the
# service template with the actual SSH user before installing.
$SSH "${USER_}@${TARGET}" bash -se <<EOF
set -euo pipefail
cd "${REMOTE_DIR}"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt
sed -i "s|__USER__|${USER_}|g" /tmp/pqreact-mcp-client.service
sudo install -m 0644 /tmp/pqreact-mcp-client.service /etc/systemd/system/pqreact-mcp-client.service
sudo systemctl daemon-reload
sudo systemctl enable --now pqreact-mcp-client.service
sleep 2
systemctl is-active pqreact-mcp-client.service && echo "OK: chat UI is up on :8081"
EOF
