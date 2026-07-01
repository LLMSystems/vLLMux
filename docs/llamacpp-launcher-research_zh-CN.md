# llama.cpp Launcher 對標調研清單

> 目的:在加入 **llama.cpp** 作為第三個推理引擎前,把要向官方最新資料查證的項目,**對標**目前
> vLLM / SGLang 在這套程式裡**實際用到的**行為,整理成可回填的清單。查到答案後即可依
> [multi-backend-engine-design_zh-CN.md](multi-backend-engine-design_zh-CN.md) §6 的落地步驟實作。
>
> 用法:把每張表 `llama.cpp(需查)` 欄的 `?` 補上官方最新答案(請以 **CUDA server image** 為準,
> 因為引擎在 backend 容器內 spawn;順手記下版本號)。
>
> **最關鍵兩項**:A 的**模型定址**(GGUF vs HF tag)、F 的 **final usage chunk**(計費依賴),建議優先確認。
>
> 對應程式:[launchers.py](../apps/backend/app/llmops/launchers.py)、
> [vllm_metrics_client.py](../apps/router-server/src/llm_router/vllm_metrics_client.py)、
> [schema.py](../packages/config-schema/schema.py)(`engine` Literal 已預留 `llamacpp`)、
> [AddModelDialog.vue](../apps/frontend_llmops/src/components/AddModelDialog.vue)(前端 ENGINE_OPTIONS 已列出)。

---

## A. 啟動與模型定址(arg builder 核心 → `build_llamacpp_cli_args`)

| 概念 | vLLM(現有) | SGLang(現有) | llama.cpp(**需查**) |
|---|---|---|---|
| 容器內啟動指令 | `vllm serve` | `python3 -m sglang.launch_server` | ? (`llama-server`?完整路徑?) |
| 官方 CUDA image 名/tag | `vllm/vllm-openai` | `lmsysorg/sglang` | ? |
| 模型怎麼指定 | 位置參數 `<hf_tag>` | `--model-path <tag>` | ? (`-m <本地gguf>`?能否 `-hf user/repo:quant` 直接拉?) |
| **served name**(router forward_name 要穩定) | 預設=tag | `--served-model-name <tag>` | ? (`--alias`/`-a`?) |
| host / port | `--host` / `--port` | 同 | ? |
| bool 慣例 | BooleanOptionalAction(`--flag`/`--no-flag`) | store_true(只 `--flag`) | ? (store_true?有無 `--no-` 對?) |

> 模型定址是**最大差異點**:vLLM/SGLang 吃 HF repo tag,llama.cpp 傳統吃**本地 GGUF 檔**。
> 請務必查清:(1) 能否用 `-hf` 在啟動時自動下載 GGUF?(2) 若只能本地檔,GGUF 檔的下載/存放要不要另接 Library。

---

## B. 通用參數翻譯(設計文件 §2.3 翻譯表 → 各引擎自家旗標)

| engine-neutral 欄位 | vLLM 旗標 | SGLang 旗標 | llama.cpp(**需查**) |
|---|---|---|---|
| `max_model_len` | `--max-model-len` | `--context-length` | ? (`-c`/`--ctx-size`?) |
| `gpu_memory_utilization` | `--gpu-memory-utilization` | `--mem-fraction-static` | ? (llama.cpp 無比例制,是 `-ngl`/`--n-gpu-layers` 層數 offload?→ 此欄語意對不上要註明) |
| `tensor_parallel_size` | `--tensor-parallel-size` | `--tp-size` | ? (多GPU:`--split-mode` / `--tensor-split` / `--main-gpu`?) |
| `dtype` | `--dtype` | `--dtype` | ? (GGUF 已預量化,應無此旗標?請確認) |
| GPU offload 層數(llama.cpp 特有) | — | — | ? (`-ngl` 全放GPU / 部分 / 純CPU 的用法) |

---

## C. 健康探針(→ `build_spec` 的 `probe_url`)

| 項目 | vLLM/SGLang(現有) | llama.cpp(**需查**) |
|---|---|---|
| readiness endpoint | `/health` | ? (`/health` 有嗎?) |
| 載入中 vs 就緒的回應碼 | starting 時 503,ready 時 200 | ? (載入中是否回 503,好當 readiness?) |

---

## D. 指標(→ autoscaler 訊號 `METRIC_NAMES_BY_ENGINE` + Grafana dashboard)

| 概念(正規化成 `{waiting,running,kv}`) | vLLM 指標 | SGLang 指標 | llama.cpp(**需查**) |
|---|---|---|---|
| 開啟 /metrics 的方式 | 預設就有 | `--enable-metrics` | ? (`--metrics`?) |
| 指標命名前綴 | `vllm:*` | `sglang:*` | ? (`llamacpp:*`?) |
| waiting / 佇列深度 | `vllm:num_requests_waiting` | `sglang:num_queue_reqs` | ? |
| running / 處理中 | `vllm:num_requests_running` | `sglang:num_running_reqs` | ? |
| KV cache 使用率 | `vllm:...kv_cache_usage_perc` | `sglang:token_usage` | ? (`llamacpp:kv_cache_usage_ratio`?) |

