<div align="center">

# LLM-Router-Server-Dashboard
**一站式 LLM 模型管理與監控平台**

![Main Console](assets/image0.png)

![Model Management](assets/image1.png)


![Model Management](assets/image2.png)

</div>

---

## 專案簡介

**LLM-Router-Server-Dashboard** 是一個針對大型語言模型（LLM）部署與管理的解決方案，提供直觀的 Web 界面來管理、監控和操作多個 LLM 模型實例。

本專案結合路由伺服器（LLM-Router-Server）與易用的管理界面，讓您能夠：
- **視覺化管理**：透過 Web 界面輕鬆管理多個模型
- **動態啟停**：即時啟動、停止模型，無需重啟服務
- **即時監控**：監控模型狀態、GPU 使用率、系統資訊
- **配置管理**：透過 YAML 配置文件靈活管理模型參數

---

## 功能特色

### 模型管理
- 基於 vLLM 的多模型、多實例管理（LLM、Embedding、Reranker）
- 每個實例獨立的生命週期（啟動/停止），具即時狀態機（`stopped → starting → ready → failed/stopping`），由 reconciler 從「進程存活 + `/health` 探測」推導真實狀態
- **在前端貼上 `vllm serve …` 指令即可新增模型** — 解析成可編輯表單，以動態 *overlay* 疊加，**不動手寫的 `config.yaml`**；router 會熱重載（`POST /reload`），新模型端到端可被路由
- 負載感知路由：router 自動選擇負載最低的實例（依運行中／等待中請求 + KV 快取使用率加權）

### 可靠性
- **VRAM 預檢防呆** — 啟動前估算顯存，可能 OOM 就擋下，並提供一鍵 *Force start* 覆寫
- **GPU 自動擺放** — 未指定 `cuda_device` 的實例會自動擺到剩餘顯存最多的 GPU
- **失敗自動重啟** — managed 模型崩潰後以指數退避自動重啟（可設次數，恢復健康後重置）

### 觀測性
- 透過 Server-Sent Events 即時更新狀態（免輪詢）
- **系統拓撲圖**（Vue Flow）— Clients → Router → 模型群組／Embedding → GPU 的即時 mission-control 圖，含流動的流量邊、GPU 擺放邊與控制平面；節點可點擊下鑽
- **Router 負載平衡視圖** — 動畫扇形圖呈現每個副本的實際流量佔比，以及 router 下一個會選的實例
- **趨勢圖** — 請求數／錯誤率／p95 延遲／tokens 的時序圖（15m–24h），由持久化的 request log 聚合
- 每模型用量（次數、錯誤率、p50/p95 延遲、tokens）、請求日誌、狀態轉移事件時間軸
- GPU／CPU／記憶體監控，以及 GPU 進程清單

### Playground
- OpenAI 相容的 **chat（串流）**、completions、**embeddings**、**reranking**，直接經由 router

### 使用體驗
- 明暗雙主題、資訊密集的「控制室」介面
- 控制操作（啟動／停止／新增／移除）有密碼閘

---

## 環境需求

### 硬體需求
- **GPU**: NVIDIA GPU（建議 CUDA 12.1+）
- **記憶體**: 16GB+ RAM（依模型大小而定）
- **硬碟**: 50GB+ 可用空間
---

## 快速開始

### 前端（Web 控制台）

