<div align="center" xmlns="http://www.w3.org/1999/html">

# LLM Router Server

<p align="center">
  <img src="assets/structure.png" width="1200px" style="vertical-align:middle;">
</p>

<p align="center">
  LLM Router Server is a high-performance routing service designed for multi-model deployment scenarios, used to uniformly manage and orchestrate multiple local Large Language Model (LLM) services, Embedding models, Re-ranking models, and other inference services.
</p>

[English](README.md) | [中文](README_zh.md)

</div>

### Key Features

- **Unified Routing Management**: Integrates multiple independent vLLM services, Embedding services, and Reranker services
- **OpenAI-Compatible API**: Provides fully compatible OpenAI API interfaces (`/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`)
- **Configuration-Based Deployment**: Easily manage startup parameters, ports, GPU allocation, etc. for multiple models through YAML configuration files
- **Multi-Model Parallelism**: Supports multiple model instances running simultaneously, each using independent processes and GPU resources
- **Intelligent Load Balancing**: Automatically selects the least-loaded instance based on real-time metrics (running requests, waiting requests, KV cache usage)
- **High-Performance Forwarding**: High-performance asynchronous architecture based on FastAPI + Gunicorn + Uvloop
- **Streaming Response Optimization**: Optimizes streaming requests to ensure low latency and stable token output

### Use Cases

- **Multi-Model Service Deployment**: Deploy multiple LLM models on single or multiple servers
- **Model Load Balancing**: Dynamically select different models based on business requirements
- **Unified API Interface**: Provide unified API endpoints for different models
- **RAG Applications**: Integrate Embedding and Reranking services to build complete Retrieval-Augmented Generation systems

---

## Features

### Independent Multi-Model Execution
- Each LLM model is launched through an independent process, using different ports and CUDA devices
- Supports dynamic configuration of model count, GPU memory allocation, concurrent request numbers, and other parameters
- Models are isolated from each other; a single model failure does not affect other services

### Intelligent Load Balancing
- **Real-Time Metrics Monitoring**: Continuously polls vLLM `/metrics` endpoint for each instance to gather:
  - Number of running requests
  - Number of waiting requests
  - KV cache usage percentage
  - Total prompt and generation tokens
- **Least-Load Selection**: Automatically routes requests to the instance with the lowest load score
- **Load Score Calculation**: Combines multiple metrics with configurable weights:
  - Waiting requests weight: 10.0
  - Running requests weight: 3.0
  - KV cache usage weight: 100.0
- **Health Monitoring**: Tracks backend health status and applies cooldown periods for failed instances
- **Inflight Request Tracking**: Monitors in-flight requests to prevent overloading any single instance

### Embedding and Reranker Integration
- Built-in Embedding server and Reranker server
- Supports multiple Embedding models (m3e-base, bge-m3, etc.)
- Supports multiple Reranking models (bge-reranker-large, etc.)
- Unified forwarding of `/v1/embeddings` requests

### Fully Compatible with OpenAI SDK
- Supports direct invocation using OpenAI Python SDK
- No need to modify existing code, just change the `base_url`
- Supports all standard parameters (temperature, top_p, max_tokens, etc.)

### Workflow

1. **Client Request**: Client sends requests to Router Server via OpenAI SDK or HTTP client
2. **Route Resolution**: Router looks up corresponding backend service configuration based on the `model` parameter in the request
3. **Load-Based Instance Selection**: For models with multiple instances:
   - Fetches real-time metrics from all instances
   - Calculates load score for each instance
   - Selects the instance with the lowest load
   - Considers health status and cooldown periods
4. **Request Forwarding**: Forwards the request to the selected vLLM or Embedding service instance
5. **Streaming Processing**: Optimizes streaming responses to ensure low-latency transmission
6. **Health Tracking**: Monitors request success/failure and updates instance health status
7. **Response Return**: Returns the backend service response to the client as-is

---

## Directory Structure

