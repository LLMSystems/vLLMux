# 控制平面 / 節點代理拆分（HA Phase 3）設計

> 路線圖 C-3 的最後一步。上承 [ha-design_zh-CN.md](ha-design_zh-CN.md) §4、
> [ha-phase2-design_zh-CN.md](ha-phase2-design_zh-CN.md)(2a–2e 已完成)。
> **這是 Phase 2 之後的大重構**:把「backend 用 subprocess 在自己的 network namespace 起 vLLM」
> 這個**單機執行模型**拆成「控制平面 + 每節點一個代理」,解鎖**真・多節點 + 多副本 inference HA**。
> 對齊現有程式碼:`apps/backend/app/llmops/process.py`(`subprocess.Popen`)、`registry.py`
> (in-memory)、`reconciler.py`(從進程+health 推導狀態)、`manager.py`、`deploy/docker-compose.yaml`
> (`router` 用 `network_mode: service:backend`)。

## 0. 設計總則

- **單機零設定不變(collapsed 模式)**:預設「一台機器 `docker compose up`」必須**完全照舊**。
  控制平面與節點代理在單機可**跑在同一個 process/容器**裡(collapsed),行為與今天逐位元一致;
  只有要水平擴 / 多節點時才**拆開**(split)。HA 與多節點是**可選部署形態**,不是預設。
- **狀態在 DB,不在某個 process 的記憶體**:Phase 2 已把 store/overlay/desired/lease/draining 入庫;
  Phase 3 再把 **registry 的「觀測狀態」與「實例位址」入庫**,讓任一控制平面副本、任一 router
  都看到同一份真相。
- **desired vs observed 不變**:沿用既有「使用者要什麼(desired)vs 實際如何(observed)」模型 ——
  只是 observed 改由**節點代理回填**到 DB,而非單一 backend 的記憶體。
- **冪等 + best-effort + 不破壞 Phase 1/2**:優雅排空、leader 選主、Postgres、遷移全都保留並沿用。
- **先解耦、再分散**:每個子階段都能在**單機 collapsed 模式**先落地(零行為改變),再啟用多節點。

## 1. 現況與硬限制（Phase 3 要拆的）

| # | 限制 | 依據 | 後果 |
|---|---|---|---|
| 1 | vLLM 綁 backend netns 的 **localhost** | [process.py](../apps/backend/app/llmops/process.py) `subprocess.Popen`;router `network_mode: service:backend` | 別台 / 別 netns 的 router 連不到 vLLM → 無法多 router、無法多節點 |
| 2 | **registry 是 in-memory**,每 backend 一份 | [registry.py](../apps/backend/app/llmops/registry.py) | 多控制平面副本看不到同一份觀測狀態;follower 的 API 動的是自己的 registry |
| 3 | backend **自己 spawn vLLM**,只能管自己這台 | process.py + manager | 無跨主機;leader 一台,follower 那台沒有 vLLM |
| 4 | reconciler 從**本機進程 + health** 推導狀態 | [reconciler.py](../apps/backend/app/llmops/reconciler.py) | 狀態判定綁在「能看到本機進程」的那個 process |
| 5 | 硬 kill backend 時 vLLM 子進程可能**殘留佔 VRAM** | `start_new_session=True` 進程組 | 故障接管後新 leader 可能拿不到 VRAM |
| 6 | VRAM 預檢 / GPU 擺放是**單機** | manager `_vram_preflight` / 擺放 | 無法跨節點排程 |

> Phase 2 已解的(不重做):store/overlay/desired/lease/draining 入庫、leader 選主、優雅排空、
> 健康探針、Postgres 遷移。

## 2. 目標架構（拆分後）

```
                         ┌──────────── Postgres(共享真相)────────────┐
                         │ desired / observed / instances(位址)/ lease │
                         └───────▲───────────────▲──────────────▲──────┘
                                 │(寫 desired)    │(回填 observed) │(讀實例位址)
        ┌────────────────────────┴──┐   ┌─────────┴─────────┐   ┌──┴───────────┐
   LB → │ control-plane × N         │   │ node-agent(每台   │   │ router × K   │ ← LB
        │  · stateless API(dashboard│   │  GPU 主機一個)    │   │  · 無狀態     │
        │  · leader = 排程器         │   │  · 認領分配到本節點 │   │  · 讀實例位址 │
        │    (決定哪顆在哪台起幾個)  │   │    的實例           │   │    over 網路  │
        │  · 不 spawn 進程            │   │  · 真的 spawn vLLM  │   │    路由       │
        └────────────────────────────┘   │  · 探活 + 回填狀態  │   └──────────────┘
                                          │  · vLLM listen 在   │
                                          │    可路由位址       │
                                          └─────────┬───────────┘
                                                    └──→ vLLM(node IP:port,可被 router 連)
```