> 查得到 → 宣告 `metrics_llamacpp` capability + 做一份並列 dashboard;查不到 → autoscaler 對 llama.cpp group 退化成固定副本(可接受)。

---

## E. Capabilities 對標(→ `SglangLauncher.capabilities` 那組 frozenset)

逐一確認 llama.cpp **有沒有**,決定它宣告哪些能力:

| capability | vLLM | SGLang | llama.cpp(**需查**) |
|---|---|---|---|
| `sleep`(/sleep+/wake_up 釋放VRAM) | ✅ `--enable-sleep-mode`+`VLLM_SERVER_DEV_MODE` | ❌ | ? (幾乎確定 ❌,請確認無等價端點) |
| `runtime_lora`(熱插 LoRA 端點) | ✅ `VLLM_ALLOW_RUNTIME_LORA_UPDATING` + `/v1/...` | ✅ `POST /load_lora_adapter` | ? (有 `GET/POST /lora-adapters` 熱切換嗎?端點路徑+payload) |
| `lora_modules`(啟動帶靜態 LoRA) | ✅ `--lora-modules name=path`(JSON) | ✅ `--lora-paths NAME=PATH` | ? (`--lora <gguf>` / `--lora-scaled`?LoRA 要不要 GGUF 格式?) |
| `kv_transfer`(跨實例KV共享) | ✅ `--kv-transfer-config` | ❌ | ? (幾乎確定 ❌) |
| metrics 格式 | `metrics_vllm` | `metrics_sglang` | ? (見 D) |

---

## F. OpenAI 端點覆蓋度(→ 驗證 router **零修改**;設計文件 §7 風險項)

| 端點/能力 | vLLM/SGLang(現有可用) | llama.cpp(**需查**) |
|---|---|---|
| `/v1/chat/completions` 串流 | ✅ | ? |
| 串流最後的 **final usage chunk**(計費靠這個) | ✅ | ? (務必確認,缺了計費會漏) |
| `/v1/completions` | ✅ | ? |
| `/v1/embeddings`(若當 embed 用) | ✅ | ? (`--embedding` 模式?) |
| `/v1/models` | ✅ | ? |
| `/tokenize` `/detokenize` | ✅ | ? (有,但形狀是否相容 router?) |
| 工具調用 / function calling | ✅(有 tool-call parser) | ? (`--jinja` / 內建 tool parser?parser 選項有哪些) |

---

## G. GPU 放置與容器需求(→ `build_spec` env + Dockerfile)

| 項目 | vLLM/SGLang(現有) | llama.cpp(**需查**) |
|---|---|---|
| 選卡 | `CUDA_VISIBLE_DEVICES`(單卡路徑) | ? (吃 `CUDA_VISIBLE_DEVICES` 嗎?多卡切片旗標) |
| 純CPU / 部分offload | 不支援 | ? (`-ngl 0` 純CPU / 部分的用法 — 這是它最大賣點,要清楚) |
| 容器特殊需求 | SGLang 需 `--ipc=host`/大 shm | ? (shm 需求?HF/GGUF cache 掛載路徑?) |

---

## H. 前端 Add Model presets(→ `AddModelDialog.vue` 的加速面板 + 工具調用)

| 項目 | 用途 | llama.cpp(**需查**) |
|---|---|---|
| 有意義的「加速參數」清單 | 填 SGLang 那種加速面板 | ? (如 `-ngl`、`-c`、batch `-b/-ub`、`--flash-attn`、KV量化 `--cache-type-k/v`、並行 `-np`、`--cont-batching`…哪些常用) |
| 工具調用 parser 選項 | 對標 vLLM/SGLang 的 tool-call-parser 下拉 | ? (支援哪些?怎麼設) |
| GGUF 量化選擇 | 使用者怎麼選 Q4_K_M 等 | ? (若走 `-hf repo:quant`,quant 標籤格式) |

---

## 回填後的落地步驟(依設計文件 §6)

1. `deploy/engine-llamacpp.Dockerfile`(`FROM` 官方 CUDA server image + 同一份 backend code)。
2. `LlamacppLauncher`(`launchers.py`):`keys()` 依 engine 過濾、`build_spec()` + 自家 arg builder
   (B 的翻譯表 + A 的模型定址 + bool 慣例)、`probe_url`(C)、capabilities(E)。
3. 指標 parser(D)接進 `METRIC_NAMES_BY_ENGINE` + 並列 Grafana dashboard(查得到才做)。
4. 前端 presets(H):加速面板 + 工具調用選項 + GGUF 量化選擇。
5. schema Literal 與前端 ENGINE_OPTIONS **已預留 `llamacpp`**,無需再改型別。
6. 單機 live 驗證:launcher spawn → READY → router proxy 推理(非串流 + 串流 final usage chunk)。
