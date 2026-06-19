# Configuration

> [中文](configuration_zh-CN.md)

The configuration file lives at `packages/config-schema/config.yaml` — the single
source of truth, validated by `packages/config-schema/schema.py`, and read by the
frontend, backend, and router alike. It controls all model startup parameters.

> You usually **don't** edit this by hand: add models from the UI by pasting a
> `vllm serve …` command, which is layered on as a dynamic overlay. Edit `config.yaml`
> only for the canonical, hand-maintained fleet.

## `config.yaml` structure

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

## Key parameters

| Parameter | Description | Recommended |
|------|------|--------|
| `gpu_memory_utilization` | GPU memory usage ratio | 0.6–0.9 |
| `max_model_len` | Maximum context length | Based on model capability |
| `tensor_parallel_size` | Multi-GPU parallelism count | Number of GPUs |
| `dtype` | Inference precision | float16 (faster) / bfloat16 (more stable) |
| `cuda_device` | GPU device number | 0, 1, 2… |

## Running multiple models at once

Yes — as long as they fit in GPU memory. A **VRAM pre-flight guard** blocks a start
that would overflow the target GPU (override per-start with *Force start*), and
instances without a pinned `cuda_device` are **auto-placed** on the GPU with the most
free memory. On a single small GPU you'll typically run one mid-size model alongside
a few small ones; models are started on demand, so a large fleet can be configured
without all running at once.

Tune the guard / restart policy via env on the backend:

| Env | Purpose |
|---|---|
| `LLMOPS_VRAM_GUARD` | Enable/disable the VRAM pre-flight guard |
| `LLMOPS_AUTO_RESTART` | Auto-restart a crashed managed model |
| `LLMOPS_MAX_RESTARTS` | Restart budget before giving up |
| `LLMOPS_RESTART_BACKOFF` | Exponential backoff base |
