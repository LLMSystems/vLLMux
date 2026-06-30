# SGLang 最新 Server 啟動整理

更新日期: 2026-06-30  
整理基準:

- 最新穩定版: `v0.5.14`
- GitHub latest release 發布時間: `2026-06-26`
- 官方建議 CLI: `sglang serve`
- 官方 Docker / compose 範例目前仍多使用 `python3 -m sglang.launch_server`

---

## 1. 快速結論

如果你是要做 launcher / router / autoscaler 對接，先看這段就夠:

- 最新版正式推薦入口是 `sglang serve --model-path ...`。
- `python -m sglang.launch_server ...` 仍然支援，但程式本身已明確警告這是舊但仍相容的入口。
- 官方 Docker 與 `docker/compose.yaml` 目前仍直接用 `python3 -m sglang.launch_server`，所以容器內沿用這種寫法也沒問題。
- 指定模型旗標是 `--model-path`，也支援 alias `--model`；可接 Hugging Face repo ID，也可接本地路徑。
- `host` / `port` 旗標就是 `--host` / `--port`。
- Tensor parallel 的 canonical 旗標是 `--tp-size`，alias 是 `--tensor-parallel-size`。
- Data parallel 的 canonical 旗標是 `--dp-size`，alias 是 `--data-parallel-size`。
- 官方文件範例還常寫 `--tp` / `--dp`，但最新版 `server_args.py` 裡我沒有看到它們被明確註冊成 CLI alias。若你要寫 arg builder，建議以 `--tp-size` / `--dp-size` 為準，不要假設 `--tp` / `--dp` 一定存在。
- GPU 指定同時牽涉 `CUDA_VISIBLE_DEVICES` 與 SGLang 自己的 `--base-gpu-id` / `--gpu-id-step`。SGLang 內部也會依可見 GPU 重新設 `CUDA_VISIBLE_DEVICES`。
- `vLLM --max-model-len` 對應 SGLang 的 `--context-length`。
- `vLLM --gpu-memory-utilization` 沒有完全同名對應；最接近的是 `--mem-fraction-static`，但語意是「靜態配置給權重 + KV cache pool 的顯存比例」，不是逐字等價。
- Bool 旗標慣例是 `store_true`，也就是通常只有 `--flag`，沒有通用的 `--no-flag` 自動對偶。
- 但 SGLang 本身有很多「負向命名的正向旗標」，例如 `--disable-radix-cache`、`--skip-server-warmup`。所以不能直接沿用 vLLM 那種 `--no-xxx` builder 邏輯。
- 旗標命名以 `kebab-case` 為主，例如 `--model-path`、`--context-length`、`--mem-fraction-static`。
- OpenAI 相容端點有 `/v1/chat/completions`、`/v1/completions`、`/v1/models`、`/v1/tokenize`、`/v1/detokenize`，streaming 支援。
- `stream_options.include_usage=true` 時，SGLang 會在串流最後補一個 `choices=[]` 的 final usage chunk。
- 若 `stream_options.continuous_usage_stats=true`，則每個 chunk 都可能帶 `usage`；若 `false`，通常只有最後那個 usage chunk。
- readiness probe 最準的是 `/health` 或 `/health_generate`。最新版預設下 `/health` 就會做真正的 1-token 生成健康檢查，不只是單純 200。
- Prometheus metrics 端點是 `/metrics`，需啟動 `--enable-metrics`。
- 與 autoscaler 最相關的 queue / running 指標已有內建:
  - `sglang:num_running_reqs`
  - `sglang:num_queue_reqs`
  - `sglang:num_grammar_queue_reqs`
  - `sglang:num_used_tokens`
  - `sglang:token_usage`
  - `sglang:gen_throughput`
