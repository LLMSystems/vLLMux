# 設定版本化 / 匯出匯入 設計

> 路線圖「加分項：設定版本化 / 匯出匯入」（overlay 改動可回滾、可備份）。
> 本文是完整設計，分階段執行；每個 Phase 都能獨立交付價值。
> 對齊現有程式碼：`apps/backend/app/services/overlay.py`（唯一寫入瓶頸）、
> `apps/backend/app/llmops/manager.py`（registry 與 self.config）、
> `packages/llmops-store`（沿用 audit/operators 的表結構與 prune 慣例）、
> `apps/backend/app/core/audit.py`（actor 在 `request.state`、middleware 全覆蓋）。

## 0. 設計總則

- **所有可變狀態只在一個檔案**：手維護的 `config.yaml` 對 app 唯讀；runtime 的每個改動
  （加模型 / 編輯 / autoscale / fallback / LoRA / embedding）全部只落在 overlay JSON
  `data/dynamic_models.json`（[overlay.py](../apps/backend/app/services/overlay.py)）。
  **備份 / 版本化 / 回滾的對象 = 這份 overlay**，不碰 `config.yaml`。
- **單一寫入瓶頸**：每個 mutation 都走
  `load_overlay → 改 → build_merged_config(驗證) → save_overlay → self.config = new`。
  版本化只要掛在這條路上即可全覆蓋，不必逐一改 manager 的十幾個方法。
- **永遠先驗證再落地**：匯入 / 回滾的 overlay 一律先 `build_merged_config()`（schema 驗證）
  通過才寫檔，跟現有 mutation 一致；驗證失敗回 400、現狀不動。
- **best-effort、不阻塞**：版本快照失敗只記 log，絕不影響 mutation 本身（仿 audit）。
- **權限**：匯出 `require_operator`（讀取 / 備份較寬鬆）；匯入 / 回滾 `require_admin`
  （整份替換是破壞性操作）。

## 1. 現況與缺口

| 能力 | 現況 |
|---|---|
| 改動持久化 | ✅ overlay JSON，原子寫入（`.tmp` + `os.replace`） |
| 改動驗證 | ✅ `build_merged_config` 先驗證才存 |
| 改動歸屬 | ✅ audit_log 已記「誰在何時做了什麼」 |
| **整份備份 / 還原** | ❌ 只能手動去 server 上 copy `data/dynamic_models.json` |
| **歷史版本 / 回滾** | ❌ 沒有；改錯只能再手動改回 |
| **整份替換後的 registry 對齊** | ❌ 沒有「拿整份新 config resync registry」的函式 |

### 🔴 關鍵實作點：匯入 / 回滾後的 registry 重新對齊
目前 registry 的 add/remove 是**每個操作各自零散處理**
（[create_overlay_model](../apps/backend/app/llmops/manager.py) `registry.add`、
[delete_overlay_model](../apps/backend/app/llmops/manager.py) `registry.remove`）。
匯入 / 回滾是**整份 overlay 替換**，所以要補一個 `resync_registry(new_config)`：

- 新 config 有、registry 沒有的 key → `registry.add`（STOPPED）。
- registry 有、新 config 沒有的 key → `registry.remove`（**須先確認沒在跑**）。
- spec 變了又**正在 RUNNING / STARTING / SLEEPING** 的 instance → launch 參數要重啟才生效。
  **安全規則：只要有受影響的非 STOPPED instance 就擋下（409），要求先停**，與現有
  `update_overlay_model` 要求 stopped 的行為一致。

## 2. Phase 1：匯出 + 匯入（核心備份 / 還原）

### 2.1 manager
新增（沿用既有 overlay helper、不動 save 慣例）：

```python
def resync_registry(self, new_config) -> tuple[set[str], set[str]]:
    """以 new_config 為準對齊 registry：回傳 (added_keys, removed_keys)。
    呼叫前已保證沒有受影響的 running instance。"""

async def import_overlay(self, overlay: dict, *, force: bool = False) -> dict:
    """整份替換 overlay：
    1. build_merged_config(overlay) 驗證 —— 失敗 raise（→ 400）。
    2. 算出 added / removed / changed key 集合。
    3. 任一 removed/changed key 對應的 instance 非 STOPPED 且 force=False → 409。
    4. save_overlay + self.config = new + resync_registry + trigger_router_reload。
    回傳變更摘要 {added, removed, changed}。"""
```

> `changed` 的判定：merge 後該 key 的 spec 與目前 registry 內的 spec 不同
> （比對 `build_spec` 結果或 instance dict）。保守起見，凡 removed 或 changed 都要求 STOPPED。

### 2.2 API（`apps/backend/app/api/config.py` 擴充）

| 路由 | 權限 | 行為 |
|---|---|---|
| `GET /api/config/export` | `require_operator` | 回傳目前 overlay，外包一層 metadata（`{version:1, exported_at, overlay:{…}}`），前端可直接存成 `.json` 檔下載 |
| `POST /api/config/import` | `require_admin` | body = 匯出格式（容忍純 overlay 或含 wrapper）；`?force=true` 可略過 running 防呆。回傳變更摘要 |

