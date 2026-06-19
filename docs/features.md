# Features in depth

> [中文](features_zh-CN.md)

## Model management

- Multi-model, multi-instance management on vLLM (LLM, Embedding, Reranker).
- Per-instance lifecycle (start/stop) with a live state machine
  (`stopped → starting → ready → failed/stopping`), driven by a reconciler that
  derives the true state from process liveness + `/health` probes.
- **Add models from the UI by pasting a `vllm serve …` command** — it is parsed into
  an editable form and layered on as a dynamic *overlay*, so the hand-maintained
  `config.yaml` stays untouched; the router hot-reloads (`POST /reload`) so new models
  are routable end-to-end.
- Load-aware routing: the router auto-selects the least-loaded instance (weighting
  running / waiting requests + KV-cache usage).

## Reliability

- **VRAM pre-flight guard** — blocks a start that would likely OOM, with a one-click
  *Force start* override.
- **GPU auto-placement** — an instance with no pinned `cuda_device` is placed on the
  GPU with the most free memory.
- **Auto-restart** — a managed model that crashes is restarted with exponential
  backoff (configurable budget, resets once healthy).

## Observability

- Real-time status via Server-Sent Events (no polling).
- **System topology** (Vue Flow) — a live mission-control graph of Clients → Router →
  model groups / Embedding → GPUs, with animated traffic edges, GPU-placement edges,
  and a control plane; nodes are clickable drill-ins.
- **Router load-balancing view** — an animated fan showing each replica's real traffic
  share and the instance the router will pick next.
- **Grafana monitoring** (bundled) — see [monitoring.md](monitoring.md).
- Per-model usage (count, error rate, p50/p95 latency, tokens), request log, and a
  state-transition event timeline.
- GPU / CPU / memory monitoring plus a GPU-process inventory.

## Playground

- OpenAI-compatible **chat (streaming)**, completions, **embeddings**, and
  **reranking**, sent straight through the router.
- **Reasoning ("thinking") display** — when a model runs with a vLLM reasoning parser,
  the `reasoning` stream is shown in a collapsible block above the answer.

## Benchmarking & evaluation (evalscope)

- **Load testing** (`/benchmark`) — concurrency sweep, arrival-rate open-loop,
  multi-turn, **SLA auto-tune**, plus **embedding / rerank** throughput and
  single-request **speed benchmark**; each run is an isolated subprocess, with live
  charts, run comparison, and the full evalscope HTML report.
  See [evalscope_模型壓測整理.md](evalscope_模型壓測整理.md).
- **Accuracy / quality evaluation** (`/eval`) — **30+ benchmark datasets** grouped by
  capability tier (Baseline, Knowledge, Chinese, Reasoning, Math, Multilingual,
  **Tool-calling**, **Long-context**, Code, and judge-scored QA): MMLU/ARC/GSM8K/IFEval,
  C-Eval/C-MMLU, GPQA/MMLU-Pro, AIME, HumanEval, ToolBench/General-FunctionCall,
  Needle-in-a-Haystack, …
  See [evalscope_LLM評測集整理.md](evalscope_LLM評測集整理.md).
  - Per-dataset scores, a **run-to-run comparison matrix** (highlights the best per
    dataset), and the interactive HTML report.
  - **LLM-as-judge** for free-form QA — pick one of your own deployed models (via the
    router) or an external OpenAI-compatible API.
  - **Advanced `dataset_args`** — few-shot count + raw per-dataset overrides (subset
    selection, etc.).
  - Sanity guards: judge-scored datasets require a judge; long-context and real
    tool-calling datasets warn about their model prerequisites (large `max_model_len`,
    vLLM tool parser).

## Libraries

- **Model library** (`/library`) — scan / pre-download / delete HF model weights from
  the UI, with live download progress.
- **Dataset library** (`/datasets`) — pre-download load-test and evaluation datasets
  into the shared ModelScope cache so a run never stalls on a first-time download.
- **Tool-calling config helper** — the model editor maps model families to the right
  vLLM `tool_call_parser` (Qwen→`hermes`, Qwen3-Coder→`qwen3_xml`,
  Llama→`llama3_json`/`llama4_pythonic`, …) with one-click preset insertion.
  See [vllm_auto_tool_整理.md](vllm_auto_tool_整理.md).
- **LoRA** — see [vLLM_LoRA_部署整理.md](vLLM_LoRA_部署整理.md).

## UX & security

- Light / dark theme, dense "control-room" interface.
- **Admin-token-gated control** (start / stop / add / edit / remove) and **API-key
  management** — mint/revoke keys that authenticate router inference, with per-key
  usage attribution in the request log.