- 若你不想 parse Prometheus，還可用 `/v1/loads` 直接拿 per-DP rank 的 JSON load 資訊。
- 沒查到 vLLM 那種 `/sleep` / `/wake_up` 類型、可釋放 GPU 顯存待命的 LLM server API。SGLang 目前只有 `--sleep-on-idle`，它是降低 CPU idle 使用率，不是釋放 VRAM。
- Runtime LoRA 熱掛載/卸載是有的: `/load_lora_adapter`、`/unload_lora_adapter`。

---

## 2. 版本與最新狀態

### 2.1 最新穩定版

- GitHub latest release: [`v0.5.14`](https://github.com/sgl-project/sglang/releases/tag/v0.5.14)
- 發布時間: `2026-06-26`

### 2.2 文件與 release 的一個小落差

官方安裝頁的 source 安裝範例目前還能看到舊 tag 範例，但 latest release 已是 `v0.5.14`。  
如果你要 pin 版本，應優先跟著 release / Docker tag 走，不要照舊文件裡的舊 tag。

---

## 3. 正式啟動方式

### 3.1 最新推薦入口

最新版 Python package 有 console script:

```bash
sglang serve --model-path <model> [options]
```

這是目前官方推薦入口。

原因:

- `python/pyproject.toml` 有註冊 `sglang = "sglang.cli.main:main"`
- `python/sglang/cli/main.py` 裡有 `serve` subcommand
- `python/sglang/launch_server.py` 在 `__main__` 中會直接 warning:
  - `python -m sglang.launch_server` still supported
  - `sglang serve` is the recommended entrypoint

### 3.2 舊入口是否還能用

可以，仍支援:

```bash
python -m sglang.launch_server --model-path <model> [options]
```

而且目前官方 Docker 範例與 `docker/compose.yaml` 也還是這樣啟動。

### 3.3 我該選哪個

建議:

- 你自己的 host-side launcher: 用 `sglang serve`
- 你要對齊官方 Docker / compose / 既有腳本: 用 `python -m sglang.launch_server`

兩者在 LLM server 路徑最後都會進到同一套 server args 與 `run_server(...)` 邏輯。

---

## 4. Docker 最新整理

### 4.1 官方 image

官方 Docker Hub:

- [`lmsysorg/sglang`](https://hub.docker.com/r/lmsysorg/sglang/tags)

官方文件說明:

- 預設是 CUDA 13 環境
- 若要 CUDA 12 系列，使用 `-cu12` 或 `-cu129` 後綴
- 有 nightly tags
- production 建議可考慮 `runtime` 變體

### 4.2 目前可確認到的新版 tag

從 2026-06-30 查到的 tags 可見至少包含:

- `latest`
- `v0.5.14`
- `latest-cu129`
- `v0.5.14-cu129`
- `latest-cu130`
- `v0.5.14-cu130`
- `latest-cu129-runtime`
- `v0.5.14-cu129-runtime`
- 多組 `nightly-*`

說明:

- `latest` 目前對應到 `v0.5.14`
- docs 明確提到 `latest-runtime`
- Docker Hub tag 頁可看到 runtime / nightly / CUDA suffix 系列

### 4.3 官方 `docker run` 範例

一般版:

```bash
docker run --gpus all \
  --shm-size 32g \
  -p 30000:30000 \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  --env "HF_TOKEN=<secret>" \
  --ipc=host \
  lmsysorg/sglang:latest \
  python3 -m sglang.launch_server \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 30000
```

runtime 版:

```bash
docker run --gpus all \
  --shm-size 32g \
  -p 30000:30000 \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  --env "HF_TOKEN=<secret>" \
  --ipc=host \
  lmsysorg/sglang:latest-runtime \
  python3 -m sglang.launch_server \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 30000
```

### 4.4 Docker 啟動注意事項

- 建議帶 `--ipc=host` 或足夠大的 `--shm-size`
- 官方文件特別提醒 Docker / Kubernetes 要注意 shared memory
- 通常會掛載 Hugging Face cache:

```bash
-v ~/.cache/huggingface:/root/.cache/huggingface
```

- 若使用 gated repo，通常要帶:

```bash
--env "HF_TOKEN=<secret>"
```

### 4.5 官方 docker compose

官方 `docker/compose.yaml` 目前重點如下:

- `image: lmsysorg/sglang:latest`
- `entrypoint: python3 -m sglang.launch_server`
- `command: --model-path ... --host 0.0.0.0 --port 30000`
- `network_mode: host`
- `privileged: true`
- `ipc: host`
- `healthcheck: curl -f http://localhost:30000/health || exit 1`
- GPU reservation 使用 NVIDIA device reservation

如果你要做 K8s / compose 對接，官方健康檢查目前就是打 `/health`。

---

## 5. 核心啟動指令範本

### 5.1 單卡

```bash
sglang serve \
  --model-path meta-llama/Meta-Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 30000
```

### 5.2 指定 dtype / context / 顯存比例

```bash
sglang serve \
  --model-path meta-llama/Meta-Llama-3.1-8B-Instruct \
  --dtype bfloat16 \
  --context-length 32768 \
  --mem-fraction-static 0.8 \
  --host 0.0.0.0 \
  --port 30000
```

### 5.3 Tensor parallel

建議用 canonical flag:

```bash
sglang serve \
  --model-path meta-llama/Meta-Llama-3.1-8B-Instruct \
  --tp-size 2
```

也可用 alias:

```bash
sglang serve \
  --model-path meta-llama/Meta-Llama-3.1-8B-Instruct \
  --tensor-parallel-size 2
```

### 5.4 Data parallel

```bash
sglang serve \
  --model-path meta-llama/Meta-Llama-3.1-8B-Instruct \
  --dp-size 2
```

或:

```bash
sglang serve \
  --model-path meta-llama/Meta-Llama-3.1-8B-Instruct \
  --data-parallel-size 2
```

### 5.5 多節點 TP

```bash
sglang serve \
  --model-path meta-llama/Meta-Llama-3.1-8B-Instruct \
  --tp-size 4 \
  --dist-init-addr sgl-dev-0:50000 \
  --nnodes 2 \
  --node-rank 0
```

第二台把 `--node-rank` 改成 `1`。

---

## 6. 參數命名與 vLLM 對照

## 6.1 高優先級映射表

| 需求 | SGLang 最新旗標 | 備註 |
| --- | --- | --- |
| 模型路徑 / HF repo | `--model-path` | alias: `--model` |
| host | `--host` | 預設 `127.0.0.1` |
| port | `--port` | 預設 `30000` |
| dtype | `--dtype` | `auto/half/float16/bfloat16/float/float32` |
| 最大長度 | `--context-length` | 對應 vLLM `--max-model-len` |
| 顯存比例 | `--mem-fraction-static` | 接近 vLLM `--gpu-memory-utilization`，但不是完全同義 |
| Tensor parallel | `--tp-size` | alias: `--tensor-parallel-size` |
| Data parallel | `--dp-size` | alias: `--data-parallel-size` |
| NCCL init addr | `--dist-init-addr` | alias: `--nccl-init-addr` |
| 多節點數 | `--nnodes` |  |
| 節點 rank | `--node-rank` |  |
| API key | `--api-key` | OpenAI compatible server 也會使用 |
| admin API key | `--admin-api-key` | 管理端點保護 |
| OpenAI `/v1/models` model name | `--served-model-name` | 不設時預設為 `model_path` |
| metrics | `--enable-metrics` | Prometheus `/metrics` |
| MFU metrics | `--enable-mfu-metrics` | 需與 `--enable-metrics` 一起用 |

## 6.2 關於 `--tp` / `--dp`

官方文件的 launch examples 仍大量寫:

- `--tp 2`
- `--dp 2`

但以最新版 `python/sglang/srt/server_args.py` 來看:

- `tp_size` 自動生成的是 `--tp-size`
- alias 是 `--tensor-parallel-size`
- `dp_size` 自動生成的是 `--dp-size`
- alias 是 `--data-parallel-size`

我沒有在最新版 parser 定義裡看到 `--tp` / `--dp` 被明確註冊成正式 alias。  
因此如果你正在做 launcher 的 arg builder，建議:

- 寫出 `--tp-size`
- 寫出 `--dp-size`
- 不要把 `--tp` / `--dp` 當成唯一可信介面

## 6.3 Bool 旗標慣例

SGLang 的 dataclass CLI 產生器對 `bool` 使用的是 `store_true`。

這代表:

- 一般情況是 `--flag` 表示設成 `true`
- 沒有通用的 `--no-flag`
- 不能直接套用 vLLM 那種 `BooleanOptionalAction` / `--no-xxx` builder

但要注意:

- 很多 SGLang 旗標本身名字就是負向，例如 `--disable-radix-cache`
- 這不是 `--no-radix-cache` 的自動對偶，而是獨立存在的旗標名

實務建議:

- 對 SGLang 寫專屬 bool builder
- 不要假設所有 bool 都能從 `--foo` 推導 `--no-foo`

## 6.4 命名風格

CLI 旗標是 `kebab-case`:

- `--model-path`
- `--context-length`
- `--mem-fraction-static`
- `--stream-response-default-include-usage`

對應 dataclass 欄位通常是 snake_case:

- `model_path`
- `context_length`
- `mem_fraction_static`

YAML config 檔也建議使用 kebab-case key。

---

## 7. GPU 指定方式

SGLang 不是只有單純吃 `CUDA_VISIBLE_DEVICES`。

最新版可以確認到兩層機制:

### 7.1 外部環境變數

PyTorch / CUDA 層面仍然會吃:

```bash
CUDA_VISIBLE_DEVICES=0,1
```

而 SGLang source 也有明確處理 `CUDA_VISIBLE_DEVICES` 與 logical-to-physical device 映射。

### 7.2 SGLang 自己的 GPU 選擇旗標

- `--base-gpu-id`
- `--gpu-id-step`

用途:

- `--base-gpu-id 2` 可從 GPU 2 開始選
- `--gpu-id-step 2` 可選 0,2,4 這類間隔 GPU

### 7.3 對 launcher 的建議

如果你有既有 vLLM launcher:

- 最穩妥方式: 先用 `CUDA_VISIBLE_DEVICES` 限縮可見卡
- 若還要在同機多實例切片，再配合 `--base-gpu-id` / `--gpu-id-step`

---

## 8. OpenAI 相容性

## 8.1 已確認存在的端點

最新版 LLM server 有:

- `/v1/chat/completions`
- `/v1/completions`
- `/v1/embeddings`
- `/v1/models`
- `/v1/models/{model}`
- `/v1/tokenize`
- `/v1/detokenize`
- `/v1/responses`
- `/v1/audio/transcriptions`
- `/v1/realtime`

也保留部分 legacy / short routes:

- `/tokenize`
- `/detokenize`

## 8.2 Streaming 支援

支援。

`/v1/chat/completions` 和 `/v1/completions` 都有完整 stream 路徑。

## 8.3 `stream_options.include_usage`

這點對 router 計費很重要，結論是:

- 有支援 `stream_options.include_usage`
- 會在串流最後補一個 usage chunk
- 這個 chunk 的 `choices` 會是空陣列

chat stream 實作可看到:

- 最後會額外組一個 `ChatCompletionStreamResponse(... choices=[], usage=usage)`

completion stream 也同樣:

- 最後會額外組一個 `CompletionStreamResponse(... choices=[], usage=usage)`

所以如果你的 router 是靠 final usage chunk 計費，SGLang 這條路是可接的。

## 8.4 `continuous_usage_stats`

最新版還多了一個能力:

```json
"stream_options": {
  "include_usage": true,
  "continuous_usage_stats": true
}
```

行為:

- `continuous_usage_stats=true`: 每個串流 chunk 都可能附 `usage`
- `continuous_usage_stats=false`: 通常只會有最後那個 final usage chunk

另外還有 server-level 預設旗標:

- `--stream-response-default-include-usage`

用途:

- 就算 client 沒帶 `stream_options.include_usage`，server 也可預設在 streaming response 裡帶 usage

## 8.5 `/v1/models` 回傳 model name 格式

預設:

- `served_model_name == model_path`

如果你有帶:

```bash
--served-model-name my-router-name
```

則 `/v1/models` 會回這個名字。

而 `/v1/models` 會列出:

- base model
- 已載入的 LoRA adapters

base model `ModelCard` 會包含:

- `id`
- `root`
- `max_model_len`

LoRA model card 會附帶:

- `parent` 指向 base model

## 8.6 `/tokenize` / `/detokenize`

有。

同時提供:

- `/v1/tokenize`
- `/tokenize`
- `/v1/detokenize`
- `/detokenize`

如果你的 router 有 tokenizer sidecar 或 prompt 預處理需求，這點可直接接。

---

## 9. 健康檢查與 readiness

## 9.1 端點總覽

最新版可確認到:

- `/health`
- `/health_generate`
- `/model_info`
- `/get_model_info` (deprecated)
- `/server_info`
- `/get_server_info` (deprecated)
- `/v1/loads`
- `/ping` (SageMaker health)

## 9.2 `/health` 與 `/health_generate`

兩者目前走同一個 handler。

而且這個 handler不是單純回 200，它的邏輯是:

- 若 server 正在 `Starting`，回 `503`
- 否則送一個特殊 request 做 1-token generate / embedding 檢查
- 只要在 timeout 內收到 scheduler / detokenizer 回應，就算健康

但有一個例外:

- 若環境變數 `SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION=false`
- 且你打的是 `/health`
- 那 `/health` 會直接回 `200`
- 此時 `/health_generate` 才是「真的做生成檢查」的 probe

最新版預設值是:

```text
SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION = true
```

所以預設情況下:

- `/health` 就是最準的 readiness probe

## 9.3 `/model_info` 能不能拿來當 ready

可以當「HTTP server 起來了」的訊號，但不是最準的「可生成服務」探針。

原因:

- server 內建 warmup 邏輯會先輪詢 `/model_info`
- 確認能取到 model info 後，才再送 warmup generate / encode request

也就是說:

- `/model_info` 比較像 control plane / metadata ready
- `/health` 或 `/health_generate` 比較像 data plane ready

## 9.4 建議 probe 策略

如果你是做 orchestrator / reconciler:

- liveness:
  - 可用 `/model_info`
  - 或直接沿用官方 `/health`
- readiness:
  - 優先用 `/health`
  - 若你刻意把 `SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION=false`，則改用 `/health_generate`

## 9.5 從啟動到 ready 的典型耗時

官方沒有給單一固定值，因為差異非常大，取決於:

- 模型大小
- 權重是否已快取
- 是否首次下載
- 是否會觸發 kernel JIT / warmup
- TP / DP / 多節點設定

但從最新版程式行為可推得:

- 啟動 warmup 會先最多輪詢 `/model_info` 約 `120s`
- `/health` 單次健康檢查 timeout 預設是 `20s`

實務上可這樣抓量級:

- 已快取的小模型 / 7B 級: 常見是數十秒
- 70B 級或首次拉權重 / 首次 JIT: 常見是數分鐘
- 多節點 / 大量 kernel 預熱: 可能更久

這段是依程式邏輯與部署經驗推論，不是官方 SLA。

---

## 10. LoRA 能力

## 10.1 啟動時靜態掛載

啟動旗標:

- `--enable-lora`
- `--lora-paths`
- `--max-lora-rank`
- `--lora-target-modules`
- `--max-loras-per-batch`
- `--max-loaded-loras`
- `--lora-eviction-policy`
- `--enable-lora-overlap-loading`

`--lora-paths` 支援:

- `<PATH>`
- `<NAME>=<PATH>`
- JSON 物件格式

## 10.2 Runtime 動態載入 / 卸載

最新版 LLM server 有:

- `POST /load_lora_adapter`
- `POST /load_lora_adapter_from_tensors`
- `POST /unload_lora_adapter`

官方文件與測試都顯示可以在 server 執行中熱掛載。

### 10.2.1 載入範例

```json
POST /load_lora_adapter
{
  "lora_name": "lora0",
  "lora_path": "algoprog/fact-generation-llama-3.1-8b-instruct-lora"
}
```

### 10.2.2 卸載範例

```json
POST /unload_lora_adapter
{
  "lora_name": "lora0"
}
```

### 10.2.3 對 capability 設計的結論

- `runtime_lora`: 有
- `lora_modules`: 有，且建議啟動時顯式指定 `--max-lora-rank` 與 `--lora-target-modules`

如果不顯式指定，SGLang 可能從初始 `--lora-paths` 推論，之後動態載入的 adapter 就要遵守相容形狀限制。

---

## 11. Sleep / 待命能力

我沒有在最新版 LLM server source 裡查到 vLLM 類似的:

- `/sleep`
- `/wake_up`
- `--enable-sleep-mode`

可確認到的只有:

- `--sleep-on-idle`

但這個旗標的說明是:

- `Reduce CPU usage when sglang is idle`

也就是:

- 它是 idle 時降低 CPU 使用率
- 不是釋放 GPU VRAM 的 sleep mode
- 也不是帶 API 的可喚醒待命機制

### 對 capability 設計的結論

- vLLM 式 `sleep capability`: 目前看起來沒有對等能力
- autoscaler 若要做 warm standby，不能假設有 `/sleep` / `/wake_up`

---

## 12. Metrics / Autoscaler 訊號

## 12.1 啟用方式

啟動時加:

```bash
--enable-metrics
```

如需 MFU 類估算指標，再加:

```bash
--enable-mfu-metrics
```

## 12.2 Prometheus 端點

端點:

```text
/metrics
```

官方 docs 與測試都以 `http://localhost:30000/metrics` 為例。

## 12.3 與 autoscaler 最相關的核心指標

最新版 source / docs / tests 可確認至少有:

- `sglang:num_running_reqs`
- `sglang:num_queue_reqs`
- `sglang:num_grammar_queue_reqs`
- `sglang:num_used_tokens`
- `sglang:token_usage`
- `sglang:gen_throughput`
- `sglang:cache_hit_rate`
- `sglang:prompt_tokens_total`
- `sglang:generation_tokens_total`
- `sglang:cached_tokens_total`
- `sglang:num_requests_total`
- `sglang:time_to_first_token_seconds`
- `sglang:inter_token_latency_seconds`
- `sglang:e2e_request_latency_seconds`
- `sglang:http_requests_active`

### 對你最關鍵的幾個

如果你要做擴縮:

- 等待中請求數 / queue depth:
  - `sglang:num_queue_reqs`
  - 另外還有 grammar queue:
    - `sglang:num_grammar_queue_reqs`
- 執行中請求數:
  - `sglang:num_running_reqs`
- 顯存 / token pool 壓力:
  - `sglang:num_used_tokens`
  - `sglang:token_usage`
- 吞吐:
  - `sglang:gen_throughput`

## 12.4 `/v1/loads` 可當另一條 autoscaler 訊號來源

最新版另有:

```text
GET /v1/loads
```

這個端點可以回 JSON，也可 `format=prometheus`。

它的 per-DP rank 欄位包含:

- `dp_rank`
- `timestamp`
- `num_running_reqs`
- `num_waiting_reqs`
- `num_waiting_uncached_tokens`
- `num_used_tokens`
- `num_total_tokens`
- `max_total_num_tokens`
- `token_usage`
- `gen_throughput`
- `cache_hit_rate`
- `utilization`
- `max_running_requests`

如果你不想自己 scrape `/metrics` 再 parse family/sample，`/v1/loads` 會更像 control-plane 友善 API。

### 建議

對 autoscaler:

- 若你原本已有 Prometheus pipeline，優先吃 `/metrics`
- 若你要在 controller 內即時拉取、且想要結構化 JSON，考慮用 `/v1/loads`

---

## 13. 給 SglangLauncher / Router 的實作建議

## 13.1 啟動命令

建議分兩層:

- host / bare-metal launcher:
  - `sglang serve`
- Docker 內部 entrypoint:
  - `python3 -m sglang.launch_server`

原因是這樣最貼近最新版官方定位與官方容器實作。

## 13.2 Arg builder

建議你把 SGLang 視為獨立 schema，不要直接重用 vLLM schema。

最低限度至少要分開處理:

- `--context-length` vs vLLM `--max-model-len`
- `--mem-fraction-static` vs vLLM `--gpu-memory-utilization`
- bool flag 不支援通用 `--no-foo`
- canonical parallel flags 建議用 `--tp-size` / `--dp-size`

## 13.3 Probe URL

建議:

- 預設 readiness: `/health`
- 若環境有把 `SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION=false`:
  - readiness 改用 `/health_generate`
- 若你還想做 cheap liveness:
  - `/model_info`

## 13.4 OpenAI router 零修改風險

大致可接，但有幾點要注意:

- `/v1/chat/completions` 與 `/v1/completions`: 有
- streaming: 有
- final usage chunk: 有
- `/v1/models`: 有
- `/tokenize` / `/detokenize`: 有
- model name 預設等於 `model_path`，若你要 forward_name 穩定，建議顯式傳 `--served-model-name`

## 13.5 Sleep capability

目前不要宣稱有 vLLM 對等 sleep/wake 能力。

## 13.6 Runtime LoRA capability

可以宣稱有，但前提是:

- server 要用 `--enable-lora`
- 最好同時設 `--max-lora-rank`
- 最好同時設 `--lora-target-modules`

## 13.7 Autoscaler metrics parser

你需要新增 SGLang-specific parser，至少抓:

- `sglang:num_running_reqs`
- `sglang:num_queue_reqs`
- `sglang:num_grammar_queue_reqs`
- `sglang:num_used_tokens`
- `sglang:token_usage`
- `sglang:gen_throughput`

不要直接用 vLLM 指標名去猜。

---

## 14. 參考來源

官方文件 / 官方原始碼 / 官方發布頁:

- SGLang 安裝與 Docker 文件: [docs.sglang.io/docs/get-started/install](https://docs.sglang.io/docs/get-started/install)
- SGLang server arguments: [docs.sglang.io/docs/advanced_features/server_arguments.html](https://docs.sglang.io/docs/advanced_features/server_arguments.html)
- SGLang production metrics: [docs.sglang.io/docs/references/production_metrics.html](https://docs.sglang.io/docs/references/production_metrics.html)
- GitHub latest release `v0.5.14`: [github.com/sgl-project/sglang/releases/tag/v0.5.14](https://github.com/sgl-project/sglang/releases/tag/v0.5.14)
- Docker Hub tags: [hub.docker.com/r/lmsysorg/sglang/tags](https://hub.docker.com/r/lmsysorg/sglang/tags)
- `python/pyproject.toml`: script entrypoints (`sglang`)
- `python/sglang/cli/main.py`: `serve` subcommand
- `python/sglang/cli/serve.py`: `sglang serve` dispatch logic
- `python/sglang/launch_server.py`: 推薦改用 `sglang serve` 的 warning
- `python/sglang/srt/server_args.py`: 最新 CLI schema
- `python/sglang/srt/entrypoints/http_server.py`: health / OpenAI routes / LoRA routes
- `python/sglang/srt/entrypoints/v1_loads.py`: `/v1/loads`
- `python/sglang/srt/entrypoints/openai/serving_chat.py`
- `python/sglang/srt/entrypoints/openai/serving_completions.py`
- `python/sglang/srt/observability/metrics_collector.py`
- `docs_new/docs/advanced_features/lora.mdx`
- 官方 `docker/compose.yaml`

