# 功能特色（詳細）

> [English](features.md)

## 模型管理

- 基於 vLLM 的多模型、多實例管理（LLM、Embedding、Reranker）。
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

## 觀測性

- 透過 Server-Sent Events 即時更新狀態（免輪詢）。
- **系統拓撲圖**（Vue Flow）— Clients → Router → 模型群組／Embedding → GPU 的即時
  mission-control 圖，含流動的流量邊、GPU 擺放邊與控制平面；節點可點擊下鑽。
- **Router 負載平衡視圖** — 動畫扇形圖呈現每個副本的實際流量佔比，以及 router 下一個會
  選的實例。
- **Grafana 監控**（內建）— 見 [monitoring_zh-CN.md](monitoring_zh-CN.md)。
- 每模型用量（次數、錯誤率、p50/p95 延遲、tokens）、請求日誌、狀態轉移事件時間軸。
- GPU／CPU／記憶體監控，以及 GPU 進程清單。

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
- **管理員權杖控管**控制操作（啟動／停止／新增／編輯／移除），以及 **API 金鑰管理** —
  發行／撤銷用於 router 推理的金鑰，並在請求日誌中做 per-key 用量歸屬。
