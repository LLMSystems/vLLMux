<div align="center">

# LLM-Router-Server-Dashboard
**One-Stop LLM Model Management and Monitoring Platform**

[English](README.md) | [中文](README_zh-CN.md)

![Main Console](assets/image0.png)

![Model Management](assets/image1.png)

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

### Core Functionality

- **Multi-Model Management**
  - Support for managing multiple LLM models simultaneously (based on vLLM)
  - Support for Embedding and Reranking models
  - Independent model lifecycle management (start/stop)
  - Automatically selects the least-loaded instance based on real-time metrics (running requests, waiting requests, KV cache usage)

- **Visual Control Panel**
  - Real-time display of model running status
  - GPU resource monitoring
  - System resource usage statistics
  - Model configuration viewing and editing

- **Resource Management**
  - GPU device allocation and management
  - Memory usage monitoring
  - Multi-GPU parallel support (Tensor Parallel)

---

## System Requirements

### Hardware Requirements
- **GPU**: NVIDIA GPU (CUDA 12.1+ recommended)
- **Memory**: 16GB+ RAM (depending on model size)
- **Disk**: 50GB+ available space
---

## Quick Start

### Frontend Deployment

#### 1. Build Frontend Container with Docker

```bash
# All containers are centralised under deploy/ (build context = repo root)
docker compose -f deploy/docker-compose.yaml up -d frontend
```

#### 2. Local Development Mode

```bash
cd apps/frontend
npm install
npm run dev
```

#### 3. Production Build

```bash
cd apps/frontend
npm install
npm run build
```

#### 4. Configure Frontend API Endpoint

Edit `apps/frontend/.env.local`:
```env
VITE_API_BASE_URL=http://localhost:5000
VITE_MODEL_CONTROL_PASSWORD=123
```

#### 5. Customize Server Configuration

Edit `apps/frontend/vite.config.js`:
```javascript
export default defineConfig({
  server: {
    host: '0.0.0.0',  // Allow external access
    port: 5111        // Custom port
  }
})
```

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

### Q4: Why can't I start multiple models simultaneously?

**Design Limitation**: The current version requires starting models one at a time to ensure:
- Proper GPU resource allocation
- Avoid memory overflow
- Process management stability

Future versions will optimize parallel startup support.