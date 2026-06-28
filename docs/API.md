# API 文檔

本專案由三個服務組成，各自有獨立的 HTTP API：

| 服務 | 預設位址 | 角色 |
|------|----------|------|
| **Dashboard Backend** | `http://localhost:5000` | 模型生命週期管理（啟動/停止/狀態）、系統資源、設定查詢 |
| **LLM Router Server** | `http://localhost:8887` | OpenAI 相容路由與負載平衡（不啟動模型） |
| **Embedding / Reranker Server** | `http://localhost:8005` | Embedding 與 Reranking 推理（由 Backend 啟動） |

> 連接埠來自 `packages/config-schema/config.yaml`：Router = `server.port`、Embedding = `embedding_server.port`、各 LLM 實例 = `instances[].port`。Backend 連接埠由其啟動指令決定（預設 5000）。

---

## 1. Dashboard Backend

所有端點前綴 `/api`。模型生命週期統一收斂在 `/api/models` 資源底下，LLM 與 Embedding 都是「model」，用 `kind` 區分。

### 模型識別 key

每個模型實例由唯一 `key` 定位：

- **LLM**：`<群組名>::<instance id>`，例如 `Qwen3-0.6B::qwen3`
- **vLLM pooling 群組**（`kind` 為 embed／rerank）：同 LLM 形式 `<群組名>::<instance id>`，經 `/v1/embeddings`、`/v1/rerank`、`/v1/score` 存取
- **專用 Embedding/Reranker Server**：固定為 `embedding::default`

> `::` 在 URL 裡可直接使用（curl 不需編碼）；瀏覽器/前端送出時會自動編碼成 `%3A%3A`，Backend 會正確解析。

### 1.1 `GET /api/models` — 列出所有模型

回傳設定中每一個模型實例的目前狀態。

```bash
curl -s http://localhost:5000/api/models
```

```json
[
  {
    "key": "Qwen3-0.6B::qwen3",
    "kind": "llm",
    "model_tag": "Qwen/Qwen3-0.6B",
    "host": "localhost",
    "port": 8002,
    "state": "ready",
    "desired": "running",
    "managed": true,
    "pid": 610074,
    "last_error": null,
    "started_at": 1781376193.90,
    "ready_at": 1781376227.69,
    "updated_at": 1781376242.51
  },
  {
    "key": "embedding::default",
    "kind": "embedding",
    "model_tag": null,
    "host": "localhost",
    "port": 8005,
    "state": "stopped",
    "desired": "stopped",
    "managed": false,
    "pid": null,
    "last_error": null,
    "started_at": null,
    "ready_at": null,
    "updated_at": 1781375897.52
  }
]
```

### 1.2 `GET /api/models/{key}` — 查單一模型

```bash
curl -s http://localhost:5000/api/models/Qwen3-0.6B::qwen3
```

回傳單一 `ModelView`（欄位同上）。`key` 不存在 → **404**。

### 1.3 `POST /api/models/{key}/start` — 啟動

非阻塞：spawn 進程後**立即**回傳 `202`，狀態為 `starting`，由背景 reconciler 推進到 `ready` / `failed`。

```bash
curl -X POST http://localhost:5000/api/models/Qwen3-0.6B::qwen3/start
```

| 狀態碼 | 情況 |
|--------|------|
| `202 Accepted` | 已開始啟動，回傳 `ModelView`（`state: "starting"`） |
| `404 Not Found` | `key` 不存在於設定 |
| `409 Conflict` | 該模型已在 `starting` 或 `ready` |

### 1.4 `POST /api/models/{key}/stop` — 停止

對由 Backend 啟動（`managed: true`）的模型，送 `SIGTERM` 給整個 process group（逾時再 `SIGKILL`），確保 vLLM 的 worker 子進程一併收掉、釋放 GPU。

```bash
curl -X POST http://localhost:5000/api/models/Qwen3-0.6B::qwen3/stop
```

| 狀態碼 | 情況 |
|--------|------|
| `202 Accepted` | 已停止/開始停止，回傳 `ModelView` |
| `404 Not Found` | `key` 不存在 |

> 若模型是「外部已在執行、被 Backend 採用」（`managed: false`），Backend 無法停止它（不是它 spawn 的），會在 `last_error` 標註並維持狀態。

### `ModelView` 欄位

