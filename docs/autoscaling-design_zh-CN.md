# 實例自動擴縮（autoscaling）設計

> 路線圖 [#2](roadmap_zh-CN.md)。本文是完整設計，分階段執行；每個 Phase 都能獨立交付價值。
> 對齊現有程式碼：`apps/backend/app/llmops/`（state / reconciler / manager / launchers）、
> `apps/router-server`（負載訊號）、`packages/config-schema`（per-group 設定）。

## 0. 設計總則

- **決策與動作分離**：autoscaler 只做「決策」並設 `desired`；實際拉起/關閉/睡眠/喚醒
  全部交給既有的 `reconcile_loop`（含 VRAM 預檢、GPU 擺放、崩潰 backoff）。
- **靜態池子**：每個 group 的 `instances:` 是預先宣告好的 slot（固定 host/port/cuda_device）。
  擴縮 = 在這個固定池裡決定幾個 slot 處於哪一階，**不動態配 port/GPU**。
- **router 只負責路由**：autoscaler 落在 **backend**（它擁有生命週期）。router 端唯一改動是
  「不要把流量送到 sleeping 的實例」（見 §4）。

## 1. 三段式暖機階梯（核心觀念）

vLLM `--enable-sleep-mode`（`level=1`）讓我們在 running 與 stopped 之間多一階。
參見 [vllm_sleep_mode.md](vllm_sleep_mode.md)。

| 階 | VRAM | 系統 RAM | 恢復時間 | 恢復動作 |
|---|---|---|---|---|
| **ready**（熱） | 佔用 | 佔用 | 0 | 服務中 |
| **asleep**（`level=1`） | **釋放** | 仍佔用 | 秒級 | `POST /wake_up` |
| **stopped**（冷） | 釋放 | 釋放 | ~100s（torch.compile + CUDA graph） | 重新 spawn |

- **縮容階梯**：`ready →(閒置 sleep_after)→ asleep →(再閒置 stop_after)→ stopped`
- **擴容順序**：先 **wake** 任何 asleep slot（秒級），沒得 wake 才 **cold-start** stopped slot
- 為什麼需要最深的 stop 階：`level=1` 只釋放 VRAM、**不釋放 RAM**。在 RAM 吃緊的機器
  （見 README 提示）睡太多仍會爆 RAM，所以 stop 階仍要保留。

### 只用 `level=1`
`level=2`（gibberish / 量化 / LoRA crash 等已知坑）**不納入自動化**。`level=2` 只保留給未來
手動的「權重熱更新」流程，不在本設計範圍。

## 2. 狀態機擴充（Phase 0）

`apps/backend/app/llmops/state.py`：

```python
class ModelState(str, Enum):
    STOPPED   = "stopped"
    STARTING  = "starting"
    READY     = "ready"
    SLEEPING  = "sleeping"   # 新增：level=1 已睡，process 還活著
    FAILED    = "failed"
    STOPPING  = "stopping"

class Desired(str, Enum):
    RUNNING = "running"
    ASLEEP  = "asleep"       # 新增：第三個目標階
    STOPPED = "stopped"
```

新增轉移：
```
READY    -> SLEEPING   (desired=ASLEEP；POST /sleep?level=1 成功)
SLEEPING -> READY      (desired=RUNNING；POST /wake_up 成功)
SLEEPING -> STOPPING   (desired=STOPPED；直接收掉 process)
```

`reconcile_once` 比對 desired vs observed 時，多處理 `ASLEEP`：
- observed=READY 且 desired=ASLEEP → 呼叫 `/sleep?level=1`，觀測改用 `GET /is_sleeping`
- observed=SLEEPING 且 desired=RUNNING → 呼叫 `/wake_up`
- 觀測 `SLEEPING` 的判定：health 仍可能回 200，**必須以 `GET /is_sleeping` 為準**

## 3. Launcher 旗標（Phase 0）

`apps/backend/app/llmops/launchers.py::VllmLauncher.build_spec`：當 group 設定
`autoscale.sleep_enabled: true` 時，注入
- env：`VLLM_SERVER_DEV_MODE=1`
- CLI：`--enable-sleep-mode`

兩者缺一不可（dev mode 才會掛出 `/sleep`、`/wake_up`、`/is_sleeping`）。預設關閉——
這些是 dev 端點，只有要參與 sleep 擴縮的 group 才打開。

## 4. 安全邊界 & router 不可路由到 sleeping（Phase 0）

**安全**：dev mode 會掛出 `/sleep`、`/wake_up`、`/collective_rpc` 等危險端點。本架構天生安全——
vLLM 在 backend 的 shared netns、只有 backend/router 以 `localhost` 連得到，client 永遠到不了那些
port（router 只代理 `/v1/...`）。sleep/wake 一律由 **backend 控制面** 在 localhost 呼叫，符合官方
要求的邊界。**不得**把這些端點經 nginx/router 對外。

**router 不可路由到 sleeping**：sleeping 的 vLLM `/health` 可能仍回 200，若不處理，router 會把
流量送進去而 hang。機制：router 既有的 per-instance metrics/health 輪詢（`metrics_poller`）增加
一次 `GET /is_sleeping`（或由 backend 發布的 per-instance 狀態判定），把 sleeping 實例標記為
**不可用**，排除在 `select_instance` 候選之外。backend 執行 sleep/wake 後一併 `trigger_router_reload`
以加速收斂。

## 5. 負載訊號（Phase 1） — `已完成`

**落地時的精簡**：原設計打算讓 backend 直接 scrape 每個實例的 `/metrics`,但 router 早已
每 ~1s scrape 全部實例並在 `GET {router_url}/metrics` 對外吐出（含本階段加的 `is_sleeping`）。
因此 backend 改成**消費 router 的 `/metrics`**,再與自己的 registry 生命週期狀態 join——
零重複 scrape、router 也不用改、且用的是 router 路由所依據的同一份數據。

實作：`app/llmops/load_monitor.py`
- `fetch_router_metrics()`：GET router `/metrics`（best-effort,失敗回 `{}`）。
- `aggregate_load(instances, router_metrics)`：純函式,把 registry snapshot 與 router 數據
  join 成 per-group 統計（只算 LLM 群組;load 只加總 `ready` 副本）。
- `load_monitor_loop()`：每 `load_poll_interval`（預設 5s）刷新 `app.state.load_stats`。

每 group 聚合：`ready_replicas` / `asleep_replicas` / `stopped_replicas`、`waiting_total`、
`running_total`、`kv_avg`,以及**主要擴容訊號**：
`waiting_per_replica = waiting_total / max(ready_replicas, 1)`。

對外：`GET /api/observability/load`（→ `/api/load`）。前端 `ModelsView` 每 5s 輪詢,
在群組卡顯示「佇列 N」與「N 睡眠」徽章;sleeping 狀態有專屬顏色/標籤。

## 6. Autoscaler 控制迴圈（Phase 2）

新檔 `apps/backend/app/llmops/autoscaler.py`，在 `main.py` 與 `reconcile_loop` 並列啟動，
每 `autoscale_interval`（預設 ~5s）對每個啟用的 group 評估一次。

### 容量會計（最關鍵）
- **暖機/喚醒中要算成產能**：`effective_ready = ready + starting + waking`。否則佇列一塞會一次
  拉起一堆，等它們暖好（~100s）負載早就過了 → 嚴重 over-provision。
- scale-up 一次只加 **1 個** slot，加完進入 cooldown 再看。

### 決策
```
若 waiting_per_replica > scale_up_threshold  持續 ≥ scale_up_window:
    優先 wake 一個 asleep slot；否則 cold-start 一個 stopped slot
    （受 max_ready 與 VRAM 預檢限制）
若 group 完全閒置（waiting==0 且 該實例 inflight==0）持續 ≥ sleep_after:
    把超過 min_ready 的 ready slot 設 desired=ASLEEP
若 asleep slot 閒置持續 ≥ stop_after 且 asleep 數 > (min_warm - min_ready):
    設 desired=STOPPED
```

### 抗抖動
- **不對稱冷卻**：scale-up 快（window 15–30s），scale-down 慢（sleep_after 數分鐘、
  stop_after 更久）。
- scale-up 後設 group 級 cooldown，期間不再 scale-up。
- 只睡/停 **inflight==0** 的實例（避免砍掉正在服務的）。

### 與既有守衛的互動
- VRAM 預檢可能擋下 scale-up（`_vram_preflight`）→ autoscaler 視為「暫時無法擴」，記錄並退避，
  **不重試風暴**。
- GPU 擺放：靜態池的 slot 已有 cuda_device，無需動態決定。

## 7. 手動 vs 自動衝突（已定：autoscaler 接管整池）

group 開了 `autoscale.enabled` 後：
- 該 group 的 slot 之 `desired` **完全由 autoscaler 控制**
- backend 的 `start`/`stop` API 對該 group 回 **409 Conflict**（或忽略），UI 上該 group 的手動
  start/stop 鈕停用，只能調 `min/max/門檻`、或關掉 autoscale 回到手動
- `min_ready` 永遠維持（即使零流量也保熱），確保不會冷到沒有可服務副本

## 8. 設定（config-schema）

per-group `autoscale` 區塊（沿用 `model_config` 旁，`extra="allow"` 已可承載；Phase 2 正式入 schema）：

```yaml
LLM_engines:
  Qwen3-0.6B:
    instances: [ ... 預宣告的池子 ... ]
    model_config: { ... }
    autoscale:
      enabled: true
      sleep_enabled: true        # 注入 dev-mode + --enable-sleep-mode
      min_ready: 1               # 永遠保熱的副本數（0 = 允許全睡，靠 wake 秒級回來）
      min_warm: 1                # ready+asleep 至少常駐數（>min_ready 的部分用 sleep 暖待命）
      max_ready: 4               # 上限（預設 = len(instances)）
      scale_up_threshold: 4      # waiting_per_replica 超過就擴
      scale_up_window_s: 20
      sleep_after_s: 180         # ready 閒置多久 → sleep
      stop_after_s: 900          # asleep 閒置多久 → stop
      cooldown_s: 60
```

預設 `enabled: false`、`sleep_enabled: false`，逐群組開啟（不影響既有部署）。

## 9. UI（Phase 3）

- 模型編輯器：autoscale 開關 + 上述參數；開 sleep 時提示 dev-mode 安全注意。
- 列表/拓撲：實例多一個 **asleep** 視覺態（介於 ready 與 stopped）；group 顯示「autoscaled」徽章
  與目前 `ready/asleep/stopped` 計數。
- autoscaled group 的手動 start/stop 鈕停用（§7）。
- 事件時間軸：記錄 scale-up/down、sleep/wake（沿用 `model_events`）。

## 10. 觀測 & 告警（Phase 4，加分）

- Grafana 面板：每 group 的 `ready/asleep/stopped` 副本數隨時間、scale 事件標註、佇列深度 vs 副本數。
- 告警：擴容被 VRAM 預檢長期擋住（想擴但擴不動）、或 group 長期佇列高但已達 max。
- scale-to-zero opt-in（`min_ready: 0`）的文件與實測建議。

---

## 分階段執行

| Phase | 內容 | 獨立價值 |
|---|---|---|
| **0** ✅ | `SLEEPING` 狀態 + `ASLEEP` desired、launcher sleep 旗標、manager `sleep()/wake()`、reconciler 處理、router 排除 sleeping、模型編輯器 sleep toggle | **手動 sleep/wake** 就能用：閒置模型一鍵釋放 VRAM、秒級喚醒 |
| **1** ✅ | backend 消費 router `/metrics` 聚合 per-group 負載 + `/api/load` + 群組卡佇列/睡眠徽章 | 每 group 即時飽和度可視化 |
| **2** | `autoscaler.py` 控制迴圈：佇列驅動擴容（wake 優先）、閒置縮容（ready→sleep→stop）、不對稱冷卻、容量會計、min/max、手動禁用 | **全自動擴縮** |
| **3** | 模型編輯器設定、asleep/autoscaled 徽章、拓撲與事件 | 完整 UX |
| **4** | Grafana 面板、告警、scale-to-zero opt-in | 營運完備 |

建議先交付 **Phase 0**（風險低、即使不做 autoscaler 也有用），驗證 sleep/wake 在你的
WSL + Qwen 配置穩定後，再推 Phase 1–2。
