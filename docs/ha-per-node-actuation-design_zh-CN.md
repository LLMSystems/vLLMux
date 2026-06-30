# Per-node actuation 設計（HA Phase 3 收尾：真並行多 GPU 節點）

> 上承 [ha-phase3-design_zh-CN.md](ha-phase3-design_zh-CN.md)。3a–3f 已完成:狀態外移、leader 選主、
> observed 入庫、跨節點排程、router 水平擴、vLLM 可路由綁定、Helm chart。**唯一剩下的**就是這份文檔的主題:
> 讓**每個 backend pod / node 各自起「分配給它」的實例**,而不是「只有 leader 起全部」。做完這步,
> `backend.replicas>1` 才從「控制平面暖備(failover)」升級成「並行多 GPU 工作節點」。
>
> ⚠️ **這步本質上需要真實的多 GPU 主機才測得起來、才有意義**;單機只能做到 collapsed-first(行為不變)+
> unit test。所以這是「等有實體多機環境再實作」的設計藍圖,不是現在就要寫的程式碼。

## 0. 現況:為什麼多 pod 只有 leader 起模型

- 所有**控制迴圈**(reconcile / autoscale / scheduler / gpu-poll / load-monitor / prune)都在 leader 的
  `_on_acquire` 裡 create_task([main.py](../apps/backend/app/main.py))—— follower 完全不跑。
- `manager.start()` 是**同步在自己 process 內 `spawn_process`**([manager.py](../apps/backend/app/llmops/manager.py))。
- autoscaler 的 `_apply` 直接呼叫 `manager.wake/start/sleep/stop`([autoscaler.py](../apps/backend/app/llmops/autoscaler.py))。

結論:即使 scheduler(3c)把實例**指派**給 node B,**沒有任何迴圈在 node B 上把它起起來** —— actuation
只發生在 leader 的 registry/process。這就是要補的洞。

> 已經鋪好的地基(這步直接用):`foreign_assignments()`(只動本 node 擁有、且擁有者存活的實例,含死節點
> 自癒)、`scheduler.place()`(greedy by free VRAM + 死節點重指派)、`instances_live`/`instance_observed`
> 的 ownership-gated 回填、vLLM `LLMOPS_VLLM_BIND_HOST` 可路由綁定、每副本都跑的 node-agent 心跳。

## 1. 核心轉變:從「同步 in-process spawn」到「desired/assignment 驅動的收斂」

今天:`API/autoscaler → manager.start() → 立刻 spawn(本機)`。
目標:**寫意圖,讓擁有者收斂**:

```
寫入端(API / autoscaler / scheduler,可在任一控制平面副本)
    └─ 寫 desired(running/asleep/stopped) + assignment(key → node) 到共享 DB(冪等)

每個 node 的 reconcile/actuation loop(每 pass)
    對「assignment == 我 且 存活」的實例做 desired → observed 收斂:
      desired=running 且 state∈{stopped,failed}  → 本機 spawn_process
      desired=stopped 且 state∈{ready,starting,sleeping} → 本機 stop
      desired=asleep  且 state=ready → 本機 sleep；desired=running 且 sleeping → wake
    回填 instances_live(可路由位址)+ instance_observed(全狀態)— 只回填自己擁有的
```

這把現有的 `replay_desired()`(boot 一次性)**泛化成「每 pass 持續收斂」**,並把它從 leader-only 移到
**每個 node 都跑**(只動自己擁有的)。collapsed 單機:唯一的 node == leader,擁有全部 → 收斂全部 ==
今天的行為(只是路徑從「同步呼叫」變「迴圈收斂」)。

## 2. 迴圈的重新切分:per-node vs singleton(leader-only)

| 迴圈 | 跑在哪 | 為什麼 |
|---|---|---|
| **reconcile / actuation**(收斂 owned 實例 + 回填 observed/live) | **每個 node** | actuation 必須發生在持有 GPU 的那台 |
| node-agent 心跳(註冊 node + 容量) | **每個 node**(現已如此) | 每台都要報自己 |
| gpu-poll(本機 GPU 指標) | **每個 node** | 各報各的 GPU |
| **scheduler**(placement:哪顆放哪台) | **leader-only(singleton)** | 全域決策,單一排程器避免衝突 |
| **autoscaler**(決定每群組 ready 數) | **leader-only(singleton)** | 全域決策;產出 = 寫 desired/assignment,不直接 spawn |
| load-monitor(彙整 router 負載) | leader-only | 給 autoscaler 用的全域視圖 |
| prune(audit / config_versions / nodes / live / observed) | leader-only | 全域清理,跑一份就好 |