| 欄位 | 型別 | 說明 |
|------|------|------|
| `key` | string | 模型實例唯一識別 |
| `kind` | `llm` \| `embedding` | 模型種類 |
| `model_tag` | string \| null | vLLM 模型 tag（embedding 為 null） |
| `host` / `port` | string / int | 該實例監聽位址 |
| `state` | enum | **觀測到的真實狀態**（見下） |
| `desired` | `running` \| `asleep` \| `stopped` | 使用者要求的目標狀態 |
| `managed` | bool | 是否由本 Backend 啟動（決定能否被停止） |
| `pid` | int \| null | 進程 PID（執行中才有） |
| `last_error` | string \| null | 最近一次失敗原因（含 log 尾端） |
| `started_at` / `ready_at` / `updated_at` | float \| null | Unix 時間戳 |

### 狀態機（`state`）

由背景 reconciler 每 ~2s 從「進程是否存活 + `/health` 探測」推導，**不會說謊**：

```
stopped ──start──▶ starting ──/health 200──▶ ready
                      │                         │
                      │ 逾時 / 進程退出          │ 進程退出
                      ▼                         ▼
                   failed ◀────────────────── failed
ready/starting ──stop──▶ stopping ──進程退出──▶ stopped
```

| `state` | 意義 |
|---------|------|
| `stopped` | 未執行 |
| `starting` | 已 spawn，尚未通過 `/health` |
| `ready` | `/health` 回 200，可服務 |
| `sleeping` | vLLM level-1 睡眠中：進程存活、VRAM 已釋放，秒級可喚醒；router 不會路由到它 |
| `failed` | 啟動逾時、或進程非預期退出（看 `last_error`） |
| `stopping` | 已送停止訊號，等待進程退出 |

### 1.4a `POST /api/models/{key}/sleep` · `POST /api/models/{key}/wake` — 睡眠／喚醒

需模型以 `enable_sleep_mode: true` 啟動。`sleep`（選用 `?level=1`）把 `ready` 實例轉 `sleeping`（釋放 VRAM、保留暖機）；`wake` 轉回 `ready`。autoscaled 群組由 autoscaler 全權，手動呼叫回 `409`。

| 狀態碼 | 情況 |
|--------|------|
| `200 OK` | 成功，回傳 `ModelView` |
| `409 Conflict` | 狀態不符（如非 ready 不能 sleep）、未開 sleep mode、或群組已 autoscaled |
| `502 Bad Gateway` | vLLM sleep/wake 端點失敗 |

### 1.4b `PUT /api/models/{group}/autoscale` — 設定群組自動擴縮

不需停機。Body：`{ "enabled": bool, "min_ready"?: int, "max_ready"?: int }`。`enabled:false` 關閉。進階時程（門檻、冷卻、sleep_after…）沿用 schema 預設，需客製時改 config.yaml。

```bash
curl -X PUT http://localhost:5000/api/models/Qwen3-0.6B/autoscale \
  -H 'Content-Type: application/json' -d '{"enabled":true,"min_ready":1,"max_ready":2}'
```

### 1.4c `PUT /api/models/{group}/fallback` — 設定跨模型 fallback 鏈

不需停機。Body：`{ "fallback": ["GroupB", "GroupC"] }`（順序即嘗試順序，`[]` 清除）。該群組所有實例不可用時 router 依序改路由到這些相容群組。回 `409` 表示有未知群組名。

```bash
curl -X PUT http://localhost:5000/api/models/Qwen3-0.6B/fallback \
  -H 'Content-Type: application/json' -d '{"fallback":["Qwen2.5-0.5B-Instruct"]}'
```

> `autoscale` 與 `fallback` 都會出現在 `GET /api/config` 的每群組欄位；皆寫入 overlay，router 在 `/reload` 時吃到。

### 1.5 `GET /api/config` — 設定摘要

回傳攤平後的設定（給前端渲染用）。

```bash
curl -s http://localhost:5000/api/config
```

```json
{
  "server": { "host": "0.0.0.0", "port": 8887, "uvicorn_log_level": "info" },
  "LLM_engines": {
    "Qwen3-0.6B::qwen3": {
      "port": 8002,
      "cuda_device": 0,
      "max_model_len": 500,
      "gpu_memory_utilization": 0.35,
      "tool_parser": "unknown"
    }
  },
  "embedding_server": {
    "port": 8005,
    "cuda_device": 1,
    "embedding_models": ["m3e-base", "bge-m3"],
    "reranking_models": ["bge-reranker-large"]
  }
}
```

