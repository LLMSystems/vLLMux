# 多使用者 + 稽核日誌（RBAC & Audit）設計

> 路線圖 [#6](roadmap_zh-CN.md)。本文是完整設計，分階段執行；每個 Phase 都能獨立交付價值。
> 對齊現有程式碼：`apps/backend/app/core/auth.py`（admin token / API key）、
> `apps/backend/app/api/*.py`（皆以 `Depends(require_admin)` 守衛）、
> `packages/llmops-store`（SQLite 儲存）、`apps/frontend_llmops`（Vue 3 + vue-i18n）。

## 0. 設計總則

- **歸屬先行**：稽核要有意義的前提是「操作能歸屬到某個人」。所以稽核（Phase 1）即使在
  仍是單一 admin token 的階段也先上，actor 暫填 `admin`；等多使用者（Phase 2）一上線，
  actor 自動變成具名身分。兩階段不需互相阻塞。
- **重用既有機制，不另起爐灶**：多使用者走「**具名 operator 憑證**」路線 —— 把現在「一把
  共用 admin token」一般化成「多把具名 token + 角色」，直接套用 API key 那套
  *hash 儲存 + 發行/撤銷 UI*（[llmops_store.py](../packages/llmops-store/llmops_store.py) 的
  `api_keys` 表是現成範本）。
- **預設免摩擦**：沒有設定任何使用者時，**完全保留現狀** —— 落回 env 的
  `LLMOPS_ADMIN_TOKEN`（[settings.py](../apps/backend/app/core/settings.py) 的
  `auth_enabled`）視為隱含 admin，本機開發零摩擦。
- **控制平面 only**：RBAC 落在 **backend 控制 API**（人在操作模型）。router 的推理路徑用的是
  另一套 **API key**，與「使用者」是不同概念，本設計不動它（見 §2）。

## 1. 三種憑證 & 角色模型（核心觀念）

系統裡會有**三種**互不混用的憑證，先釐清避免混淆：

| 憑證 | 給誰 | 認證什麼 | 現況 | 本設計 |
|---|---|---|---|---|
| **admin token** | 維運者 | backend 控制 API（啟停 / 編輯 / 擴縮…） | 單一共用 env 秘密 | →一般化成 operator 憑證（保留為 fallback） |
| **operator 憑證** | 每位使用者 | backend 控制 API，**帶角色** | （新增） | Phase 2 |
| **API key** | 推理客戶端 | router 的 `/v1/*` 推理 | 已有 hash 儲存 + 配額 | **不變** |

### 角色（3 階，刻意精簡）

| 角色 | 能做什麼 | 對應路由 |
|---|---|---|
| `viewer` | 唯讀：dashboards / monitoring / logs / 拓撲 | 所有 `GET` |
| `operator` | 啟停 / sleep / wake / autoscale / fallback / 模型增刪改 / benchmark / eval / 下載 | 目前多數 `require_admin` 路由 |
| `admin` | 以上 **＋** 管理使用者與 operator 憑證 ＋ 管理 API keys ＋ 設定 | `/users`、`/keys`、設定類 |

角色是**全域**的（不做 per-group / per-model 細粒度），符合本專案規模；要再細分是未來的事。
角色採**單調包含**：`admin ⊃ operator ⊃ viewer`，所以守衛只需比較「最低角色」。

## 2. 與現有 auth 的關係

現在每個寫入/控制端點都掛 `Depends(require_admin)`
（[core/auth.py](../apps/backend/app/core/auth.py)）：

```python
def require_admin(request, authorization=Header(None), x_admin_token=Header(None, alias="X-Admin-Token")):
    settings = request.app.state.settings
    if not settings.auth_enabled:          # 沒設 token → 放行（本機 dev）
        return
    token = extract_token(authorization, x_admin_token)
    if not token or not secrets.compare_digest(token, settings.admin_token):
        raise HTTPException(401, ...)
```

Phase 2 把它一般化為 `require_role(min_role)`，並回傳「**目前 actor**」供稽核中介層取用。
**向後相容**規則（依序）：

1. `auth_enabled` 為 false 且沒有任何 operator 憑證 → 放行，actor = `local-dev`（隱含 admin）。
2. 帶的 token 命中某把 operator 憑證 → actor = 該憑證 label、角色 = 該憑證角色。
3. token 等於 env `LLMOPS_ADMIN_TOKEN` → actor = `admin`、角色 = `admin`（永遠保留的後門/救援帳號）。
4. 其餘 → 401。

> 這樣既有「只設一個 `LLMOPS_ADMIN_TOKEN`」的部署無痛升級：那把 token 永遠是 admin。

---

## Phase 1 — 稽核日誌

> 可單獨上，不破壞現狀，且為歸屬鋪路。

### 1.1 資料表（`packages/llmops-store`）

新增 `audit_log` 表（比照 `request_logs` 的寫法與索引）：

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    actor       TEXT    NOT NULL,   -- operator label / 'admin' / 'local-dev'
    role        TEXT,               -- viewer|operator|admin（Phase 1 先填 'admin'）
    method      TEXT    NOT NULL,   -- POST/PUT/DELETE/PATCH
    path        TEXT    NOT NULL,   -- /api/models/Qwen3-0.6B/autoscale
    target      TEXT,               -- 從 path 萃取的主體（group/model/key id）
    status      INTEGER NOT NULL,   -- 回應 HTTP 狀態碼
    detail      TEXT,               -- 脫敏後的 request 摘要（JSON 字串）
    source_ip   TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor);
