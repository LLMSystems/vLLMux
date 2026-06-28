# 控制平面高可用 / 規模化（HA）設計

> 路線圖 C-3。本文是完整設計，**分階段、且每階段都能獨立交付價值**；不要求一次走到多節點。
> 對齊現有程式碼：`apps/backend/app/main.py`（lifespan 起的單例迴圈）、
> `apps/backend/app/llmops/`（registry / reconciler / manager / process）、
> `apps/backend/app/services/overlay.py`（檔案型 overlay）、
> `packages/llmops-store`（SQLite/WAL）、`apps/router-server`（單 worker、in-memory 指標）。

## 0. 設計總則

- **不破壞單機體驗**：今天「一台機器、`docker compose up`」必須維持零設定可跑。HA 是
  **可選的部署形態**，不是預設。
- **分階段、各自獨立有價值**：韌性(Phase 1) → 狀態外移+選主(Phase 2) → 控制/節點拆分
  (Phase 3)。即使只做 Phase 1/2 也立刻更穩、更好維運。
- **狀態外移是地基**：HA 的本質是「把狀態從『單一進程的記憶體 + 本機檔案』搬到一個可共享、
  有交易保證的地方」。其餘(選主、多副本)都是建立在這之上。
- **冪等優先**：所有控制動作(啟/停/擴/縮)做到可重入、可重放，副本接管或重啟才安全。
- **best-effort 不退化**：監控/告警/稽核等旁路,任何 HA 改造都不得讓它們阻塞狀態機。

## 1. 現況與缺口（HA 視角）

| # | 缺口 | 依據 | 為何擋 HA | 階段 |
|---|---|---|---|---|
| 1 | 控制平面單進程、SPOF | `uvicorn main:app`（無 `--workers`）；registry 為 in-memory dict，靠 `asyncio.Lock` | backend 掛 = 控制停擺；registry 無法共享 | P2/P3 |
| 2 | 權威狀態存本機檔案 | overlay = `data/dynamic_models.json`；store = 單一 SQLite(WAL) | 非網路型 DB，跨機器多副本無法安全共用、單寫者瓶頸 | P2 |
| 3 | 綁死單一主機 | `process.py` 用 `subprocess.Popen` 在本機拉 vLLM；registry 存 `Popen` handle | 只能管自己這台；副本拿不到別台的 handle | P3 |
| 4 | 背景迴圈是單例 | `main.py` lifespan 起 reconcile / autoscaler / gpu / load / prune 共 6 個 in-process 迴圈 | 跑兩份 = 兩個 reconciler/autoscaler 互相打架 | P2 |
| 5 | 無跨進程鎖 / 選主 | 僅 `asyncio.Lock`（單進程內） | 無法保證「同一時刻一個寫者 / 一個 reconciler」 | P2 |
| 6 | Router 單 worker、指標 in-memory | `gunicorn workers = 1`；`metrics_cache`/`backend_inflight`/`rr_counters` 在 `app.state` | 多副本 inflight 計數與 RR 游標不一致、metrics 重複抓 | P2 |
| 7 | 無優雅排空 | 停模型直接 `terminate_process_group`，不等 in-flight | 滾動更新 / 縮容時客戶端斷線 | P1 |
| 8 | 恢復僅限「單機重啟自己」 | `adopt_running` + reconciler 從進程+health 重建 | 解單機重啟,不解副本接管 | P1 底子 |
| 9 | SQLite 單寫者瓶頸 | WAL 多讀單寫；高頻寫(request_logs/audit) | 量大時卡單寫者 | P2 |
| 10 | SSO session 金鑰預設隨機 | `settings.signing_secret` 未設則 process-local 隨機 | 多副本/重啟使登入失效 | 設定即解 |

