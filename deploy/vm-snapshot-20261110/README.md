# Testbed deployment snapshot (2026-11-10)

What was running on the PQ-REACT testbed VMs just before they were
decommissioned, and where each piece now lives in this repository.
Hosts are named by role; addresses, hostnames and credentials are
deliberately left out.

| Role | What ran there | How | Captured to |
|---|---|---|---|
| GPU / Ollama VM | Chat client (FastAPI + UI) on `:8081`, Ollama on loopback `:11434` | systemd `pqreact-mcp-client.service`, `/opt/pqreact-mcp-client`, venv | `mcp-ollama_server/client_mcp/` (UI changes ported), `patches/01-*` |
| GPU / Ollama VM | CBOM "Security Scanner" client (not running) | `~/cbom_mcp/client` | `cbom_mcp/client/` |
| MCP server VM | `pqreact-mcp-server` (FastMCP SSE `:5040`) + `pqreact-mcp-mariadb` (MariaDB 10.6, `:3307`) | docker compose, `~/mcp-deploy/server` | `Dockerfile.server`, `docker-compose.yml`, `etl/schema/`, `etl/import_device_specs.py`, `patches/02-*` |
| CBOM / asset VM | `pqreact-asset-inventory` on `:8082` | plain `docker run` | identical to `asset_inventory/` (nothing to port) |
| QUJATA VM | Older MCP deploy bundle (May 2026), not running | `~/mcp-deploy` | `etl/_db.py`, `etl/import_*.py`, `etl/schema_map.md` |

## Chat client (GPU / Ollama VM)

- Effective config: `OLLAMA_HOST=http://localhost:11434`,
  `OLLAMA_MODEL=glm-4.7-flash:q4_K_M`, `MCP_API_PORT=8081`,
  `MCP_SERVER_URL=http://<mcp-server-host>:5040/sse` (from
  `/opt/pqreact-mcp-client/.env`). The unit file matched
  `systemd/pqreact-mcp-client.service` with `__USER__` substituted.
- Ollama: stock `ollama.service`, no drop-in overrides, no Modelfiles,
  v0.16.2 on an NVIDIA L4 (24 GB). Models pulled: `glm-4.7-flash:q4_K_M`
  (the one in use), `qwen3:8b`, `qwen2.5:7b`, `qwen2.5:14b`,
  `llama3.1:8b`. No `OLLAMA_BACKENDS` / `SPECIALIST_*` overrides were set.
- `api.py` was identical to the repo. `index.html`, `script.js`,
  `style.css` and `pq-console.css` carried the "Compare algorithms" panel
  and a dark-mode-aware console skin; both are now ported. The compare
  dropdown lists ML-DSA, SLH-DSA, Falcon and HQC as well, although the
  CAM dataset it targets only has ML-KEM / BIKE / FrodoKEM / hybrid
  rows.
- Python 3.12 venv, notable pins: fastapi 0.136.3, uvicorn 0.49.0,
  mcp 1.27.2, ollama 0.6.2, pydantic 2.13.4, python-dotenv 1.2.2.
- Not ported: `patches/01-client-disable-message-size-routing.patch`.
  The deployed `agents.py` / `client.py` had `query_by_message_size` and
  the size-routing hints commented out, so the agent never chose the
  message-size tools. This looks like a demo-time experiment, and the
  public client keeps them. Apply with `git apply` to reproduce the
  deployed behaviour.

## MCP server stack (MCP server VM)

- The image was built from a copy of the core-ncsrd tree dated
  2026-05-20. Its `mariadb_mcp.py` has the same 35 tools as the repo but
  lacks the later per-source methodology helpers (`_render_by_source`,
  `_methodology_*`). The repo version is newer; no server code was
  ported. Package pins inside the container: mcp 1.27.0, pymysql 1.1.3,
  uvicorn 0.46.0, starlette 1.0.0, sse-starlette 3.4.1.
- **"unhealthy" container.** The status was a healthcheck artefact, not
  an outage: SSE kept answering. The old `curl /sse | head -c 1` probe
  never let curl exit, because `/sse` is an endless stream. Docker's 5 s
  timeout killed only the shell. The orphaned curl/head processes were
  re-parented to PID 1, `python serve_sse.py`, which does not reap
  children. 9,478 zombies filled the container's `pids.max` (9,483)
  about two days after the 2026-10-26 restart. From then on every
  healthcheck and every `docker exec` failed with
  `OCI runtime exec failed: ... procReady not received` (failing streak
  of about 41k). Fixed in `Dockerfile.server` (bounded probe) and
  `docker-compose.yml` (`init: true`).
- The deployed compose also had `extra_hosts:
  host.docker.internal:host-gateway` and forwarded `BESU_RPC_BASE`,
  `UC3_*` and `KATANA_ALLOW_CONFIGURE`. All of these are now in the
  public compose, with blank defaults in place of the testbed IPs.
- Not ported: `patches/02-server-pin-message-size-1000.patch`. A later
  (2026-10-26) edit of the server source on that VM, which was never
  rebuilt into the running image, hard-coded `message_size = 1000` into
  five queries. It also contains a bug: `query_by_security_level` builds
  `... = 1000GROUP BY ...` (missing space). A scratch copy in the home
  directory went further: it commented out `get_fastest_algorithms` and
  had an invalid `WHERE ... and source = ..., message_size = 1000`.
  That copy is not preserved.

### Database (PQREACT on MariaDB 10.6)

Row counts at capture time (schema only is in the repo; no data was
copied):

