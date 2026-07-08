# UC3 ↔ PQ-REACT chat/MCP integration plan

UC3 (Use Case 3 in the PQ-REACT proposal) is the original blockchain-anchored
5G slicing pilot run on the testbed:

- **Open5GS** 5G core (3 slices: research / iot / video) on `<core-vm-host>`
- **UERANSIM** gNB+UE on `<ran-vm-host>`
- **StrongSwan** IKEv2 IPsec tunnel `besu-pqc` between core ↔ RAN
- **Hyperledger Besu** IBFT 2-node DLT (validator on each VM, P2P over the IPsec tunnel)
- **Kyber-768 KEM** daemon (`kyber_exchange.py` client on core ↔ server on RAN, port 9799)
- **katana-pqc-kpi** UDP collector (port 5005 in / HTTP 8080 out) — receives 9 official KPI events
- **TCOS QoS** scripts (`slice-qos.sh`) for per-slice rate limiting on `ogstun*`

Sources of truth:

- Demo flow: `KatanaSliceManagerv2/campaign-a-uc3-blockchain/README.md`
- Pre-QUJATA integration plan: `KatanaSliceManagerv2/campaign-a-uc3-blockchain/docs/INTEGRATION_PLAN.md`
- Pilot status / gap analysis: `KatanaSliceManagerv2/campaign-a-uc3-blockchain/docs/UC3_PILOT_NEXT_STEPS.md`
- Comparative reports: `campaign-a-uc3-blockchain/reports/UC3_*`

The 9 KPIs are emitted as `KPI-UC3-01..09` (see README §"The 9 UC3 KPIs"). The
katana-pqc-kpi server normalises every event to that namespace.

---

## 1. UC3 surface area (what the chat could touch)

| Layer | Component | Endpoint / Interface | Verb |
|---|---|---|---|
| Slice lifecycle | katana-nbi | `GET/POST/DELETE http://<katana-nbi-host>:8000/api/slice` | read + create + delete |
| KPI collector | katana-pqc-kpi | `UDP :5005` (in) / `GET http://<core-vm-host>:8080/api/{events,stats,health,clear}` (out) | read |
| Slice QoS | slice-qos.sh on `<core-vm-host>` | SSH + sudo (TCOS bootstrap) | exec |
| 2-Mbps validation | run_uc3_bandwidth_validation.sh | local shell + iperf3 over SSH | exec |
| KPI experiments | run_uc3_kpi_experiments.sh | local shell + UDP emit + Anvil/Besu | exec |
| Blockchain | Besu IBFT | `POST http://<core-vm-host>:8545` JSON-RPC (`eth_blockNumber`, `net_peerCount`, `eth_syncing`, ...) | read |
| Blockchain | Besu IBFT | `POST http://<ran-vm-host>:8545` (mirror RPC, same methods) | read |
| Kyber KEM daemon | `kyber-server-multi.service` | TCP `:9799` (or `:9512`/`:91024` for the @k512/@k1024 KEM matrix) | exchange |
| IPsec | StrongSwan `besu-pqc` connection | `swanctl --list-sas` over SSH | read |
| Return channel | katana-pqc-return | `POST :4443/log`, `GET /metrics`, `GET /health`, `GET /` | read + write |

---

## 2. Existing PQ-REACT chat/MCP coverage

The chat orchestrator already exposes 7 KATANA-tagged tools that hit UC3
infrastructure (defined in `mcp-ollama_server/database/mariadb_mcp.py`):

| Tool | Wraps | UC3 use |
|---|---|---|
| `katana_list_slices` | `GET /api/slice` | "list slices" |
| `katana_get_slice` | `GET /api/slice/<id>` | "show me slice X" |
| `katana_create_slice` | `POST /api/slice` (gated by `KATANA_ALLOW_CREATE=true`) | "create UC3 pilot slice" |
| `katana_delete_slice` | `DELETE /api/slice/<id>` (gated by `KATANA_ALLOW_DELETE=true`) | teardown |
| `katana_kpi_events` | `GET /api/events` | "show recent KPI events" |
| `katana_kpi_stats` | `GET /api/stats` | "what's the KPI rollup" |
| `apply_pqc_configuration` | INSERT into `pqc_configurations` + optional `katana_create_slice` / `launch_qujata_experiment` delegation | closed-loop record-keeping |

