# LLM-Router-Server-Dashboard 重構方案（Monorepo）

> 狀態：**已執行（Phase 0–4 完成）**。三個子專案已整理成 `apps/` + `packages/` + `deploy/` 的正式 Monorepo。
> 後端已分層並收斂 config、前端已建立 services/stores/router/views、共用 config-schema 已上線，
> backend / router / config-schema 三套 pytest 全綠。下方為原始規劃，保留作為設計紀錄與後續參考。

## 目前架構的問題診斷

實際掃過三個子專案後，發現的檔案層級問題：

### 1. LLM-Router-Server 根目錄有「重複且已 drift 的檔案」（最嚴重）

| 根目錄 | 整理過的位置 | 狀態 |
|--------|------------|------|
| `basic_metrics.py` | `src/metrics/basic_metrics.py` | **內容不同**（已分岔，不確定哪份在跑）|
| `start_all_models.py` | `scripts/start_all_models.py` | **內容不同** |
| `test_router_server.py` | `test/test_router_server.py` | 完全相同（純冗餘）|
| `test.py` | — | 名字無意義的腳本 |

同名兩份且內容不一致，改 bug 時容易改錯邊。

### 2. 前端：裝了工具卻沒用，邏輯全擠在組件裡

- `package.json` 裝了 **pinia** 與 **vue-router**，但 `src/` 底下沒有 `stores/`、`router/`、`views/`
- 所有 API 呼叫全塞在 `ModelList.vue`（349 行），且用裸 `fetch(${VITE_API_BASE_URL}/api/...)` 重複 8+ 次，沒有 API service 層（裝了 axios 卻沒用）
- 6 個檔案全平鋪在 `components/`，沒有區分頁面/容器/展示組件

### 3. 後端：分層不完整、config 多處各讀

- `app/` 底下只有 `launcher/`，沒有 service 層；商業邏輯散在 `routes/` 裡
- config 在後端內部就有三處各自讀取，沒有單一來源：
  - `main.py` 用環境變數 `LLM_ROUTER_SERVER_CONFIG_PATH`，預設 `config.yaml`
  - `routes/config.py` 自己 hardcode `Path(__file__).parent.parent / "config.yaml"`
  - `app/launcher/embedding_launcher.py` 又開一次
- `main.py` 從 **route 模組** 反向呼叫 `system.get_gpu_processes_with_info`，分層是破的
- `routes/system.py`（118 行）偏肥
- 有 Windows 下載殘留垃圾檔 `app/launcher/env.pyZone.Identifier`

### 4. 設定檔重複

`backend/config.yaml` 與 `LLM-Router-Server/configs/config.yaml` 兩份，靠人工約定「用 backend 那份」，沒有強制。

---

## A. 目標頂層結構

```
LLM-Router-Server-Dashboard/
├── apps/
│   ├── frontend/                  # Vue Dashboard（原 frontend/）
│   ├── backend/                   # Dashboard API（原 backend/）
│   └── router-server/             # 原 LLM-Router-Server/
├── packages/
│   └── config-schema/             # ★ 共用 config.yaml 的 schema 與 loader
│       ├── config.yaml            # ← 單一來源
│       ├── schema.py              # pydantic 模型，前後端共用驗證
│       └── README.md
├── deploy/                        # ★ 集中所有 docker / compose
│   ├── frontend.Dockerfile
│   ├── backend-router.Dockerfile  # backend + router 同容器（README 的約束）
│   └── docker-compose.yaml
├── docs/                          # ★ 散落的中文技術筆記集中於此
│   ├── router-streaming-issue.md
│   ├── router-throughput.md
│   ├── architecture.md
│   └── REFACTOR_PLAN.md
├── README.md / README_zh-CN.md
├── LICENSE
└── pnpm-workspace.yaml / Makefile # 統一啟動入口
```

三個原則：`apps/` 放可獨立部署的服務、`packages/` 放跨服務共用的東西、`deploy/` 與 `docs/` 把目前散落各處的 Docker 與 .md 集中。

---

## B. 各區塊搬遷對照

### B1. router-server（清掉重複是第一要務）

| 現況 | 處置 |
|------|------|
| `basic_metrics.py`（根）vs `src/metrics/basic_metrics.py` **內容不同** | ⚠️ 先 diff 確認哪份在跑，保留正確的進 `src/metrics/`，刪根目錄那份 |
| `start_all_models.py`（根）vs `scripts/start_all_models.py` **內容不同** | ⚠️ 同上，保留 `scripts/`，刪根目錄 |
| `test_router_server.py`（根）= `test/` 那份 | 直接刪根目錄，留 `test/` |
| `test.py` | 看內容：臨時腳本就刪，有用就改名進 `test/` 或 `scripts/` |
| `src/llm_router/`、`src/embedding_reranker/` | 已分層良好，維持，搬進 `apps/router-server/src/` |