> 實作面:把 `_on_acquire`(leader-only)拆成兩組 —— 一組「每 node 啟動」(reconcile + gpu-poll + 心跳)
> 在 lifespan 直接起;一組「leader 才啟動」(scheduler + autoscaler + load-monitor + prune)留在 `_on_acquire`。

## 3. 各元件具體改法

### 3.1 manager.start/stop/sleep/wake → 寫意圖,不直接 spawn
- 改成:設 `desired`(+ 必要時請 scheduler 指派 node),寫進 DB;**實際 actuation 由 owning node 的
  reconcile 做**。API 形狀不變,回 202 + 後續收斂(本來就已是 `202 Accepted` 風格)。
- collapsed:owning node = 自己 → 下一個 reconcile pass(數百 ms)就起來,體感近今天。
- **保留同步預檢的取捨**:現在 `start()` 會同步做 VRAM/GPU 預檢並回 4xx。拆成非同步後:
  - 方案 A(建議):API 仍對「目標 node」做一次**軟預檢**(讀該 node 的 `nodes.capacity`),容量明顯不足就
    當場回 409;最終仍以 owning node 的本機預檢為準(起不來 → FAILED + 事件 + scheduler 重指派)。
  - 方案 B:完全非同步,UI 以 observed 狀態(starting→failed + last_error)呈現結果。
  - collapsed 可走「同步本機預檢」捷徑(owning node 就是自己)保持今天體感。

### 3.2 autoscaler `_apply` → 寫 desired/assignment
- `start` 動作:不再 `manager.start(key)`;改成「請 scheduler 選 node(或對既有 assignment)+ 寫 desired=running」。
- `stop/sleep/wake`:寫對應 desired;owning node 收斂。
- autoscaler 仍只在 leader 跑(全域決策);它**產意圖**,node 們**執行**。

### 3.3 scheduler(3c)加「node 拒絕 → 重指派」回饋
- 現在:`place()` 對「未指派 / 指派到死節點」的 desired-running 實例挑最空的 node。
- 補:owning node 本機預檢失敗(VRAM 不足/起不來達重試上限)→ 在 DB 標記該 (key,node) 為「拒絕/不適配」
  → 下個 scheduler pass 跳過該 node、改派別台。避免反覆派到同一台失敗。
- node 死(心跳停)→ 既有死節點重指派邏輯接手 → 新 node 收斂起來。

### 3.4 reconcile loop:un-leader-gate + ownership-scoped
- 從 `_on_acquire` 移到 lifespan(每 node 跑)。
- 每 pass:`foreign = foreign_assignments()`(已含存活判斷與自癒);**只對 owned 實例**做 §1 的收斂 +
  回填 observed/live(回填已 ownership-gated)。
- `trigger_router_reload` / prometheus targets:router reload 冪等,任一 node 在自己實例 READY 變化時觸發即可;
  k8s 下 Prometheus 走 k8s SD(或讀 `instances_live`),不再依賴單機 targets 檔。

## 4. 失敗與邊界情境

| 情境 | 處理 |
|---|---|
| node 當機 | 心跳停 → scheduler 重指派其 owned desired-running 到別台 → 新 node 收斂。vLLM 隨 pod 一起死(同 pod),不殘留別台 |
| 指派切換 race(同 key 短暫被兩台起) | actuation 前**再讀一次 assignment 確認仍是自己**;搭配 drain;以 assignment 為單一真相 |
| node VRAM 不足 | 本機預檢擋下 → 標記拒絕 → scheduler 換台(§3.3) |
| split-brain | leader lease(2c)保證單一 scheduler;assignment 保證單一擁有者;observed 各 node 只寫自己 |
| follower 收到 API 寫入 | 寫 desired/assignment(冪等)即可,不在本機 spawn → 由 owning node 執行(順帶完成 3d 説的「任一副本可受寫」)|

## 5. 分階段執行（collapsed-first、每步單機 0 行為改變）