```
LLM-Router-Server/
├── configs/                    # Configuration directory
│   ├── config.yaml            # Main configuration file (models, server settings)
│   └── gunicorn.conf.py       # Gunicorn configuration
├── docker/                     # Docker related files
│   ├── Dockerfile             # Docker image build file
│   └── docker-compose.yaml    # Docker Compose configuration
├── logs/                       # Log directory
├── scripts/                    # Startup scripts directory
│   ├── start_all_models.py    # Python script to start all models
│   └── start_all.sh           # One-click startup script (models + router)
├── src/                        # Main source code directory
│   ├── embedding_reranker/    # Embedding and Reranker module
│   │   ├── __init__.py
│   │   ├── embedding_reranker_launcher.py  # Launcher
│   │   ├── schema.py          # Data structure definitions
│   │   └── embedding_engine/  # Inference engine
│   │       ├── baseinferencer.py  # Base inference class
│   │       ├── embed_rerank.py    # Embedding/Rerank implementation
│   │       ├── generator.py       # Generator
│   │       └── optimize.py        # Optimization tools
│   ├── llm_router/            # LLM routing module
│   │   ├── __init__.py
│   │   ├── config_loader.py   # Configuration loader
│   │   ├── env.py             # Environment variable management
│   │   ├── main.py            # FastAPI application entry point
│   │   ├── router.py          # Routing logic
│   │   └── vllm_launcher.py   # vLLM launcher
│   └── metrics/               # Monitoring and metrics
│       └── basic_metrics.py   # Basic metrics collection
├── test/                       # Test files directory
│   └── test_router_server.py  # Router server tests
├── requirements.txt            # Python dependencies list
└── README.md                   # Project documentation
```

---

## Installation Guide

### Install Dependencies

```bash
pip install -r requirements.txt
```

## Configuration Guide

### 1. Edit Configuration File

The main configuration file is located at `configs/config.yaml` and contains two main sections:

#### LLM Engine Configuration

Configure one or more LLM models with multiple instances:
```yaml
LLM_engines:
  # Model with multiple instances
  Qwen3-0.6B:
    instances:
      # First instance
      - id: "qwen3-1"                         # Instance ID
        host: "localhost"                     # Service host
        port: 8002                            # Service port
        cuda_device: 0                        # CUDA device number
      
      # Second instance
      - id: "qwen3-2"                         # Instance ID
        host: "localhost"                     # Service host
        port: 8004                            # Service port
        cuda_device: 0                        # CUDA device number
    
    # Model configuration (shared by all instances)
    model_config:
      model_tag: "Qwen/Qwen3-0.6B"           # Model path or HuggingFace ID
      dtype: "float16"                       # Data type
      max_model_len: 500                     # Maximum sequence length
      gpu_memory_utilization: 0.35           # GPU memory utilization
      tensor_parallel_size: 1                # Tensor parallel size

# Embedding and Reranking server configuration
embedding_server:
  host: "localhost"
  port: 8005
  cuda_device: 1

  # Embedding model list
  embedding_models:
    m3e-base:
      model_name: "moka-ai/m3e-base"
      model_path: "./models/embedding_engine/model/embedding_model/m3e-base-model"
      tokenizer_path: "./models/embedding_engine/model/embedding_model/m3e-base-tokenizer"
      max_length: 512
      use_gpu: true
      use_float16: true
    
    bge-m3:
      model_name: "BAAI/bge-m3"
      model_path: "./models/embedding_engine/model/embedding_model/bge-m3-model"
      tokenizer_path: "./models/embedding_engine/model/embedding_model/bge-m3-tokenizer"
      max_length: 512
      use_gpu: true
      use_float16: true

  # Reranking model list
  reranking_models:
    bge-reranker-large:
      model_name: "BAAI/bge-reranker-large"
      model_path: "./models/embedding_engine/model/reranking_model/bge-reranker-large-model"
      tokenizer_path: "./models/embedding_engine/model/reranking_model/bge-reranker-large-tokenizer"
      max_length: 512
      use_gpu: true
      use_float16: true
```

#### Configuration Parameters

**LLM Engine Parameters:**