### 1.6 `GET /api/resources` — 系統資源

```bash
curl -s http://localhost:5000/api/resources
```

```json
{
  "cpu": 0.5,
  "memory": { "total": 16633208832, "available": 10212667392, "percent": 38.6, "used": 6420541440, "free": 5736230912 },
  "gpus": [
    { "index": 0, "name": "NVIDIA GeForce RTX 3060 Ti", "memory_used": 2188, "memory_total": 8192, "gpu_util": 1 }
  ]
}
```

- `cpu`：百分比；`memory`：`psutil.virtual_memory()` 全欄位（bytes）；`gpus`：`nvidia-smi` 解析（`memory_*` 單位 MiB，`gpu_util` 為百分比）。

### 1.7 `GET /api/gpu/processes` — GPU 進程清單

由背景任務每 ~5s 更新的快取，列出佔用 GPU 的進程（依使用記憶體排序）。

```bash
curl -s http://localhost:5000/api/gpu/processes
```

```json
[
  {
    "pid": 610074,
    "nvidia_smi_name": "python",
    "used_memory_mib": 2901,
    "exe": "/path/to/.venv/bin/python",
    "name": "python",
    "cmdline": ["...", "vllm", "serve", "Qwen/Qwen3-0.6B"],
    "username": "max"
  }
]
```

> 若進程已消失，該筆會帶 `"error": "No such process"`。沒有 GPU 進程時回傳 `[]`。

### 1.8 觀測與遙測

事件與請求記錄持久化在共用 SQLite（`<repo>/data/llmops.db`，WAL）：模型**狀態轉移事件**由 Backend 寫入，**請求日誌**由 Router 寫入同一個 DB，兩者皆透過 Backend 的端點呈現。

#### `GET /api/events?limit=100` — 全域狀態轉移事件

```bash
curl -s "http://localhost:5000/api/events?limit=100"
```

```json
[
  { "id": 4, "ts": 1781376242.5, "key": "Qwen3-0.6B::qwen3", "kind": "llm",
    "from_state": "starting", "to_state": "ready", "detail": null }
]
```

#### `GET /api/models/{key}/events?limit=50` — 單一模型轉移歷史

`key` 不存在 → **404**。`detail` 在 `failed` 時帶失敗原因（含 log 尾段）。

```bash
curl -s "http://localhost:5000/api/models/Qwen3-0.6B::qwen3/events"
```

#### `GET /api/usage?since=<unix>` — per-model 用量摘要

`since` 選填（只統計該 Unix 時間之後）。延遲單位 ms。

```bash
curl -s http://localhost:5000/api/usage
```

```json
[
  { "model_key": "Qwen3-0.6B", "count": 1, "error_count": 0,
    "avg_latency_ms": 1692.6, "max_latency_ms": 1692.6,
    "total_tokens": 21, "p50_latency_ms": 1692.6, "p95_latency_ms": 1692.6 }
]
```

#### `GET /api/load` — 每群組即時負載（autoscaling 訊號）

每 ~5s 由 backend 從 router 的 `/metrics` 聚合並 join registry 狀態。回傳 `{group: {...}}`：

```json
{ "Qwen3-0.6B": { "ready_replicas": 1, "asleep_replicas": 0, "stopped_replicas": 3,
                  "waiting_total": 0, "running_total": 0, "waiting_per_replica": 0, "kv_avg": 0 } }
```

> backend 另在根路徑 `GET /metrics` 吐 Prometheus 格式的擴縮指標（`llmops_group_*`、`llmops_autoscale_*`），由內建 Prometheus（job `llmops-backend`）抓取、餵 Grafana 的 Autoscaling dashboard。

#### `GET /api/requests?model_key=&limit=100` — 近期請求日誌

`model_key` 選填（過濾單一模型）。每筆含延遲、狀態碼、token（非串流回應才有）、error。

```bash
curl -s "http://localhost:5000/api/requests?model_key=Qwen3-0.6B&limit=20"
```

```json
[
  { "id": 1, "ts": 1781377673.2, "model_key": "Qwen3-0.6B", "instance_id": "qwen3",
    "path": "/v1/chat/completions", "status_code": 200, "latency_ms": 1692.6,
    "prompt_tokens": 13, "completion_tokens": 8, "total_tokens": 21, "error": null }
]
```