| 子階段 | 產出 | 單機 collapsed? | 風險 | 狀態 |
|---|---|---|---|---|
| **A** 把 reconcile 的「desired→observed 收斂(start/stop/sleep/wake)」抽成一個函式 | 邏輯就位,仍 leader 跑、行為不變 | ✅ | 中 | ✅ 已完成 |
| **B** un-leader-gate reconcile(每 node 跑)、ownership-scoped | follower 開始收斂自己擁有的(單機沒有 follower → 不變) | ✅ | 中高(改執行模型) | ✅ 已完成 |
| **C** API/autoscaler 改寫 desired/assignment(非同步收斂)+ 保留軟預檢 | 寫入與執行解耦 | ✅(owning=self) | 高(API 時序/預檢語意) | ⬜ |
| **D** scheduler 加 node 拒絕回饋 + 重指派 | 放置自我修正 | — | 中 | ⬜ |
| **E** 真多 GPU 主機 live 驗:並行起模型、node failover 接管 | 真並行多節點 | — | 需實體多機 | ⬜ |

> **A+B 已完成**([reconciler.py](../apps/backend/app/llmops/reconciler.py) `converge_desired` +
> [main.py](../apps/backend/app/main.py) 迴圈拆分):reconcile/actuation + gpu-poll 移到 lifespan(每 node 跑);
> scheduler / autoscaler / load-monitor / prune 留 leader-only。`converge_desired` 對 owned(非 foreign)實例做
> desired→observed 收斂(STOPPED→start、live→stop、ready→sleep、sleeping→wake;FAILED 留給 `_process_restarts`)。
> 單機 collapsed 行為不變(唯一 node=leader=全擁有);全測 409 綠 + live collapsed 起模型→READY 驗過。
> 多 node 收斂以 fake 多 node unit test(foreign 排除等)覆蓋。剩 C/D(寫意圖解耦 + 排程拒絕回饋)。
>
> **E 大部分可在單機驗(已做)**:原以為要實體多機,實際上**單機跑 2 個 backend 容器(vLLM image +
> SGLang image)共享一顆 Postgres + 一個 router + 一張 GPU** 就能驗證 per-node ownership / 跨容器路由 /
> follower 自我修復。Live 驗證結果:兩個不同引擎的模型各自被「對的 node」起起來、各自 backfill 到
> `instances_live`(`mixed-vllm-backend:8002`/vllm-node、`mixed-sglang-backend:8100`/sglang-node)、**同一個
> router** 路由到兩者(`2+2=4` 走 vLLM、`3+3=6` 走 SGLang)。期間 vLLM 首次啟動因並發競爭逾時 FAILED,
> **由 follower(vllm-node)自己的 reconcile loop auto-restart 回 READY** —— 直接證明 B 解除 leader 綁定後
> follower 真的會 actuate 自己那份。SGLang 跨容器路由靠新加的 `LLMOPS_VLLM_BIND_HOST` bind-host 支援。
> 真「並行多 GPU 加速」仍需實體多卡,但**功能正確性已單機驗完**。

> 每一步:**單機先過既有全套測試 0 退化**、SQLite + Postgres 雙驗;多 node 邏輯以 fake 多 node 寫 unit test。
> A→D 都能在單機完成且行為不變;**E 一定要有第二台 GPU 主機**才驗得起來。

## 6. 維持不變（承諾）

- 單機 `docker compose up` / Helm 單 backend:行為與今天逐位元一致(唯一 node = leader = 全擁有 = 全收斂)。
- Phase 1/2/3a–3f 的優雅排空、leader 選主、Postgres、遷移、健康探針、可路由綁定、Helm 全部保留沿用。

## 7. 一句話總結

剩下的不是「再加功能」,而是**把 actuation 從「leader 一台做」變成「每台做自己被指派的那份」**:寫入端只寫
desired+assignment,各 node 的 reconcile 迴圈各自收斂。地基(ownership 判斷、排程、可路由綁定、observed 入庫)
都已就緒;這步是把 reconcile 迴圈解除 leader 綁定 + 把 start/stop 從「同步 spawn」改成「寫意圖」。**單機可做到
collapsed-first 不變,但真正的價值與驗證要有實體多 GPU 主機。**
