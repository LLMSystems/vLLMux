<div align="center">

# LLM-Router-Server-Dashboard
**One-Stop LLM Model Management and Monitoring Platform**

[English](README.md) | [中文](README_zh-CN.md)

![Main Console](assets/image0.png)

![Model Management](assets/image1.png)


![Model Management](assets/image2.png)

![Model Management](assets/image3.png)
![Model Management](assets/image4.png)

</div>

---

## Project Overview

**LLM-Router-Server-Dashboard** is a solution for large language model (LLM) deployment and management, providing an intuitive web interface to manage, monitor, and operate multiple LLM model instances.

This project combines a routing server (LLM-Router-Server) with an easy-to-use management interface, enabling you to:
- **Visual Management**: Easily manage multiple models through a web interface
- **Dynamic Control**: Start and stop models in real-time without service restarts
- **Real-time Monitoring**: Monitor model status, GPU utilization, and system information
- **Configuration Management**: Flexibly manage model parameters through YAML configuration files

---

## Key Features

### Model Management
- Multi-model, multi-instance management on vLLM (LLM, Embedding, Reranker)
- Per-instance lifecycle (start/stop) with a live state machine (`stopped → starting → ready → failed/stopping`), driven by a reconciler that derives the true state from process liveness + `/health` probes
- **Add models from the UI by pasting a `vllm serve …` command** — it is parsed into an editable form and layered on as a dynamic *overlay*, so the hand-maintained `config.yaml` stays untouched; the router hot-reloads (`POST /reload`) so new models are routable end-to-end
- Load-aware routing: the router auto-selects the least-loaded instance (weighting running / waiting requests + KV-cache usage)

### Reliability
- **VRAM pre-flight guard** — blocks a start that would likely OOM, with a one-click *Force start* override
- **GPU auto-placement** — an instance with no pinned `cuda_device` is placed on the GPU with the most free memory
- **Auto-restart** — a managed model that crashes is restarted with exponential backoff (configurable budget, resets once healthy)

### Observability
- Real-time status via Server-Sent Events (no polling)
- **System topology** (Vue Flow) — a live mission-control graph of Clients → Router → model groups / Embedding → GPUs, with animated traffic edges, GPU-placement edges, and a control plane; nodes are clickable drill-ins
- **Router load-balancing view** — an animated fan showing each replica's real traffic share and the instance the router will pick next
- **Trends** — time-series charts (requests, error rate, p95 latency, tokens) over 15m–24h, aggregated from the persisted request log
- Per-model usage (count, error rate, p50/p95 latency, tokens), request log, and a state-transition event timeline
- GPU / CPU / memory monitoring plus a GPU-process inventory

### Playground
- OpenAI-compatible **chat (streaming)**, completions, **embeddings**, and **reranking**, sent straight through the router
- **Reasoning ("thinking") display** — when a model runs with a vLLM reasoning parser, the `reasoning` stream is shown in a collapsible *思考過程* block above the answer

### Benchmarking & Evaluation (evalscope)
- **Load testing** (`/benchmark`) — concurrency sweep, arrival-rate open-loop, multi-turn, **SLA auto-tune**, plus **embedding / rerank** throughput and single-request **speed benchmark**; each run is an isolated subprocess, with live charts, run comparison, and the full evalscope HTML report
- **Accuracy / quality evaluation** (`/eval`) — **30+ benchmark datasets** grouped by capability tier (Baseline, Knowledge, Chinese, Reasoning, Math, Multilingual, **Tool-calling**, **Long-context**, Code, and judge-scored QA): MMLU/ARC/GSM8K/IFEval, C-Eval/C-MMLU, GPQA/MMLU-Pro, AIME, HumanEval, ToolBench/General-FunctionCall, Needle-in-a-Haystack, …
  - Per-dataset scores, a **run-to-run comparison matrix** (highlights the best per dataset), and the interactive HTML report
  - **LLM-as-judge** for free-form QA — pick one of your own deployed models (via the router) or an external OpenAI-compatible API
  - **Advanced `dataset_args`** — few-shot count + raw per-dataset overrides (subset selection, etc.)
  - Sanity guards: judge-scored datasets require a judge; long-context and real tool-calling datasets warn about their model prerequisites (large `max_model_len`, vLLM tool parser)

