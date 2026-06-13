<div align="center">

# LLM-Router-Server-Dashboard
**One-Stop LLM Model Management and Monitoring Platform**

[English](README.md) | [中文](README_zh-CN.md)

![Main Console](assets/image0.png)

![Model Management](assets/image1.png)


![Model Management](assets/image2.png)

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

### UX
- Light / dark theme, dense "control-room" interface
- Password-gated control actions (start / stop / add / remove)

---

## System Requirements

### Hardware Requirements
- **GPU**: NVIDIA GPU (CUDA 12.1+ recommended)
- **Memory**: 16GB+ RAM (depending on model size)
- **Disk**: 50GB+ available space
---

## Quick Start

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
VITE_MODEL_CONTROL_PASSWORD=123                # gate for start / stop / add / remove
```

> **Run all three services for full functionality**: the Dashboard Backend (`:5000`), the LLM Router (`:8887`), and the model instances the backend launches on demand. The backend and router both merge the dynamic-model overlay at startup, so models added from the UI survive restarts.

### Backend Deployment

**Important Note**: The backend needs to monitor LLM model status (process management), so it must run in the same container as LLM-Router-Server.

#### 1. Build Container

```bash
# backend + router share one container (see deploy/backend-router.Dockerfile)
docker compose -f deploy/docker-compose.yaml up -d backend-router
```

**Ensure docker-compose.yaml exposes necessary ports**:
- `8887`: LLM-Router-Server API
- `5000`: Dashboard Backend API
- Other model ports (e.g., 8002, 8003, etc.)

#### 2. Start Backend in Container

```bash
# Enter the container
docker exec -it <container_id> bash

# Start backend
cd /app/apps/backend
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 5000
```

### LLM-Router-Server Deployment
For installation and startup details, refer to [LLM-Router-Server Startup Guide](apps/router-server/README.md)
#### 1. Start Router Server in Container

```bash
cd /app/apps/router-server
pip install -r requirements.txt
sh scripts/start_all.sh ../../packages/config-schema/config.yaml ./configs/gunicorn.conf.py
```

**Note**: Use `packages/config-schema/config.yaml` as the single source of truth so the frontend, backend, and router all read the same configuration.

**Model lifecycle**: the router only routes and load-balances — it no longer launches models. Model processes (vLLM instances, Embedding/Reranker server) are owned by the Dashboard backend and started on demand via `POST /api/models/{key}/start`.

#### 2. Verify Service Status

```bash
# Check router server (lists configured model groups)
curl http://localhost:8887/v1/models

# Check backend API (lifecycle state of every model instance)
curl http://localhost:5000/api/models
```

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