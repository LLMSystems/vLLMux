<div align="center">

<p align="center">
  <img src="assets/icon.png" width="500px" style="vertical-align:middle;">
</p>

**One-stop platform to deploy, route, monitor & evaluate your vLLM cluster**

[English](README.md) · [中文](README_zh-CN.md)

![vLLMux](https://img.shields.io/badge/vLLM-multiplexed-5b8def)
![license](https://img.shields.io/badge/license-MIT-green)
![stack](https://img.shields.io/badge/FastAPI%20·%20Vue%203%20·%20Grafana-informational)

![Main Console](assets/image0.png)
![Main Console](assets/image1.png)
![Main Console](assets/image2.png)
![Main Console](assets/image3.png)

</div>

---

**vLLMux** is a self-hosted control plane for serving many LLMs on
[vLLM](https://github.com/vllm-project/vllm). Paste a `vllm serve …` command and it
becomes a routable model; the router load-balances across instances; and a bundled Prometheus + Grafana stack monitors everything — all behind one Vue dashboard.

## Highlights

- **Add a model by pasting `vllm serve …`** — parsed into a form and layered on as a dynamic overlay; the router hot-reloads, no `config.yaml` edits.
- **Lifecycle + self-healing** — per-instance state machine (`stopped → starting → ready → failed`), VRAM pre-flight guard, GPU auto-placement, crash auto-restart with backoff.
- **Pluggable routing strategies** — pick the load-balancing policy per model group or globally: `least_load` (default), `round_robin`, `random`, `least_inflight`, `p2c`, plus `session_affinity` / `prefix_affinity` for cache reuse on multi-turn chat & shared prompts. Switch it live from the dashboard; transparent failover + per-backend cooldown apply to every strategy.
- **Cross-instance KV-cache sharing** — toggle it per model group in the editor: instances offload/load KV blocks to a shared store (vLLM `OffloadingConnector`) so a prefix computed on one replica is reused by another (verified ~99% external prefix-cache hit, 31% lower TTFT on a warmed prompt). Traffic & topology views show which groups pool KV vs keep their own.
- **Live observability** — SSE status, animated system-topology & router-balancing graphs, per-model usage / latency / error stats.
- **Bundled Grafana monitoring** — Prometheus auto-discovers every running instance; Overview / Capacity / Performance / GPU / Host dashboards embedded in-app, with SLO thresholds & alerts.
- **Playground** — OpenAI-compatible chat (streaming) / completions / embeddings / reranking, with reasoning display.
- **Benchmark & evaluate** — evalscope load tests (concurrency, arrival-rate, SLA auto-tune) plus 30+ accuracy datasets with LLM-as-judge.
- **Libraries** — browse / pre-download HF model weights & datasets from the UI; tool-calling parser helper; LoRA support.
- **Secure by config** — admin-token-gated controls, plus mint/revoke API keys with per-key usage attribution.

See [docs/features.md](docs/features.md) for the full breakdown.

## Quick start

Requires Docker with the NVIDIA Container Toolkit (on WSL2, enable GPU support in
Docker Desktop).

```bash
cp deploy/.env.example deploy/.env   # set HF_TOKEN, which GPUs, the admin token
make up                              # build + start the whole stack
# open http://localhost:8884
```

`make down` stops it · `make logs` tails all services · `make ps` shows status.

```bash
curl http://localhost:8887/v1/models     # router: configured model groups
curl http://localhost:5000/api/models    # backend: lifecycle state of each instance
# http://localhost:8884/grafana          # dashboards + alerts
```

Every `deploy/.env` setting — host **ports** (`FRONTEND_PORT`/`ROUTER_PORT`/…), auth
tokens, GPU selection, and cache locations — is documented inline in
[deploy/.env.example](deploy/.env.example) and tabulated in
[docs/deployment.md#environment-variables-deployenv](docs/deployment.md#environment-variables-deployenv).
Full topology, the shared-netns rationale, volumes, and a manual run are in the same
[docs/deployment.md](docs/deployment.md).

## One endpoint for the whole fleet

Every model is reachable through a single OpenAI-compatible origin — the router on
`:8887` (or `/v1` via the dashboard's nginx). Pick the model by its `model` field and
the router load-balances across that group's instances:

| Endpoint | Purpose |
|---|---|
| `POST /v1/chat/completions` | Chat — streaming supported; load-balanced across the model group |
| `POST /v1/completions` | Text completion |
| `POST /v1/messages` | Anthropic-compatible Messages API — streaming supported (Anthropic SSE) |
| `POST /v1/messages/count_tokens` | Count tokens for a Messages request (no generation) |
| `POST /v1/embeddings` | Embeddings — OpenAI-compatible (forwarded to the embedding server) |
| `POST /v1/rerank` | Reranking — Jina/Cohere-compatible (`query` + `documents` → sorted `results`) |
| `POST /v1/score` | Pairwise relevance scoring (`text_1` × `text_2`) |
| `POST /tokenize` · `POST /detokenize` | Token utilities — text ⇄ token ids (any model kind) |
| `GET /v1/models` | List configured model groups |

```bash
curl http://localhost:8887/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "Qwen3-0.6B", "messages": [{"role": "user", "content": "hi"}]}'
```

Request/response shapes and auth details are in [docs/API.md](docs/API.md).

## Architecture

```mermaid
flowchart LR
    Client([Clients])
    FE["<b>frontend</b><br/>nginx · :8884<br/>single origin"]
    VLLM["<b>vLLM instances</b><br/>:800x"]
    GF["<b>grafana</b><br/>/grafana"]
    DCGM["dcgm-exporter<br/>:9400 · GPU"]
    NODE["node-exporter<br/>:9100 · host"]

    subgraph netns["shared network namespace"]
        BE["<b>backend</b> · :5000<br/>model lifecycle"]
        RT["<b>router</b> · :8887<br/>OpenAI-compatible LB"]
        PR["<b>prometheus</b> · :9090"]
    end

    Client --> FE
    FE -->|/api| BE
    FE -->|/v1| RT
    FE -->|/grafana| GF
    BE -->|launch on demand| VLLM
    RT -->|route + balance| VLLM
    PR -->|scrape /metrics| VLLM
    PR --> DCGM
    PR --> NODE
    GF -->|query| PR
```

The **router only routes** — the **backend owns model lifecycle**. The frontend, router,
backend, and Grafana sit behind nginx on a single origin; backend, router, and Prometheus
share one network namespace so the spawned vLLM instances are reachable on `localhost`.

## Documentation

| Topic | |
|---|---|
| Deployment & topology | [docs/deployment.md](docs/deployment.md) |
| Configuration (`config.yaml`) | [docs/configuration.md](docs/configuration.md) |
| Features in depth | [docs/features.md](docs/features.md) |
| Monitoring (Prometheus + Grafana) | [docs/monitoring.md](docs/monitoring.md) |
| HTTP API | [docs/API.md](docs/API.md) |

## Requirements

NVIDIA GPU (CUDA 13.1+ recommended) · 16GB+ RAM · 50GB+ disk.

> **Tip — running multiple instances on limited RAM.** Each vLLM instance runs
> `torch.compile` + CUDA-graph capture on startup, which is heavy on **system RAM**
> (not VRAM). On a small box (e.g. WSL2 with ~8GB RAM), launching a second instance
> of the same model can exhaust RAM and thrash swap, leaving the new instance stuck
> in `starting`. Add **`--enforce-eager`** to the launch command to skip compilation:
> startup drops from minutes to seconds and RAM/CPU pressure falls sharply, at a small
> inference-latency cost. RAM — not VRAM — is usually the bottleneck for multi-instance,
> so give WSL more memory (`.wslconfig` → `memory=12GB`, then `wsl --shutdown`) before
> scaling out.

## License

MIT — see [LICENSE](LICENSE).
