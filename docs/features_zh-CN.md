# 功能特色（詳細）

> [English](features.md)

## 模型管理

- 基於 vLLM 的多模型、多實例管理（LLM、Embedding、Reranker）。
- **可插拔推理引擎 — vLLM 與 SGLang。** 每顆模型宣告 `engine`（預設 `vllm`）；*新增模型*
  對話框有引擎選擇器，啟動參數會依引擎翻譯。混合部署下，一個 vLLM backend 與一個 SGLang
  backend 共用同一個 router／控制台，並由 **engine-aware 排程器**把每顆模型擺到「跑得動它」
  的 backend 上。SGLang 支援路由、推理、生命週期、runtime LoRA、metrics + autoscaling（沒有
  sleep mode，autoscaler 對它退化成 `ready ↔ stopped`）。見
  [mixed-engine-deployment_zh-CN.md](mixed-engine-deployment_zh-CN.md) 與
  [multi-backend-engine-design_zh-CN.md](multi-backend-engine-design_zh-CN.md)。
- 每個實例獨立的生命週期（啟動/停止），具即時狀態機
  （`stopped → starting → ready → failed/stopping`），由 reconciler 從「進程存活 +
  `/health` 探測」推導真實狀態。
- **在前端貼上 `vllm serve …` 指令即可新增模型** — 解析成可編輯表單，以動態 *overlay*
  疊加，**不動手寫的 `config.yaml`**；router 會熱重載（`POST /reload`），新模型端到端
  可被路由。
- 負載感知路由：router 自動選擇負載最低的實例（依運行中／等待中請求 + KV 快取使用率加權）。

## 可靠性

- **VRAM 預檢防呆** — 啟動前估算顯存，可能 OOM 就擋下，並提供一鍵 *Force start* 覆寫。
- **GPU 自動擺放** — 未指定 `cuda_device` 的實例會自動擺到剩餘顯存最多的 GPU。
- **失敗自動重啟** — managed 模型崩潰後以指數退避自動重啟（可設次數，恢復健康後重置）。
- **Sleep mode（暖待命）** — 群組設 `enable_sleep_mode` 後多一個 `sleeping` 狀態：vLLM
  level-1 睡眠釋放副本 VRAM，但秒級即可喚醒（非冷啟）。router 會跳過睡眠中的實例；
  sleep/wake 為 admin 端點，也是 autoscaler 的暖待命階。
- **自動擴縮** — 每群組由 backend 保留 `min_ready` 暖機副本，依佇列深度
  （`waiting_per_replica`）擴容（優先喚醒睡眠副本、其次冷啟）到 `max_ready`；閒置時逐階
  縮回 `ready → sleep → stop`（不對稱冷卻）。config.yaml 或控制台皆可設定（開啟後該群組
  手動啟停停用）。見 [autoscaling-design_zh-CN.md](autoscaling-design_zh-CN.md)。
- **跨模型 fallback** — 群組可設 `fallback` 鏈；該群組所有實例不可用時，router 改路由到
  下一個相容群組（回應反映實際服務的模型），而非直接失敗。
- **Router 健康探針** — `GET /health`(liveness,永遠 200)與 `GET /ready`(readiness,
  config 載入 + 啟動完成才 200,否則 503),皆免 auth,供 k8s 探針 / 負載器在啟動 / reload
  期間正確探活。
- **優雅排空(graceful drain)** — 停模型 / 縮容前,backend 先請 router 對該 instance 停送
  新請求,等 in-flight 跑完(上限 `LLMOPS_DRAIN_TIMEOUT`,清空即提早結束)才殺進程,避免滾動
  更新 / 停機切斷進行中的請求。見 [ha-design_zh-CN.md](ha-design_zh-CN.md)。
- **重啟自動恢復(desired 重放)** — 每顆模型「該不該跑」的意圖會持久化;backend 重啟後會把
  原本在跑、卻因崩潰/重啟而停掉的模型自動拉回(`LLMOPS_REPLAY_DESIRED`,預設開)。
