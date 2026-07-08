# UC3 portal runbook

Operational runbook for the Use Case 3 (Campaign A) demo: 5G slicing +
Hyperledger Besu IBFT 2-node DLT + Kyber-768 KEM daemon + StrongSwan
IPsec tunnel + katana-pqc-kpi UDP collector. Pairs with
[`UC3_MCP_INTEGRATION_PLAN.md`](UC3_MCP_INTEGRATION_PLAN.md) (the
chat/MCP wiring side).

This document is the answer to the question "I want to fire up UC3 and
make sure it's working — where do I go and what do I check?".

---

## 1. Portal map (URLs to open)

All portals listed here were verified reachable on 2026-05-08 from
`<cbomkit-host>` (which has VPN routing to every testbed VM). Replace
`<katana-nbi-host>`, `<core-vm-host>`, `<gpu-vm-host>` with the live
IPs listed in [`PQREACT_TESTBED_STATE.md`](../PQREACT_TESTBED_STATE.md).

### 1.1 Katana stack (on `<katana-nbi-host>`)

| Service | URL | Status check |
|---|---|---|
| **katana-swagger** (API portal) | `http://<katana-nbi-host>:8001/` | `curl -fsS http://<katana-nbi-host>:8001/` → 200, ~1.4 KB |
| **katana-nbi** (slice CRUD) | `http://<katana-nbi-host>:8000/api/slice` | `curl -s http://<katana-nbi-host>:8000/api/slice \| jq length` |
| **katana-grafana** | `http://<katana-nbi-host>:3000/` (login `admin`/`admin`) | redirects to `/login` (302) |
| **katana-prometheus** | `http://<katana-nbi-host>:9090/` | `curl -s 'http://.../api/v1/targets' \| jq '.data.activeTargets \| length'` → 4 |
| **katana-alertmanager** | `http://<katana-nbi-host>:9093/` | `curl -s 'http://.../api/v2/status' \| jq .cluster.status` → `"ready"` |
| **katana-nfv_mon** | `http://<katana-nbi-host>:8002/` | `curl -fsS http://.../` → 200 |

The Katana CLI (`katana-cli`) is bundled in the same docker-compose stack
as the NBI but is local-only (no exposed port).

### 1.2 UC3 data plane (on `<core-vm-host>` + `<ran-vm-host>`)

| Service | URL / endpoint | Verb |
|---|---|---|
| **katana-pqc-kpi** UDP ingest | `<core-vm-host>:5005/UDP` | `nc -u <core-vm-host> 5005 <<<'{"kpi_id":"KPI-UC3-03",...}'` |
| **katana-pqc-kpi** HTTP read | `http://<core-vm-host>:8080/api/{health,events,stats,clear}` | `curl -fsS http://<core-vm-host>:8080/api/health` |
| **katana-pqc-return** | `http://<core-vm-host>:4443/{,/health,/metrics}` | `POST /log` to write, `GET /metrics` to read |
| **Besu validator (core)** | `http://<core-vm-host>:8545` (JSON-RPC) | `curl -X POST -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'` |
| **Besu P2P (core)** | `<core-vm-host>:30303` | `nc -zv <core-vm-host> 30303` |
| **Besu validator (RAN)** | `http://<ran-vm-host>:8545` | (mirror — same RPC methods) |
| **Kyber-768 KEM daemon** | `<ran-vm-host>:9799` | requires `kyber_exchange.py --mode client` |
| **iperf3 servers** | `<ran-vm-host>:5201/5202/5203` | `iperf3 -c <ran-vm-host> -p 5201 -u -b 2M -t 15` |

### 1.3 PQ-REACT chat (on `<gpu-vm-host>`)

| Service | URL | Notes |
|---|---|---|
| Chat UI + REST | `http://<gpu-vm-host>:8081/` | static SPA + `POST /chat`; `mode=orchestrator` for the 3-specialist routing path |
| Specialist status | `http://<gpu-vm-host>:8081/agents/v2/status` | per-specialist warm/cold + tool count + Ollama backend |

---

## 2. Bring-up checklist (cold start)

Run these once after a cold reboot of the testbed. Most steps are
idempotent; the script wrapping them lives at
`KatanaSliceManagerv2/platform/bin/`.

### 2.1 On `<katana-nbi-host>`

```bash
cd /opt/katana
DOCKER_TAG=test docker compose -f docker-compose.yaml up -d --build \
  mongo zookeeper kafka katana-nbi katana-mngr \
  katana-swagger katana-grafana katana-prometheus \
  katana-alertmanager katana-nfv_mon katana-pqc-kpi katana-pqc-return
docker compose ps
```

Expect 12 services up. The `katana-pqc-kpi` and `katana-pqc-return`
containers run here too — they're not the same as the testbed's
production katana-pqc-kpi running on `<core-vm-host>`. Both can coexist;
the production one is what the UC3 demo emits to.

### 2.2 On `<core-vm-host>`

Open5GS, Besu node 1, kyber_exchange (client side), iperf adapters all
run as native systemd services on this VM:

