# LlamacppLauncher 實作設計

> 目標:把 **llama.cpp(`llama-server`)** 接成第三個推理引擎,與 vLLM / SGLang 對稱。做法沿用
> [multi-backend-engine-design_zh-CN.md](multi-backend-engine-design_zh-CN.md) 的抽象層:新增一個
> `LlamacppLauncher` + 自家 arg builder + capabilities + per-engine image,**不碰既有路徑**。
>
> **依據**:官方調研與本機 live 實測見 [llama_cpp_serve.md](llama_cpp_serve.md)(image `server-cuda`
> build `b9853`,已跑通 `-hf` 啟動 / `--alias` / `/health` / 串流 usage / `--metrics` / 靜態+runtime LoRA)。
> 對標調研原始清單見 [llamacpp-launcher-research_zh-CN.md](llamacpp-launcher-research_zh-CN.md)。
>
> **設計原則**:collapsed-first、零行為變更、capability-gated。schema 的 `engine` Literal 與前端
> `ENGINE_OPTIONS` **已預留 `llamacpp`**,不需改型別。

## 0. 兩個與 vLLM/SGLang 不同、必須在實作時處理的差異(live 實測得出)

這兩點是本引擎最容易踩雷的地方,先講:

1. **readiness:啟動中 `/health` 是「連線被拒」,不是 503。**
   llama-server **先把模型載入完才綁 port**(`load_model → model loaded → listening`),所以下載/載入期間
   探針會拿到 connection-refused,不是 vLLM/SGLang 那種「已綁 port + 回 503」。→ 探針邏輯必須把連線被拒
   當成「還沒好」(我們現有輪詢「等 200」本來就相容,但註解要寫明,別誤判成容器崩潰)。

2. **runtime LoRA 只能「調既有 adapter 的 scale/開關」,不能「上傳新 adapter」。**
   `POST /lora-adapters` 只吃 `[{id, scale}]`,對象是**啟動時已 `--lora` 載入**的 adapter;沒有 vLLM/SGLang
   的 `POST /load_lora_adapter`(給 name+path 動態掛新的)。→ **本引擎不宣告 `CAP_RUNTIME_LORA`**
   (該能力在本專案語意=動態掛新 adapter,見 [manager.py](../apps/backend/app/llmops/manager.py) `load_lora`/
   `_post_lora`),只宣告 `CAP_LORA_MODULES`(啟動靜態 adapter)。scale 熱調整列為**未來可選能力**,不擋 v1。

## 1. 檔案改動清單(對照 SGLang 的落地面)

| # | 檔案 | 改動 | 對照 SGLang |
|---|---|---|---|
| 1 | [launchers.py](../apps/backend/app/llmops/launchers.py) | 新增 `build_llamacpp_cli_args` + `LlamacppLauncher` | `build_sglang_cli_args` / `SglangLauncher` |
| 2 | [deploy/engine-llamacpp.Dockerfile](../deploy/engine-llamacpp.Dockerfile) | `FROM ghcr.io/ggml-org/llama.cpp:server-cuda` + 同一份 backend code | `engine-sglang.Dockerfile` |
| 3 | [docker-compose.mixed.yaml](../deploy/docker-compose.mixed.yaml) | 新增 `llamacpp-backend` service(`LLMOPS_NODE_ENGINES=llamacpp`,獨立 SD path/port) | `sglang-backend` |
| 4 | [vllm_metrics_client.py](../apps/router-server/src/llm_router/vllm_metrics_client.py) | `METRIC_NAMES_BY_ENGINE["llamacpp"]` | `["sglang"]` |
| 5 | ~~Prometheus scrape config~~ | **無需改**:[prometheus.mixed.yml](../deploy/prometheus.mixed.yml) 的 `job_name: engines` 已 glob `targets/*.json`,新 backend 寫自己的 SD 檔即自動被抓 | — |
| 6 | [vllm_command.py](../apps/backend/app/services/vllm_command.py) | `parse_llamacpp_command` + dispatcher 增 llamacpp 分支(選配,paste 用) | `parse_sglang_command` |
| 7 | [AddModelDialog.vue](../apps/frontend_llmops/src/components/AddModelDialog.vue) | llamacpp 加速面板 + jinja/chat_template + GGUF 量化欄位 | SGLang 加速面板 |
| 8 | 測試 | `test_launchers` / `test_vllm_command` 加 llamacpp 案例 | 既有 sglang 測試 |