```

Store API：`record_audit(...)`、`list_audit(actor=None, action=None, since=None, until=None, limit=200)`、
以及保留策略 `prune_audit(max_rows | older_than)`（比照其他 log 的裁剪）。

### 1.2 攔截：ASGI 中介層（零逐路由改動）

在 [main.py](../apps/backend/app/main.py) 加一個中介層，攔截**非 GET 且命中 admin-gated 路由**的請求：

- 只記變更類動詞（`POST/PUT/DELETE/PATCH`），跳過 `GET/HEAD/OPTIONS` 與 SSE/串流。
- 從 `request.state.actor`（由 `require_role` 寫入；Phase 1 尚無則填 `admin`）取得 actor/role。
- `target` 用 path 規則萃取（如 `/api/models/{group}/...` → group）。
- `detail` = request body 摘要，**強制脫敏**：移除/遮罩任何 `token`、`password`、`key`、
  `authorization`、`secret`、新建 API key 的 plaintext 等欄位（白名單式擷取較安全）。
- 記下回應 `status`。**只記事實，不擋請求**（中介層失敗不可影響業務）。

> 覆蓋率：一個中介層即涵蓋 start/stop/sleep/wake/autoscale/fallback/模型增刪改/eval/
> benchmark/下載… 約 90% 的控制面變更，**不必動每條路由**。

### 1.3 端點 & UI

- `GET /api/audit`（admin-only）：分頁 + 篩選（actor / action / target / 時間範圍）。
- 前端新增「稽核日誌」分頁（可放在現有 Monitoring 或 Keys 鄰近）：表格欄位
  `時間 / 操作者(含 avatar) / 動作 / 目標 / 結果`，可篩選。actor 顯示用 §6 的 avatar。

### 1.4 與既有兩個 log 的區隔（重要）

| log | 來源 | 內容 |
|---|---|---|
| router `request_logs` | router | **推理流量**（誰用 API key 打了哪個 model、tokens） |
| `model_events` | backend reconciler | **狀態機轉移**（starting→ready…） |
| **`audit_log`（新）** | backend 中介層 | **控制面的人為變更**（誰改了什麼設定） |

三者互補不重疊。

---

## Phase 2 — 多使用者 / RBAC

> 把單一 admin token 一般化成具名 operator 憑證。

### 2.1 資料表

新增 `operators` 表（直接仿 `api_keys`）：

```sql
CREATE TABLE IF NOT EXISTS operators (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    label        TEXT    NOT NULL,            -- 顯示名 = avatar seed
    token_hash   TEXT    NOT NULL UNIQUE,     -- 只存 SHA-256，明文僅建立時顯示一次
    prefix       TEXT    NOT NULL,            -- 顯示用前綴 sk-op-xxxx…last4
    role         TEXT    NOT NULL,            -- viewer|operator|admin
    created_at   REAL    NOT NULL,
    last_used_at REAL,
    revoked      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_operators_hash ON operators(token_hash);
```

明文 token 沿用 [core/auth.py](../apps/backend/app/core/auth.py) 的 `generate_api_key()` 模式
（換前綴 `sk-op-`），**只在建立當下回傳一次**。

### 2.2 守衛：`require_admin` → `require_role`

```python
def require_role(min_role: Role):
    async def dep(request, authorization=Header(None), x_admin_token=Header(None, alias="X-Admin-Token")):
        actor, role = await resolve_actor(request)     # 走 §2 的相容規則
        if rank(role) < rank(min_role):
            raise HTTPException(403, "insufficient role")
        request.state.actor, request.state.role = actor, role   # 供稽核中介層使用
    return dep
```

逐路由標註最低角色（大多是機械式替換）：

| 範圍 | 由 | 改成 |
|---|---|---|
| 啟停 / 編輯 / 擴縮 / fallback / eval / benchmark / 下載 | `require_admin` | `require_role(operator)` |
| `/api/keys`、新 `/api/users`/`/api/operators`、設定 | `require_admin` | `require_role(admin)` |
| 既有 `GET` | （多半無守衛） | 視需要 `require_role(viewer)`（若要鎖唯讀也需登入） |

> 注意：目前 `GET` 多數未守衛。若要連「看」都需登入，再替 GET 加 `viewer` 守衛；否則維持唯讀公開。
> 這是一個**部署選項**（`LLMOPS_REQUIRE_AUTH_FOR_READ`），預設維持現狀（讀公開）。

### 2.3 端點

- `GET /api/operators`（admin）：列出（label / role / prefix / 建立時間 / 最後使用 / 撤銷狀態）。
- `POST /api/operators`（admin）：建立（label + role）→ 回傳一次性明文。
- `DELETE /api/operators/{id}`（admin）：撤銷。
- `GET /api/me`：回傳目前 actor + role（前端決定要顯示/啟用哪些控制）。

### 2.4 前端

- 新增「使用者 / 憑證」管理頁（admin 可見）—— 直接複製 Keys 頁的版型（mint/撤銷/一次性複製明文）。
- 頂部列顯示目前登入者（avatar + label + 角色徽章），提供「貼上 token 登入 / 登出」。
  目前前端已用 Bearer header 送 admin token，改成送個人 token 是小改。
- 依 `GET /api/me` 的角色，**對沒權限的按鈕做 disable/隱藏**（後端仍以 403 為準，前端只是體驗）。

---

## Phase 3（可選）— 真正的登入 / SSO

只有真的多人、且不想用「貼 token」時才做，二選一：

- **本機帳號**：`users` 表（username + 密碼 hash，如 `argon2`）＋ session 表 / 簽章 JWT ＋ 登入頁。
  自包含、無外部依賴。operator 憑證可保留為「機器對機器」用途。
- **OIDC / SSO**：委派 Google / GitHub / Authentik / Keycloak，把 IdP 身分映射到 §1 的三個角色。
  最完整但最重，且需要既有 IdP。

> 建議 Phase 3 等需求明確再上，別現在背 OIDC 的複雜度。

---

## §6 使用者頭像（avatar）— DiceBear `notionists`

- 採 **DiceBear** + `notionists` 風格（黑白線稿，最貼工具型 / 控制室 UI；對比圖見
  [docs/assets/dicebear-notionists.png](assets/dicebear-notionists.png)、
  [dicebear-avatars.png](assets/dicebear-avatars.png)、[dicebear-peeps.png](assets/dicebear-peeps.png)）。
- **決定性**：`seed = operator.label`，同一人永遠同一張臉；**不需上傳頭像**、純前端離線生成、
  MIT 可商用。
- 前端只裝 `@dicebear/core` 與 `@dicebear/collection`（只 import 用到的風格，tree-shake 後很小）：

```ts
import { createAvatar } from '@dicebear/core'
import { notionists } from '@dicebear/collection'
const svg = computed(() => createAvatar(notionists, { seed: label, radius: 50 }).toString())
// <span v-html="svg" /> 或包成 <UserAvatar :seed="label" /> 元件
```

- 顯示位置：頂部列目前登入者、使用者管理頁清單、**稽核日誌每列的操作者**。
- avatar 只是辨識；**真正語意是角色徽章**（viewer/operator/admin，沿用既有 Badge 變體配色）。

---

## 7. 安全考量

- token 一律**只存 SHA-256**，明文僅建立時顯示一次（沿用現有 `hash_key`）；比對用
  `secrets.compare_digest`。
- 稽核 `detail` **嚴格脫敏**：白名單擷取，永不寫入任何 token/密碼/新 key 明文。
- env `LLMOPS_ADMIN_TOKEN` 永遠是 admin 後門（救援/初次建立 operator 用），不可被刪。
- 撤銷即時生效（`resolve_actor` 每次查 `revoked=0`，可加短 TTL 快取，比照 router 的 key 快取）。
- 角色比較以**後端 403 為準**，前端隱藏按鈕只是體驗，不可當作授權。

---

## 分階段執行

- **Phase 1（稽核）**：`audit_log` 表 + store API + 脫敏 → ASGI 中介層 → `GET /api/audit` →
  前端稽核分頁（含 avatar 欄）。actor 暫填 `admin`。✅ 可獨立交付。
- **Phase 2（RBAC）**：`operators` 表 + store API → `resolve_actor` / `require_role`（含相容規則）→
  逐路由標角色 → `/api/operators` + `/api/me` → 前端使用者管理頁 + 頂部列登入 + avatar
  （`notionists`）。稽核 actor 自動變具名。
- **Phase 3（登入/SSO，選用）**：本機帳號或 OIDC，視需求再定。

> 先做 Phase 1 取得立即價值與歸屬基礎；再做 Phase 2 完成多使用者；Phase 3 視團隊/IdP 需求。