```bash
sudo systemctl status open5gs-amfd open5gs-smfd-iot open5gs-smfd-research open5gs-smfd-video
sudo systemctl status besu-node1.service
sudo systemctl status katana-ansible-api.service katana-metrics.service
docker ps   # katana-pqc-kpi:8080 + katana-pqc-return:4443
```

### 2.3 On `<ran-vm-host>`

UERANSIM, Besu node 2, Kyber server, iperf3 servers:

```bash
sudo systemctl status ueransim-gnb.service ueransim-ue.service
sudo systemctl status besu-node2.service kyber-kem.service
sudo systemctl status 'kyber-server-multi@k512.service' 'kyber-server-multi@k1024.service'
sudo systemctl status qujata-iperf3-server.service
```

### 2.4 StrongSwan IPsec (both VMs)

```bash
sudo swanctl --list-conns
sudo swanctl --list-sas | grep ESTABLISHED
```

The `besu-pqc` IKEv2 SA must be **ESTABLISHED** before Besu P2P can
traverse the tunnel. `net_peerCount` returning `0x0` instead of `0x1`
is the canonical sign that the SA dropped.

### 2.5 PQ-REACT chat (on `<gpu-vm-host>`)

```bash
sudo systemctl status pqreact-mcp-client.service
curl -s http://localhost:8081/ -o /dev/null -w 'http=%{http_code}\n'
```

---

## 3. Verification battery (run-once smoke test)

Two equivalent batteries: **direct API** and **via the PQ-REACT chat**.
Run both — discrepancies between them are the most useful signal that
the chat tool wiring drifted from reality.

### 3.1 Direct API battery (10 tests, all under 1 minute)

Result column captures the actual values seen on 2026-05-08.

| # | Endpoint | Command | Expected |
|---|---|---|---|
| T1 | swagger | `curl -fsS http://<katana-nbi-host>:8001/` | 200, ~1.4 KB |
| T2 | grafana | `curl -fsS -o /dev/null -w '%{http_code}' http://<katana-nbi-host>:3000/` | 302 (redirect to /login) |
| T3 | prometheus targets | `curl -s 'http://<katana-nbi-host>:9090/api/v1/targets' \| jq '.data.activeTargets \| length'` | **4** active targets (`katana`, `katana-nfv_mon` UP; `katana_pqc_kpi`, `katana_pqc_return` **DOWN** — see §4) |
| T4 | alertmanager | `curl -s 'http://<katana-nbi-host>:9093/api/v2/status' \| jq .cluster.status` | `"ready"` |
| T5 | nfv_mon | `curl -fsS -o /dev/null -w '%{http_code}' http://<katana-nbi-host>:8002/` | 200 |
| T6 | NBI list slices | `curl -s http://<katana-nbi-host>:8000/api/slice \| jq 'map({_id,name,status}) \| .[0:3]'` | 3 slices: `uc3-slice-research`, `uc3-slice-iot`, `uc3-slice-video` all `Running` |
| T7 | NBI get slice | `SID=$(curl -s http://<katana-nbi-host>:8000/api/slice \| jq -r '.[0]._id'); curl -s "http://<katana-nbi-host>:8000/api/slice/$SID" \| jq 'keys'` | `_id, name, status` (note the field is `_id`, not `slice_id`) |
| T8 | KPI collector | `curl -s http://<core-vm-host>:8080/api/health` | `{"status":"ok","udp_listen_port":5005,"events_in_memory":50,...}` |
| T9 | KPI stats | `curl -s http://<core-vm-host>:8080/api/stats \| jq .kpi_ids` | `{"KPI-UC3-03":42, "KPI-UC3-04":8}` |
| T10 | Besu RPC | `for m in eth_blockNumber net_peerCount eth_syncing eth_chainId; do curl -s -X POST -H 'Content-Type: application/json' -d "{\"jsonrpc\":\"2.0\",\"method\":\"$m\",\"params\":[],\"id\":1}" http://<core-vm-host>:8545; echo; done` | `eth_blockNumber=0x1c5be9` (≈1.86M), `net_peerCount=0x1`, `eth_syncing=false`, `eth_chainId=0x539` (1337) |

### 3.2 PQ-REACT chat battery (7 questions, exercises the full orchestrator)

Run from `<gpu-vm-host>` against the local chat or from anywhere with
network reach:

```bash
curl -s -X POST http://<gpu-vm-host>:8081/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"<question>","mode":"orchestrator"}' \
  | jq -r '.tool_calls[].name, .response'
```

| # | Question | Expected `tool_calls` | Expected response shape |
|---|---|---|---|
| C1 | "Is the UC3 demo ready right now?" | `[uc3_health]` | ✅ checklist, 3-4 prerequisites all PASS |
| C2 | "What block is the UC3 blockchain at and how many peers?" | `[uc3_blockchain_status]` | block_height (decimal), peer_count, status |
| C3 | "Summarise the KPI-UC3-04 events for the last 50 samples" | `[uc3_kpi_events]` (alias → `uc3_kpi_summary`) | per-(kpi_id, algorithm) rollup table |
| C4 | "Show me the latest UC3 pilot artifacts" | `[uc3_list_artifacts]` | rolled-up artifact list, or graceful "not configured" |
| C5 | "How many peers does the besu validator have?" (no `uc3` keyword) | `[uc3_health]` | readiness table surfaced via auto-surface |
| C6 | "List all the katana slices and their status" | `[katana_list_slices]` (or `query_slices` alias) | 3 slices Running |
| C7 | "Show me the KPI-UC3-03 transaction receipt latency events" | `[uc3_kpi_summary]` | filtered rollup for KPI-UC3-03 |