#### `GET /api/models/{key}/logs?tail=200` — 模型 log 尾段

讀該實例的 vLLM / embedding log 檔尾段。`key` 不存在 → **404**；log 檔不存在 → **404**。

```bash
curl -s "http://localhost:5000/api/models/Qwen3-0.6B::qwen3/logs?tail=200"
```

```json
{ "key": "Qwen3-0.6B::qwen3", "log_path": "./logs/Qwen3-0.6B__qwen3.log", "content": "..." }
```

#### `GET /api/stream/models` — 即時狀態（SSE）

Server-Sent Events，模型快照有變化時推送一筆 `data:`（內容為 `GET /api/models` 的陣列），閒置時每 ~15s 送心跳。取代前端輪詢。

```bash
curl -N http://localhost:5000/api/stream/models
```

```
data: [{"key":"Qwen3-0.6B::qwen3","state":"starting",...}]

data: [{"key":"Qwen3-0.6B::qwen3","state":"ready",...}]
```

### 1.9 `GET /healthz` — Backend 存活探測

無 `/api` 前綴（給 k8s liveness）。回傳本身存活 + 各狀態的模型數。

```bash
curl -s http://localhost:5000/healthz
```

```json
{ "status": "ok", "models": { "ready": 1, "stopped": 1 } }
```

---

### 1.10 認證、角色與稽核

控制 API 用 token 認證（`Authorization: Bearer <token>` 或 `X-Admin-Token: <token>`）。
角色單調：`viewer ⊂ operator ⊂ admin`。解析規則依序為：

1. 未設 env `LLMOPS_ADMIN_TOKEN` 且尚無任何 operator → 視為 local-dev（admin），API 開放。
2. token 命中某把未撤銷的 operator 憑證 → 該 operator 的角色。
3. token 等於 env `LLMOPS_ADMIN_TOKEN` → 永遠 admin（啟動／救援後門）。
4. 否則 → 401。

路由所需最低角色：唯讀 `GET` 不限；模型啟停／編輯／擴縮／eval／benchmark／下載 = `operator`；
使用者與金鑰管理、稽核 = `admin`。權限不足回 **403**，未認證回 **401**。

| 端點 | 角色 | 說明 |
|---|---|---|
| `GET /api/me` | 任一（已認證） | 回傳 `{actor, role}`；未認證 401 |
| `GET /api/auth/status` | 公開 | `{auth_enabled}`，供前端決定是否要求登入 |
| `GET /api/operators` | admin | 列出使用者（不回 token hash） |
| `POST /api/operators` | admin | 建立使用者 `{label, role}`，**回傳一次性明文 token**（`sk-op-…`） |
| `PATCH /api/operators/{id}` | admin | 改角色 `{role}`，即時生效（含 router） |
| `POST /api/operators/{id}/rotate` | admin | 重新產生 token，舊的立即失效；回傳一次性明文 |
| `DELETE /api/operators/{id}` | admin | 撤銷使用者 |
| `GET /api/audit` | admin | 稽核日誌，新到舊；query：`actor`、`action`（path 子字串）、`target`、`since`、`until`（epoch 秒）、`before`（id 游標分頁）、`limit` |
| `GET /api/keys` · `POST /api/keys` · `DELETE /api/keys/{id}` | admin | API 金鑰管理（用於 router 推理） |

稽核每筆記錄 `actor / role / method / path / target / status / 脫敏 body / source_ip`；保留筆數上限由
`LLMOPS_*`（`audit_max_rows`，預設 50000）控制，每小時裁剪。Router 推理也接受登入的
operator/admin token（依 label 歸屬，不限流），但 **viewer 不能推理（403）**。

```bash
# 建立一個 operator，取得一次性 token
curl -s -X POST http://localhost:5000/api/operators \
  -H "X-Admin-Token: $ADMIN" -H 'Content-Type: application/json' \
  -d '{"label":"alice","role":"operator"}'
# 查稽核（admin）
curl -s "http://localhost:5000/api/audit?actor=alice&limit=50" -H "X-Admin-Token: $ADMIN"
```

### 1.11 通知（生命週期告警）