- **控制平面(control-plane)**:無狀態 API(可水平擴 + 放 LB),其中 leader(沿用 2c)是**排程器**
  —— 決定 desired 放置(哪顆模型、起幾個、在哪個節點),寫進 DB;**不直接 spawn 進程**。
- **節點代理(node-agent)**:每台 GPU 主機一個。認領「分配到本節點」的實例,實際 `spawn_process`、
  探活、把 observed 狀態 + 實例位址回填 DB。**`Popen` handle 留在本機**(故障時由 agent 自己 reap,
  解限制 #5)。
- **router**:沿用 2d(多副本安全),從 DB 讀**實例→位址**對映,over 網路路由(解限制 #1)。
- **單機 collapsed**:control-plane + node-agent 同 process,vLLM 仍可 localhost —— 等於今天。

## 3a. vLLM 可路由位址 + 實例註冊（解耦的鑰匙，先做）

**目標**:vLLM 不再只綁 localhost;實例的**可連位址**進 DB,router 從 DB 讀位址路由 ——
這一步就讓「多 router」與「跨節點」成為可能,且**單機仍可用 localhost**。

### 做法
- 新 `instances_live(key, node_id, host, port, state, updated_at, …)` 表(或擴充既有觀測狀態入庫):
  記每顆**正在跑**的實例的可連位址 + 觀測狀態。
- 起 vLLM 時 bind 在**節點可路由位址**(node IP,collapsed 模式仍可 `127.0.0.1`);agent 把
  `(key, node host:port)` 寫進 `instances_live`。
- **router 改讀 DB 的實例位址**(而非只認 config 的 localhost):router 的 metrics poller(已每秒跑)
  順手刷新 `instances_live` → 路由候選 = DB 裡 ready 的實例位址。**移除 `network_mode: service:backend`
  的硬耦合**(改成 router 用網路位址連 vLLM)。
- backend 的 `/metrics`/load 訊號改以 DB 的實例清單為準。

### 測試 / 風險
- 測:agent 註冊/反註冊實例位址;router 從假 DB 讀位址選路;collapsed 模式 localhost 仍可。
- 風險:位址過期/殘留(用 TTL/heartbeat,如 lease/draining 的自癒);config-defined 與 live 位址
  的合併(config 仍是「該有哪些」,instances_live 是「現在在哪」)。

## 3b. 節點代理抽出（把 spawn 從控制平面拆走）

**目標**:把「spawn/kill 進程 + 探活 + 回填狀態」從 backend 抽成獨立的 **node-agent**;控制平面只寫
desired,不再直接碰進程。**單機先 collapsed**(agent 與控制平面同容器,零行為改變)。

### 做法
- 新 `apps/node-agent/`(或 backend 內一個可獨立啟動的 mode):
  - 認領 `assignments(key, node_id)`(控制平面排程器寫;或 agent 自取「分配給本 node 的」)。
  - 對認領到的實例跑**既有 reconciler 邏輯**(進程存活 + `/health` → observed),回填 DB +
    `instances_live`。`Popen` handle 與 reap 留在 agent 本機(解 #5)。
  - 沿用既有 `launchers`/`process.py`/`reconciler.py`(搬到 agent,邏輯不改)。
- 控制平面的 `manager.start/stop/...` 改成**寫 desired/assignment**,由 agent 去執行(desired→observed
  收斂);API 形狀不變。
- **collapsed 模式**:同 process 內同時跑控制平面 + 一個本機 agent,vLLM localhost —— 等於今天。

### 測試 / 風險
- 測:agent 認領 → spawn(用 FakeProc)→ 回填 observed;控制平面只寫 desired 不 spawn。
- 風險:這是**最大的一塊**(改執行模型);務必 collapsed 模式先過既有全套測試 0 退化,再啟用 split。

## 3c. 跨節點排程

**目標**:多台 GPU 主機時,排程器決定「哪顆模型放哪台、起幾個」。

### 做法
- agent 回報**節點容量**(GPU 數/型號/可用 VRAM,沿用 `gpu_service`)到 `nodes` 表。
- 排程器(leader 控制平面)把 desired 副本**擺到有容量的節點**(VRAM 預檢從單機升級為跨節點);
  autoscaling(Phase 2 已有控制迴圈)改成**跨節點放置**。
- 節點故障(agent 心跳停)→ 排程器把該節點的實例重新指派到別台。

### 測試 / 風險
- 測:擺放挑容量足的節點;節點失聯 → 重指派。
- 風險:分散式排程複雜度高;先做「手動指定節點」再做自動擺放。

## 3d. 控制平面無狀態化 + observed 入庫

**目標**:任一控制平面副本都看到同一份真相;follower 不再動自己的 in-memory registry。

### 做法
- registry 的**觀測狀態改以 DB 為準**(observed 由 agent 回填;控制平面讀 DB 組裝視圖)。in-memory
  僅當快取。
- 寫入(start/stop/編輯)= 寫 desired/assignment 到 DB(冪等);**任一控制平面副本都能受寫**(因為實際
  執行在 agent,衝突由排程器 leader 收斂)—— 順帶解掉 Phase 2c 留的「follower 寫入只動自己 registry」。

### 測試 / 風險
- 測:兩個控制平面副本看到同一 observed;寫 desired 後 agent 收斂。
- 風險:觀測狀態 SSE/即時性(目前 SSE 來自記憶體)—— 改成 DB 變更通知或短輪詢。

## 3e. Router 多副本(收尾)

- 站在 2d(draining 共享、inflight 近似)+ 3a(實例位址入庫)上:router 純無狀態,讀 DB 實例位址,
  多副本放 LB 後面。`/health` `/ready`(C-1)做探活;`/ready` 可選擇反映「有可路由實例」。

## 3f. 部署（k8s）

- **k8s 為主要多節點載體**:control-plane Deployment(N 副本 + leader 選主)、node-agent DaemonSet
  (每 GPU 節點一個 Pod,`hostPID`/裝置掛載)、router Deployment(K 副本)、Postgres(StatefulSet 或
  託管)、各自 Service + Ingress/LB。vLLM 由 agent 在節點起、以 Pod/Service 位址暴露。
- 滾動更新 + Pod 級優雅排空(沿用 Phase 1 drain);節點層級隔離(一個 Pod 掛不影響別台)。
- 提供 Helm chart / manifests + `collapsed` 與 `split` 兩種部署範例。

---

## 從現況演進的路徑（不破壞單機）

1. **3a** 先把實例位址入庫、router 改讀 DB 位址 —— 單機仍 localhost,零行為改變,但解開 netns 耦合。
2. **3b** 把 agent 抽出但**預設 collapsed**(同容器)—— 既有測試 0 退化後,才允許 split。
3. **3d** observed 入庫 → 控制平面可多副本。
4. **3c** 跨節點排程 → 真多節點。
5. **3e/3f** router 多副本 + k8s 部署收尾。

> 每一步都「單機先 collapsed、零行為改變」,確認穩了再啟用 split/多節點 —— 風險可控、可分批上線。

## 維持不變(承諾)

- 單機 `docker compose up`:control-plane + 一個 collapsed agent + 一個 router,SQLite 或 PG 皆可,
  **與今天逐位元一致**。
- Phase 1/2 的優雅排空、leader 選主、Postgres、遷移、健康探針全部保留沿用。

## 風險與取捨

- **最大工程量、最高風險**:改的是核心執行模型(subprocess→agent)。務必 collapsed-first + 既有
  全套測試 0 退化把關。
- **分散式排程 / 節點故障 / 網路分區**:是另一個量級的問題;先「手動指定節點」再自動擺放,先單一
  排程器 leader(已有 lease)再談更複雜的協調。
- **殘留進程 / VRAM**:由 agent 擁有 `Popen` + reap 解決(節點本機負責清理),比現在「backend 掛了
  子進程孤兒」乾淨。
- **何時做**:**等有實際多 GPU 主機 / 多副本 inference 需求再啟動**。Phase 2 已能「單主機池上的控制
  平面 HA(待命接管)」;Phase 3 才是「多節點 + 多副本 inference」。

## 分階段執行（建議順序）

| 子階段 | 產出 | 單機先 collapsed? | 風險 | 狀態 |
|---|---|---|---|---|
| **3a** 實例位址入庫 + router 讀 DB | 解開 netns 耦合 | ✅ localhost 不變 | 中 | ✅ 已完成（live 驗） |
| **3b** node-agent 抽出 | spawn 與控制平面分離 | ✅ 同容器 | **高**(核心執行模型) | ✅ 已完成（3b-1 節點註冊/assignments；3b-2 ownership-gated actuation + 心跳自癒） |
| **3d** observed 入庫 + 控制平面無狀態 | 控制平面可多副本 | ✅ | 中高 | ✅ 已完成（3d-1 observed 回填；3d-2 follower 讀 DB 組裝視圖，**雙副本 live 驗**） |
| **3c** 跨節點排程 | 真多節點 | — | 高 | ✅ 已完成（greedy by free VRAM + 死節點重指派；多節點為 unit test，單機 no-op） |
| **3e** router 多副本 | router 水平擴 | — | 低 | ✅ 已完成（router 無狀態、`/ready` 報 routable 數，**第二副本 live 驗**） |
| **3f** k8s 部署 | 一鍵多節點 | — | 中 | ✅ Helm chart 已完成（`deploy/helm/vllmux`,lint + 雙 profile render 過;無 cluster 不能 live 裝） |

> 起手式建議:**3a**…(已完成)。

## 實作狀態（2026-06）

3a–3e **全部落地**、SQLite + Postgres 雙跑測試通過、單機 collapsed 行為逐位元不變,並做了
**雙 backend 副本**(follower 從 DB 讀 fleet)與**雙 router 副本**(共享 store 讀路由)的 live 驗證。

**netns 解耦也完成了**:`LLMOPS_VLLM_BIND_HOST`(opt-in,預設空=綁 localhost 不變)讓 vLLM 綁可路由
介面(如 `0.0.0.0`),搭配 `LLMOPS_NODE_HOST`(廣播給 router 連的可路由位址,寫進 `instances_live`)。
只改 `--host` 綁定位址;本機健康探針與 record 仍走 localhost。**已 docker live 驗**:bind=0.0.0.0 +
node_host=backend 時,vLLM 廣播 `backend:8002`,**別的 netns 的容器連得到 `/health`,而且一個獨立 netns
(不共享 backend)的第二個 router 能真的跑通 `/v1/chat/completions`** —— 即「跨 netns inference」這件
原本做不到的事現在 work 了。

**3f Helm chart 已完成**(`deploy/helm/vllmux`):backend StatefulSet(穩定 pod 身分 = `LLMOPS_INSTANCE_ID`、
GPU、`bindAll`+pod IP 廣播)、router Deployment(無狀態水平擴 + `/health` `/ready` 探針)、bundled Postgres
StatefulSet(或外部 DSN)、Service/Ingress,附 `values-collapsed` / `values-split` 兩種 profile。已 `helm lint`
+ 雙 profile render + YAML 結構驗證通過(env 順序、downward API、DSN 都對);**沒有 k8s cluster 所以無法 live 裝**。

**唯一還沒做的**:真・多節點 inference —— 需要**真實的多 GPU 主機**才測得起來、才有意義(單機 GPU-less
副本起不了模型),且 backend 多副本目前是「控制平面 HA(leader + 暖備接管)」而非「並行 GPU 工作節點」
(每個 backend pod 各跑自己那份 fleet 需要 per-node actuation)。**完整設計藍圖**見
[ha-per-node-actuation-design_zh-CN.md](ha-per-node-actuation-design_zh-CN.md)(collapsed-first 分階段、
各元件改法、失敗情境;等有實體多機環境再實作)。

換言之:**控制平面 HA(狀態外移、leader、雙副本讀寫、跨節點排程、router 水平擴、vLLM 可路由綁定、
k8s 打包)全部完成且驗過**;只差「真多 GPU 主機 + per-node actuation」這層 —— 那要有實體多機環境才推進。

> ⚠️ 綁 `0.0.0.0` 會把 vLLM 暴露到 localhost 以外,只在可信/內網環境使用。

> **節點身分建議**:多節點部署請給每台設**穩定**的 `LLMOPS_INSTANCE_ID`(預設 `hostname:pid` 會
> 隨重啟改變)。assignments 已對「指派到已死節點」做自癒回收,但穩定 id 讓 nodes/leader/assignment
> 更易讀、failover 更乾淨。
