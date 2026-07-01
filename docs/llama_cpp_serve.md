# llama.cpp Launcher 對標調研回填（官方資料版）

檢查日期: 2026-07-01  
檢查基準: 官方 `ggml-org/llama.cpp` 文件、`llama-server` README、官方 GitHub Container Registry、官方 server API changelog、官方測試/討論串。  

## 先講結論

最關鍵的兩項目前可以先下結論：

1. **模型定址**：`llama-server` 現在不只吃本地 `GGUF`，也支援啟動時直接用 `-hf <user>/<model>[:quant]` 從 Hugging Face 拉取 GGUF；若不指定 quant，預設找 `Q4_K_M`，找不到才 fallback 到 repo 第一個檔案。下載後會進 **標準 Hugging Face cache**，不一定需要另接 model library。[[S2]](#sources) [[S5]](#sources)
2. **final usage chunk**：目前官方 changelog 說明必須帶 `stream_options.include_usage` 才會回 usage；官方測試則明確要求最後一個 stream chunk 在 `[DONE]` 前必須帶 `usage` 與 `timings`，而且 `choices` 應為空陣列。這代表 **router 若要計費，請主動送 `stream_options: {"include_usage": true}`**。[[S4]](#sources) [[S6]](#sources)

## 版本快照

- 官方 Docker 文件列出的 CUDA server rolling tags 為：
  - `ghcr.io/ggml-org/llama.cpp:server-cuda`（CUDA 12）
  - `ghcr.io/ggml-org/llama.cpp:server-cuda13`（CUDA 13）[[S1]](#sources)
- 官方 GitHub Container Registry 版本頁在 **2026-07-01** 顯示，當前 rolling tag 對應到：
  - `server-cuda12-b9853` / `server-cuda-b9853`
  - `server-cuda13-b9853` / `server-cuda13`
  - 兩者都標示為約 4 小時前發布。[[S3]](#sources)
- 官方 `llama-server` REST API changelog 目前最新列出的變更條目是 `b9358`；因此「容器 build 編號」與「changelog 最近條目」不是同一個欄位，實作時建議都記。[[S4]](#sources)

## Live 實測驗證(2026-07-01,本機 RTX 3060 Ti,image `ghcr.io/ggml-org/llama.cpp:server-cuda` build `b9853-7af4279f4`)

以下項目已在**實際容器**上跑過確認(不是只讀官方文件),詳見各節:

| 項目 | 結果 | 節 |
|---|---|---|
| `-hf <repo>:<quant>` 啟動時自動拉 GGUF 進 HF cache | ✅ | A |
| `--alias` → `/v1/models` 的 id 穩定 | ✅ `Qwen2.5-0.5B-Instruct` | A |
| `/health` 就緒 200、`/v1/health` 200 | ✅ | C |
| **啟動中 `/health` = 連線被拒(非 503)**——port 載入完才綁 | ⚠️ 與 vLLM/SGLang 不同 | C |
| 非串流 chat + usage | ✅ | F |
| **串流帶 `stream_options.include_usage` → final chunk `choices:[]`+`usage`+`timings`+`[DONE]`** | ✅ | F |
| **串流不帶 include_usage → 完全沒有 usage**(router 必須主動送) | ✅ 證實 | F |
| `--metrics`:`llamacpp:requests_processing`(running)、`llamacpp:requests_deferred`(waiting) | ✅ | D |
| KV cache 使用率指標**不存在** | ✅ 證實缺 | D |
| `/tokenize` → `{"tokens":[...]}` | ✅(非 OAI 形狀) | F |
| `-ngl 99` GPU offload 生效 | ✅ VRAM +~630MB | G |
| 靜態 `--lora <adapter.gguf>` + runtime `POST /lora-adapters` 調 scale + per-request 覆寫 | ✅ | E-LoRA |
| **llama.cpp runtime LoRA 只能調既有 adapter 的 scale,不能上傳新 adapter** | ⚠️ 與 vLLM/SGLang 不同 | E-LoRA |

---

## A. 啟動與模型定址

| 概念 | vLLM(現有) | SGLang(現有) | llama.cpp（查證結果） |
|---|---|---|---|
| 容器內啟動指令 | `vllm serve` | `python3 -m sglang.launch_server` | **`llama-server`**；官方 Docker server image 只包含這個 executable。[[S1]](#sources) [[S2]](#sources) |
| 官方 CUDA image 名/tag | `vllm/vllm-openai` | `lmsysorg/sglang` | **`ghcr.io/ggml-org/llama.cpp:server-cuda`**（CUDA 12）、**`ghcr.io/ggml-org/llama.cpp:server-cuda13`**（CUDA 13）；2026-07-01 rolling build 為 `b9853`。[[S1]](#sources) [[S3]](#sources) |
| 模型怎麼指定 | 位置參數 `<hf_tag>` | `--model-path <tag>` | **本地 GGUF**：`-m/--model <file.gguf>`；**直接拉 HF GGUF**：`-hf/--hf-repo <user>/<model>[:quant]`；可再用 `-hff/--hf-file FILE` 精確指定檔名；另有 `-mu/--model-url` 與 `-dr/--docker-repo`。[[S2]](#sources) |
| **served name** | 預設=tag | `--served-model-name <tag>` | **`-a/--alias STRING`**。`/v1/models` 的 `id` 預設是 `-m` 指向的模型路徑，官方明講可用 `--alias` 覆蓋成穩定名稱。[[S2]](#sources) |
| host / port | `--host` / `--port` | 同 | **`--host` / `--port`**。[[S2]](#sources) |
| bool 慣例 | `--flag` / `--no-flag` | 多半只 `--flag` | **多數是成對的 `--flag` / `--no-flag`**，例如 `--jinja/--no-jinja`、`--metrics`、`--cache-prompt/--no-cache-prompt`、`--cont-batching/--no-cont-batching`；部分還有短負旗標或 `on/off/auto` 三態。不要把它當成單純 `store_true`。[[S2]](#sources) |

### A 小結

- `llama.cpp` 現在已經可以像 vLLM / SGLang 一樣直接用 **HF repo 名稱** 啟動，但前提仍是下載 **GGUF**，不是直接吃 safetensors。[[S2]](#sources) [[S5]](#sources)
- 如果你們想讓 router 的 `forward_name` 穩定，**一定要在 launcher 固定塞 `--alias <model_tag>`**，不要依賴 `-m` 預設路徑。[[S2]](#sources)
- `-hf` 下載模型現在會落在 **標準 HF cache directory**。若你們只需要「給定 repo/tag 即可啟動」，不一定需要另接一套下載 library；若還要做 catalog、預抓、清理策略、或 UI 枚舉 quant，再考慮外接。[[S5]](#sources)

---

## B. 通用參數翻譯

| engine-neutral 欄位 | vLLM 旗標 | SGLang 旗標 | llama.cpp（查證結果） |
|---|---|---|---|
| `max_model_len` | `--max-model-len` | `--context-length` | **`-c/--ctx-size N`**。[[S2]](#sources) |
| `gpu_memory_utilization` | `--gpu-memory-utilization` | `--mem-fraction-static` | **無直接等價的比例旗標**。官方主軸是用 **`--n-gpu-layers`** 控制 offload 層數，再配 **`--split-mode` / `--tensor-split` / `--main-gpu` / `--device`** 控多 GPU 放置。這一欄應標註為「語意不對齊」。[[S1]](#sources) [[S2]](#sources) [[S5]](#sources) |
| `tensor_parallel_size` | `--tensor-parallel-size` | `--tp-size` | **無單一 `tp-size`**。多 GPU 是靠 **`--split-mode` / `--tensor-split` / `--main-gpu` / `--device`**。[[S2]](#sources) |
| `dtype` | `--dtype` | `--dtype` | **無通用模型權重 `dtype` 旗標**。權重型別/量化基本上由 **GGUF 檔本身**決定；但另有 KV cache dtype：`--cache-type-k`、`--cache-type-v`。[[S2]](#sources) [[S5]](#sources) |
| GPU offload 層數 | — | — | **`--n-gpu-layers`** 為官方 Docker/CUDA 範例中的主旗標。依官方語意，它是 offload 到 GPU 的層數控制；CPU+GPU 混合推理是官方主打能力。`0/部分/全部` 這三種 UX 完全合理，但 `0 == 純 CPU` 這點建議在你們目標 image 上 live probe 一次後寫死。[[S1]](#sources) [[S5]](#sources) |

### B 實作建議

- `gpu_memory_utilization` 這欄不要硬翻成某個 llama.cpp 旗標，建議：
  - schema 層標記成「**not_applicable / semantic_mismatch**」
  - 前端另外顯示 llama.cpp 專屬欄位：`n_gpu_layers`、`split_mode`、`tensor_split`
- `dtype` 也不要照搬 vLLM/SGLang UX；對 llama.cpp，**真正有意義的是 GGUF quant 選擇** 與 **KV cache dtype**。[[S2]](#sources) [[S5]](#sources)

---

## C. 健康探針

| 項目 | vLLM/SGLang(現有) | llama.cpp（查證結果 + live 實測） |
|---|---|---|
| readiness endpoint | `/health` | **`/health`**（就緒回 `200 {"status":"ok"}`），且 **`/v1/health` 也 200**。✅ live | [[S2]](#sources) |
| 啟動中的回應 | starting **503**（server 早早綁 port） | ⚠️ **與 vLLM/SGLang 不同**：llama-server **先把模型載入完才綁 port**，所以啟動/下載期間 `/health` 是 **連線被拒（curl `000`），不是 503**。503 `Loading model` 只發生在「已綁 port 後的重載 / router mode」。✅ live |

### C 結論（含 live 實測修正）

- `build_spec().probe_url` 可以直接走 **`/health`**（就緒 200）。[[S2]](#sources)
- **但 readiness 探針必須把「連線被拒」也視為「還沒好」**，不能只判 503。實測啟動日誌順序是
  `load_model → model loaded → listening on ...`（port 最後才開),這點和 vLLM/SGLang（載入中就能回 503）
  相反,launcher 註解要寫清楚,避免誤判成「容器起不來」。我們現有的輪詢「等 200」邏輯本來就相容。
- 啟動日誌摘要(build `b9853-7af4279f4`,image `server-cuda`)：

  ```text
  srv load_model:   loading model 'HuggingFaceTB/SmolLM2-360M-Instruct-GGUF:Q8_0'
  srv llama_server: model loaded
  srv llama_server: listening on http://0.0.0.0:8098   ← port 此時才開
  ```

---

## D. 指標

| 概念（正規化） | vLLM 指標 | SGLang 指標 | llama.cpp（查證結果） |
|---|---|---|---|
| 開啟 `/metrics` 的方式 | 預設就有 | `--enable-metrics` | **`--metrics`**；預設 disabled。[[S2]](#sources) |
| 指標命名前綴 | `vllm:*` | `sglang:*` | **`llamacpp:*`**。[[S2]](#sources) |
| waiting / 佇列深度 | `vllm:num_requests_waiting` | `sglang:num_queue_reqs` | **`llamacpp:requests_deferred`** 最接近。[[S2]](#sources) |
| running / 處理中 | `vllm:num_requests_running` | `sglang:num_running_reqs` | **`llamacpp:requests_processing`**。[[S2]](#sources) |
| KV cache 使用率 | `...kv_cache_usage_perc` | `token_usage` | **目前沒有對等欄位**。官方 changelog 已記錄：`/metrics` 的 KV cache tokens/cells 欄位已移除。[[S2]](#sources) [[S4]](#sources) |

### D 結論

- `metrics_llamacpp` 這個 capability **可以做**，但目前比較穩的是：
  - `waiting = llamacpp:requests_deferred`
  - `running = llamacpp:requests_processing`
- `kv` **沒有可直接正規化的現成指標**；如果 autoscaler 必須用 KV 使用率，llama.cpp group 可能需要退化成固定副本或只吃 waiting/running。[[S2]](#sources) [[S4]](#sources)

### D 額外風險

- 官方近期 issue 指出：在 router mode 且 `--models-autoload` 開啟時，`GET /metrics?model=X` 目前可能觸發 autoload；若你們未來走多模型 router mode，監控抓 metrics 時建議顯式加 `autoload=false` 或先避開。[[S7]](#sources)

---

## E. Capabilities 對標

| capability | vLLM | SGLang | llama.cpp（查證結果） |
|---|---|---|---|
| `sleep` | ✅ | ❌ | **部分成立，但不是同一語意**。llama.cpp 有 **`--sleep-idle-seconds`** 自動 idle sleep；睡眠時會卸載模型與記憶體/KV，下一個 task 自動喚醒。**沒有查到官方 `/sleep` / `/wake_up` 手動端點**。如果你們 capability 指的是「顯式控制端點」，建議標 ❌；如果指「可釋放記憶體」，可標「auto-only」。[[S2]](#sources) |
| `runtime_lora` | ✅ | ✅ | **有,但語意不同（見下 E-LoRA live 實測）**：只能對「啟動時已 `--lora` 載入」的 adapter 用 **`POST /lora-adapters`**（payload `[{id,scale}]`）調 scale / 開關,**無法透過 API 上傳一顆新 adapter**。與 vLLM/SGLang 的「動態掛新 adapter」不是同一種能力。✅ live | [[S2]](#sources) [[S8]](#sources) |
| `lora_modules` | ✅ | ✅ | **有**。啟動旗標 **`--lora <adapter.gguf>`**、**`--lora-scaled <adapter.gguf>:<scale>`**；**只吃與基座匹配的 GGUF 格式 LoRA**（safetensors 不行）。✅ live | [[S2]](#sources) [[S5]](#sources) [[S8]](#sources) |
| `kv_transfer` | ✅ | ❌ | **未查到官方等價能力**。目前看到的是單機 slot save/load、prompt cache、cache reuse，不是跨實例 KV transfer。建議標 ❌。[[S2]](#sources) |
| metrics 格式 | `metrics_vllm` | `metrics_sglang` | **可定義 `metrics_llamacpp`**，但 `kv` 維度缺失。[[S2]](#sources) [[S4]](#sources) |

### E 結論

若按最保守 capability 宣告，建議：

```text
frozenset({
  "runtime_lora",   # 但僅限已載入 adapter 的 scale / enable / disable
  "lora_modules",
  "metrics_llamacpp"
})
```

另外可考慮新增一個內部註記：

```text
"sleep_auto_only"
```

因為 llama.cpp 的睡眠能力和 vLLM 的 `/sleep`、`/wake_up` 不是同一種接口。[[S2]](#sources)

### E-LoRA. Live 實測(2026-07-01,image `server-cuda` build `b9853`)

基座 `HuggingFaceTB/SmolLM2-360M-Instruct-GGUF:Q8_0` + 現成 GGUF adapter
[`asynclee/SmolLM2-360M-Instruct-fc-cn-lora-F16-GGUF`](https://huggingface.co/asynclee/SmolLM2-360M-Instruct-fc-cn-lora-F16-GGUF)(單一 `*-f16.gguf`,對得上基座架構):

| 測試 | 指令 / 端點 | 結果 |
|---|---|---|
| 靜態掛載 | `--lora /lora/adapter.gguf` | ✅ `GET /lora-adapters` → `[{"id":0,"path":"/lora/adapter.gguf","scale":1.0,...}]` |
| runtime 調 scale(停用) | `POST /lora-adapters [{"id":0,"scale":0.0}]` | ✅ HTTP 200,`GET` 反映 `scale:0.0` |
| runtime 重新啟用 | `POST /lora-adapters [{"id":0,"scale":1.0}]` | ✅ 回到 `1.0` |
| per-request 覆寫 | chat body 帶 `"lora":[{"id":0,"scale":1.0}]` | ✅ 推理正常 |

**⚠️ runtime 語意差異(對實作最關鍵)**:

| | vLLM / SGLang | llama.cpp(實測) |
|---|---|---|
| runtime 能做什麼 | **上傳一顆啟動時不存在的新 adapter**(`POST /load_lora_adapter`,給 name+path) | **只能對啟動時已 `--lora` 載入的 adapter 調 scale / 開關** |
| 加新 adapter | ✅ 動態 | ❌ 必須在 launch 就宣告 |
| runtime payload | name + path | `[{id, scale}]` |

**落地影響**:

1. llama.cpp group 的 LoRA adapter 必須**在 config / launch 時就列好**(轉成一或多個 `--lora`),不能像
   現在 SGLang 那樣事後 `_post_lora` 掛一顆全新的。
2. 現有 `_post_lora`(依引擎選端點)對 llama.cpp 要走**不同模型**:動作是「調既有 adapter 的 scale /
   開關」,payload `[{id, scale}]`,端點 `POST /lora-adapters`(無 `/v1` 前綴);UI 的「新增 LoRA」對
   llama.cpp 應是「編輯 group → 加啟動 adapter → 重啟」,runtime 只暴露 scale/toggle。
3. adapter 必須是**與基座匹配的 GGUF**;safetensors PEFT LoRA 需先用 llama.cpp 的
   `convert_lora_to_gguf.py` 轉檔(Library 若要支援 LoRA,得處理這步)。

---

## F. OpenAI 端點覆蓋度

| 端點/能力 | vLLM/SGLang(現有可用) | llama.cpp（查證結果） |
|---|---|---|
| `/v1/chat/completions` 串流 | ✅ | **✅**。官方 README 明寫同步與 streaming 都支援。[[S2]](#sources) |
| 串流最後的 **final usage chunk** | ✅ | **✅，但要 client 主動要求 `stream_options.include_usage`**。官方 changelog 明記 usage 只在指定時才回；官方測試則要求 `[DONE]` 前最後一個 chunk 帶 `usage`、`timings`、且 `choices` 為空陣列。[[S4]](#sources) [[S6]](#sources) |
| `/v1/completions` | ✅ | **✅**。官方 README 與 changelog 都標為 OAI-compatible。[[S2]](#sources) [[S4]](#sources) |
| `/v1/embeddings` | ✅ | **✅**。官方 README 有 `POST /v1/embeddings`；但要求模型 pooling 不能是 `none`。若要 token-level/raw embedding，應改走非 OAI 的 `/embeddings`。[[S2]](#sources) |
| `/v1/models` | ✅ | **✅**。[[S2]](#sources) |
| `/tokenize` `/detokenize` | ✅ | **✅ 有端點**。`/tokenize` 回傳形狀已文件化；`/detokenize` 文件只寫了 input，未在同頁明示 response schema。這兩個是非 OAI 端點，若 router 已有既定 schema，建議 live probe 一次再鎖定 parser。[[S2]](#sources) |
| 工具調用 / function calling | ✅ | **✅**。官方 README 明寫 `/v1/chat/completions` 支援 `tools` 與 `tool_choice`；CLI 入口以 **`--jinja`** 為核心，必要時配 **`--chat-template` / `--chat-template-file`**。它不是 vLLM/SGLang 那種獨立「tool-call parser」旗標，而是 **template 驅動**。[[S2]](#sources) [[S4]](#sources) |

### F 實作重點

1. **router 送 stream 時要加**：

```json
{
  "stream": true,
  "stream_options": { "include_usage": true }
}
```

2. 計費 parser 應接受：
   - 最後一個 chunk `choices: []`
   - 同 chunk 內含 `usage`
   - llama.cpp 還會多帶 `timings`

3. 工具調用若要零修改接 router，最穩的 UI/配置做法不是「parser 下拉」，而是：
   - `jinja: on/off`
   - `chat_template` 選擇
   - 必要時 `chat_template_file`

---

## G. GPU 放置與容器需求

| 項目 | vLLM/SGLang(現有) | llama.cpp（查證結果） |
|---|---|---|
| 選卡 | `CUDA_VISIBLE_DEVICES` | ? | **官方 server 文件主推的是 `--device` / `--main-gpu` / `--tensor-split` / `--split-mode`**；Docker 文件則要求 `--gpus all`。**`CUDA_VISIBLE_DEVICES` 並未在官方 llama.cpp 文件中作為一級設定說明**，雖然底層 CUDA runtime 很可能仍會受它影響，但這點應視為推論，不要寫成已證實行為。[[S1]](#sources) [[S2]](#sources) |
| 純 CPU / 部分 offload | 不支援 | ? | **這正是 llama.cpp 的強項之一**。官方 README 明寫支援 CPU+GPU hybrid inference；Docker/CUDA 文件明確要求搭配 `--n-gpu-layers`。你們前端可以把「純 CPU / 部分 / 盡量全上 GPU」做成明確 UX。[[S1]](#sources) [[S5]](#sources) |
| 容器特殊需求 | SGLang 需 `--ipc=host`/大 shm | ? | **官方 Docker 文件沒有要求 `--ipc=host` 或大 shm**；已知需求是模型 volume 掛載、NVIDIA 場景下安裝 `nvidia-container-toolkit`，以及啟動時 `--gpus all`。HF 下載檔已遷到標準 HF cache。[[S1]](#sources) [[S5]](#sources) |

### G 結論

- `build_spec` 至少應考慮：
  - model volume / cache volume
  - 若用 `-hf`，最好把 HF cache 掛成持久化 volume
  - GPU 啟用時使用 Docker `--gpus all`
- 目前沒有找到像 SGLang 那樣必需的 shm / ipc 特殊要求。[[S1]](#sources)

---

## H. 前端 Add Model presets

| 項目 | 用途 | llama.cpp（建議） |
|---|---|---|
| 有意義的「加速參數」清單 | 填加速面板 | **高優先**：`-ngl/--n-gpu-layers`、`-c/--ctx-size`、`-b/--batch-size`、`-ub/--ubatch-size`、`-np/--parallel`、`--cont-batching/--no-cont-batching`、`--flash-attn`、`--cache-type-k`、`--cache-type-v`、`--split-mode`、`--tensor-split`、`--main-gpu`、`--device`。[[S2]](#sources) |
| 工具調用 parser 選項 | 對標 vLLM/SGLang 的 tool-call-parser 下拉 | **不建議照抄 parser 概念**。llama.cpp 官方入口是 `--jinja` + `--chat-template` / `--chat-template-file`；工具調用更像「模板相容性」問題，不是單獨 parser 枚舉。[[S2]](#sources) |
| GGUF 量化選擇 | 使用者怎麼選 Q4_K_M 等 | **如果走 `-hf repo:quant`，quant 就直接是 repo suffix**，例如 `ggml-org/GLM-4.7-Flash-GGUF:Q4_K_M`；若再指定 `-hff`，則由具體檔名覆蓋 quant。[[S2]](#sources) |

### H UX 建議

- llama.cpp 的前端 UX 比較像：
  - **模型來源**：本地檔 / HF repo
  - **量化**：GGUF quant 或具體檔名
  - **硬體放置**：CPU only / partial offload / multi-GPU split
  - **KV/吞吐調校**：`batch`、`ubatch`、`cache-type-k/v`
  - **聊天/工具調用相容性**：`jinja` + `chat_template`

而不是 vLLM/SGLang 那種：

- `dtype`
- `gpu_memory_utilization`
- `tensor_parallel_size`

這三個中至少前兩個在 llama.cpp 都不夠貼切。[[S2]](#sources) [[S5]](#sources)

---

## 可直接回填到實作的結論

### 1. `build_llamacpp_cli_args`

最小可行映射建議：

```text
model:
  local file -> -m <path.gguf>
  hf repo    -> -hf <user/repo:quant> [-hff <file>]

served name:
  always pass --alias <forward_name>

common:
  --host <host>
  --port <port>
  -c <max_model_len>

llamacpp-specific:
  --n-gpu-layers <N>
  --split-mode <none|layer|row>
  --tensor-split <csv>
  --main-gpu <idx>
  --device <csv>
  -b <batch>
  -ub <ubatch>
  -np <parallel>
  --flash-attn
  --cache-type-k <type>
  --cache-type-v <type>
  --metrics
```

### 2. `probe_url`

直接用：

```text
/health
```

理由：loading = 503，ready = 200。[[S2]](#sources)

### 3. `capabilities`

建議先保守宣告：

```text
runtime_lora
lora_modules
metrics_llamacpp
```

不要把以下當成與 vLLM 等價能力：

- `sleep`：只有 auto idle sleep，沒有手動 sleep/wake 端點
- `kv_transfer`：未查到官方能力
- `runtime_lora`：**只能調既有 adapter 的 scale / 開關(`POST /lora-adapters [{id,scale}]`),不能像
  vLLM/SGLang 那樣動態上傳新 adapter**。adapter 必須 launch 時就 `--lora` 宣告(見 E-LoRA live 實測)。

### 4. Router / 計費相容

串流請務必加：

```json
"stream_options": {"include_usage": true}
```

否則官方 changelog 明示 usage 不保證出現。[[S4]](#sources)

### 5. Autoscaler / Grafana

- 可以接：
  - `llamacpp:requests_deferred`
  - `llamacpp:requests_processing`
- 目前**不要期待**現成 `kv cache usage ratio`。[[S2]](#sources) [[S4]](#sources)

---

## 仍建議在你們目標 image 上做一次 live probe 的項目

這些不是因為官方沒支援，而是因為你們要做的是 **「launcher + router 零修改接入」**，最好在實際 image 上再跑一次：

1. `server-cuda-b9853` / `server-cuda13-b9853` 上，`stream_options.include_usage=true` 時的最後一個 chunk 實際 JSON。
2. `/tokenize`、`/detokenize` 的 response shape 是否和你們 router 既有 parser 完全相容。
3. `-ngl 0` 在你們容器包裝下是否完全等價於 CPU only，或是否還需要顯式 `--device none`。
4. 多 GPU 時你們要採用的 UX 是 `--device` 還是 `--tensor-split` 為主。
5. `GET /metrics?model=...` 在 router mode + autoload 下是否會影響你們監控策略。[[S7]](#sources)

---

## I. 建議拿來測 `llama.cpp` 的小模型

這一節的目標不是做模型評測，而是幫你選出 **適合驗證 launcher / router / OpenAI 相容端點** 的小型 GGUF 模型。優先原則是：

- 有現成 GGUF repo
- 官方或模型作者頁面直接給 `llama.cpp` / `llama serve -hf` 用法
- 體積小，適合做 smoke test / CI / 單機驗證

### I-1. 聊天模型

| 模型 | 規模 | 建議用途 | 建議 quant | 啟動範例 | 備註 |
|---|---|---|---|---|---|
| [HuggingFaceTB/SmolLM2-360M-Instruct-GGUF](https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct-GGUF) | 0.4B | **最小聊天 smoke test** | `Q8_0` | `llama-server -hf HuggingFaceTB/SmolLM2-360M-Instruct-GGUF:Q8_0` | 官方頁面直接給 `llama.cpp` 指令，適合先驗證 `spawn -> /health -> /v1/chat/completions`。[[S9]](#sources) |
| [Qwen/Qwen2.5-0.5B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF) | 0.49B | **中文 / 多語 smoke test** | `Q4_K_M` | `llama-server -hf Qwen/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M` | 官方頁面列出多種 quant，中文能力比 SmolLM2 更適合拿來驗 router 中文 prompt。[[S10]](#sources) |
| [Qwen/Qwen3-0.6B-GGUF](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF) | 0.6B | **新一代 Qwen 模板 / 工具調用相容性** | `Q8_0` | `llama-server -hf Qwen/Qwen3-0.6B-GGUF:Q8_0` | 若你們要驗證 `jinja` / chat template / function calling 行為，這顆值得保留一組。[[S11]](#sources) |
| [HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF](https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF) | 1.7B | **第二階段可用性測試** | `Q4_K_M` | `llama-server -hf HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF:Q4_K_M` | 如果 360M 太弱，1.7B 很適合做「能不能實際回答」的回歸測試。[[S12]](#sources) |
| [ibm-granite/granite-3.3-2b-instruct-GGUF](https://huggingface.co/ibm-granite/granite-3.3-2b-instruct-GGUF) | 2B~3B class | **較正式的 router 回歸** | `Q4_K_M` | `llama-server -hf ibm-granite/granite-3.3-2b-instruct-GGUF:Q4_K_M` | 官方頁面直接提供 `llama serve -hf` 範例，適合驗證較完整的 chat 路徑。[[S13]](#sources) |

### I-2. Embedding 模型

| 模型 | 規模 | 建議用途 | 建議 quant | 啟動範例 | 備註 |
|---|---|---|---|---|---|
| [Qwen/Qwen3-Embedding-0.6B-GGUF](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF) | 0.6B | **`/v1/embeddings` 主測試模型** | `Q8_0` | `llama-server -hf Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0` | 官方頁面直接給 `llama.cpp` 啟動方式，適合測你們的 embedding path。[[S14]](#sources) |
| [ggml-org/bge-small-en-v1.5-Q8_0-GGUF](https://huggingface.co/ggml-org/bge-small-en-v1.5-Q8_0-GGUF) | 33.2M | **超輕量 embedding smoke test** | `Q8_0` | `llama-server --hf-repo ggml-org/bge-small-en-v1.5-Q8_0-GGUF --hf-file bge-small-en-v1.5-q8_0.gguf -c 2048` | 只有約 36.7 MB，非常適合驗證 `/v1/embeddings` 或非 OAI `/embeddings` 的最小路徑。[[S15]](#sources) |

### I-3. 我建議的最小測試矩陣

如果目標是最少模型數量下，覆蓋你們這次要接的主要能力，我建議保留這 4 顆：

1. `HuggingFaceTB/SmolLM2-360M-Instruct-GGUF:Q8_0`
2. `Qwen/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M`
3. `HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF:Q4_K_M`
4. `Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0`

覆蓋理由：

- `SmolLM2-360M`：最便宜的 chat smoke test
- `Qwen2.5-0.5B`：中文 / 多語 prompt 驗證
- `SmolLM2-1.7B`：較像真實聊天模型的回歸
- `Qwen3-Embedding-0.6B`：`/v1/embeddings` 路徑

### I-4. 若只想先跑 2 顆

若你現在只是要先驗證 `LlamacppLauncher` 能不能接起來，我會先跑：

1. `Qwen/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M`
2. `Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0`

這兩顆的好處是：

- 都能直接 `-hf` 拉
- 都是官方 repo
- 一顆測 chat，一顆測 embeddings
- 對你們目前設計文件裡最關鍵的 OpenAI 相容驗證最有代表性

---

## Sources

- <a id="sources"></a>[S1] 官方 Docker 文件: [docs/docker.md](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md)
- [S2] 官方 `llama-server` README: [tools/server/README.md](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [S3] 官方 GitHub Container Registry 版本頁: [Package versions of llama.cpp](https://github.com/ggml-org/llama.cpp/pkgs/container/llama.cpp/versions?filters%5Bversion_type%5D=tagged)
- [S4] 官方 `llama-server` REST API changelog: [Issue #9291](https://github.com/ggml-org/llama.cpp/issues/9291)
- [S5] 官方 repo README / hot topics / quick start: [README](https://github.com/ggml-org/llama.cpp/blob/master/README.md)
- [S6] 官方 server 測試，stream final usage chunk 行為: [tools/server/tests/utils.py](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/tests/utils.py)
- [S7] 官方 issue，router mode `GET /metrics` + autoload 風險: [Issue #23096](https://github.com/ggml-org/llama.cpp/issues/23096)
- [S8] 官方 LoRA hot reload 說明 / 討論: [Discussion #10123](https://github.com/ggml-org/llama.cpp/discussions/10123)
- [S9] Hugging FaceTB 官方 GGUF 模型頁: [HuggingFaceTB/SmolLM2-360M-Instruct-GGUF](https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct-GGUF)
- [S10] Qwen 官方 GGUF 模型頁: [Qwen/Qwen2.5-0.5B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF)
- [S11] Qwen 官方 GGUF 模型頁: [Qwen/Qwen3-0.6B-GGUF](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF)
- [S12] Hugging FaceTB 官方 GGUF 模型頁: [HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF](https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF)
- [S13] IBM Granite 官方 GGUF 模型頁: [ibm-granite/granite-3.3-2b-instruct-GGUF](https://huggingface.co/ibm-granite/granite-3.3-2b-instruct-GGUF)
- [S14] Qwen 官方 GGUF Embedding 模型頁: [Qwen/Qwen3-Embedding-0.6B-GGUF](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF)
- [S15] ggml-org GGUF Embedding 模型頁: [ggml-org/bge-small-en-v1.5-Q8_0-GGUF](https://huggingface.co/ggml-org/bge-small-en-v1.5-Q8_0-GGUF)
