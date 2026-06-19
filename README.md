<div align="center">

# vLLMux

**One-stop platform to deploy, route, monitor & evaluate your vLLM fleet**

[English](README.md) · [中文](README_zh-CN.md)

![vLLMux](https://img.shields.io/badge/vLLM-multiplexed-5b8def)
![license](https://img.shields.io/badge/license-MIT-green)
![stack](https://img.shields.io/badge/FastAPI%20·%20Vue%203%20·%20Grafana-informational)

![Main Console](assets/image0.png)

</div>

---

**vLLMux** is a self-hosted control plane for serving many LLMs on
[vLLM](https://github.com/vllm-project/vllm). Paste a `vllm serve …` command and it
becomes a routable model; the router load-balances across instances; a reconciler keeps
the fleet converged to the desired state; and a bundled Prometheus + Grafana stack
monitors everything — all behind one Vue dashboard.

> One image, three roles (**backend** launches the models · **router** load-balances ·
> **Prometheus** scrapes them, sharing one network namespace) plus a Vue **frontend** —
> started by a single `docker compose`.

## ✨ Highlights

- **Add a model by pasting `vllm serve …`** — parsed into a form and layered on as a dynamic overlay; the router hot-reloads, no `config.yaml` edits.
- **Lifecycle + self-healing** — per-instance state machine (`stopped → starting → ready → failed`), VRAM pre-flight guard, GPU auto-placement, crash auto-restart with backoff.
- **Load-aware routing** — picks the least-loaded replica (running/waiting requests + KV-cache usage).
- **Live observability** — SSE status, animated system-topology & router-balancing graphs, per-model usage / latency / error stats.
- **Bundled Grafana monitoring** — Prometheus auto-discovers every running instance; Overview / Capacity / Performance / GPU / Host dashboards embedded in-app, with SLO thresholds & alerts.
- **Playground** — OpenAI-compatible chat (streaming) / completions / embeddings / reranking, with reasoning display.
- **Benchmark & evaluate** — evalscope load tests (concurrency, arrival-rate, SLA auto-tune) plus 30+ accuracy datasets with LLM-as-judge.
- **Libraries** — browse / pre-download HF model weights & datasets from the UI; tool-calling parser helper; LoRA support.
- **Secure by config** — admin-token-gated controls, plus mint/revoke API keys with per-key usage attribution.

See [docs/features.md](docs/features.md) for the full breakdown.

## 🚀 Quick start

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

Full topology, the shared-netns rationale, volumes, and a manual run are in
[docs/deployment.md](docs/deployment.md).

## 🧭 Architecture

Clients → **frontend** (nginx, `:8884`, single origin) → **router** (`:8887`,
OpenAI-compatible) → vLLM instances launched on demand by the **backend** (`:5000`).
**Prometheus** + **Grafana** + the **DCGM / node** exporters provide monitoring. The
router only routes — the backend owns model lifecycle.

## 📚 Documentation

| Topic | |
|---|---|
| Deployment & topology | [docs/deployment.md](docs/deployment.md) |
| Configuration (`config.yaml`) | [docs/configuration.md](docs/configuration.md) |
| Features in depth | [docs/features.md](docs/features.md) |
| Monitoring (Prometheus + Grafana) | [docs/monitoring.md](docs/monitoring.md) |
| HTTP API | [docs/API.md](docs/API.md) |
| Benchmarking | [docs/evalscope_模型壓測整理.md](docs/evalscope_模型壓測整理.md) |
| Evaluation datasets | [docs/evalscope_LLM評測集整理.md](docs/evalscope_LLM評測集整理.md) |
| LoRA | [docs/vLLM_LoRA_部署整理.md](docs/vLLM_LoRA_部署整理.md) |
| Tool-calling parsers | [docs/vllm_auto_tool_整理.md](docs/vllm_auto_tool_整理.md) |

## Requirements

NVIDIA GPU (CUDA 13.1+ recommended) · 16GB+ RAM · 50GB+ disk.

## License

MIT — see [LICENSE](LICENSE).