- **控制平面 HA(選用)** — 把共用 store 指向 Postgres(`LLMOPS_DB_URL`)即可跑多個後端副本:
  狀態、設定與 desired 意圖都在 DB,並以 **leader 租約**確保只有一個副本跑單例背景迴圈
  (reconcile / autoscale / prune);leader 掛掉時,待命副本會在約 `LLMOPS_LEADER_LEASE_TTL`
  秒內搶下過期租約接手。單機 SQLite 仍是零設定預設。見
  [ha-phase2-design_zh-CN.md](ha-phase2-design_zh-CN.md)。

## 觀測性

- 透過 Server-Sent Events 即時更新狀態（免輪詢）。
- **系統拓撲圖**（Vue Flow）— Clients → Router → 模型群組／Embedding → GPU 的即時
  mission-control 圖，含流動的流量邊、GPU 擺放邊與控制平面；節點可點擊下鑽。
- **Router 負載平衡視圖** — 動畫扇形圖呈現每個副本的實際流量佔比，以及 router 下一個會
  選的實例。
- **Grafana 監控**（內建）— 見 [monitoring_zh-CN.md](monitoring_zh-CN.md)。含一個 **Autoscaling**
  dashboard（副本階梯、佇列 vs 門檻、擴縮事件、VRAM-blocked），嵌入為 Monitoring 分頁，
  資料來自 backend 的 `/metrics`。
- 每群組即時負載（`GET /api/load`）：ready/asleep/stopped 副本數 + 佇列深度，並在每張模型卡
  顯示佇列／睡眠徽章。
- 每模型用量（次數、錯誤率、p50/p95 延遲、tokens）、請求日誌、狀態轉移事件時間軸。
- GPU／CPU／記憶體監控，以及 GPU 進程清單。
- **生命週期告警** — 離散的模型事件（崩潰、退避用盡、復原）推到 Slack／Discord／通用
  webhook，含 severity 門檻與 per-model 去重，避免崩潰迴圈洗版。用 `LLMOPS_ALERT_*` 環境變數
  或 admin「通知」頁（含一鍵測試）設定；與 Grafana 指標告警互補。見
  [alerting-design_zh-CN.md](alerting-design_zh-CN.md)。

## Playground

- OpenAI 相容的 **chat（串流）**、completions、**embeddings**、**reranking**，直接經由 router。
- **思考（reasoning）顯示** — 模型搭配 vLLM reasoning parser 時，`reasoning` 串流會顯示
  在答案上方的可摺疊「思考過程」區塊。

## 壓測與評測（evalscope）

- **壓測**（`/benchmark`）— 並發 sweep、到達率 open-loop、多輪、**SLA 自動調優**，以及
  **embedding／rerank** 吞吐與單請求**速度基準**；每次執行為獨立子進程，含即時圖表、
  run 比較、完整 evalscope HTML 報告。見 [evalscope_模型壓測整理.md](evalscope_模型壓測整理.md)。
- **準確度／品質評測**（`/eval`）— **30+ 個基準資料集**，依能力分組（基線、知識進階、
  中文、推理、數學、多語言、**工具調用**、**長上下文**、程式碼、需裁判的問答）：
  MMLU/ARC/GSM8K/IFEval、C-Eval/C-MMLU、GPQA/MMLU-Pro、AIME、HumanEval、
  ToolBench/General-FunctionCall、Needle-in-a-Haystack…
  見 [evalscope_LLM評測集整理.md](evalscope_LLM評測集整理.md)。
  - 每資料集分數、**run 對 run 的比較表**（每列標出最高分）、互動式 HTML 報告。
  - **裁判模型（LLM-as-judge）** 給自由問答評分 — 可選自家部署的模型（經 router）或外部
    OpenAI 相容 API。
  - **進階 `dataset_args`** — few-shot 數 + 依資料集的原始覆寫（子集選擇等）。
  - 防呆：需裁判的資料集會強制設定裁判；長上下文與真實工具調用資料集會提醒模型前提
    （夠大的 `max_model_len`、vLLM tool parser）。