### 已經做對、有利 HA 的底子
- Router `/health` `/ready` 探針(k8s 探活就緒)。
- SQLite WAL + autocommit + `busy_timeout`(同機 backend+router 共享已穩)。
- reconciler 從「進程存活 + /health」推導狀態(冪等雛形)。
- SSO session 金鑰可跨副本共用（設 `LLMOPS_SESSION_SECRET` 即可）。

---

## 2. Phase 1 — 韌性（單機就受益，不需多副本）  ✅ 優雅排空已實作

目標：讓「重啟一下 / 滾動更新」不痛。**無需任何外部依賴**。

> 進度：**§2.1 優雅排空已完成**（backend `stop()` → router `/drain` → 等 in-flight 清空再殺）。
> §2.2 的「desired 重放」與 Phase 2「desired 入庫」高度重疊，已併入 Phase 2 一起做（避免做兩次
> 持久化）；現況的 `adopt_running` 仍負責單機重啟接回既有 vLLM。

### 2.1 優雅排空（缺口 #7）— `已完成`
- `stop` / 縮容 / 關機前，先讓該 instance **停止接新流量**，等 in-flight 跑完(或逾時)再殺。
- 作法:停一顆模型時,先在 router 標記該 instance「draining」(從負載池移除、新請求不再選它),
  backend 等 `inflight==0` 或 `drain_timeout` 後才 `terminate_process_group`。
  - router 已有 per-instance inflight([backend_runtime_state.py](../apps/router-server/src/llm_router/backend_runtime_state.py)),
    新增一個「draining set」+ 選擇器跳過 draining 實例即可。
  - backend `stop()` 改為:先呼叫 router「drain 這個 instance」→ 輪詢 inflight → 逾時上限後強停。
- app 關機(`stop_all`)同理:先 drain 全部、再殺。

### 2.2 啟停冪等 / 可重入（缺口 #8）
- 把 `start` / `stop` 設計成「重放安全」:重複 start 已 READY 的 → no-op;stop 已 STOPPED → no-op
  (現多已如此,補齊缺角並加測試)。
- 重啟後 `adopt_running` 已能接回;補上「啟動時把 desired 狀態從 store 重放」的能力,讓重啟後
  自動把該跑的拉回來(目前 desired 在 registry 記憶體,重啟即失憶 —— 這條也替 Phase 2 鋪路)。

> Phase 1 不改部署型態,只讓單機更穩。可獨立交付。

---

## 3. Phase 2 — 狀態外移 + 選主（HA 的分水嶺）

目標：backend 可跑 **N 個副本**、無 SPOF;router 可水平擴。需引入 **Postgres**(可選後端,
SQLite 仍為單機預設)。

### 3.1 狀態外移到 Postgres（缺口 #2 #9）
- **store 抽象化**:`LLMOpsStore` 介面不變,新增 Postgres 實作(`asyncpg`);
  `LLMOPS_DB_URL=postgres://…` 時走 PG,否則沿用 SQLite。schema 以既有表為準。
- **overlay 入庫**:`data/dynamic_models.json` → DB 一行(JSONB),沿用既有「設定版本化」的
  `config_versions`(本來就把整份 overlay 存 DB)。overlay 讀寫改走 DB 交易,消除檔案 race +
  讓所有副本看到同一份權威設定。
  - 既有 `build_merged_config` / `save_overlay` 介面保留,底層換成 DB load/store。
- **desired 狀態入庫**:每 instance 的 desired(running/asleep/stopped)持久化,副本/重啟都據此 reconcile。

### 3.2 Leader election（缺口 #4 #5）
- 單例迴圈(reconcile / autoscaler / prune / 寫 Prometheus targets)**只有 leader 跑**;
  follower 只服務(唯讀 + 轉發寫到 leader,或寫 DB 由 leader 套用)。
- 機制:**Postgres advisory lock / lease 表**(`SELECT pg_try_advisory_lock(...)` 或一張
  `leader_lease(holder, expires_at)` 搶租約 + 心跳續租)。leader 掛 → 租約過期 → follower 接手。