schema 無需改(`engine` Literal 已含 `llamacpp`)。

## 2. arg builder:`build_llamacpp_cli_args`(核心)

### 2.1 模型定址(最大差異點)

llama.cpp 吃 **GGUF**,不吃 safetensors repo tag。三種來源,由 `model_tag`(+ 選配 extra 欄位)決定:

| 情境 | config | 產生的旗標 |
|---|---|---|
| HF GGUF repo(主流) | `model_tag: Qwen/Qwen2.5-0.5B-Instruct-GGUF`,`gguf_quant: Q4_K_M`(extra) | `-hf Qwen/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M` |
| HF GGUF 指定檔名 | + `hf_file: xxx.gguf`(extra) | `-hf <repo> -hff xxx.gguf` |
| 本地 GGUF 檔 | `model_tag: /models/foo.gguf`(以 `.gguf` 結尾) | `-m /models/foo.gguf` |

- **served name**:一律 `--alias <served_model_name or model_tag>`,讓 `/v1/models` 的 id 穩定 =
  router forward_name(live 驗證有效)。
- 判斷規則:`model_tag` 以 `.gguf` 結尾 → `-m`;否則視為 HF repo → `-hf`,quant 從 `gguf_quant`
  附成 `repo:quant`(沒給就不附,llama.cpp 預設找 `Q4_K_M`)。

### 2.2 通用參數翻譯表(§2.3 的 llamacpp 欄)

| engine-neutral 欄位 | llama.cpp 旗標 | 備註 |
|---|---|---|
| `max_model_len` | `-c` / `--ctx-size` | 直接對應 |
| `gpu_memory_utilization` | **不翻譯 / 跳過** | llama.cpp 無比例制;語意不對齊(見 2.4) |
| `tensor_parallel_size` | **不翻譯 / 跳過** | 多 GPU 走 `--split-mode`/`--tensor-split`(引擎原生欄位透傳) |
| `dtype` | **不翻譯 / 跳過** | 權重量化由 GGUF 決定;KV dtype 另走 `--cache-type-k/v` |
| `model_tag` | `-hf`/`-m` + `--alias`(見 2.1) | |

> 跳過的三個欄位:arg builder 直接**丟棄**(不報錯),因為它們是 engine-neutral 的通用欄位,對 llama.cpp
> 無意義。前端不對 llamacpp 顯示這三欄(見 §7)。其餘 `extra="allow"` 的引擎原生旗標(`n_gpu_layers`、
> `split_mode`、`batch_size`…)kebab-case 後透傳。

### 2.3 bool 慣例

⚠️ **與 SGLang 不同**:llama.cpp 多數 bool 是**成對** `--flag`/`--no-flag`(如 `--jinja`/`--no-jinja`、
`--cont-batching`/`--no-cont-batching`)。**不能沿用 SGLang 的 store_true**(只 `--flag`)。
比較接近 **vLLM 的 BooleanOptionalAction**:`True → --flag`,`False → --no-flag`。
但仍有少數單向旗標(如 `--flash-attn` 有些版本是 `on/off/auto` 三態),實作時對「已知三態旗標」特判、
其餘 bool 走 `--flag`/`--no-flag`。**先以官方 `--help` 為準逐一確認再寫死。**

### 2.4 固定注入

- `--metrics`:一律帶(autoscaler/Prometheus 訊號需要;預設 disabled)。對照 SGLang 的 `--enable-metrics`。
- `--host` / `--port`:同 vLLM/SGLang;沿用 `LLMOPS_VLLM_BIND_HOST` 決定 bind host(HA split)。
- LoRA:有 `lora_modules` 時,每個 adapter 產一個 `--lora <path>`(或 `--lora-scaled <path>:<scale>`);
  **adapter 必須是與基座匹配的 GGUF**。無 vLLM 的 `--lora-modules` JSON、無 SGLang 的 `NAME=PATH`。