router 內部結構本身是好的（`router.py` / `backend_selector.py` / `metrics_poller.py` 職責清楚），問題只在根目錄的重複殘留。

### B2. backend（補 service 層 + 統一 config）

```
apps/backend/
├── main.py                 # 只負責 app 組裝、lifespan、掛 router
├── app/
│   ├── routes/             # ← 只做 HTTP I/O，瘦身
│   │   ├── config.py  llm.py  embedding.py  status.py  system.py
│   ├── services/           # ★ 新增：商業邏輯從 routes 移出
│   │   ├── gpu_service.py       # ← system.get_gpu_processes_with_info 搬這
│   │   ├── model_service.py     # 啟停模型的邏輯
│   │   └── config_service.py    # ★ 唯一讀 config 的地方
│   ├── launcher/           # llm_launcher / embedding_launcher（維持）
│   └── core/
│       ├── config.py       # 讀 packages/config-schema，取代三處各讀
│       └── logging.py
└── requirements.txt
```

關鍵修正：
- 三處讀 config → 收斂到 `core/config.py` 一處，main.py、routes、launcher 都從這裡拿
- `get_gpu_processes_with_info` 從 route 移到 `services/gpu_service.py`，main.py 改 import service 而非 route
- 清掉垃圾檔 `app/launcher/env.pyZone.Identifier`

### B3. frontend（把裝了沒用的工具用起來）

```
apps/frontend/src/
├── main.js
├── App.vue
├── router/index.js         # ★ vue-router 已裝，建路由
├── stores/                 # ★ pinia 已裝，建 store
│   ├── models.js               # 模型清單 / 狀態輪詢
│   └── auth.js                 # 登入密碼狀態（原 LoginForm 邏輯）
├── services/
│   └── api.js              # ★ 統一 axios client，收掉 8 處裸 fetch
├── views/                  # ★ 頁面層
│   ├── DashboardView.vue
│   └── LoginView.vue
└── components/             # 只留純展示組件
    ├── ModelCard.vue  ModelGroup.vue  ModelList.vue  LoginForm.vue
```

關鍵修正：
- 建 `services/api.js`，把 `${VITE_API_BASE_URL}/api/...` 那 8+ 處集中成 `getStatus()`、`startLlm(name)` 等函式
- `ModelList.vue`（349 行）把資料抓取/輪詢邏輯抽到 `stores/models.js`，組件只管渲染

### B4. 共用 config（解決 backend ↔ router 兩份）

`backend/config.yaml` 與 `router-server/configs/config.yaml` 收斂成 `packages/config-schema/config.yaml` 單一來源，兩個服務啟動時用環境變數指向它（backend 已支援 `LLM_ROUTER_SERVER_CONFIG_PATH`，router 端比照）。schema.py 用 pydantic 同時給兩端做驗證。

---

## C. 分階段執行順序（由安全到動大刀）

1. **Phase 0 — 清垃圾（零風險）**：刪 `*.Zone.Identifier`、刪確認重複的 `test_router_server.py`（根）。先 diff `basic_metrics.py` / `start_all_models.py` 兩份，確認真本後刪假本。
2. **Phase 1 — 後端分層**：抽 `services/`、收斂 config 到 `core/config.py`。純後端、有測試可驗。
3. **Phase 2 — 前端分層**：建 `services/api.js` → `stores/` → `views/`。一次一層，每步可跑 `npm run dev` 驗。
4. **Phase 3 — 頂層 monorepo 搬移**：建 `apps/`、`packages/`、`deploy/`、`docs/`，搬目錄、改 import 路徑、改 Dockerfile/compose。風險最大，最後做。
5. **Phase 4 — 共用 config package**：合併兩份 config.yaml，加 pydantic schema。

每個 Phase 都應該是一個獨立 commit/PR，可單獨 review 與 rollback。

---

## E. 測試策略（pytest）

### 現況問題

- 專案目前**沒有真正的測試**。現有 `test.py`、`test_router_server.py` 都是手動 smoke script：`from openai import OpenAI` 直接打一台活的 server、`print` 結果、**沒有任何 `assert`**，無法自動化、無法判定通過與否。
- `pytest` 連 `requirements` 都沒有（backend 只有 `httpx`，剛好可拿來做 FastAPI `TestClient`）。
- 這些 smoke script 在 Phase 0 會被清掉，但它們驗證的行為（router 能列模型、能 streaming、embedding 能回向量）要用真正的測試補回來。