All 7 chat queries returned grounded answers in the 2026-05-08 smoke
run. The orchestrator routes via the regulation specialist (which owns
the `KATANA` category and carries the 13 `katana_*`+`uc3_*` tools after
the routing-bug fix in commit 315d0b8).

---

## 4. Known issues + fixes

### 4.1 Prometheus targets `katana_pqc_kpi` + `katana_pqc_return` show DOWN

`http://<katana-nbi-host>:9090/api/v1/targets` lists 4 active targets:

| job | health | scrapeUrl |
|---|---|---|
| `katana` | ✅ up | `http://localhost:9090/metrics` |
| `katana` | ✅ up | `http://katana-nfv_mon:8002/metrics` |
| `katana_pqc_kpi` | ❌ down | `http://katana-pqc-kpi:8080/metrics` |
| `katana_pqc_return` | ❌ down | `http://katana-pqc-return:4443/logs/metrics` |

**Root cause**: the scrape URLs use the docker-compose service names
`katana-pqc-kpi` / `katana-pqc-return`. Prometheus on `<katana-nbi-host>`
resolves them on its docker network, but the **production** UC3
emitters are the containers running on `<core-vm-host>` (NOT the ones
on `<katana-nbi-host>`). The names don't resolve across host networks.

**Impact**: zero on the UC3 demo. The KPI events still flow over UDP to
the `<core-vm-host>` collector, which is what `uc3_kpi_summary` and
`katana_kpi_events` (the chat's tools) read from.

**Fix when you want the dashboard pulse**: edit
`prometheus/prometheus.yml` on `<katana-nbi-host>` to scrape
`http://<core-vm-host>:8080/metrics` and
`http://<core-vm-host>:4443/logs/metrics` instead of the in-stack names,
then `docker compose restart katana-prometheus`.

### 4.2 Slice id field is `_id`, not `slice_id`

`GET /api/slice` returns objects with `{_id, name, created_at, status}`.
`GET /api/slice/{_id}` works; `GET /api/slice/{slice_id}` returns
`{"error": ...}`. The chat's `katana_get_slice` tool accepts either
spelling because the model often guesses `slice_id`; internally it
falls back to the field that exists.

### 4.3 llama3.1:8b hallucinates tool names

The chat client at `<gpu-vm-host>` runs `llama3.1:8b` by default, which
sometimes invents tool names that don't exist:

| Hallucinated name | Real tool | Mitigation |
|---|---|---|
| `uc3_kpi_events` | `uc3_kpi_summary` | registered as a real `@mcp.tool` alias (delegates to the original) |
| `query_slices` | `katana_list_slices` | registered as a real `@mcp.tool` alias |

Both aliases are documented in their docstrings so the catalogue isn't
confusing. They cost one extra `@mcp.tool()` decorator each and remove a
class of failure that was hard to fix at the prompt level.

### 4.4 Empty / looping tool calls

llama3.1:8b sometimes emits the JSON for a tool call inside the message
body instead of as a structured `tool_calls` field, e.g.:

```json
{"name": "uc3_blockchain_status", "args": {}}
```

The chat client's `_extract_text_tool_calls` regex catches this. It
accepts `"arguments"`, `"parameters"`, and `"args"` as the args key —
the `"args"` form was added 2026-05-08 after a smoke test where the
model used that key consistently and `tool_calls` came back empty.

If a tool call still loops past `_MAX_TOOL_HOPS`, the agent loop now
returns the **captured tool output from the first hop** (instead of the
cryptic "couldn't reach a final answer" string). The auto-surface
heading list includes `### UC3 readiness`, `### UC3 blockchain`,
`### UC3 KPI summary`, etc., so the capture mechanism fires for UC3
outputs.

---

## 5. Tear-down

The stack is designed to stay up; tear-down is for forced redeploy.

```bash
# Katana
ssh <user>@<katana-nbi-host> 'cd /opt/katana && docker compose down'

# Core VM (Open5GS, Besu, kyber)
ssh <user>@<core-vm-host> 'sudo systemctl stop open5gs-* besu-node1.service'
ssh <user>@<core-vm-host> 'docker stop katana-pqc-kpi katana-pqc-return'

# RAN VM
ssh <user>@<ran-vm-host> 'sudo systemctl stop ueransim-* besu-node2 kyber-kem.service'
```

`besu-node1` and `besu-node2` keep their chain-data volumes across
restarts; the chain doesn't reset to genesis unless you rm those
volumes too.

---

*Runbook written 2026-05-08 alongside the live UC3 verification battery
(10 direct API tests + 7 PQ-REACT chat tests, all PASS).*