- 匯入接受兩種輸入：完整 wrapper 或裸 `{"LLM_engines": {...}}`，取出 `overlay` 後交給
  `import_overlay`。
- audit middleware 既有，`POST /import` 自動入稽核（target=config）。

### 2.3 前端（沿用 Keys/Notifications 頁版型）
- 在「系統 / 設定」區塊加 **匯出**（下載目前設定 JSON）與 **匯入**（選檔 → 預覽變更摘要 →
  確認）。匯入回 409 時提示「請先停掉 X 模型，或勾選強制」。
- `api.ts`：`exportConfig()`、`importConfig(payload, force)`；i18n en/zh-TW。

## 3. Phase 2：自動版本快照 + 歷史 / 回滾

### 3.1 儲存（`config_versions` 表，仿 `audit_log` / `operators`）
```sql
CREATE TABLE IF NOT EXISTS config_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    actor       TEXT,                 -- operator label / 'admin' / 'local-dev'
    role        TEXT,
    summary     TEXT,                 -- 觸發來源，如 'PUT /api/models/Qwen/autoscale'
    sha256      TEXT    NOT NULL,     -- overlay 內容雜湊（去重：相同內容不重存）
    overlay     TEXT    NOT NULL      -- 完整 overlay JSON 快照
);
CREATE INDEX IF NOT EXISTS idx_config_versions_ts ON config_versions(ts);
```
Store 方法：`record_config_version(...)`、`list_config_versions(limit, before)`、
`get_config_version(id)`、`prune_config_versions(max_rows)`。沿用 audit 的 prune loop /
`LLMOPS_CONFIG_VERSIONS_MAX` 設定。

### 3.2 自動快照：middleware-diff（零侵入、全覆蓋、含 actor）
不在 `save_overlay`（sync、無 actor、無 store）裡做。改在一個輕量 middleware
（或擴充 audit middleware 的 after 段）：

- **before**：請求進來前記 overlay 檔的 hash（或 mtime）。
- **after**：若是成功的 mutation 且 overlay hash 變了 → 讀 overlay、以 `request.state.actor/role`
  + `method path` 當 summary，寫一筆 `config_versions`（內容 hash 與最後一筆相同則跳過）。

如此**每個會改 overlay 的操作自動留版**，且天然帶操作者，與 audit 一一對應，**完全不必動
manager 的任何 save 點**。

### 3.3 API + UI
| 路由 | 權限 |
|---|---|
| `GET /api/config/versions`（分頁，仿 audit） | `require_operator` |
| `GET /api/config/versions/{id}`（取單版 overlay，可下載） | `require_operator` |
| `GET /api/config/versions/{id}/diff`（與目前 / 與另一版的 JSON diff） | `require_operator` |
| `POST /api/config/versions/{id}/rollback`（= 拿該版 overlay 走 `import_overlay`） | `require_admin` |

- 回滾**本身也是一次 mutation**，會再產生一筆新版本（不破壞歷史，可再前滾）。
- UI：版本列表（時間 / 操作者 / 摘要 / 角色徽章）＋ 展開 diff ＋「回滾到此版」按鈕（含 running
  防呆與強制選項）。與 Audit 頁串連（同一筆操作既有 audit 也有 version）。

## 4. 邊界與取捨

- **只版本化 overlay，不版本化 `config.yaml`**：後者是人維護的真實來源，用 git 管即可；混進來
  反而模糊「誰是真實來源」。匯出可選擇「附帶當下 merged 結果」純供檢視，但匯入只吃 overlay。
- **跨機器匯入**：overlay 內含 GPU / port 等與機器相關欄位；匯入到不同機器可能撞 port 或無對應
  GPU。`build_merged_config` 會擋掉 schema 級錯誤，但語意級（port 衝突）沿用既有 create/edit 的
  檢查；無法保證的部分在 UI 提示「請檢視後再啟動」。
- **機密**：overlay 不存 token / 金鑰（那些在 DB / env），所以匯出檔可安全外帶；仍在文檔提醒。
- **Email/SMTP、跨環境 promote 等**不在範圍。

---

## 分階段執行

- **Phase 1（備份 / 還原）**：`resync_registry` + `import_overlay`（running 防呆）+
  `GET /export`、`POST /import` + 前端匯出 / 匯入 + 單元測試。✅ 立刻有「整份備份與還原」。
- **Phase 2（歷史 / 回滾）**：`config_versions` 表 + 自動快照 middleware + prune +
  `versions` / `diff` / `rollback` API + 歷史 UI（含 diff 與一鍵回滾）。

> 先做 Phase 1（最小可用的備份 / 還原 + registry resync 這塊唯一的新風險）；歷史與回滾 UI 隨後。