- `main.py` lifespan:把 6 個迴圈包成「僅 leader 啟動」;失去 leadership 即停迴圈。

### 3.3 Router 水平擴（缺口 #6）
- inflight / 負載指標:可接受「近似」—— 每個 router 副本各自 poll vLLM `/metrics`(那是
  **後端真實狀態**,本就一致);per-副本的 `backend_inflight` 只影響該副本自己的選擇,放大樣本後
  影響可忽略。RR 游標改無狀態策略(`p2c` / `least_load` 不需共享游標)。
- 配合已有 `/health` `/ready`,router 可直接開多副本放 LB 後面。

> 注意：Phase 2 仍是「**單一控制平面、單一主機池**」—— 多副本是為了 **HA(不掛)**,還不是多節點。
> backend 副本仍假設管同一台/同一組 GPU(見 #3 留待 Phase 3)。

### 3.4 設定
| env | 說明 |
|---|---|
| `LLMOPS_DB_URL` | 設 Postgres 連線即啟用 HA 後端;空 → SQLite 單機(預設) |
| `LLMOPS_SESSION_SECRET` | 多副本必設(共享 SSO session,缺口 #10) |
| `LLMOPS_LEADER_LEASE_TTL` | 租約秒數(預設 15;心跳每 1/3 TTL) |

---

## 4. Phase 3 — 控制平面 / 節點代理拆分（真正多節點，最大一步）

目標：跨 **多台 GPU 主機**管理模型 + 完整 HA。解缺口 #3,改動核心執行模型。

- **拆兩個角色**：
  - **控制平面**(無狀態 API + 排程器,可水平擴 + leader):決定「哪顆模型該在哪台、起幾個」,
    寫入 DB 期望狀態,不直接 spawn 進程。
  - **node-agent**(每台 GPU 主機一個)：認領分配到自己的 instance,實際 `spawn_process`、
    探活、回報狀態到 DB / 控制平面。registry 的 `Popen` handle 留在 agent 本機。
- **排程**:VRAM 預檢、GPU 擺放從「單機」升級為「跨節點」—— 控制平面依各 agent 回報的容量挑節點。
- **通訊**:agent ↔ 控制平面用 DB 期望狀態輪詢(最簡)或 gRPC/HTTP 心跳。沿用既有「desired vs
  observed」模型,只是 observed 由 agent 回填。
- 這一步同時解鎖**多節點規模**;但複雜度高(分散式排程、節點故障處理、網路分區),**等有實際
  多主機需求再啟動**。

---

## 分階段執行（建議順序）

- **Phase 1（韌性）**：優雅排空 + 啟停冪等 + desired 重放。✅ 單機立刻更穩,零外部依賴。
- **Phase 2（狀態外移 + 選主）**：Postgres store/overlay/desired + leader election + router 多副本。
  ✅ 真正消除 SPOF,且狀態外移本身讓備份/查詢/多服務共用更乾淨 —— **CP 值最高的一階**。
- **Phase 3（control/agent 拆分）**：多節點 + 完整 HA。改動核心執行模型,**需求確定再做**。

## 取捨與風險

- **不要把 HA 當一個任務吞**：Phase 1 與 Phase 2 各自獨立、可分別上線。
- **Postgres 為可選**：SQLite 永遠是單機預設;只有要 HA 才需 PG。維持「零設定單機」承諾。
- **Phase 3 風險最高**：分散式排程 + 節點故障 + 網路分區是另一個量級的工程,沒有多主機需求前
  不啟動。
- **近似可接受**：router 的 per-副本 inflight 不必強一致(vLLM `/metrics` 才是真相),避免為了
  完美一致引入分散式協調的複雜度。

> 我的建議：先 **Phase 1**（便宜、單機就賺到韌性與優雅排空），需要真 HA 時再認真投入
> **Phase 2**（Postgres + 選主,分水嶺）；**Phase 3** 留給確有多節點需求時。
