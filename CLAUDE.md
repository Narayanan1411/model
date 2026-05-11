# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

**Guardient** is a Zero-Trust Telemetry Analysis Platform. It ingests raw device telemetry (network, identity, cloud, hardware) through a FastAPI gateway, streams everything through Kafka, runs it through an 8-stage pipeline of microservices that produce a **Trust Score (0–100)** per device, and serves results to a Next.js SOC dashboard.

**Stack:** FastAPI · Kafka · PostgreSQL · Next.js 14 · Python 3 · TypeScript

**Ports:** API=8000, Simulation Controller=8001 (legacy), Frontend=3000

**Detailed documentation:** `poc Guardient/docs/`
- [ARCHITECTURE.md](poc%20Guardient/docs/ARCHITECTURE.md) — components, directory layout, frontend pages
- [ML_MODEL.md](poc%20Guardient/docs/ML_MODEL.md) — Welford, z-score, trust engine math
- [DATA_FLOW.md](poc%20Guardient/docs/DATA_FLOW.md) — end-to-end data flow diagrams
- [KAFKA.md](poc%20Guardient/docs/KAFKA.md) — topics, consumer groups, producer pattern

---

## Running the System

### Recommended: Single Command (Linux)

```bash
cd "poc Guardient"
bash start.sh           # launch everything and validate
bash start.sh --restart # kill all services and relaunch from scratch
```

`start.sh` auto-detects the active network interface, brings up Docker containers, bootstraps the DB schema and Kafka topics, launches all 11 pipeline microservices + API + frontend, and runs a 7-step health validation.

### Hard Reset (wipe all state)

```bash
cd "poc Guardient"
bash hard_reset.sh         # interactive confirmation
bash hard_reset.sh --yes   # skip prompt
```

Truncates all 17 PostgreSQL tables, purges and recreates all Kafka topics, deletes ML model files, clears logs and caches. Docker containers stay running.

### Manual Infrastructure

```bash
cd "poc Guardient"
docker compose up -d           # Kafka :9092 + PostgreSQL :5432
python3 -m pipeline.topics     # create all Kafka topics
python3 -m db.db               # initialise DB schema
```

### Manual Backend + Services

```bash
cd "poc Guardient"
python3 -m uvicorn api.main:app --port 8000 --reload

python3 services/enrichment_service.py
python3 services/feature_engine.py
python3 services/ml_monitor.py
python3 services/risk_engine.py
python3 services/graph_correlator.py
python3 services/trust_engine.py
python3 services/decision_engine.py
python3 services/response_engine.py       # deprecated shim — still required
python3 services/simulation_controller.py # port 8001 (legacy)
python3 services/network_discovery_service.py
python3 services/hardware_monitoring_service.py
```

### Frontend

```bash
cd "poc Guardient/techgium frontend"
npm install
npm run dev    # → http://localhost:3000
```

### Python Dependencies

```bash
cd "poc Guardient"
pip3 install -r requirements.txt
```

### Environment Variables

```bash
export SMTP_USER="your@gmail.com"      # SMTP email alerts (response_executor)
export SMTP_PASS="your_app_password"
export NETWORK_INTERFACE="wlp1s0"      # Optional: auto-detected from ip route
export DB_HOST=localhost               # All DB vars default to "guardient"
```

API keys (`NETWORK_KEY`, `IDENTITY_KEY`, `CLOUD_KEY`, `HARDWARE_KEY`) have hardcoded development defaults in `api/main.py`.

### Test Data

```bash
cd "poc Guardient"
python3 simulate_data.py   # HTTP POST to API, exercises full pipeline end-to-end
```

---

## Architecture

### Directory Layout

```
poc Guardient/              ← all Python run from here
  api/                      ← FastAPI ingestion + frontend bridge
  pipeline/                 ← Kafka producer/consumer base (CANONICAL — never use kafka/)
  services/                 ← Pipeline microservices (11 processes)
  response_executor/        ← Active response orchestration (single source of truth)
  db/                       ← PostgreSQL connection pool + DDL + write helpers
  utils/                    ← GeoIP, DNS, MAC lookup, device classifier
  network/                  ← Scapy packet sniffer + MaxMind .mmdb files
  colud and identity/       ← Cloud/identity/hardware/temporal collectors
  techgium frontend/        ← Next.js 14 SOC dashboard
  kafka/                    ← STALE DUPLICATE — never import from here
  logs/                     ← JSONL fallback logs per telemetry category
  docs/                     ← Architecture, ML, Kafka, and data flow docs
  start.sh                  ← Full Linux stack startup with health validation
  hard_reset.sh             ← Wipes all state back to clean first-install
  docker-compose.yml        ← Kafka + PostgreSQL

new_work/                   ← (removed) LSTM autoencoder was here
```