### 目標結構

每個 app 各自帶 `tests/`，鏡像 `app/` 的結構：

```
apps/backend/
├── app/
├── tests/
│   ├── conftest.py             # 共用 fixtures：TestClient、假 config、mock subprocess
│   ├── unit/
│   │   ├── test_config_service.py     # config 載入 / 驗證
│   │   ├── test_gpu_service.py        # GPU 進程解析（mock nvidia-smi 輸出）
│   │   └── test_model_service.py      # 啟停邏輯（mock launcher）
│   └── api/
│       ├── test_status_routes.py      # GET /api/status/all
│       ├── test_llm_routes.py         # POST /api/llm/start|stop
│       └── test_config_routes.py
└── requirements-dev.txt        # pytest, pytest-asyncio, httpx

apps/router-server/
├── src/
└── tests/
    ├── conftest.py
    ├── unit/
    │   ├── test_backend_selector.py   # ★ 最該優先：負載最低實例的選擇邏輯
    │   ├── test_config_loader.py
    │   └── test_vllm_metrics_client.py
    └── integration/
        └── test_router_smoke.py       # 取代原手動 script，加 assert + skip marker
```

### 原則與作法

- **單元測試優先於整合測試**：`backend_selector`（依 running/waiting/KV cache 選實例）是純邏輯、無 IO，CP 值最高，最該先寫且能 100% 覆蓋。
- **API 層用 FastAPI `TestClient`**：`httpx` 已具備，不需起真的 server。透過 `app.dependency_overrides` 或 fixture 注入假 config、mock 掉 `subprocess` 啟動模型，測 route 的請求/回應契約。
- **外部依賴一律 mock**：`nvidia-smi`、`subprocess.Popen`、vLLM HTTP endpoint 都用 `pytest` 的 `monkeypatch` / `unittest.mock` 隔離，測試不依賴 GPU 或活的 server。
- **整合 smoke 測試保留但標記**：把原本的手動 script 改寫成帶 `assert` 的 `@pytest.mark.integration` + `@pytest.mark.skipif`（無 server 時自動跳過），平常 CI 不跑、要驗真環境時才 `pytest -m integration`。
- **設定集中在 `pyproject.toml` 或 `pytest.ini`**：定義 `testpaths`、`markers`（unit / api / integration）、`asyncio_mode = auto`。
- **目標覆蓋率**：核心邏輯（services、selector、config）優先拉到 80%+；route 層保證 happy path + 主要錯誤碼即可。

### 對應的階段安排

測試不另開一個大 Phase，而是**跟著各 Phase 一起補**，讓重構有安全網：

- Phase 1（後端分層）→ 同時補 `apps/backend/tests/unit` + `tests/api`，把 config 收斂、service 抽離的結果用測試鎖住。
- Phase 2（前端分層）→ 前端另議（Vitest，不在本 pytest 範圍）。
- 在 Phase 1 之前可先單獨補 `router-server` 的 `test_backend_selector.py`，因為那段邏輯目前完全沒被任何測試保護，重構風險最高。
- `pytest`、`pytest-asyncio` 加進各 app 的 `requirements-dev.txt`。

---

## D. 風險提醒

- **Phase 0 的兩組 drift 檔案**是最該先釐清的 —— 在搞清楚哪份是線上實際在跑之前，不要刪任何一份。
- README 寫死了不少路徑（`/app/backend/config.yaml`、`cd frontend/docker` 等），頂層搬移後 README 與 compose 內的路徑都要同步改，否則部署會壞。
- 後端與 router「同容器」的約束在 monorepo 下不變，Dockerfile 要確保兩者都被 COPY 進去。
- **WSL / CUDA 13 啟動 workaround**：vLLM 在 WSL 下需 `VLLM_USE_V2_MODEL_RUNNER=0`（V2 runner 需要 UVA，WSL 不支援），且 flashinfer 0.6.12 與 CUDA 13 的 CUB 不相容，需 `VLLM_USE_FLASHINFER_SAMPLER=0` + `VLLM_ATTENTION_BACKEND=FLASH_ATTN`。這三個變數已收進兩個 launcher 共用的 `env_setup()`（`apps/backend/app/launcher/env.py`、`apps/router-server/src/llm_router/env.py`），預設**只在偵測到 WSL 時自動套用**，正常 GPU 機不受影響；以 `LLM_ROUTER_VLLM_COMPAT=on|off` 可手動覆寫，且皆用 `setdefault`（外層 export 永遠優先）。`start_llm.sh` 保留作手動測試用。
