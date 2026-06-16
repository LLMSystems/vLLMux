<div align="center">

# LLM-Router-Server-Dashboard
**一站式 LLM 模型管理與監控平台**

![Main Console](assets/image0.png)

![Model Management](assets/image1.png)


![Model Management](assets/image2.png)
![Model Management](assets/image3.png)
![Model Management](assets/image4.png)

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
- **思考（reasoning）顯示** — 模型搭配 vLLM reasoning parser 時，`reasoning` 串流會顯示在答案上方的可摺疊「💭 思考過程」區塊

### 壓測與評測（evalscope）
- **壓測**（`/benchmark`）— 並發 sweep、到達率 open-loop、多輪、**SLA 自動調優**，以及 **embedding／rerank** 吞吐與單請求**速度基準**；每次執行為獨立子進程，含即時圖表、run 比較、完整 evalscope HTML 報告
- **準確度／品質評測**（`/eval`）— **30+ 個基準資料集**，依能力分組（基線、知識進階、中文、推理、數學、多語言、**工具調用**、**長上下文**、程式碼、需裁判的問答）：MMLU/ARC/GSM8K/IFEval、C-Eval/C-MMLU、GPQA/MMLU-Pro、AIME、HumanEval、ToolBench/General-FunctionCall、Needle-in-a-Haystack…
  - 每資料集分數、**run 對 run 的比較表**（每列標出最高分）、互動式 HTML 報告
  - **裁判模型（LLM-as-judge）** 給自由問答評分 — 可選自家部署的模型（經 router）或外部 OpenAI 相容 API
  - **進階 `dataset_args`** — few-shot 數 + 依資料集的原始覆寫（子集選擇等）
  - 防呆：需裁判的資料集會強制設定裁判；長上下文與真實工具調用資料集會提醒模型前提（夠大的 `max_model_len`、vLLM tool parser）

### 資料庫
- **模型庫**（`/library`）— 在 UI 掃描／預下載／刪除 HF 權重，含即時下載進度
- **資料集庫**（`/datasets`）— 預先下載壓測與評測資料集到共用 ModelScope 快取，執行時就不會卡在首次下載
- **工具調用設定助手** — 模型編輯器把模型家族對應到正確的 vLLM `tool_call_parser`（Qwen→`hermes`、Qwen3-Coder→`qwen3_xml`、Llama→`llama3_json`/`llama4_pythonic`…），一鍵帶入（見 `docs/vllm_auto_tool_整理.md`）

### 使用體驗
- 明暗雙主題、資訊密集的「控制室」介面
- **管理員權杖控管**控制操作（啟動／停止／新增／編輯／移除），以及 **API 金鑰管理** —
  發行／撤銷用於 router 推理的金鑰，並在請求日誌中做 per-key 用量歸屬

---

## 環境需求

### 硬體需求
- **GPU**: NVIDIA GPU（建議 CUDA 13.1+）
- **記憶體**: 16GB+ RAM（依模型大小而定）
- **硬碟**: 50GB+ 可用空間
---

## 快速開始

### Docker 一鍵部署

整套服務（Dashboard 後端、LLM router、Vue 前端）由單一 Compose 檔建置並啟動。
需要安裝 Docker 與 NVIDIA Container Toolkit（WSL2 請在 Docker Desktop 開啟 GPU 支援）。

```bash
cp deploy/.env.example deploy/.env   # 填 HF_TOKEN、要用的 GPU、管理員權杖
make up                              # docker compose -f deploy/docker-compose.yaml up -d --build
# 瀏覽器開 http://localhost:8884
```

`make down` 停止、`make logs` 追蹤所有服務日誌、`make ps` 看狀態。

**架構**（見 [`deploy/docker-compose.yaml`](deploy/docker-compose.yaml)）：

| 服務       | 映像                   | 端口  | 角色 |
|------------|------------------------|-------|------|
| `backend`  | `llmops-engine`（GPU） | 5000  | Dashboard API；在 `:800x` 拉起 vLLM 子進程 |
| `router`   | `llmops-engine`        | 8887  | OpenAI 相容路由；**共用後端的 network namespace**，才打得到那些 localhost vLLM 端口 |
| `frontend` | `llmops-frontend`      | 8884  | nginx 服務 SPA，並反向代理 `/api` → 後端、`/v1` → router |

為何一份映像、兩個服務：只有後端真的需要 vLLM（它負責拉起子進程），而 router 必須在
`localhost` 看到那些子進程——所以單一 [`engine.Dockerfile`](deploy/engine.Dockerfile)
（基於官方 `vllm/vllm-openai`）以 `network_mode: service:backend` 跑成兩個服務。

前端透過 nginx 以單一來源（same-origin）連到後端與 router，因此 build 不會硬編任何
host/port。SQLite 與動態模型 overlay 放在 `llmops-data` named volume；下載的模型**權重**
則以 bind-mount 掛在主機 HF 快取（`HF_CACHE_DIR`，預設 `~/.cache/huggingface`），所以
本機就能直接瀏覽、也和主機端工具共用。`packages/config-schema/config.yaml` 同樣 bind-mount
掛入，因此改模型不必重新 build。

> **模型生命週期**：router 只負責路由與負載平衡，不會啟動模型。vLLM 實例（與
> Embedding/Reranker 服務）由後端管理，從 **Models** 頁按需啟動（或
> `POST /api/models/{key}/start`）。後端與 router 都會在啟動時合併動態模型 overlay，
> 所以從前端新增的模型在重啟後仍會保留。

#### 驗證

```bash
curl http://localhost:8887/v1/models     # router：列出設定的模型群組
curl http://localhost:5000/api/models    # 後端：每個實例的生命週期狀態
```

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
```

> **驗證**改由後端控管（不再是 build-time 密碼）。在後端與 router 設定
> `LLMOPS_ADMIN_TOKEN` 即可鎖住所有控制操作（啟動／停止／新增／編輯／移除 +
> 金鑰管理）；UI 會要求輸入一次 token 並在 session 內沿用。在 router 設定
> `LLMOPS_REQUIRE_API_KEY=true` 則要求所有 `/v1/*` 推理都帶 bearer token（admin
> token，或在 **API 金鑰** 頁建立的金鑰）。兩者預設關閉，方便本機開發。

> **三個服務都要跑才完整**：Dashboard 後端（`:5000`）、LLM Router（`:8887`）、以及後端按需啟動的模型實例。後端與 router 都會在啟動時合併動態模型 overlay，所以從前端新增的模型在重啟後仍會保留。

### 手動 / 開發啟動

也可自行啟動三個部分（Python 依賴在 repo 根目錄的 `.venv`）：

```bash
# Dashboard 後端（:5000）
cd apps/backend && pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 5000

# LLM router（:8887）— 細節見 apps/router-server/README_zh.md
cd apps/router-server && pip install -r requirements.txt
sh scripts/start_all.sh ../../packages/config-schema/config.yaml ./configs/gunicorn.conf.py
```

配置文件統一使用 `packages/config-schema/config.yaml`（單一來源），確保前端、後端與
router 讀到同一份設定。

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