### End-to-End Data Flow (abbreviated)

```
Collectors  →  api/main.py  →  raw_events (Kafka)
→ enrichment_service  →  enriched_events
→ feature_engine      →  feature_stream
→ ml_monitor          →  ml_scores
→ risk_engine         →  risk_scores  ─┬─► graph_correlator → graph_scores ─┐
                                        └────────────────────────────────────►┤
                                                                  trust_engine → trust_scores
→ decision_engine  →  alerts + security_actions
→ response_engine (shim)  →  response_executor  →  response_executions (Kafka + PostgreSQL)
```

**Read path (frontend):** `api/v1.py` queries PostgreSQL directly — no Kafka involved.

---

## Response Executor

`response_executor/` is the single source of truth for all response actions.

### Package layout

```
response_executor/
  executor.py   ← ResponseExecutor class + get_executor() singleton
  models.py     ← ActionStatus, APPROVAL_REQUIRED_ACTIONS, ACTION_DESCRIPTIONS
  handlers.py   ← one stub per action; replace bodies with real enforcement APIs
```

### How it works

`ResponseExecutor.execute()` entry point:
1. **Deduplication check** — if a `pending`/`running` record already exists for `(device_id, action)`, returns it immediately (no duplicate insert)
2. If `action ∈ APPROVAL_REQUIRED_ACTIONS` and `auto_approve=False` → `status=pending`, waits for SOC
3. Otherwise dispatches to `handlers.dispatch()` immediately
4. Writes to `response_executions` (PostgreSQL)
5. Updates `device_blocks` for containment actions
6. Publishes to `response_executions` Kafka topic
7. Sends SMTP alert if `trust_score < 30` (5-minute cooldown per device)

### Actions requiring SOC approval

`restrict_network`, `isolate_vlan`, `lock_account`, `firewall_block`, `kill_process`

### Always auto-approved

`allow`, `monitor`, `require_mfa`, `block_device` (manual), `unblock_device` (manual)

---

## Response Executor REST API

All routes under `/api/v1/response/...`:

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/response/executions` | List executions (`?device_id=&status=&limit=`) |
| `GET`  | `/response/executions/pending` | Pending approvals only |
| `GET`  | `/response/executions/{id}` | Single execution |
| `POST` | `/response/executions/{id}/approve` | Approve pending |
| `POST` | `/response/executions/{id}/reject` | Reject pending |
| `GET`  | `/response/blocks` | Active blocks (`?active_only=false` for history) |
| `POST` | `/response/block` | Immediately block device (admin) |
| `POST` | `/response/unblock` | Immediately unblock device (admin) |
| `POST` | `/response/execute` | Manually trigger any action |

### Simulation API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/simulation/run` | In-memory attack simulation (no ML contamination) |
| `GET`  | `/devices` | Pipeline-processed devices only (has trust_score entries) |

```jsonc
// POST /simulation/run
{ "device_id": "dev_abc123", "attack_type": "lateral_movement" }
// attack_type ∈ { c2_beaconing, credential_abuse, lateral_movement, ransomware_activity }

// Response includes: run_id, trajectory (7 points), triggered_executions
```

---

## PostgreSQL Schema

| Table | Written by | Purpose |
|-------|-----------|---------|
| `devices` | enrichment_service | Device registry |
| `device_aliases` | device_resolver (API) | MAC/IP/hostname → DGID |
| `events` | enrichment_service | Full enriched event audit log |
| `features` | feature_engine | Per-event feature vectors (JSONB) |
| `ml_scores` | ml_monitor | Anomaly scores + feature breakdown |
| `device_baselines` | ml_monitor | Per-device Welford baseline (JSONB) |
| `risk_scores` | risk_engine | Risk 0–100 + I/C/V/N category |
| `trust_scores` | trust_engine | Trust 0–100 + adjusted risk |
| `trust_states` | trust_engine | Persistent I/C/V/N state (JSONB) |
| `alerts` | decision_engine | Fired alerts |
| `response_executions` | **response_executor** | Full execution audit log |
| `device_blocks` | **response_executor** | Active/historical device block registry |
| `feedback_weights` | feedback_service | Per-detection severity weights |
| `feedback_labels` | feedback_service | Analyst labels for active learning |
| `simulation_runs` | simulation_controller | Legacy simulation records |
| `response_actions` | response_engine (old) | Legacy — no longer written |
| `graph_correlation_events` | graph_correlator | Lateral movement chains |

---

## Kafka Topic Map

