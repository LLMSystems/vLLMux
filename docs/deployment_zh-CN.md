# 部署與架構

> [English](deployment.md)

整套服務（Dashboard 後端、LLM router、Prometheus、Grafana、GPU/主機 exporter、Vue 前端）
由單一 Compose 檔建置並啟動。需要安裝 Docker 與 NVIDIA Container Toolkit（WSL2 請在
Docker Desktop 開啟 GPU 支援）。

```bash
cp deploy/.env.example deploy/.env   # 填 HF_TOKEN、要用的 GPU、管理員權杖
make up                              # docker compose -f deploy/docker-compose.yaml up -d --build
# 瀏覽器開 http://localhost:8884
```

`make down` 停止、`make logs` 追蹤所有服務日誌、`make ps` 看狀態。

## 服務

見 [`deploy/docker-compose.yaml`](../deploy/docker-compose.yaml)。

| 服務             | 映像                   | 端口  | 角色 |
|------------------|------------------------|-------|------|
| `backend`        | `llmops-engine`（GPU） | 5000  | Dashboard API；在 `:800x` 拉起 vLLM 子進程 |
| `router`         | `llmops-engine`        | 8887  | OpenAI 相容路由；**共用後端的 network namespace**，才打得到那些 localhost vLLM 端口 |
| `prometheus`     | `prom/prometheus`      | 9090  | 透過 file-based SD 抓取 vLLM 集群的 `/metrics`；**同樣共用後端 netns**，`localhost:800x` 才解析得到那些實例 |
| `grafana`        | `grafana/grafana`      | （代理）| Dashboards 與告警；經前端 nginx 以單一來源代理在 `/grafana` |
| `dcgm-exporter`  | `nvcr.io/.../dcgm-exporter`（GPU） | 9400 | NVIDIA GPU 遙測（利用率、顯存、溫度、功耗） |
| `node-exporter`  | `prom/node-exporter`   | 9100  | 主機指標（CPU、RAM、磁碟、網路） |
| `frontend`       | `llmops-frontend`      | 8884  | nginx 服務 SPA，並反向代理 `/api` → 後端、`/v1` → router、`/grafana` → grafana |

### 為何一份映像、多個服務共用一個 netns

只有後端真的需要 vLLM（它負責拉起子進程），而 router 與 Prometheus 必須在 `localhost`
看到那些子進程——所以單一 [`engine.Dockerfile`](../deploy/engine.Dockerfile)（基於官方
`vllm/vllm-openai`）跑成 `backend` + `router`，並（連同 Prometheus）以
`network_mode: service:backend` 串接。前端透過 nginx 以單一來源（same-origin）連到後端、
router 與 Grafana，因此 build 不會硬編任何 host/port。

### 資料持久化

- SQLite 與動態模型 overlay → `llmops-data` named volume
- Prometheus TSDB → `prometheus-data`；Grafana 狀態 → `grafana-data`
- 模型**權重**以 bind-mount 掛在主機 HF 快取（`HF_CACHE_DIR`，預設 `~/.cache/huggingface`），
  本機就能直接瀏覽、也和主機端工具共用
- `packages/config-schema/config.yaml` 同樣 bind-mount 掛入，因此改模型不必重新 build

> **模型生命週期**：router 只負責路由與負載平衡，不會啟動模型。vLLM 實例（與
> Embedding/Reranker 服務）由後端管理，從 **Models** 頁按需啟動（或
> `POST /api/models/{key}/start`）。後端與 router 都會在啟動時合併動態模型 overlay，
> 所以從前端新增的模型在重啟後仍會保留。

### 驗證

```bash
curl http://localhost:8887/v1/models     # router：列出設定的模型群組
curl http://localhost:5000/api/models    # 後端：每個實例的生命週期狀態
```

## 環境變數（`deploy/.env`）

把 [`deploy/.env.example`](../deploy/.env.example) 複製成 `deploy/.env` 再調整;以下每個變數
在該檔案中都有逐行註解。除了 `HF_TOKEN`(只有 gated/私有權重才需要)之外全部可選 —— 預設值
即可跑起一套本機部署。