離散事件（`model_failed` / `model_gave_up` / `model_recovered`）推到 Slack / Discord /
通用 webhook，與 Grafana 指標告警互補。sink 可用 `LLMOPS_ALERT_*` env（內建、不可改）或
下列 API（存 DB、即時生效）設定。皆 `require_admin`。

| 端點 | 說明 |
|---|---|
| `GET /api/alerts/sinks` | 列出 env + DB sink；URL 只回**遮罩**版（`url_preview`）+ `source`（env/db） |
| `POST /api/alerts/sinks` | 新增 DB sink：`{type: slack\|discord\|webhook, url, min_severity}` |
| `DELETE /api/alerts/sinks/{id}` | 移除 DB sink（env sink 不可刪） |
| `POST /api/alerts/test` | 送測試推播：`{id}` 測單一 sink，省略則測全部；回各 sink `{type, ok, error?}` |

```bash
curl -s -X POST http://localhost:5000/api/alerts/sinks -H "X-Admin-Token: $ADMIN" \
  -H 'Content-Type: application/json' \
  -d '{"type":"slack","url":"https://hooks.slack.com/services/…","min_severity":"error"}'
curl -s -X POST http://localhost:5000/api/alerts/test -H "X-Admin-Token: $ADMIN" \
  -H 'Content-Type: application/json' -d '{}'
```

---

## 2. LLM Router Server

OpenAI 相容端點，提供統一入口並對多實例做 load-aware 路由。請求 body 的 `model` 欄位填**群組名**（如 `Qwen3-0.6B`），Router 會自動選最低負載的實例並改寫成後端的 `model_tag`。

### 2.1 `POST /v1/chat/completions`

OpenAI Chat Completions 相容，支援 `stream: true`（SSE）。

```bash
curl http://localhost:8887/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
        "model": "Qwen3-0.6B",
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 64
      }'
```

| 狀態碼 | 情況 |
|--------|------|
| `200` | 成功（buffered 或 `text/event-stream` 串流） |
| `400` | body 缺少 `model` 欄位 |
| `404` | `model` 群組不在設定中 |
| `500` | 後端轉發發生未預期錯誤 |

### 2.2 `POST /v1/completions`

OpenAI Completions 相容；參數與行為同上（路由、串流、錯誤碼一致）。

```bash
curl http://localhost:8887/v1/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "Qwen3-0.6B", "prompt": "天空是", "max_tokens": 16}'
```

### 2.3–2.5 Embeddings / Rerank / Score（pooling 端點）

`/v1/embeddings`、`/v1/rerank`、`/v1/score` 共用同一套分派邏輯：Router 先讀請求的 `model`，依此決定上游——

- **vLLM pooling 群組**：`model` 命中 `LLM_engines` 中 `kind` 為 `embed`／`rerank` 的群組時，走完整的 backend 路由機制（負載感知的實例選擇、故障轉移、metrics、用量記錄）。`/v1/embeddings` 需 `kind=embed`；`/v1/rerank` 與 `/v1/score` 需 `kind=rerank`。
- **專用 embedding server**：`model` 不在 `LLM_engines` 時，fall through 到輕量的 Embedding/Reranker Server（見 §3.2–3.4）。

`model` 命中了 pooling 群組但 `kind` 與端點不符（例如把 embed 模型送到 `/v1/rerank`）→ **404**。下方範例的 `model` 可替換成任一 pooling 群組名。

#### 2.3 `POST /v1/embeddings`

OpenAI Embeddings 相容。

```bash
curl http://localhost:8887/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model": "m3e-base", "input": ["文字一", "文字二"]}'
```

- `503`：上游為專用 Embedding Server 且無法連線。

#### 2.4 `POST /v1/rerank`

獨立的重排序端點，Jina / Cohere 相容；回傳每個候選的 `relevance_score`，依分數降冪排序。

```bash
curl http://localhost:8887/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{"model": "bge-reranker-large", "query": "問題", "documents": ["候選一", "候選二"]}'
```

#### 2.5 `POST /v1/score`

成對相關性打分（cross-encoder，與 rerank 同 `kind=rerank`）。

```bash
curl http://localhost:8887/v1/score \
  -H "Content-Type: application/json" \
  -d '{"model": "bge-reranker-large", "text_1": "問題", "text_2": ["候選一", "候選二"]}'
```

### 2.6 `GET /v1/models`

列出設定中的模型群組（OpenAI 格式）。

```bash
curl -s http://localhost:8887/v1/models
```