| Topic | Producer | Consumer | Purpose |
|-------|----------|----------|---------|
| `raw_events` | api/main.py | enrichment_service | Normalised events |
| `enriched_events` | enrichment_service | feature_engine | + geo/DNS/vendor |
| `feature_stream` | feature_engine | ml_monitor | Numeric feature vectors |
| `ml_scores` | ml_monitor | risk_engine | Anomaly scores |
| `risk_scores` | risk_engine | graph_correlator + trust_engine | Risk + category |
| `graph_scores` | graph_correlator | trust_engine | + lateral movement CAF |
| `trust_scores` | trust_engine | decision_engine | Per-device trust 0–100 |
| `security_actions` | decision_engine | response_engine (shim) | Action directives |
| `response_executions` | **response_executor** | (SIEM/audit) | Execution records |
| `alerts` | decision_engine | (frontend/SIEM) | Security alerts |

---

## Frontend Pages (Next.js)

| Route | Purpose |
|-------|---------|
| `/` | Dashboard overview |
| `/entities` | Device list with trust scores |
| `/adaptive-trust` | Per-device trust history + ML charts |
| `/audit` | Alert audit log |
| `/responses` | Response Center — approvals, blocks, execution history |
| `/simulation` | Attack Simulation Lab (isolated, no ML contamination) |
| `/data-transparency` | Aggregate pipeline stats |
| `/how-it-works` | Architecture explanation |

TypeScript types: `src/types/index.ts` — `Entity`, `AuditLog`, `TrustEvaluation`, `ApprovalRequest`, `ResponseExecution`, `DeviceBlock`, `ManualExecuteRequest`, `ExecutionApprovalRequest`, `ExecutionRejectRequest`.

---

## Key Non-Obvious Behaviors

**Response executor deduplication.** Before inserting a new pending/running record, `executor.execute()` calls `db.find_active_execution(device_id, action)`. If one already exists, the existing record is returned — no duplicate is created. This prevents the pipeline from creating a new `restrict_network` entry every Kafka cycle for the same device.

**Response executor is the single source of truth.** `services/response_engine.py` is a deprecated shim that only delegates to `response_executor.executor.get_executor()`. Never add logic back to `response_engine.py`.

**Approval gate.** Hard containment actions (`restrict_network`, `isolate_vlan`, `lock_account`, `firewall_block`, `kill_process`) create `pending` records. `require_mfa` is NOT in this set — it auto-dispatches.

**Warmup guard.** Devices with fewer than 100 `trust_score` entries are force-returned as `trust=100, decision=trusted` by `api/v1.py`. The ML monitor also uses rule-based scoring (not z-scores) until 100 observations are collected per feature.

**Network aggregation.** `/network/telemetry` aggregates all individual flows into one summary per device before Kafka publish to prevent trust score inflation from flow batches.

**Read-time decay.** `api/v1.py` recomputes trust at GET time using the same exponential formula as the Trust Engine so dashboard scores decrease naturally between pipeline events.

**`kafka/` directory is a duplicate.** Always import from `pipeline/` — `kafka/producer.py`, `kafka/consumer.py`, `kafka/topics.py` are stale copies.

**graph_correlator is blocking for trust_engine.** trust_engine subscribes only to `graph_scores` (not `risk_scores` directly). If graph_correlator is down, trust_engine starves.

**Simulation is ML-isolated.** `POST /api/v1/simulation/run` computes trust trajectories purely in memory using trust_engine math. No events are written to `feature_stream`, `ml_scores`, `trust_states`, or `device_baselines`. Other tabs show real data unaffected.

**Simulation executions are tagged.** Response executor records from a simulation run carry `triggered_by = "sim:<run_id>:<attack_type>"`. The simulation page filters by `run_id` to show only its own actions; the Response Center shows them as real pending approvals.

**Response Center deduplication.** The responses page deduplicates pending entries by `(device_id, action)` client-side, keeping only the latest. Combined with the server-side executor deduplication, the approval queue shows exactly one outstanding entry per (device, action).

**`/devices` endpoint filtering.** Returns only devices that have at least one `trust_score` entry — excludes network-discovery-only devices that never passed through the full pipeline.

**Feedback loop.** `services/feedback_service.py` lets analysts label alerts as `true_attack`/`false_positive` via `POST /api/v1/feedback`. Risk engine refreshes severity weights every 5 minutes.

**start.sh network detection.** On startup, `start.sh` reads the active default route (`ip route show default`) and exports `NETWORK_INTERFACE` so `network_discovery_service.py` binds to the correct interface. USB tethering and Wi-Fi switches are handled automatically.