| Table | Rows | Schema in repo |
|---|---:|---|
| `performance_test` | 284 | seed dump + `etl/schema/performance_test.sql` (new) |
| `crypto_assets` | 38,162 | `etl/schema/inventory.sql` |
| `cbom_documents` | 5 | `etl/schema/inventory.sql` |
| `compliance_results` | 5 | `etl/schema/inventory.sql` |
| `obligations` | 27 | `etl/schema/regulations.sql` |
| `regulations` | 11 | `etl/schema/regulations.sql` |
| `sector_profiles` | 4 | `etl/schema/regulations.sql` |
| `device_crypto_specs` | 18 | `etl/schema/device_specs.sql` (new) |
| `pqc_configurations` | 16 | `etl/schema/pqc_configurations.sql` |
| `uc3_runs` | 0 | `etl/schema/uc3_runs.sql` |

`performance_test` by `source`: `upstream` 200 (same as the repo seed),
`cam-context-agility` 24, `campaign-b-qujata-live` 30,
`campaign-b-demo5-bench` 14, `campaign-b-demo4` 8, `campaign-b-demo5` 6,
`campaign-b-qujata` 2. The testbed table had the upstream
`cpu_frequency`, `power_core` and `power_system` columns dropped by hand.
The repo keeps them, and `etl/_db.py` copes with either shape.

The `regulations` / `obligations` / `sector_profiles` and
`device_crypto_specs` contents can be regenerated from
`etl/data/*.yaml`. `crypto_assets`, `cbom_documents` and
`compliance_results` come from CBOM ingests (`asset_inventory/`).
`pqc_configurations` holds the audit log, and the non-upstream
`performance_test` rows are measurements. None of those can be
regenerated from the repo.

## Asset inventory (CBOM / asset VM)

The source in the running container and in `~/asset-deploy` was
byte-identical to `asset_inventory/`. It was started with plain
`docker run`, equivalent to the compose `inventory` profile:

```bash
docker run -d --name pqreact-asset-inventory --restart unless-stopped \
  -p 8082:8082 \
  -e DB_HOST=<mcp-server-host> -e DB_PORT=3307 -e DB_NAME=PQREACT \
  -e DB_USER=pqreact -e DB_PASSWORD=<MCP_DB_PW> \
  pqreact-asset-inventory:latest
```

## Older deploy bundle (QUJATA VM)

This was the May 2026 pre-release layout, with a `patches/` series over
a mirrored `upstream/` tree, `server/` and `client/` env templates, and
install scripts. The patches (env-driven config, auto-connect, QUJATA
cross-tools) are already absorbed into this repo, and the install
scripts are superseded by `deploy/`. Only the ETL importers and
`schema_map.md` were new. They are now under `etl/`, with hosts and
passwords moved to env vars.

## core-ncsrd working copy (GPU / Ollama VM)

`~/PQ-REACT_MCP-Server` (remote core-ncsrd/PQ-REACT_MCP-Server, HEAD
`ee54fd3`, 2026-05-24) had uncommitted changes. Their status relative to
this repo:

- `api.py` (`/pq-console.css` route) and the new `pq-console.css`,
  `requirements.txt`: already in the repo. `requirements.txt` sits at
  the root as `requirements-client.txt`.
- `index.html`, `script.js`, `style.css`, compare-panel rules in
  `pq-console.css`: an earlier draft of the panel the GPU VM deployed.
  Now ported in its deployed form. The draft also had debug text
  ("Click any NO HELLO OK question"), which was not ported.
- `agents.py`, `client.py`: same message-size disabling as `patches/01`.
  Not ported.
- `mariadb_mcp.py`: relabelled the CAM source header from "(seconds)"
  to "(milssliseconds)". Not ported: the label has a typo, and the
  stored CAM durations (0.05 to 3.1) read as seconds for a TLS 1.3
  handshake, not milliseconds.
- Untracked `*.bak*` files: older copies of the same files. Not ported.

## Left on the VMs (not PQ-REACT MCP code)

- GPU / Ollama VM: `ollama-mcp-bridge` (third-party; mostly vendored
  dependencies), `zortenet-train`, `zt`, `test_cnn`, `benchmark.py` (a
  PyTorch GPU micro-benchmark), `env/` (venv), editor/agent state
  (`.antigravity*`, `.gemini`, `.copilot`, `.vscode-server`).
  `~/mcp-ollama_server` is a checkout of the third-party
  sethuram2003/mcp-ollama_server. It is the original upstream that this
  project's `mcp-ollama_server/` grew from: its single local commit adds
  the first FastAPI UI and MariaDB server. Its dirty files and the loose
  `~/agents.py`, `~/api.py` and `~/client.py` are older versions of
  files in this repo, with nothing missing from the repo.
  `~/mcp_workspace` was empty.
- MCP server VM: `~/mcp-deploy/upstream/.../client_mcp`. This was an
  old client copy baked into the server image and unused there.
- CBOM / asset VM: `cbomkit-theia` (third-party), `katana-portal`,
  `uc3-realapps` (UC3 demo containers `uc3-realapp-{chat,iot,video}`),
  `go/`, agent state (`.claude`, `.codex`, `.copilot`).
- QUJATA VM: the QUJATA stack (`qujata-deploy/QUJATA-Telefonica`, with
  a locally modified `api/src/services/analyze_service.py` that writes
  the `campaign-b-qujata-live` rows) and `~/cam`. `~/cam` is a copy of
  the CAM (Context Agility Manager) project, whose `pqreact_hooks/`
  write the `cam-context-agility` rows. Both belong to other components
  and need their own capture if wanted.