### Libraries
- **Model library** (`/library`) — scan / pre-download / delete HF model weights from the UI, with live download progress
- **Dataset library** (`/datasets`) — pre-download load-test and evaluation datasets into the shared ModelScope cache so a run never stalls on a first-time download
- **Tool-calling config helper** — the model editor maps model families to the right vLLM `tool_call_parser` (Qwen→`hermes`, Qwen3-Coder→`qwen3_xml`, Llama→`llama3_json`/`llama4_pythonic`, …) with one-click preset insertion (see `docs/vllm_auto_tool_整理.md`)

### UX
- Light / dark theme, dense "control-room" interface
- **Admin-token-gated control** (start / stop / add / edit / remove) and
  **API-key management** — mint/revoke keys that authenticate router inference,
  with per-key usage attribution in the request log

---

## System Requirements

### Hardware Requirements
- **GPU**: NVIDIA GPU (CUDA 13.1+ recommended)
- **Memory**: 16GB+ RAM (depending on model size)
- **Disk**: 50GB+ available space
---

## Quick Start

### Docker Deployment (one command)

The whole stack — dashboard backend, LLM router, and the Vue frontend — is built
and started by a single Compose file. Requires Docker with the NVIDIA Container
Toolkit (on WSL2, enable GPU support in Docker Desktop).

```bash
cp deploy/.env.example deploy/.env   # set HF_TOKEN, which GPUs, the admin token
make up                              # docker compose -f deploy/docker-compose.yaml up -d --build
# open http://localhost:8884
```

`make down` stops it, `make logs` tails all services, `make ps` shows status.

**Topology** (see [`deploy/docker-compose.yaml`](deploy/docker-compose.yaml)):

| Service    | Image                  | Port    | Role |
|------------|------------------------|---------|------|
| `backend`  | `llmops-engine` (GPU)  | 5000    | Dashboard API; spawns vLLM subprocesses on `:800x` |
| `router`   | `llmops-engine`        | 8887    | OpenAI-compatible router; **shares the backend's network namespace** so it reaches those localhost vLLM ports |
| `frontend` | `llmops-frontend`      | 8884    | nginx serving the SPA + reverse-proxying `/api` → backend and `/v1` → router |

Why one image, two services: only the backend truly needs vLLM (it launches the
subprocesses), and the router must see them on `localhost` — so a single
[`engine.Dockerfile`](deploy/engine.Dockerfile) (based on the official
`vllm/vllm-openai`) runs as two services joined by `network_mode: service:backend`.

The frontend reaches the backend and router through nginx on a single origin, so
no host/port is baked into the build. SQLite + the dynamic-model overlay persist
in the `llmops-data` named volume; downloaded model **weights** are bind-mounted
from the host HF cache (`HF_CACHE_DIR`, default `~/.cache/huggingface`) so they're
browsable locally and shared with host-side tools. The canonical
`packages/config-schema/config.yaml` is bind-mounted too, so you can edit models
without rebuilding.

> **Model lifecycle**: the router only routes and load-balances — it never
> launches models. vLLM instances (and the Embedding/Reranker server) are owned
> by the backend and started on demand from the **Models** page (or
> `POST /api/models/{key}/start`). The backend and router both merge the
> dynamic-model overlay at startup, so models added from the UI survive restarts.

#### Verify

```bash
curl http://localhost:8887/v1/models     # router: configured model groups
curl http://localhost:5000/api/models    # backend: lifecycle state of each instance
```

### Frontend (Web Dashboard)

