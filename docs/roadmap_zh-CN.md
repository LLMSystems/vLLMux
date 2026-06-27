# 優化建議與路線圖

> 站在使用者角度，盤點現有能力後，這份文件記錄「值得補強的實用功能」。
> 現有主線（部署 → 路由 → 監控 → 評測）已相當完整；以下著重在
> **營運 / 多租戶 / 自動化**這一層。優先序依「實用性 × 與現有架構的契合度」排定。

狀態圖例：`規劃中` · `進行中` · `已完成`

---

## 🔴 高優先（與現有設計天然接得上）

### 1. Token 額度與成本預算（per-key budget / cost） — `進行中`

現況：`auth.py` 已有 `rpm_limit`（速率限制）+ `request_logs` 的 per-key 用量歸屬，
但只有「速率」沒有「總量」。

要補：
- 每把 API key 的 **token 額度**（總量 / 每日 / 每月），超額即拒（HTTP 429）。
- 每模型定價表 → 把 `total_tokens` 換算成 **成本**，做 cost dashboard。

為什麼：多人共用同一個 cluster 時，「誰用了多少、誰該被擋」是最常見的需求。
`request_logs` 已有 `total_tokens` 與 `api_key_name`，只需在 `api_keys` 加額度欄位、
在 `authenticate()` 內查當期累計用量即可——改動小、價值高。

落地範圍：
- `packages/llmops-store`：`api_keys` 加 `token_quota` / `quota_period` 欄位、
  新增「查某 key 自週期起點累計 tokens」的查詢。
- `apps/router-server` `auth.py`：請求進來時做 **軟性額度檢查**（帶短 TTL 快取，
  因為 token 數要等回應結束才算得出，故為「已超額才擋」的事後型限制）。
- `apps/backend` `api/auth.py`：建立／列出 key 時帶額度欄位。
- `apps/frontend_llmops` `KeysView.vue`：建立時設額度、列表顯示「已用 / 額度 / 剩餘」。

---

### 2. 實例自動擴縮（autoscaling by load） — `規劃中`

現況具備：每實例生命週期狀態機、GPU 自動擺放、router 端 inflight / queue / KV 負載指標。

要補：依群組的排隊深度 / inflight 自動對同群組多拉一個實例，閒置時自動縮回
（含冷卻時間與上下限，避免抖動）。

為什麼：這是「lifecycle」+「load-aware routing」兩個現有能力的自然交集，
目前擴縮全靠手動。對自架 cluster 是殺手級功能。

完整設計（含 vLLM sleep mode 三段式暖機階梯、分階段執行）見
[autoscaling-design_zh-CN.md](autoscaling-design_zh-CN.md)。

---

### 3. 跨模型 fallback 鏈 — `規劃中`

現況：failover 只在「同一群組的實例之間」。

要補：群組層級的降級鏈（A 群組全掛 / 全超載 → 自動轉 B 群組），
或 `model` 別名 / 虛擬模型（把 `gpt-4` 對到某個本地群組）。

為什麼：客戶端不該因為某顆模型整組 down 就直接失敗。

---

## 🟡 中優先（屬新模組，價值高但工程量較大）

### 4. 回應快取（exact / 語意 cache） — `規劃中`

已有跨實例 KV cache 共享，但沒有「相同請求直接回快取」。
對 RAG / 重複 prompt 場景能直接省算力與延遲。先做精確比對快取，再考慮語意快取。

### 5. 生命週期事件告警（Slack / Webhook / Email） — `規劃中`

auto-restart 已存在，但崩潰、`failed`、退避重啟用盡只在 UI 顯示。
接一個 webhook 通知，讓人不用一直盯著 dashboard。`model_events` 表已是現成的事件源。

### 6. 多使用者 + 稽核日誌（RBAC / SSO） — `規劃中`

現況：單一 admin token 控管所有操作。
團隊使用時缺：多帳號 / 角色，以及「誰在何時 start/stop/edit 了哪顆模型」的稽核軌跡
（`model_events` 已有雛形，補上操作者欄位即可）。

---

## 🟢 加分項

- **Router 自身 `/health`、`/ready`**：給 k8s / 負載器探活（目前有 `/metrics` 但無健康探針端點）。
- **設定版本化 / 匯出匯入**：overlay 改動可回滾、可備份。
- **多模態 Playground**：目前 Playground 偏文字向（chat / completions / embed / rerank），缺 vision 圖片輸入。
- **OpenAI `/v1/batch` 離線批次**：大量離線推理。

---

## 建議實作順序

1. **#1 額度 / 成本** — 改動最小、最貼合剛完成的 API-key 用量歸屬，立刻解決多租戶痛點。
2. **#2 autoscaling** — 最能拉開差異化的功能。
3. 其餘依需求插入。