控制台位於 **`apps/frontend_llmops`** — Vue 3 + Vite + TypeScript、Tailwind CSS v4、shadcn-vue 元件、[Vue Flow](https://vueflow.dev)（拓撲／路由圖）、Pinia + Vue Router。（舊的 `apps/frontend` 已棄用。）

#### 本地開發

```bash
cd apps/frontend_llmops
npm install
npm run dev          # http://localhost:5173
```

#### 生產環境建置

```bash
npm run build        # 輸出到 dist/
```

#### 設定 — `apps/frontend_llmops/.env`

```env
VITE_API_BASE_URL=http://localhost:5000        # Dashboard 後端（生命週期、遙測）
VITE_ROUTER_BASE_URL=http://localhost:8887     # LLM Router（推理 + /metrics + /reload）
VITE_MODEL_CONTROL_PASSWORD=123                # 啟動／停止／新增／移除的密碼閘
```

> **三個服務都要跑才完整**：Dashboard 後端（`:5000`）、LLM Router（`:8887`）、以及後端按需啟動的模型實例。後端與 router 都會在啟動時合併動態模型 overlay，所以從前端新增的模型在重啟後仍會保留。

### 後端部署

**重要提醒**：後端需要監聽 LLM 模型狀態（進程管理），因此必須與 LLM-Router-Server 在同一個容器內運行。

#### 1. 建立容器

```bash
# 後端與 router 共用同一容器（見 deploy/backend-router.Dockerfile）
docker compose -f deploy/docker-compose.yaml up -d backend-router
```

**確保 docker-compose.yaml 中暴露了必要的端口**：
- `8887`: LLM-Router-Server API
- `5000`: Dashboard 後端 API
- 其他模型端口（如 8002, 8003 等）

#### 2. 在容器內啟動後端

```bash
# 進入容器
docker exec -it <container_id> bash

# 啟動後端
cd apps/backend
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 5000
```

### LLM-Router-Server 部署
安裝&啟動細節可參考 [LLM-Router-Server 啟動指南](apps/router-server/README_zh.md)
#### 1. 在容器內啟動路由服務器

```bash
cd /app/apps/router-server
pip install -r requirements.txt
sh scripts/start_all.sh ../../packages/config-schema/config.yaml ./configs/gunicorn.conf.py
```

**注意**：配置文件統一使用 `packages/config-schema/config.yaml`（單一來源），確保前端、後端與 router 讀到同一份設定。

**模型生命週期**：router 只負責路由與負載平衡，不再啟動模型。模型進程（vLLM 實例、Embedding/Reranker 服務）由 Dashboard 後端管理，透過 `POST /api/models/{key}/start` 按需啟動。

#### 2. 驗證服務狀態

```bash
# 檢查路由服務器（列出設定的模型群組）
curl http://localhost:8887/v1/models

# 檢查後端 API（每個模型實例的生命週期狀態）
curl http://localhost:5000/api/models
```

---

## 配置說明

### config.yaml 結構

配置文件位於 `packages/config-schema/config.yaml`（單一來源，由 `packages/config-schema/schema.py` 驗證），控制所有模型的啟動參數。

```yaml
# 路由服務器配置
server:
  host: "0.0.0.0"
  port: 8887
  uvicorn_log_level: "info"

# LLM 模型配置
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

# Embedding 服務器配置（可選）
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

### 關鍵參數說明

| 參數 | 說明 | 建議值 |
|------|------|--------|
| `gpu_memory_utilization` | GPU 記憶體使用比例 | 0.6-0.9 |
| `max_model_len` | 最大上下文長度 | 依模型能力 |
| `tensor_parallel_size` | 多 GPU 並行數 | GPU 數量 |
| `dtype` | 推理精度 | float16（速度快） / bfloat16（更穩定） |
| `cuda_device` | GPU 設備編號 | 0, 1, 2... |

---

### Q4: 可以同時啟動多個模型嗎？

可以 — 只要顯存放得下。**VRAM 預檢防呆**會擋下會撐爆目標 GPU 的啟動（可用 *Force start* 逐次覆寫），未指定 `cuda_device` 的實例會**自動擺放**到剩餘顯存最多的 GPU。單張小卡通常能跑一顆中型模型加幾顆小模型；模型是按需啟動的，所以可以設定一大批而不必全部同時運行。

可在後端用環境變數調整防呆／重啟策略：`LLMOPS_VRAM_GUARD`、`LLMOPS_AUTO_RESTART`、`LLMOPS_MAX_RESTARTS`、`LLMOPS_RESTART_BACKOFF`。