*Instance Configuration:*
- `id`: Unique identifier for the instance
- `host`: Host address for vLLM service to listen on
- `port`: Port for vLLM service to listen on
- `cuda_device`: GPU device number to use

*Model Configuration (shared by all instances):*
- `model_tag`: Model file path or HuggingFace model ID
- `dtype`: Model precision type (`float16`, `bfloat16`, etc.)
- `max_model_len`: Maximum context length
- `gpu_memory_utilization`: GPU memory utilization (0.0-1.0)
- `tensor_parallel_size`: Tensor parallelism degree (multi-GPU inference)

**Embedding Server Parameters:**
- `host`, `port`: Server listening address and port
- `cuda_device`: GPU device to use
- `model_path`: Model weight file path
- `tokenizer_path`: Tokenizer file path
- `max_length`: Maximum sequence length
- `use_gpu`: Whether to use GPU
- `use_float16`: Whether to use FP16 precision

### 2. Configure Gunicorn

Edit `configs/gunicorn.conf.py`:

```python
# gunicorn.conf.py
import os

# Bind address and port
bind = "0.0.0.0:8947"

# Number of workers (recommended: CPU core count)
workers = 4

# Worker class (using Uvicorn Worker for ASGI support)
worker_class = "uvicorn.workers.UvicornWorker"

# Timeout (0 means unlimited)
timeout = 0

# Log level
loglevel = "info"

# Access log output to stdout
accesslog = "-"

# Error log output to stdout
errorlog = "-"

# Whether to preload the application
preload_app = False
```

---

## Usage Guide

### 1. Start All Services

Use the one-click startup script:

```bash
sh scripts/start_all.sh ./configs/config.yaml ./configs/gunicorn.conf.py
```

This script will execute in sequence:
1. Start all configured vLLM model services
2. Start Embedding and Reranker services (if configured)
3. Start Router Server (using Gunicorn + multiple workers)

### 3. Verify Service Status

Check all available models:

```bash
curl http://localhost:8947/v1/models
```

### 4. Using OpenAI SDK

#### Chat Completions

```python
from openai import OpenAI

client = OpenAI(
    api_key="EMPTY",
    base_url="http://localhost:8947/v1"
)

# Non-streaming request
response = client.chat.completions.create(
    model="Qwen2.5-14B-Instruct",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Please introduce the advantages of Python."}
    ],
    temperature=0.7,
    max_tokens=500
)

print(response.choices[0].message.content)

# Streaming request
stream = client.chat.completions.create(
    model="Qwen2.5-14B-Instruct",
    messages=[
        {"role": "user", "content": "Write a poem about spring."}
    ],
    temperature=0.8,
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

#### Embeddings

```python
response = client.embeddings.create(
    model="m3e-base",
    input=["This is the first text", "This is the second text"]
)

# Get embedding vectors
embedding_1 = response.data[0].embedding
embedding_2 = response.data[1].embedding

print(f"Embedding dimension: {len(embedding_1)}")
```

#### Reranking

```python
documents = [
    "Machine learning is best learned through projects.",
    "Theory is essential for understanding machine learning.",
    "Practical tutorials are the best way to learn machine learning."
]

response = client.embeddings.create(
    model="bge-reranker-large",
    input=documents,
    extra_body={"query": "How to learn machine learning?"}
)

# Get reranking scores
for idx, item in enumerate(response.data):
    print(f"Document {idx}: Score {item.embedding}")
```

---

## API Documentation

### Endpoint List

| Endpoint | Method | Description |
|------|------|------|
| `/v1/chat/completions` | POST | Chat completion (supports streaming) |
| `/v1/completions` | POST | Text completion (supports streaming) |
| `/v1/embeddings` | POST | Text embeddings / Reranking |
| `/v1/models` | GET | List all available models |

### Internal Project Documentation

- `LLM Router Streaming 問題紀錄與解法.md`: Streaming response optimization guide
- `LLM Router 吞吐優化.md`: Throughput optimization guide

---

## License
This project is licensed under the MIT License.