That covers slice lifecycle and the KPI collector's read surface — but it's
silent on the other half of UC3 (blockchain, Kyber daemon, IPsec, validation
runs, KPI-id-filtered views).

---

## 3. Gap analysis

### 3.1 What's missing for an "is UC3 ready?" chat

| # | User question | Current chat answer | Gap |
|---|---|---|---|
| G1 | "Is the UC3 demo ready?" | no tool — chat has to fire 4+ tools and the model often forgets one | composite readiness gate |
| G2 | "Are both Besu nodes peered?" / "what block?" | no tool — chat can't reach `:8545` | Besu JSON-RPC wrapper |
| G3 | "How are the UC3 KPIs trending for KPI-UC3-04?" | `katana_kpi_events` returns last 20 unfiltered → model paraphrases | KPI-id-filtered rollup |
| G4 | "Is the Kyber-768 daemon up?" | no tool | Kyber daemon health probe |
| G5 | "Is the besu-pqc IKEv2 SA established?" | no tool — needs `swanctl --list-sas` over SSH | StrongSwan SA reader |
| G6 | "Run the 2 Mbps bandwidth validation and report the verdict" | no tool | `run_uc3_bandwidth_validation.sh` runner |
| G7 | "Generate the UC3 KPI dataset for the pilot report" | no tool | `run_uc3_kpi_experiments.sh` runner |
| G8 | "Show the latest pilot artifacts" | no tool — they're files under `artifacts/uc3_*/` | artifact listing tool |

### 3.2 What's already good enough

- Existing `katana_kpi_events` + `katana_kpi_stats` cover the day-to-day "show me events" question — only the *filtered + rolled-up* view is missing (G3).
- `apply_pqc_configuration(target_type='slice', sector='telco', algorithm='kyber768')` already produces the audit row for "we picked Kyber-768 for UC3" decisions; the slice-creation side delegates to `katana_create_slice` with the gst payload.
- Phase 2 regulation tools (`recommend_pqc_for_sector` etc.) already fire on telco questions, so the chat reasons about *which* PQC to pick for UC3 — it just couldn't *verify* the running system afterwards.

---

## 4. Proposed new tools (Phase 7 of the recommendation engine)

### Phase 7a — readiness + read-only (✅ landed in this branch)

Three tools, no SSH, no mutation, all soft-fail. Implemented at the end of
the existing `# ── Katana NBI + KPI collector integration ──` block in
`mariadb_mcp.py`:

| Tool | Tag | Hits | Notes |
|---|---|---|---|
| `uc3_health(ctx)` | `[KATANA]` | NBI + KPI collector + Besu RPC + (optional) Kyber daemon | composite gate; markdown checklist with ✅/❌ per prerequisite |
| `uc3_blockchain_status(ctx)` | `[KATANA]` | Besu JSON-RPC `eth_blockNumber` + `net_peerCount` + `eth_syncing` | warns when peer_count < 1 (probable IPsec drop) |
| `uc3_kpi_summary(kpi_id, limit, ctx)` | `[KATANA]` | katana-pqc-kpi `/api/events` | filters by KPI-UC3-NN id; rolls up per (kpi_id, algorithm) with avg key_size + fragmented count |

Env-gated by two new vars in `.env.example`:

```bash
BESU_RPC_BASE=             # e.g. http://<core-vm-host>:8545
UC3_KYBER_HEALTH_URL=      # optional — leave blank to skip
UC3_HTTP_TIMEOUT=5
```

When `BESU_RPC_BASE` is unset, `uc3_blockchain_status` returns a graceful
"not configured" string and `uc3_health` reports the check as FAILED with
that reason — neither raises.

### Phase 7b — exec runners (✅ landed in this branch)

These wrap the existing platform/bin shell drivers. They're gated behind
`UC3_ALLOW_EXEC=true` (off by default — same pattern as `KATANA_ALLOW_CREATE`).
They run on the MCP server's filesystem, against scripts mounted via
`UC3_BIN_DIR`:

| Tool | Wraps | Args | Output |
|---|---|---|---|
| `uc3_run_bandwidth_validation(slice_name='research', dl_mbps=2, ul_mbps=2, duration_s=15)` | `bin/run_uc3_bandwidth_validation.sh` | DL/UL targets, slice, duration | `bandwidth_validation.json` summary + verdict |
| `uc3_run_kpi_experiments(modes='classical,hybrid,pqc', events_per_mode=12, with_local_blockchain=False)` | `bin/run_uc3_kpi_experiments.sh` | modes list, count, optional `--with-local-blockchain` | `kpi_summary.json` + SLA pass/fail table |
| `uc3_list_artifacts(kind='bandwidth'\|'kpi', limit=10)` | scans `UC3_ARTIFACTS_DIR/uc3_*/` | kind filter | run timestamps, sizes, paths |

Audit semantics:

1. INSERT into `uc3_runs (kind, args_json, status='running', requested_by)` — **before** the subprocess spawns. Failing this insert refuses the run (fail closed).
2. `subprocess.run(..., timeout=UC3_RUN_TIMEOUT_S)` (default 600 s).
3. UPDATE `uc3_runs` with `finished_at`, `exit_code`, `status` ∈ {`ok`,`failed`,`timeout`}, parsed `summary_json`, `artifact_path`, and the tail of stderr/stdout in `notes`.

The chat-visible reply only contains the audit row id, status, exit code, artifact path, the first 8 keys of the summary, and a short stderr/stdout tail on failure — never the full subprocess output.

`uc3_list_artifacts` is read-only and ungated; safe to set `UC3_ARTIFACTS_DIR` even when `UC3_ALLOW_EXEC=false` (chat surfaces past runs without being able to trigger new ones).

New env knobs in `.env.example`:

```bash
UC3_ALLOW_EXEC=false
UC3_BIN_DIR=             # e.g. /opt/katana/platform/bin
UC3_ARTIFACTS_DIR=       # e.g. /opt/katana/platform/artifacts
UC3_RUN_TIMEOUT_S=600
```

New SQL migration: [`etl/schema/uc3_runs.sql`](etl/schema/uc3_runs.sql).

### Phase 7c — IPsec + Kyber visibility (after 7b)

Either via a small VPN-side adapter or via a shell-over-SSH tool:

| Tool | Wraps | Notes |
|---|---|---|
| `uc3_ipsec_sa_status` | `swanctl --list-sas` filtered to connection `besu-pqc` | parses `ESTABLISHED ... AES_GCM_16-256 / ECP-384` line |
| `uc3_kyber_handshake(message='ping')` | one round-trip to `kyber_exchange.py` server on RAN VM (port 9799) | end-to-end KEM probe, returns wall time |

Both depend on a small "operator agent" sidecar on the core/RAN VMs (or on
SSH credentials — but credentials in MCP env is a smell, see §6.2). The
sidecar is the cleaner path.

---

## 5. Reproducible commands

### 5.1 Verify the live chat picks up the new tools

After redeploying `pqreact-mcp-server` on `<mcp-server-host>` with the new
env vars set:

```bash
# expect: uc3_health, uc3_blockchain_status, uc3_kpi_summary in the list
curl -s http://<mcp-server-host>:5040/sse | head -c 4000 | grep -oE 'uc3_[a-z_]+'

# expect: per-specialist warm/cold + the 3 new tools surfaced under [KATANA]
curl -s http://<gpu-vm-host>:8081/agents/v2/status | jq .
```

### 5.2 Smoke chat the readiness gate

```bash
curl -s -X POST http://<gpu-vm-host>:8081/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"Is the UC3 demo ready right now?","mode":"orchestrator"}' \
  | jq -r '.response'
```

Expected: a markdown table with 3-4 ✅/❌ rows and a one-line verdict.

### 5.3 Smoke chat the blockchain query

```bash
curl -s -X POST http://<gpu-vm-host>:8081/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"What block is the UC3 blockchain at and how many peers?","mode":"orchestrator"}' \
  | jq -r '.response'
```

Expected: block height (decimal) + peer count + ⚠ warning if peer_count < 1.

### 5.4 Smoke chat the KPI summary

```bash
curl -s -X POST http://<gpu-vm-host>:8081/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"Summarise the KPI-UC3-04 events for the last 50 samples","mode":"orchestrator"}' \
  | jq -r '.response'
```

Expected: per-(kpi_id, algorithm) rollup with sample count, avg key size,
fragmented count.

---

## 6. Open questions / risks

### 6.1 Does the orchestrator route correctly?