```json
{ "object": "list", "data": [{ "id": "Qwen3-0.6B", "object": "model" }] }
```

### 2.7 `GET /metrics`

回傳 Router 快取的每個實例 vLLM 指標（給負載決策與觀測用）。

```bash
curl -s http://localhost:8887/metrics
```

```json
{
  "Qwen3-0.6B": {
    "qwen3": {
      "base_url": "http://localhost:8002",
      "running": 0, "waiting": 0, "kv_cache_usage_perc": 0.0,
      "prompt_tokens": 0, "generation_tokens": 0
    }
  }
}
```

> 負載分數權重：waiting ×10、running ×3、kv_cache ×100、inflight ×5（越低越優先）；連不上的後端給 `inf` 分數（fail-open）。

---

## 3. Embedding / Reranker Server

由 Backend 透過 `POST /api/models/embedding::default/start` 啟動。一般情況透過 Router 的 `/v1/embeddings` 存取，但本服務也可直接呼叫。

### 3.1 `GET /health`

就緒探測：模型載入完成才回 `200`（Backend reconciler 用此判定 `ready`）。

```bash
curl -s http://localhost:8005/health
```

```json
{ "status": "ok" }
```

- `503`：模型仍在載入中。

### 3.2 `POST /v1/embeddings`

OpenAI Embeddings 相容。`model` 須是設定中的 embedding 模型。

```bash
curl http://localhost:8005/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model": "m3e-base", "input": ["文字一", "文字二"]}'
```

```json
{
  "object": "list",
  "data": [{ "object": "embedding", "embedding": [0.01, -0.02, ...], "index": 0 }],
  "model": "m3e-base",
  "usage": { "prompt_tokens": 12, "total_tokens": 12 }
}
```

### 3.3 `POST /v1/rerank`

Jina / Cohere 相容重排序。`model` 須是設定中的 reranking 模型；結果依 `relevance_score`（sigmoid 映射到 `[0, 1]`）降冪排序。

```bash
curl http://localhost:8005/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{"model": "bge-reranker-large", "query": "問題", "documents": ["候選一", "候選二"], "top_n": 2}'
```

```json
{
  "id": "rerank-…",
  "model": "bge-reranker-large",
  "results": [
    { "index": 1, "relevance_score": 0.97, "document": { "text": "候選二" } },
    { "index": 0, "relevance_score": 0.12, "document": { "text": "候選一" } }
  ],
  "usage": { "prompt_tokens": 20, "total_tokens": 20 }
}
```

- `top_n`（選填）：排序後只保留前 N 筆。
- `return_documents`（選填，預設 `true`）：是否在結果中回傳 `document.text`。

### 3.4 `POST /v1/score`

成對相關性打分（cross-encoder）。任一邊為純字串時，會與另一邊的清單逐一配對；兩邊皆為清單時長度須相等、逐位配對。`score` 為 `[0, 1]`。

```bash
curl http://localhost:8005/v1/score \
  -H "Content-Type: application/json" \
  -d '{"model": "bge-reranker-large", "text_1": "問題", "text_2": ["候選一", "候選二"]}'
```

```json
{
  "id": "score-…",
  "model": "bge-reranker-large",
  "data": [
    { "index": 0, "object": "score", "score": 0.12 },
    { "index": 1, "object": "score", "score": 0.97 }
  ],
  "usage": { "prompt_tokens": 20, "total_tokens": 20 }
}
```

| 狀態碼 | 情況 |
|--------|------|
| `200` | 成功 |
| `400` | 請求格式錯誤（如 `documents` 為空、雙清單長度不一致） |
| `404` | 指定的 embedding / reranking 模型不存在 |
| `500` | 推理發生錯誤 |

---

## 典型流程

```bash
# 1. 查狀態
curl -s http://localhost:5000/api/models

# 2. 啟動一個 LLM（立即回 202）
curl -X POST http://localhost:5000/api/models/Qwen3-0.6B::qwen3/start

# 3. 輪詢直到 ready
watch -n 2 'curl -s http://localhost:5000/api/models/Qwen3-0.6B::qwen3'

# 4. 經 Router 推理
curl http://localhost:8887/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3-0.6B","messages":[{"role":"user","content":"你好"}]}'

# 5. 停止（釋放 GPU）
curl -X POST http://localhost:5000/api/models/Qwen3-0.6B::qwen3/stop
```
