# 混合引擎部署（vLLM + SGLang 同一個 fleet）

> 一個 vLLM backend + 一個 SGLang backend 共享**一顆 Postgres、一個 router、一個 dashboard**。
> 每個 backend 只跑自己引擎的 image,leader 的 engine-aware 排程把每個模型放到能跑它的 node 上;
> 不在對的 node 上的控制動作會被「延後」給擁有者執行(HA Phase 7C)。**單機就能跑**(兩個容器共享一張卡)。
> 已 live 驗證:從 dashboard 新增一個 SGLang 模型 → 自動跑到 SGLang backend → 經同一個 router 路由。

## 為什麼要分兩個 backend

vLLM 和 SGLang 各自死釘不同的 torch/CUDA/flashinfer,塞同一個 image 會打架。launcher 是**在 backend 容器內
spawn 引擎子行程**,所以「一個 backend 能跑哪些引擎 = 它 image 裡裝了什麼」。因此每個引擎一顆 image,各跑一個
backend node。見 [multi-backend-engine-design](multi-backend-engine-design_zh-CN.md) §5。

## 快速啟動

```bash
make up-mixed      # build + 起 postgres + vLLM backend + SGLang backend + router + dashboard
make logs-mixed    # 看 log
make down-mixed    # 收掉
```

需要 `deploy/.env`(admin token、HF token、session secret 等,跟一般 `make up` 同一份)。

埠(可用環境變數覆蓋):dashboard `:8884`、router `:8887`、vLLM backend API `:5071`、SGLang backend API `:5072`。

## 怎麼用 SGLang 模型

1. 開 dashboard(`http://localhost:8884`),**Add Model** → **Inference engine** 選 `sglang`,填 model_tag,送出。
   (或直接打 API:`POST /api/models`,`model_config.engine = "sglang"`。)
2. 按 **Start**。即使你的 dashboard 連到的是 vLLM backend 也沒關係:它會把「意圖」寫進共享 store,
   排程器把模型指派到 **SGLang node**,SGLang backend 自己把它起起來(overlay 會自動同步到每個 node)。
3. 用 `model: <group>` 對 router(`:8887/v1/...`)發推理即可,router 會路由到 SGLang backend。

> SGLang 沒有 sleep(autoscaler 對它退化成 ready↔stopped);runtime LoRA / metrics+autoscaling 都支援。

## 運作原理(對應程式)

| 機制 | 說明 | 程式 |
|---|---|---|
| **node 宣告引擎** | `LLMOPS_NODE_ENGINES=vllm` / `sglang`(每個 image 設一個)寫進 `nodes.engines` | [node_agent.py](../apps/backend/app/llmops/node_agent.py) |
| **engine-aware 排程** | 每個 desired-running 模型指派到「能跑它引擎」的最空 node;放錯 node 會搬走 | [scheduler.py](../apps/backend/app/llmops/scheduler.py) `place()` |
| **寫意圖(Phase 7C)** | 在不能跑該引擎的 node 上 start/stop → 只寫 desired,不本機 spawn;擁有者收斂 | [manager.py](../apps/backend/app/llmops/manager.py) `_defer_to_owner` |
| **per-node 收斂** | 每個 node 各自把「指派給它」的模型起/停(不是只有 leader) | [reconciler.py](../apps/backend/app/llmops/reconciler.py) `converge_desired` |
| **overlay 同步** | dashboard 動態加的模型透過 store 傳播到每個 node 的 registry | [manager.py](../apps/backend/app/llmops/manager.py) `sync_overlay_from_store` |
| **跨容器路由** | 各 node 用 `LLMOPS_VLLM_BIND_HOST=0.0.0.0` 綁可路由位址,寫進 `instances_live`,router 依此連 | [launchers.py](../apps/backend/app/llmops/launchers.py) / [metrics_poller.py](../apps/router-server/src/llm_router/metrics_poller.py) |
| **監控(對標主 compose)** | Prometheus + dcgm-exporter + node-exporter;各 backend 把 ready 實例以**可路由位址**寫進共享 `mixed-sd` volume 的 file_sd 檔(`build_targets(node_host=...)`),Prometheus glob `/etc/prometheus/targets/*.json` 一起抓;Grafana datasource 指向 `mixed-prometheus` | [prometheus.mixed.yml](../deploy/prometheus.mixed.yml) / [prometheus_targets.py](../apps/backend/app/services/prometheus_targets.py) |

## 監控指標命名(重要)

- **vLLM** 用傳統 Prometheus text 格式 → 指標名保留冒號(`vllm:num_requests_running`),官方 vLLM dashboard 照用不變。
- **SGLang** 用 **OpenMetrics** 格式(名稱不允許冒號)→ Prometheus 入庫時把 `:` 正規化成 `_`
  (`sglang:num_running_reqs` → `sglang_num_running_reqs`)。所以 SGLang Grafana dashboard 一律查 `sglang_*`。
- (router 的 autoscaler 解析的是**原始 endpoint 文字**(冒號名),不經 Prometheus,所以不受影響。)

## 限制

- **GPU 容量**:單機單卡上,兩個引擎的模型 + 各自的 KV 都要塞進同一張卡。8GB 卡同時跑兩個小模型會很緊
  (調低各模型的 `gpu_memory_utilization` / `max_model_len`)。真「並行多卡加速」需要實體多卡。
- 自動 placement 只在 Postgres(HA)模式生效;SQLite 單機是 collapsed(一個 node 跑全部),行為不變。