### 2.5 `_SKIP_CLI_KEYS`(對照 `_SGLANG_SKIP_CLI_KEYS`)

跳過:`model_tag`、`served_model_name`、`id`、`cuda_device`、`enable_lora`、`lora_modules`、
`gguf_quant`、`hf_file`、`allow_runtime_lora` + `_ROUTER_ONLY_KEYS`(`routing_strategy`/`kind`/`engine`)
+ 通用不對齊的 `gpu_memory_utilization`/`tensor_parallel_size`/`dtype`。

## 3. `LlamacppLauncher` class

```python
class LlamacppLauncher:
    kind = ModelKind.LLM
    engine = "llamacpp"
    # 只宣告靜態 LoRA + 指標。無 sleep(只有 auto idle,非 /sleep+/wake_up)、
    # 無 kv_transfer、無 CAP_RUNTIME_LORA(llama.cpp 只能調既有 adapter scale,
    # 不能動態掛新 adapter;見 §0.2 與 llama_cpp_serve.md E-LoRA)。
    capabilities = frozenset({CAP_LORA_MODULES, CAP_METRICS_LLAMACPP})

    def keys(self, config): ...        # 依 engine == "llamacpp" 過濾,同 SglangLauncher
    def build_spec(self, config, config_path, key) -> LaunchSpec:
        # 同 SglangLauncher:merge settings+instance override、單卡 cuda_device →
        # CUDA_VISIBLE_DEVICES、drop id、bind_host。
        command = ["llama-server"] + build_llamacpp_cli_args(cli_cfg)
        # probe_url = /health(就緒 200;啟動中連線被拒 → 探針視為未就緒)
        return LaunchSpec(engine="llamacpp", capabilities=..., probe_url=".../health", ...)
```

- 新增能力常數:`CAP_METRICS_LLAMACPP = "metrics_llamacpp"`(對照 `CAP_METRICS_SGLANG`)。
- `command[0]`:image 內是 **`llama-server`**(在 PATH,非 `python -m`)。
- 探針:`http://{host}:{port}/health`;**不要**因啟動中連線被拒就判失敗(§0.1)。
- `CUDA_VISIBLE_DEVICES`:沿用單卡路徑(live 未逐證多卡,單卡先過)。

## 4. 指標:`METRIC_NAMES_BY_ENGINE["llamacpp"]`

```python
"llamacpp": {
    "running": "llamacpp:requests_processing",   # live 驗證
    "waiting": "llamacpp:requests_deferred",      # live 驗證
    "kv_cache_usage_perc": "llamacpp:__absent__", # 無此指標 → parse 取不到 → 預設 0.0
    "prompt_tokens": "llamacpp:prompt_tokens_total",
    "generation_tokens": "llamacpp:tokens_predicted_total",
},
```

- KV 使用率 llama.cpp **不吐**(live 證實),給一個不存在的名字讓 `parsed.get(...)` 落回 `0.0`;
  autoscaler 對 llamacpp group 實質只看 `waiting`/`running`(可接受)。
- Prometheus:**scrape config 零改**。llamacpp backend 設 `LLMOPS_PROMETHEUS_SD_PATH=/sd/targets-llamacpp.json`
  + 掛共用 `mixed-sd` 卷即可;[prometheus.mixed.yml](../deploy/prometheus.mixed.yml) 的 `job_name: engines` 已
  glob `/etc/prometheus/targets/*.json` 並保留 `engine` label。實例帶 `engine=llamacpp` 由
  [prometheus_targets.py](../apps/backend/app/services/prometheus_targets.py) L60 自動寫入(引擎無關,已支援)。
- Grafana:可做一份並列 llamacpp dashboard(query `llamacpp:*`),或先略過(非阻塞)。

## 5. Image + compose

### 5.1 `deploy/engine-llamacpp.Dockerfile`(對照 sglang 版)