## 資料庫

- **模型庫**（`/library`）— 在 UI 掃描／預下載／刪除 HF 權重，含即時下載進度。
- **資料集庫**（`/datasets`）— 預先下載壓測與評測資料集到共用 ModelScope 快取，執行時
  就不會卡在首次下載。
- **工具調用設定助手** — 模型編輯器把模型家族對應到正確的 vLLM `tool_call_parser`
  （Qwen→`hermes`、Qwen3-Coder→`qwen3_xml`、Llama→`llama3_json`/`llama4_pythonic`…），
  一鍵帶入。見 [vllm_auto_tool_整理.md](vllm_auto_tool_整理.md)。
- **LoRA** — 見 [vLLM_LoRA_部署整理.md](vLLM_LoRA_部署整理.md)。

## 使用體驗與安全

- 明暗雙主題、資訊密集的「控制室」介面。
- **多使用者 RBAC** — 具名 **operator 憑證**帶角色（`viewer` ⊂ `operator` ⊂ `admin`）：
  viewer 唯讀、operator 操作模型（啟停／擴縮／評測…）、admin 另可管理使用者與金鑰。env
  `LLMOPS_ADMIN_TOKEN` 保留為永遠 admin 的啟動／救援後門；未建立任何使用者時 API 以
  local-dev 開放——既有單一 token 與 dev 部署完全不受影響。admin 可就地改使用者角色或
  重新產生 token；登入者顯示 DiceBear 頭像與角色徽章。見
  [rbac-audit-design_zh-CN.md](rbac-audit-design_zh-CN.md)。
- **SSO 登入(OIDC)** — 可用公司 IdP(Google / Entra / Okta / 任何 OIDC)登入,
  IdP 的 email / groups 映射成角色;session 走自簽 HttpOnly cookie。人走 SSO、機器 / CI 仍用
  token,兩者並存於同一授權模型。設定 OIDC 即關閉 open-dev 後門、使驗證成為必要。預設關閉
  (未設 issuer 行為不變)。見 [sso-design_zh-CN.md](sso-design_zh-CN.md)。
- **稽核日誌** — 每筆控制平面變更（誰／做什麼／何時／結果，body 已脫敏）都會記錄並可瀏覽
  （依操作者／動作、時間範圍篩選，可分頁），與推理 request log、狀態轉移時間軸彼此區隔；
  保留筆數有上限，每小時裁剪。
- **API 金鑰管理** — 發行／撤銷用於 router 推理的金鑰，含 per-key 用量歸屬、每分鐘
  **速率上限**，以及在 router 強制的 **token 額度**（總量／每日／每月，超額回 429）。
  登入的 operator／admin token 也能直接用 Playground 推理（viewer 不能推理）。
- **成本 dashboard** — 每模型定價表（每 100 萬 tokens 的輸入／輸出價,admin 可改）把
  `prompt`／`completion` token 用量換算成 **成本**;「成本」頁顯示總花費與 per-model／
  per-key 明細,可選時間範圍;未定價模型用預設價並標示。`/api/cost/*`。
- **設定版本化 / 匯出匯入** — 動態模型 overlay（所有 runtime 改動所在；`config.yaml` 唯讀）
  可一鍵**匯出**成可攜檔備份、**匯入**整份還原（先 schema 驗證；有 instance 在跑會擋下，
  可強制）。每次會改 overlay 的請求都**自動快照**並歸屬操作者；「設定版本」頁可看歷史、
  並排 **diff** 與**一鍵回滾**到任一版（回滾本身也記成新版本,可前滾）。匯出 operator、
  匯入／回滾 admin。見 [config-versioning-design_zh-CN.md](config-versioning-design_zh-CN.md)。