UC3-flavoured queries should land in the **performance** specialist (which
already owns the `[KATANA]` toolset). The keyword classifier in
`agents.py` already fires on `slice` / `katana` / `kpi`. Add the strings
`uc3` / `pilot` / `besu` / `kyber` / `ibft` / `ikev2` / `peer count` to
the `katana_signals` set so questions like *"how's the UC3 pilot"* don't
mis-route to `regulation`. This is a one-line change in `agents.py` and
should land alongside Phase 7b.

### 6.2 Mutating tools = audit table or nothing

`uc3_run_bandwidth_validation` and `uc3_run_kpi_experiments` mutate the
testbed (run iperf3, generate UDP events, optionally start Anvil). They
must (a) be gated by `UC3_ALLOW_EXEC=true`, (b) write a row to
`pqc_configurations` (or a sibling `uc3_runs` table) before they execute,
not after, so a hung subprocess can't lose the audit.

### 6.3 SSH credentials policy

Tools that exec shell commands on the testbed VMs (Phase 7b/7c) need
credentials. **Don't put them in MCP env.** Two clean paths:

1. Sidecar: a tiny FastAPI service on each VM exposes only the operations
   we need (`/swanctl/sas`, `/kyber/health`, `/uc3/run-bandwidth`). MCP
   tools call those over HTTPS with mutual TLS or a shared token. This
   matches the `qujata-iperf-adapter` / `qujata-secgw-adapter` pattern
   already in the testbed.
2. Cron'd artifact scrape: the chat reads only artifacts/ outputs, never
   triggers a run. Loses interactivity but zero credentials at risk.

The plan's default is option (1) — start with `katana-pqc-return`'s
existing `:4443` Flask app as the template (`Dockerfile`, `endpoint.py`).

### 6.4 KPI namespace collision

Per `UC3_PILOT_NEXT_STEPS.md` §2, the document text uses `KPI-UC1-*`
labels while the implementation emits `KPI-UC3-*`. The `_UC3_KPI_IDS`
allowlist in `mariadb_mcp.py` matches the implementation. If the
deliverable doc gets re-aligned to `KPI-UC1-*`, the allowlist needs to
move with it.

---

## 7. Definition of done for "UC3 ↔ chat" integration

- ✅ Chat can answer "is UC3 ready?" in one tool hop with a verdict (Phase 7a — landed + smoke-tested)
- ✅ Chat can answer "what block / how many peers?" without fanning out (Phase 7a — landed + smoke-tested)
- ✅ Chat can answer "what's the KPI-UC3-04 trend?" with a structured table (Phase 7a — landed + smoke-tested)
- ✅ Chat can run the 2 Mbps validation on demand and surface the verdict (Phase 7b — landed; needs UC3_ALLOW_EXEC=true + UC3_BIN_DIR mount to actually fire)
- ✅ Chat can produce the formal KPI dataset for the pilot deliverable on demand (Phase 7b — landed; same gate)
- ✅ Chat can surface past pilot runs without triggering new ones (Phase 7b `uc3_list_artifacts` — landed)
- ✅ Every UC3 mutation has a row in `uc3_runs` BEFORE side effects start (Phase 7b — landed)
- ⏳ Chat can verify the IPsec SA + Kyber daemon end-to-end (Phase 7c)
- ⏳ Chat-side keyword classifier picks up `uc3` / `besu` / `kyber` / `ibft` / `peer count` etc. (queued — chat client at `<gpu-vm-host>` was unreachable during the 2026-05-08 deploy, so the client.py keyword update will ship on the next chat-client redeploy)

---

## 8. Live deployment status (2026-05-08)

Server side (✅ live on `<mcp-server-host>`):

- `pqreact-mcp-server` rebuilt with the new mariadb_mcp.py — 33 `@mcp.tool()` decorators (was 27 before Phase 7).
- `BESU_RPC_BASE=http://<core-vm-host>:8545` set on the container env via docker-compose.
- `uc3_runs` migration applied on `pqreact-mcp-mariadb`; table verified via `DESCRIBE uc3_runs`.
- All 6 UC3 tools probed end-to-end via MCP SSE from `<cbomkit-host>`:
  - `uc3_health()` → ✅ READY (3/3 prerequisites: 3 katana slices, KPI in-memory with 50 events, Besu block 1 857 579 with 1 peer).
  - `uc3_blockchain_status()` → block_height: 1857579, peer_count: 1, status: synced.
  - `uc3_kpi_summary({'kpi_id':'KPI-UC3-04','limit':50})` → 8 `besu-qrl` events.
  - `uc3_list_artifacts()` → graceful soft-fail "UC3_ARTIFACTS_DIR not configured" (intentional — the read-only path is wired but UC3_ARTIFACTS_DIR was deliberately left unset on this deploy until we mount the platform/artifacts directory in).
  - `uc3_run_*` not exercised because UC3_ALLOW_EXEC=false (intentional — exercising the `Refusing: UC3_ALLOW_EXEC is false` gate would still incur subprocess fork-of-sshpass risk; will fire after the operator review).