```dockerfile
FROM ghcr.io/ggml-org/llama.cpp:server-cuda
WORKDIR /app
COPY apps/backend/requirements.txt /tmp/backend-req.txt
COPY apps/router-server/requirements.txt /tmp/router-req.txt
# 這顆 image 只啟動 llama.cpp;移除 vllm/sglang/pytest,避免拉多 GB 輪子。
RUN sed -i -E '/^(vllm|sglang|pytest.*)$/d' /tmp/router-req.txt /tmp/backend-req.txt \
    && pip install --no-cache-dir -r /tmp/backend-req.txt -r /tmp/router-req.txt
COPY apps/backend ./apps/backend
COPY apps/router-server ./apps/router-server
COPY packages ./packages
ENTRYPOINT []
CMD ["bash"]
```

> ✅ **已驗證(step 1)**：`server-cuda`(build `b9853`)有 python3.12 但 **無 pip/ensurepip/gcc**(runtime-only)。
> 解法(不需改用 `full-cuda`）：`apt-get install python3-pip python3-venv` → 裝進隔離 venv（避 PEP-668）。
> 另有兩個 base 坑，Dockerfile 已處理：
> 1. **`llama-server` 不在 PATH**：binary 在 `/app/llama-server`（base 的 ENTRYPOINT，我們清掉了）→ symlink 到 `/usr/local/bin`。
> 2. **`LD_LIBRARY_PATH`（真 bug）**：`llama-server` 的 `.so`（`libllama-server-impl.so`、`libggml-*.so`）在 `/app`，
>    透過 symlink 執行時 `$ORIGIN` rpath 解析斷掉 → rc=127「cannot open shared object file」。Dockerfile 補
>    `ENV LD_LIBRARY_PATH=/app:/usr/local/cuda/lib64`。
> 3. **剝離**：`vllm`/`bitsandbytes`（依賴 torch，base 無）/`sglang`/`pytest`。evalscope 可留（perf/eval 走隔離子行程，啟動不 import）。

### 5.2 compose service(對照 `sglang-backend`)

```yaml
llamacpp-backend:
  build: { context: .., dockerfile: deploy/engine-llamacpp.Dockerfile }
  image: llmops-engine-llamacpp:latest
  container_name: mixed-llamacpp-backend
  command: uvicorn main:app --host 0.0.0.0 --port 5000
  environment:
    - LLMOPS_INSTANCE_ID=llamacpp-node
    - LLMOPS_NODE_HOST=mixed-llamacpp-backend
    - LLMOPS_NODE_ENGINES=llamacpp
    - LLMOPS_NODE_API_URL=http://mixed-llamacpp-backend:5000
    - LLMOPS_PROMETHEUS_SD_PATH=/sd/targets-llamacpp.json
    - LLMOPS_VLLM_BIND_HOST=0.0.0.0
    # …其餘同 sglang-backend(DB/overlay/HF_HOME/router url…)
  ports: ["${MIXED_LLAMACPP_PORT:-5073}:5000"]
  # llama.cpp 不需 sglang 的大 shm;GPU reservation 照舊
```

## 6. paste-command 解析(選配,`parse_llamacpp_command`)

對照 `parse_sglang_command`:tokenize `llama-server ...`、取 `-m`/`-hf`(→ model_tag,`-hf repo:quant` 拆出
`gguf_quant`)、`--alias`(→ served name)、`-c`(→ max_model_len)、`--lora`(→ lora_modules,GGUF path)、
反向 kebab→snake 透傳其餘。dispatcher(`parse_command`)sniff `"llama-server" in cmd` 或 engine hint。
**v1 可先只做手動表單,paste 後補。**

## 7. 前端 Add Model presets(對照 SGLang 面板)

- **隱藏** engine-neutral 的 `gpu_memory_utilization` / `dtype` / `tensor_parallel_size`(對 llamacpp 無意義)。
- **llamacpp 加速面板**(高優先):`n_gpu_layers`、`ctx_size`、`batch_size`(-b)、`ubatch_size`(-ub)、
  `parallel`(-np)、`cont_batching`、`flash_attn`、`cache_type_k`、`cache_type_v`、`split_mode`、`tensor_split`、
  `main_gpu`。
- **模型來源 / 量化**:HF repo + `gguf_quant`(下拉常見 quant:Q4_K_M/Q8_0/…)或本地 `.gguf` path;選配 `hf_file`。
- **工具調用**:llama.cpp 是 **template 驅動**(`--jinja` + `--chat-template`/`--chat-template-file`),
  **不是** vLLM/SGLang 的 tool-call-parser 下拉 → UI 給 `jinja on/off` + chat template 選擇,別照抄 parser 枚舉。
- 能力 gating:沿用現有機制,`engineHasSleep`/`engineHasKvShare` 對 llamacpp = false(自動隱藏 sleep/KV 分享);
  LoRA 對 llamacpp 顯示為「啟動靜態 adapter(GGUF)」,不顯示 runtime 熱掛(因無 `CAP_RUNTIME_LORA`)。

## 8. 落地步驟(每步可獨立 commit + 跑全測確保零行為變更)

1. ✅ **Image 前提驗證** — 已完成:[engine-llamacpp.Dockerfile](../deploy/engine-llamacpp.Dockerfile)。
   驗證 base 有 python(無 pip → apt 裝 venv)、`llama-server --version` OK(需 symlink + `LD_LIBRARY_PATH`,見 §5.1)、
   backend FastAPI `from app.main import app` OK、vllm/bitsandbytes 已剝離。
2. ✅ **`LlamacppLauncher` + arg builder** — 已完成:[launchers.py](../apps/backend/app/llmops/launchers.py)
   (`build_llamacpp_cli_args` + `LlamacppLauncher` + `CAP_METRICS_LLAMACPP`)+ [main.py](../apps/backend/app/main.py) 註冊。
   **bool 慣例修正**:經 `llama-server --help` 確認**不可**合成 `--no-<flag>`（`--mlock`/`--check-tensors` 無負向形），
   改用 store_true（True→`--flag`，False→省略），負向旗標用原生 key。**驗收**:+16 測試,backend 333/333 綠。
3. ✅ **compose + 指標 + live 端到端** — 已完成、已 live 驗證(見設計文檔外的驗證記錄):
   [docker-compose.mixed.yaml](../deploy/docker-compose.mixed.yaml) `llamacpp-backend` + `METRIC_NAMES_BY_ENGINE["llamacpp"]`。
   驗證:node 註冊 `engines:["llamacpp"]` → scheduler engine-aware 排到 llamacpp-node → launcher 用 arg builder
   逐字指令 spawn llama-server → READY → router 推理(非串流 + **串流 final chunk 帶 usage**,router 零改自動注入
   `include_usage`)→ Prometheus `engines` glob 自動抓 `up=1 engine=llamacpp` + `llamacpp:*` → router poller
   正規化成 `{running,waiting,prompt_tokens,generation_tokens}`。**LD_LIBRARY_PATH 是唯一真 bug,已修**。
4. ✅ **前端 presets** — 已完成:[AddModelDialog.vue](../apps/frontend_llmops/src/components/AddModelDialog.vue)
   `engineIsLlamacpp` + llamacpp 加速面板(gguf_quant/n_gpu_layers/ctx/batch/ubatch/parallel/flash_attn/
   cache_type_k,v/split_mode/tensor_split)+ 工具調用改 jinja 驅動(非 parser 下拉)+ 沿用 KV/sleep gating
   自動隱藏。i18n(en/zh)補齊。type-check + build 綠。
5. ✅ **llamacpp Grafana dashboard** — 已完成、已 live 驗證:
   [deploy/grafana/dashboards/llamacpp/llamacpp-dashboard.json](../deploy/grafana/dashboards/llamacpp/llamacpp-dashboard.json)
   (9 panel,`llamacpp:*` + `{engine="llamacpp"}` 篩選 + `instance` 模板變數;`foldersFromFilesStructure`
   自動載入,無需改 provisioning)。Grafana API 確認載入 folder `llamacpp`、template 變數解析、panel query 有資料
   (無「No data」)。**註**:llama.cpp 無 KV-usage 指標,dashboard 明載此事、不放 KV panel。
6. **(選配)** paste 解析(§6)、多 GPU split 驗證、capability 回歸細測(`/sleep`/runtime-LoRA 對 llamacpp 回 409)。

### 測試模型(來自 [llama_cpp_serve.md](llama_cpp_serve.md) §I,均可 `-hf` 直拉)

- chat smoke:`Qwen/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M`(已在本機快取,live 驗證過)
- LoRA:base `HuggingFaceTB/SmolLM2-360M-Instruct-GGUF:Q8_0` + GGUF adapter
  `asynclee/SmolLM2-360M-Instruct-fc-cn-lora-F16-GGUF`(live 驗證過)
- embedding(若之後擴到 embed):`Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0`

## 9. 計費 / router 相容(核對後:router 零改)

llama.cpp 串流**不帶 `stream_options.include_usage` 就完全沒有 usage**(live 證實)。但**router 已經替我們做了**:
[router.py](../apps/router-server/src/llm_router/router.py) L35-36 `_OPENAI_STREAM_PATHS = ("/v1/chat/completions",
"/v1/completions")`,L381-387 對這些路徑無條件 `opts.setdefault("include_usage", True)`——**引擎無關**。
所以 llamacpp 計費**零改 router**,forward 過去自動帶 usage(live 驗證 llama.cpp 會照 `include_usage` 回 final
usage chunk)。**唯一注意**:`/v1/messages`(Anthropic)不注入此欄(見 L381 註解),但那條路徑本來就不吃
llama.cpp 的 OpenAI usage;走 `/v1/chat/completions` 即可。router proxy 全程零修改。

## 10. capabilities 總表(對照)

| capability | vLLM | SGLang | **llamacpp** |
|---|---|---|---|
| `CAP_SLEEP` | ✅ | ❌ | ❌(只有 auto idle,非 /sleep+/wake_up) |
| `CAP_RUNTIME_LORA` | ✅ | ✅ | ❌(只能調既有 adapter scale,不能掛新的) |
| `CAP_LORA_MODULES` | ✅ | ✅ | ✅(`--lora <gguf>`) |
| `CAP_KV_TRANSFER` | ✅ | ❌ | ❌ |
| metrics | `CAP_METRICS_VLLM` | `CAP_METRICS_SGLANG` | `CAP_METRICS_LLAMACPP`(缺 kv 維度) |

## 11. 不做什麼

- **不重寫 router**:OpenAI proxy 引擎無關;唯一動作是確保串流帶 `include_usage`(§9)。
- **不在 embedding 引入 llamacpp**(本次):聚焦 `kind=llm`。llama.cpp 的 `--embedding` 模式未來再說。
- **不做 runtime「掛新 adapter」**:llama.cpp 做不到;LoRA 走「編輯 group 加啟動 adapter → 重啟」。
- **不假設多 GPU**:單卡先過,`--split-mode`/`--tensor-split` 當引擎原生欄位透傳,之後再 live 驗證。

## 12. 相關檔案索引

- [llama_cpp_serve.md](llama_cpp_serve.md) — 官方調研 + live 實測(本設計依據)
- [llamacpp-launcher-research_zh-CN.md](llamacpp-launcher-research_zh-CN.md) — 對標調研清單
- [multi-backend-engine-design_zh-CN.md](multi-backend-engine-design_zh-CN.md) — 抽象層藍圖(§4 capabilities、§5 image)
- [launchers.py](../apps/backend/app/llmops/launchers.py) — `SglangLauncher` 為主要範本
- [manager.py](../apps/backend/app/llmops/manager.py) — `_launcher_for` / `load_lora` / `_post_lora`(LoRA 能力 gate)
- [vllm_metrics_client.py](../apps/router-server/src/llm_router/vllm_metrics_client.py) — `METRIC_NAMES_BY_ENGINE`
- [engine-sglang.Dockerfile](../deploy/engine-sglang.Dockerfile) — image 對稱範本
- [docker-compose.mixed.yaml](../deploy/docker-compose.mixed.yaml) — `sglang-backend` service 範本
