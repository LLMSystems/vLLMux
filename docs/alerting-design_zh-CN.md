# 生命週期事件告警（alerting）設計

> 路線圖 [#5](roadmap_zh-CN.md)。本文是完整設計，分階段執行；每個 Phase 都能獨立交付價值。
> 對齊現有程式碼：`apps/backend/app/llmops/`（manager / reconciler / state）、
> `apps/backend/app/core/settings.py`、`packages/llmops-store`（`model_events` 事件源）、
> `deploy/grafana/provisioning/alerting/`（既有指標型告警）。

## 0. 設計總則

- **兩條通道、互補不重疊**：
  - **Grafana**＝**指標/趨勢**型告警（VRAM-blocked、撐滿上限、vLLM 指標），已存在，只差把
    contact point 接到真實 URL。
  - **Backend Notifier**＝**離散生命週期事件**（崩潰、退避用盡、自動復原）。本文主要在補這條。
  - 兩者可指同一個 Slack channel；不互相取代。
- **best-effort、永不阻塞狀態機**：告警派送沿用既有 fire-and-forget 風格，任何失敗只記 log，
  絕不影響 reconcile / 啟停。
- **單一事件漏斗**：持久化 + 告警判斷 + 派送收斂到一處，避免「哪些事件會告警」的邏輯散落。

## 1. 現況與關鍵漏洞

事件目前有**兩個寫入點**，但只有一個會發告警：

| 寫入點 | 走哪 | 會發 webhook？ | 負責的轉移 |
|---|---|---|---|
| [manager._record()](../apps/backend/app/llmops/manager.py) | 直接寫 store + FAILED 時發 webhook | ✅（僅 FAILED） | 多為**使用者操作**（start/stop/sleep/wake） |
| [reconciler._persist()](../apps/backend/app/llmops/reconciler.py) | 直接寫 `model_events` | ❌ **繞過 webhook** | **真正的崩潰、啟動逾時** |

### 🔴 漏洞一：真正的崩潰不會告警
崩潰偵測（`process exited rc=…`）與啟動逾時都在 **reconciler** 發生，而 reconciler 的
`_persist` 只寫 DB、**不發 webhook**。所以「模型掛了」這種最該推播的事件，今天反而不會推 ——
只有 manager 自己標 FAILED（罕見）才會。**這是「半成品」的核心。**

### 🔴 漏洞二：退避用盡沒有事件
[`_maybe_schedule_restart`](../apps/backend/app/llmops/reconciler.py) 在
`restart_count >= max_restarts` 時只是**靜默不再排程**，不發任何訊號 —— 而這正是最需要人介入
的時刻（自動恢復已放棄）。

### 其他
- `alert_webhook` 是**單一 URL、單一 raw JSON**，沒有 Slack/Discord 格式化。
- Grafana [contactpoints.yaml](../deploy/grafana/provisioning/alerting/contactpoints.yaml) 的
  `$GRAFANA_ALERT_WEBHOOK` 仍是佔位值。

---

## 2. 統一事件漏斗（Phase 1，核心重構）

新增 `apps/backend/app/llmops/events.py`：

```python
async def emit_transition(store, notifier, inst, frm, to, detail=None):
    """所有狀態轉移的唯一出口：持久化 + 告警判斷 + 派送。"""
    if store is not None:
        await store.record_model_event(inst.key, inst.kind.value,
                                       frm.value if frm else None, to.value, detail)
    ev = classify_event(inst, frm, to, detail)   # 回傳 AlertEvent | None
    if ev and notifier is not None:
        await notifier.notify(ev)                # best-effort、不阻塞
```

- **reconciler `_persist`** 與 **manager `_record`** 都改呼叫 `emit_transition` ——
  崩潰/逾時從此會告警，漏洞一消失。
- `notifier` 由 lifespan 建好、掛在 `app.state`，傳進 reconcile_loop / manager（沿用 store 的
  注入方式）。

### 退避用盡事件（漏洞二）
`_maybe_schedule_restart` 在「想重啟但 `restart_count >= max_restarts`」的分支，產生一個
**合成事件** `model_gave_up`（非狀態轉移，直接呼叫 `notifier.notify`，並寫一筆
`model_events`（kind=`alert`）作為事件源）。

## 3. Notifier（Phase 1）

新增 `apps/backend/app/llmops/notifier.py`：

```python
@dataclass
class AlertEvent:
    event: str          # model_failed | model_gave_up | model_recovered | ...
    severity: str       # info | warning | error | critical
    model: str
    kind: str
    detail: str | None
    restart_count: int
    ts: float

class Notifier:
    def __init__(self, sinks: list[Sink], min_severity="error", cooldown_s=300): ...
    async def notify(self, ev: AlertEvent) -> None:
        # 每個 sink 自決門檻（無全域硬閘）；至少一個 sink 收得到才消耗 cooldown
        eligible = [s for s in self.sinks
                    if _rank(ev.severity) >= _rank(s.min_severity)]
        if not eligible or self._suppressed(ev): return
        for sink in eligible:
            asyncio.create_task(self._send(sink, ev))   # fire-and-forget
```

- **三個格式化器**：
  - `slack`  → `{"text": "..."}`（含 emoji + 欄位；severity→顏色用 attachments）
  - `discord`→ `{"content": "..."}`
  - `webhook`→ 原始 `AlertEvent` JSON（**沿用既有 `alert_webhook` 行為**，向後相容）
- **severity 門檻**：**每個 sink 自決**（無全域硬閘）。全域 `min_severity` 只當「預設值」——
  套用到 env sink、並作為 UI 新增 sink 的預設選項。所以把某個 sink 設成 `info`，它就真的收得到
  info 事件（如 `model_recovered`），不會被全域擋掉。
- **去重/節流**：`self._last_sent[(model, event)]`，cooldown 內同一組合不重送，避免崩潰迴圈
  每次退避都洗版。
- **永不拋例外**：每個 `_send` 包 try/except，逾時 5s。

## 4. 要告警的事件（精選）

不是每個 transition 都告警 —— 只挑要人知道的：

| 事件 | 觸發 | severity | 來源 |
|---|---|---|---|
| `model_failed` | `* → FAILED`（崩潰/啟動逾時，含 `rc` + log tail 摘要） | **error** | reconciler / manager |
| `model_gave_up` | 退避用盡（`restart_count >= max_restarts` 仍 FAILED） | **critical** | reconciler |
| `model_recovered` | `FAILED → READY`（自動復原，讓人知道已自癒，可消音 Grafana） | **info** | reconciler |
| `autoscale_blocked`（選用） | 擴容被 VRAM 卡住一段時間 | warning | autoscaler（或保留給 Grafana） |

> `model_recovered` 預設低於門檻（`error`）不發；想要「掛了→好了」成對通知的人可把
> `min_severity` 調到 `info`。

## 5. 設定面（Phase 1，向後相容）

`BackendSettings` + `from_env`：

| env | 預設 | 說明 |
|---|---|---|
| `LLMOPS_ALERT_WEBHOOK` | `""` | 通用 webhook（**保留既有**） |
| `LLMOPS_ALERT_SLACK_WEBHOOK` | `""` | Slack incoming webhook |
| `LLMOPS_ALERT_DISCORD_WEBHOOK` | `""` | Discord webhook |
| `LLMOPS_ALERT_MIN_SEVERITY` | `error` | `info\|warning\|error\|critical` |
| `LLMOPS_ALERT_COOLDOWN_S` | `300` | 同 (model,event) 去重視窗 |

任一 URL 有值就建一個對應 sink；全空＝Notifier 等於停用（行為等同今天沒設 webhook）。

## 6. 後台 UI（Phase 2）

admin「通知設定」頁（沿用 Keys/Operators 頁版型，`require_admin`）：

- **sink 清單**：類型（slack/discord/webhook）+ URL + 最低 severity，存 DB（新表
  `alert_sinks`，仿 `operators`）。
- **一鍵測試推播**：`POST /api/alerts/test` 送一筆假 `AlertEvent` 到指定/全部 sink，回各自結果。
- **設定合併**：env 當「內建預設 sink」，DB 為使用者新增的；Notifier 啟動讀 env，runtime 變更
  從 DB reload（仿 overlay 的 reload 流程）。
- **Activity 頁**：`model_events` 已是事件源，給 `alert`/`failed` 類事件加 severity 徽章。

API（皆 `require_admin`）：
`GET/POST /api/alerts/sinks`、`DELETE /api/alerts/sinks/{id}`、`POST /api/alerts/test`。

## 7. Grafana 收尾（Phase 3）

- `deploy/.env` 把 `GRAFANA_ALERT_WEBHOOK` 指到真實 URL（generic webhook 即可送 Slack via
  Slack 的 webhook）。
- 想要原生 Slack 格式：把 [contactpoints.yaml](../deploy/grafana/provisioning/alerting/contactpoints.yaml)
  的 `type: webhook → slack`、`settings.url` 換成 Slack webhook（Grafana 內建 Slack 整合）。
- 文檔：roadmap #5 半成品→完成、monitoring 文檔補「兩條通道」、`.env.example` 補新變數。

---

## 分階段執行

- **Phase 1（後端核心）**：`events.py` 統一漏斗（修漏洞一）＋ `notifier.py`（slack/discord/webhook
  ＋ severity ＋ cooldown）＋ `model_gave_up`/`model_recovered`（修漏洞二）＋ settings ＋ 單元測試。
  ✅ 立刻讓「崩潰會告警」。
- **Phase 2（後台 UI）**：`alert_sinks` 表 ＋ `/api/alerts/*` ＋ 通知設定頁（含測試推播）＋
  Activity severity 徽章。
- **Phase 3（Grafana ＋ 文檔）**：接真實 contact point URL、原生 Slack 選項、更新 roadmap/
  monitoring/`.env.example`。

> 先做 Phase 1（補回最關鍵的「崩潰告警」與退避用盡）；UI 與 Grafana 收尾隨後。Email(SMTP)
> 不在本設計範圍，未來要再加一個 sink 類型即可。
