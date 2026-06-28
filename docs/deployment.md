# Deployment & Topology

> [中文](deployment_zh-CN.md)

The whole stack — dashboard backend, LLM router, Prometheus, Grafana, the GPU/host
exporters, and the Vue frontend — is built and started by a single Compose file.
Requires Docker with the NVIDIA Container Toolkit (on WSL2, enable GPU support in
Docker Desktop).

```bash
cp deploy/.env.example deploy/.env   # set HF_TOKEN, which GPUs, the admin token
make up                              # docker compose -f deploy/docker-compose.yaml up -d --build
# open http://localhost:8884
```

`make down` stops it, `make logs` tails all services, `make ps` shows status.

## Services

See [`deploy/docker-compose.yaml`](../deploy/docker-compose.yaml).

| Service          | Image                  | Port    | Role |
|------------------|------------------------|---------|------|
| `backend`        | `llmops-engine` (GPU)  | 5000    | Dashboard API; spawns vLLM subprocesses on `:800x` |
| `router`         | `llmops-engine`        | 8887    | OpenAI-compatible router; **shares the backend's network namespace** so it reaches those localhost vLLM ports |
| `prometheus`     | `prom/prometheus`      | 9090    | Scrapes the vLLM fleet's `/metrics` via file-based SD; **also shares the backend's netns** so `localhost:800x` resolves to the spawned instances |
| `grafana`        | `grafana/grafana`      | (proxied) | Dashboards + alerting; served single-origin under `/grafana` via the frontend nginx |
| `dcgm-exporter`  | `nvcr.io/.../dcgm-exporter` (GPU) | 9400 | NVIDIA GPU telemetry (util, memory, temperature, power) |
| `node-exporter`  | `prom/node-exporter`   | 9100    | Host metrics (CPU, RAM, disk, network) |
| `frontend`       | `llmops-frontend`      | 8884    | nginx serving the SPA + reverse-proxying `/api` → backend, `/v1` → router, `/grafana` → grafana |

### Why one image, multiple services on one netns

Only the backend truly needs vLLM (it launches the subprocesses), and the router +
Prometheus must see them on `localhost` — so a single
[`engine.Dockerfile`](../deploy/engine.Dockerfile) (based on the official
`vllm/vllm-openai`) runs as `backend` + `router`, joined (with Prometheus) by
`network_mode: service:backend`.

The frontend reaches the backend, router, and Grafana through nginx on a single
origin, so no host/port is baked into the build.

### Persistence

- SQLite + the dynamic-model overlay → `llmops-data` named volume
- Prometheus TSDB → `prometheus-data`; Grafana state → `grafana-data`
- Model **weights** are bind-mounted from the host HF cache (`HF_CACHE_DIR`, default
  `~/.cache/huggingface`) so they're browsable locally and shared with host-side tools
- `packages/config-schema/config.yaml` is bind-mounted too, so you can edit models
  without rebuilding

> **Model lifecycle**: the router only routes and load-balances — it never launches
> models. vLLM instances (and the Embedding/Reranker server) are owned by the backend
> and started on demand from the **Models** page (or `POST /api/models/{key}/start`).
> The backend and router both merge the dynamic-model overlay at startup, so models
> added from the UI survive restarts.

### Verify

```bash
curl http://localhost:8887/v1/models     # router: configured model groups
curl http://localhost:5000/api/models    # backend: lifecycle state of each instance
```

## Environment variables (`deploy/.env`)

Copy [`deploy/.env.example`](../deploy/.env.example) to `deploy/.env` and adjust.
Every key below is also documented inline in that file. All are optional except
`HF_TOKEN` (only for gated/private weights) — the defaults give a working local
deployment.

**Host ports** — the browser only needs `FRONTEND_PORT`; the other three publish a
service for *direct* API access and can be remapped if the default is already taken.
(The container-internal ports are fixed; you only change the host side here.)

| Variable | Default | Purpose |
|---|---|---|
| `FRONTEND_PORT` | `8884` | Dashboard origin (SPA + `/api` + `/v1` + `/grafana` via nginx) |
| `ROUTER_PORT` | `8887` | Direct access to the OpenAI-compatible router |
| `BACKEND_PORT` | `5000` | Direct access to the dashboard backend API |
| `PROMETHEUS_PORT` | `9090` | Prometheus UI / API |

**Models & caches**

| Variable | Default | Purpose |
|---|---|---|
| `HF_TOKEN` | *(blank)* | HuggingFace token for gated/private weights (public models need none) |
| `HF_CACHE_DIR` | `~/.cache/huggingface` | Host dir bind-mounted as the weight cache (absolute path only — no `~`/`${HOME}`) |
| `MODELSCOPE_CACHE_DIR` | `~/.cache/modelscope` | Host dir for benchmark/eval datasets (same rule) |
| `NVIDIA_VISIBLE_DEVICES` | `all` | GPUs the engine may use — `all` or a comma list e.g. `0,1` |