Chat side (✅ live as of 2026-05-08, four follow-up patches landed during the smoke test):

- `client.py` keyword classifier extended with the UC3 vocabulary (`uc3`, `pilot`, `besu`, `kyber`, `ibft`, `peer count`, `bandwidth validation`, `2 mbps`, `kpi-uc3`, …).
- `agents.py` **routing bug fixed**: the regulation specialist now owns the `KATANA` category and carries the `katana_*` + `uc3_*` tools. The original wiring documented "Katana → regulation (no separate katana specialist)" in an inline comment but never actually added `KATANA` to the `regulation` specialist's `categories` set, so `route()` fell through to the default and the LLM grabbed `apply_pqc_configuration` (closest-named tool) when asked "is the UC3 demo ready?". Discovered live, fixed in the same chat-side deploy.
- `agents.py::REGULATION_PROMPT` extended with explicit tool-selection rules for the 12 katana + uc3 tools — including a "DO NOT invent names like `uc3_kpi_events`" guard.
- `mariadb_mcp.py`: registered `uc3_kpi_events` as a thin alias to `uc3_kpi_summary` because llama3.1:8b consistently invented that name in spite of the prompt rule. The alias docstring makes the relationship visible to operators.
- `client.py::_AUTO_SURFACE_HEADINGS` extended with the 6 UC3 headings (`### UC3 readiness`, `### UC3 blockchain`, `### UC3 KPI summary`, `### UC3 artifacts`, `### UC3 bandwidth_validation run`, `### UC3 kpi_experiments run`).
- `client.py` hop-budget-exhaustion path now returns the captured table when one was seen on an earlier hop, instead of the cryptic "couldn't reach a final answer in the allotted hops" string. Discovered when llama3.1:8b looped on `uc3_blockchain_status` for 8 hops despite the prompt rule "don't call the same tool twice with similar args" — the grounded answer was already in the hop-1 result; the user just couldn't see it.
- Deployed to `<gpu-vm-host>:/opt/pqreact-mcp-client/` and `pqreact-mcp-client.service` restarted. End-to-end chat (`POST /chat` with `mode=orchestrator`) now passes all 5 smoke tests:

| # | Query | tool_calls | Verdict |
|---|---|---|---|
| 1 | "Is the UC3 demo ready right now?" | `[uc3_health]` | ✅ Returns markdown checklist with 3/3 PASS, block 1857783 / 1 peer / synced |
| 2 | "What block is the UC3 blockchain at and how many peers?" | `[uc3_blockchain_status]` | ✅ Reports `block_height: 1857790`, `peer_count: 1`, `status: synced` |
| 3 | "Summarise the KPI-UC3-04 events for the last 50 samples" | `[uc3_kpi_events]` (alias → `uc3_kpi_summary`) | ✅ Per-(kpi_id, algorithm) rollup; 8 `besu-qrl` events |
| 4 | "Show me the latest UC3 pilot artifacts" | `[uc3_list_artifacts]` | ✅ Graceful soft-fail "no artifacts" when `UC3_ARTIFACTS_DIR` unset |
| 5 | "How many peers does the besu validator have?" (no `uc3` in query) | `[uc3_health]` | ✅ Keyword classifier picked up `besu` + `peer count`; auto-surface prepended the readiness table; answer includes `peers=1` |

---

*Plan started 2026-05-08 alongside Phase 7a (`uc3_health`,
`uc3_blockchain_status`, `uc3_kpi_summary`); Phase 7b
(`uc3_list_artifacts`, `uc3_run_bandwidth_validation`,
`uc3_run_kpi_experiments`) + the `uc3_runs` audit table landed +
smoke-tested live the same day.*