The dashboard lives in **`apps/frontend_llmops`** — Vue 3 + Vite + TypeScript, Tailwind CSS v4, shadcn-vue components, [Vue Flow](https://vueflow.dev) for the topology/router graphs, Pinia + Vue Router. (The older `apps/frontend` is deprecated.)

#### Local development

```bash
cd apps/frontend_llmops
npm install
npm run dev          # http://localhost:5173
```

#### Production build

```bash
npm run build        # outputs to dist/
```

#### Configuration — `apps/frontend_llmops/.env`

```env
VITE_API_BASE_URL=http://localhost:5000        # Dashboard backend (lifecycle, telemetry)
VITE_ROUTER_BASE_URL=http://localhost:8887     # LLM Router (inference + /metrics + /reload)
```

> **Authentication** is backend-driven (not a build-time password). Set
> `LLMOPS_ADMIN_TOKEN` on the backend + router to gate every control action
> (start / stop / add / edit / remove + API-key management); the UI prompts for
> the token once and reuses it for the session. Set `LLMOPS_REQUIRE_API_KEY=true`
> on the router to require a bearer token (the admin token, or an API key minted
> on the **API 金鑰** page) for all `/v1/*` inference. Both default to off for
> local dev.

> **Run all three services for full functionality**: the Dashboard Backend (`:5000`), the LLM Router (`:8887`), and the model instances the backend launches on demand. The backend and router both merge the dynamic-model overlay at startup, so models added from the UI survive restarts.

### Manual / development run

Run the three pieces yourself (Python deps in the repo-root `.venv`):

```bash
# Dashboard backend (:5000)
cd apps/backend && pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 5000

# LLM router (:8887)  — see apps/router-server/README.md for details
cd apps/router-server && pip install -r requirements.txt
sh scripts/start_all.sh ../../packages/config-schema/config.yaml ./configs/gunicorn.conf.py
```

Use `packages/config-schema/config.yaml` as the single source of truth so the
frontend, backend, and router all read the same configuration.

---

## Configuration Guide

### config.yaml Structure

The configuration file is located at `packages/config-schema/config.yaml` (the single source of truth, validated by `packages/config-schema/schema.py`) and controls all model startup parameters.

```yaml
# Router server configuration
server:
  host: "0.0.0.0"
  port: 8887
  uvicorn_log_level: "info"

# LLM model configuration
LLM_engines:
  Qwen3-0.6B:
    instances:
      - id: "qwen3"
        host: "localhost"
        port: 8002
        cuda_device: 0
      - id: "qwen3-2"
        host: "localhost"
        port: 8004
        cuda_device: 0

    model_config:
      model_tag: "Qwen/Qwen3-0.6B"
      dtype: "float16"
      max_model_len: 500
      gpu_memory_utilization: 0.35
      tensor_parallel_size: 1

# Embedding server configuration (optional)
embedding_server:
  host: "localhost"
  port: 8005
  cuda_device: 1
  
  embedding_models:
    m3e-base:
      model_name: "moka-ai/m3e-base"
      model_path: "./models/embedding_engine/model/embedding_model/m3e-base-model"
      tokenizer_path: "./models/embedding_engine/model/embedding_model/m3e-base-tokenizer"
      max_length: 512
      use_gpu: true
      use_float16: true
  
  reranking_models:
    bge-reranker-large:
      model_name: "BAAI/bge-reranker-large"
      model_path: "./models/embedding_engine/model/reranking_model/bge-reranker-large-model"
      tokenizer_path: "./models/embedding_engine/model/reranking_model/bge-reranker-large-tokenizer"
      max_length: 512
      use_gpu: true
      use_float16: true
```

### Key Parameter Descriptions

| Parameter | Description | Recommended Value |
|------|------|--------|
| `gpu_memory_utilization` | GPU memory usage ratio | 0.6-0.9 |
| `max_model_len` | Maximum context length | Based on model capability |
| `tensor_parallel_size` | Multi-GPU parallelism count | Number of GPUs |
| `dtype` | Inference precision | float16 (faster) / bfloat16 (more stable) |
| `cuda_device` | GPU device number | 0, 1, 2... |

---

### Q4: Can I run multiple models at once?

Yes — as long as they fit in GPU memory. A **VRAM pre-flight guard** blocks a start that would overflow the target GPU (override per-start with *Force start*), and instances without a pinned `cuda_device` are **auto-placed** on the GPU with the most free memory. On a single small GPU you'll typically run one mid-size model alongside a few small ones; models are started on demand, so a large fleet can be configured without all running at once.

Tune the guard / restart policy via env on the backend: `LLMOPS_VRAM_GUARD`, `LLMOPS_AUTO_RESTART`, `LLMOPS_MAX_RESTARTS`, `LLMOPS_RESTART_BACKOFF`.