**Authentication** (see [Authentication](#authentication) below)

| Variable | Default | Purpose |
|---|---|---|
| `LLMOPS_ADMIN_TOKEN` | *(blank)* | Shared admin token gating all control ops; **blank = auth disabled (dev only)** |
| `LLMOPS_REQUIRE_API_KEY` | `false` | When `true`, the router rejects `/v1/*` without a valid bearer token |

**Alerting & monitoring**

| Variable | Default | Purpose |
|---|---|---|
| `LLMOPS_ALERT_WEBHOOK` | *(blank)* | Webhook that receives a JSON POST when a model enters FAILED (Slack/Discord/any) |
| `GRAFANA_ADMIN_PASSWORD` | `admin` | Login for Grafana's `admin` user (anonymous access stays read-only) — change for any non-local deploy |
| `GRAFANA_ALERT_WEBHOOK` | *(placeholder)* | Webhook the provisioned vLLM alert rules notify; blank leaves a non-resolving placeholder |

After editing `deploy/.env`, re-run `make up` to apply.

## Frontend (Web dashboard)

The dashboard lives in **`apps/frontend_llmops`** — Vue 3 + Vite + TypeScript,
Tailwind CSS v4, shadcn-vue components, [Vue Flow](https://vueflow.dev) for the
topology/router graphs, Pinia + Vue Router. (The older `apps/frontend` is deprecated.)

```bash
cd apps/frontend_llmops
npm install
npm run dev          # http://localhost:5173
npm run build        # production build → dist/
```

Configuration — `apps/frontend_llmops/.env`:

```env
VITE_API_BASE_URL=http://localhost:5000        # Dashboard backend (lifecycle, telemetry)
VITE_ROUTER_BASE_URL=http://localhost:8887     # LLM Router (inference + /metrics + /reload)
```

### Authentication

Authentication is backend-driven (not a build-time password). Set
`LLMOPS_ADMIN_TOKEN` on the backend + router to gate every control action (start /
stop / add / edit / remove + API-key management); the UI prompts for the token once
and reuses it for the session. Set `LLMOPS_REQUIRE_API_KEY=true` on the router to
require a bearer token (the admin token, or an API key minted on the **API Keys**
page) for all `/v1/*` inference. Both default to off for local dev.

## High availability (multi-replica, optional)

The default is single-machine SQLite, zero config. For a control plane that
survives a crash, point the shared store at **Postgres** and run **multiple
backend replicas**. Design + phases: [ha-phase2-design_zh-CN.md](ha-phase2-design_zh-CN.md).

**1. Bring up Postgres and switch to it.** The compose ships a profile-gated
`postgres` service (off by default):

```bash
# deploy/.env:
LLMOPS_DB_URL=postgresql://llmops:llmops@postgres:5432/llmops
LLMOPS_SESSION_SECRET=<long random>   # required for replicas (shared SSO session)

docker compose -f deploy/docker-compose.yaml --profile ha up -d
```

All store data (keys / audit / config versions / cost / request logs / desired)
then lives in Postgres. Note: it's a **fresh empty DB** — existing SQLite data
isn't migrated automatically yet. Unset `LLMOPS_DB_URL` to go back to SQLite,
unchanged.

**2. Leader election is automatic.** In Postgres mode the backend elects a
leader: **only the leader runs the singleton loops** (reconcile / autoscale /
prune); others stand by. If the leader dies, a standby steals the expired lease
within ~`LLMOPS_LEADER_LEASE_TTL` (default 15s). Nothing to switch on.

| Var | Default | Meaning |
|---|---|---|
| `LLMOPS_DB_URL` | empty | set = Postgres = HA; empty = single-machine SQLite |
| `LLMOPS_SESSION_SECRET` | empty | **required** for replicas (shared so they trust each other's SSO sessions) |
| `LLMOPS_LEADER_LEASE_TTL` | `15` | lease seconds (failover speed vs heartbeat rate) |
| `LLMOPS_INSTANCE_ID` | `hostname:pid` | replica id — auto-unique, leave unset |

**3. How many replicas is your call** — a deployment choice, not automatic. The
bundled `docker-compose.yaml` is single-backend by design (the router shares its
netns to reach localhost vLLM, and host ports are fixed), so it isn't meant for
`--scale`. In production use **k8s** (one backend per Pod, same `LLMOPS_DB_URL` +
`LLMOPS_SESSION_SECRET`, an LB in front) or a customised compose. Managing models
across **multiple GPU hosts** is Phase 3 (not built); today's multi-replica is
"standby takeover on one host pool", not multi-node scheduling.

**Verify failover** with the headless demo compose (2 replicas + its own Postgres):

```bash
docker compose -p hademo -f deploy/docker-compose.ha-demo.yaml up -d
docker exec hademo-ha-postgres-1 psql -U llmops -d llmops -c "SELECT * FROM leader_lease;"
docker kill hademo-backend-a-1          # kill the leader (or -b)
# wait ~TTL, re-check leader_lease: holder changed; the standby logs "Control loops started"
docker compose -p hademo -f deploy/docker-compose.ha-demo.yaml down -v
```

## Manual / development run

Run the three pieces yourself (Python deps in the repo-root `.venv`):

```bash
# Dashboard backend (:5000)
cd apps/backend && pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 5000

# LLM router (:8887) — see apps/router-server/README.md for details
cd apps/router-server && pip install -r requirements.txt
sh scripts/start_all.sh ../../packages/config-schema/config.yaml ./configs/gunicorn.conf.py
```

Use `packages/config-schema/config.yaml` as the single source of truth so the
frontend, backend, and router all read the same configuration.

## Requirements

- **GPU**: NVIDIA GPU (CUDA 13.1+ recommended)
- **Memory**: 16GB+ RAM (depending on model size)
- **Disk**: 50GB+ available space