**對外 host port** —— 瀏覽器只需要 `FRONTEND_PORT`;另外三個是給「直接打 API」用的,埠號被占用
時可改。(容器內部埠號是固定的,這裡只改 host 端。)

| 變數 | 預設 | 用途 |
|---|---|---|
| `FRONTEND_PORT` | `8884` | 控制台入口(經 nginx 提供 SPA + `/api` + `/v1` + `/grafana`) |
| `ROUTER_PORT` | `8887` | 直接存取 OpenAI 相容 router |
| `BACKEND_PORT` | `5000` | 直接存取 Dashboard 後端 API |
| `PROMETHEUS_PORT` | `9090` | Prometheus UI / API |

**模型與快取**

| 變數 | 預設 | 用途 |
|---|---|---|
| `HF_TOKEN` | *(空)* | 存取 gated/私有權重的 HuggingFace token(公開模型不需要) |
| `HF_CACHE_DIR` | `~/.cache/huggingface` | 綁定掛載為權重快取的 host 目錄(只能用絕對路徑,`~`/`${HOME}` 不會展開) |
| `MODELSCOPE_CACHE_DIR` | `~/.cache/modelscope` | 壓測/評測資料集的 host 目錄(規則同上) |
| `NVIDIA_VISIBLE_DEVICES` | `all` | engine 可用的 GPU —— `all` 或逗號清單如 `0,1` |

**驗證**(見下方[驗證機制](#驗證機制))

| 變數 | 預設 | 用途 |
|---|---|---|
| `LLMOPS_ADMIN_TOKEN` | *(空)* | 控管所有操作的共享管理員權杖;**留空 = 關閉驗證(僅限開發)** |
| `LLMOPS_REQUIRE_API_KEY` | `false` | 設為 `true` 時,router 會拒絕沒有有效 bearer token 的 `/v1/*` 請求 |

**告警與監控**

| 變數 | 預設 | 用途 |
|---|---|---|
| `LLMOPS_ALERT_WEBHOOK` | *(空)* | 模型進入 FAILED 時收到 JSON POST 的 webhook(Slack/Discord/任意端點) |
| `GRAFANA_ADMIN_PASSWORD` | `admin` | Grafana `admin` 使用者的登入密碼(匿名存取維持唯讀)—— 非本機部署請務必更換 |
| `GRAFANA_ALERT_WEBHOOK` | *(佔位字串)* | 內建 vLLM 告警規則通知的 webhook;留空則保留一個不會解析的佔位 URL |

改完 `deploy/.env` 後,重跑 `make up` 即可套用。

## 前端（Web 控制台）

控制台位於 **`apps/frontend_llmops`** — Vue 3 + Vite + TypeScript、Tailwind CSS v4、
shadcn-vue 元件、[Vue Flow](https://vueflow.dev)（拓撲／路由圖）、Pinia + Vue Router。
（舊的 `apps/frontend` 已棄用。）

```bash
cd apps/frontend_llmops
npm install
npm run dev          # http://localhost:5173
npm run build        # 生產環境建置 → dist/
```

設定 — `apps/frontend_llmops/.env`：

```env
VITE_API_BASE_URL=http://localhost:5000        # Dashboard 後端（生命週期、遙測）
VITE_ROUTER_BASE_URL=http://localhost:8887     # LLM Router（推理 + /metrics + /reload）
```

### 驗證機制

驗證改由後端控管（不再是 build-time 密碼）。在後端與 router 設定 `LLMOPS_ADMIN_TOKEN`
即可鎖住所有控制操作（啟動／停止／新增／編輯／移除 + 金鑰管理）；UI 會要求輸入一次
token 並在 session 內沿用。在 router 設定 `LLMOPS_REQUIRE_API_KEY=true` 則要求所有
`/v1/*` 推理都帶 bearer token（admin token，或在 **API 金鑰** 頁建立的金鑰）。兩者預設
關閉，方便本機開發。

## 手動 / 開發啟動

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

## 環境需求

- **GPU**：NVIDIA GPU（建議 CUDA 13.1+）
- **記憶體**：16GB+ RAM（依模型大小而定）
- **硬碟**：50GB+ 可用空間
