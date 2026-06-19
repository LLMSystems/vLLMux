# vLLM + Grafana 監控方案整理

查詢日期：2026-06-19

## TL;DR

- vLLM **原生支援的是 Prometheus 相容的 `/metrics` endpoint**；Grafana 監控是透過 Prometheus 接上去，不是 vLLM 直接內建 Grafana UI。這一點是我根據官方文件的 exposed metrics、Prometheus/Grafana 範例，以及 production-stack 文件整理出的結論。
- 目前最成熟的路線有 3 條：
  1. **官方單機範例**：`vLLM + Prometheus + Grafana`，適合 PoC / 單機驗證。
  2. **自建通用正式方案**：`vLLM + Prometheus + Grafana + node_exporter + dcgm-exporter`，適合 VM / bare metal / Docker 正式環境。
  3. **Kubernetes 正式方案**：`vLLM production-stack Helm + kube-prometheus-stack + ServiceMonitor + Grafana dashboards`，這是官方最完整的 K8s 參考解。
- 如果你要做真正的正式監控，**不要只看 vLLM 自身 metrics**。至少還要補：
  - `dcgm-exporter`：GPU 利用率、記憶體、溫度、功耗
  - `node_exporter`：CPU、RAM、磁碟、網路
  - 視需要再補 logs / traces

## 1. 官方現況：vLLM 到底有沒有「支援 Grafana」？

### 有，但型態是「Prometheus + Grafana」生態整合

官方文件明確寫到：

- vLLM 會在 OpenAI-compatible API server 上暴露 `/metrics`
- 指標採 **Prometheus-compatible** 格式
- 官方提供：
  - `Prometheus and Grafana` 範例
  - `Monitoring Dashboards` 範例
  - `production-stack` 的 Grafana dashboard 與 K8s observability 方案

所以正確說法不是「vLLM 內建 Grafana」，而是：

> vLLM 原生暴露 Prometheus metrics，並且官方提供了 Grafana dashboard 與整合範例。

這已經算是很成熟、很標準的監控方式。

## 2. 成熟方案地圖

| 方案 | 成熟度 | 適用場景 | 關鍵元件 | 評價 |
| --- | --- | --- | --- | --- |
| 官方 `Prometheus and Grafana` 範例 | 高 | 單機、PoC、驗證 metrics | vLLM, Prometheus, Grafana | 最快上手 |
| 官方 `Monitoring Dashboards` JSON + 自建監控堆疊 | 高 | Docker、VM、bare metal 正式環境 | vLLM, Prometheus, Grafana | 最通用，推薦 |
| `production-stack` Helm + `kube-prometheus-stack` | 很高 | Kubernetes 正式環境 | vLLM stack, Prometheus Operator, Grafana | 官方 K8s 參考解 |
| vLLM metrics + `dcgm-exporter` + `node_exporter` | 很高 | 所有正式環境 | vLLM + GPU/host metrics | 這才是完整可營運方案 |
| OpenTelemetry tracing | 中 | 需要 trace 時 | vLLM + OTel collector/Jaeger/Tempo | 補充型，不是主監控主線 |

## 3. 建議的整體監控架構

```text
                 +----------------------+
                 |      Client/App      |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 |      vLLM Server     |
                 |   /v1/*   /metrics   |
                 +----+------------+----+
                      |            |
                      |            |
                      |            +-------------------+
                      |                                |
                      v                                v
          +----------------------+        +----------------------+
          |      Prometheus      |        |    dcgm-exporter     |
          | scrape vLLM metrics  |<-------|    GPU telemetry     |
          +----------+-----------+        +----------------------+
                     ^
                     |
                     |
          +----------+-----------+
          |     node_exporter    |
          | host/system metrics  |
          +----------------------+

                     |
                     v
          +----------------------+
          |       Grafana        |
          | dashboards + alerts  |
          +----------------------+
```

### 為什麼要這樣拆？

- **vLLM metrics** 負責回答：
  - 現在有多少 request 在跑？
  - 排隊是否變長？
  - TTFT / TPOT / E2E latency 是否惡化？
  - KV cache 是否滿了？
  - prefix cache hit rate 是否下降？
- **GPU metrics** 負責回答：
  - 是不是 GPU 已經打滿？
  - 是算力瓶頸、記憶體瓶頸，還是溫度/功耗問題？
- **Host metrics** 負責回答：
  - 是不是 CPU、RAM、磁碟或網路在拖後腿？

只看 vLLM metrics 常常知道「慢了」，但不知道「為什麼慢」。

## 4. vLLM 目前官方支援的監控能力

## 4.1 `/metrics` endpoint

官方文件指出，vLLM 在 OpenAI-compatible API server 上暴露 `/metrics`，可直接 `curl http://<host>:8000/metrics` 查看。

### 重要結論

- **Prometheus metric logging 預設就是開的**
- metric 名稱以 `vllm:` 為前綴
- 幾乎所有 metric 都帶 `model_name` label
- 除了 vLLM 自有 metrics，也有 HTTP metrics

## 4.2 指標分類

官方將 metrics 分成兩大類：

- **Server-level metrics**
  - 例如執行中的 request 數、KV cache 使用率、prefix cache hits
- **Request-level metrics**
  - 例如 TTFT、inter-token latency、queue time、prefill/decode time、E2E latency

這個分類很重要，因為正式環境通常會：

- 用 request-level metrics 做 SLO/SLA
- 用 server-level metrics 做 root cause 分析

## 4.3 官方明確提到的重要 metric

### Server-level

- `vllm:num_requests_running`
- `vllm:num_requests_waiting`
- `vllm:kv_cache_usage_perc`
- `vllm:prefix_cache_queries`
- `vllm:prefix_cache_hits`
- `vllm:prompt_tokens_total`
- `vllm:generation_tokens_total`
- `vllm:request_success_total`

### Request-level

- `vllm:time_to_first_token_seconds`
- `vllm:inter_token_latency_seconds`
- `vllm:e2e_request_latency_seconds`
- `vllm:request_queue_time_seconds`
- `vllm:request_prefill_time_seconds`
- `vllm:request_decode_time_seconds`
- `vllm:request_prompt_tokens`
- `vllm:request_generation_tokens`

### HTTP-level

官方 metrics 設計文件也提到 vLLM 會暴露 HTTP metrics，例如：

- `http_requests_total`
- `http_request_duration_seconds_count`
- `http_request_size_bytes_count`
- `http_response_size_bytes_count`

這些可以拿來監控 API 層 2xx / 4xx / 5xx 狀態與 HTTP latency。

## 4.4 多進程注意事項

官方說明提到：

- 現在 metrics 主要在 API server process 收集
- 只有在 `--api-server-count > 1` 時才需要 multi-process mode
- 這種情況下，一些 Python/process 類 metrics 不會暴露，例如：
  - `process_cpu_seconds_total`
  - `process_resident_memory_bytes`
  - `python_gc_*`

所以如果你在多 API server 模式下發現某些 process metrics 不見了，這是官方已知設計，不是你 Prometheus 壞掉。

## 4.5 deprecated metrics 注意事項

vLLM 有一套 metrics deprecation policy。舊 metrics 可能在下一個 minor version 被隱藏，再下一個 minor version 直接移除。官方也提供暫時遷移用的 escape hatch：

```bash
--show-hidden-metrics-for-version=X.Y
```

如果你的 dashboard 依賴舊 metric 名稱，升版前要先檢查，不要等 Grafana 全紅才處理。

## 5. 方案一：官方單機範例

這是官方最直接的上手方式。

### 官方流程

1. 啟動 vLLM
2. 用 Docker Compose 啟動 Prometheus 與 Grafana
3. 打 `http://localhost:8000/metrics` 確認 vLLM metrics
4. 在 Grafana 新增 Prometheus data source
5. 匯入 `grafana.json`

### 官方範例重點

- Prometheus scrape `host.docker.internal:8000`
- Grafana data source 連 `http://prometheus:9090`
- 官方範例的 `prometheus.yaml`：

```yaml
global:
  scrape_interval: 5s
  evaluation_interval: 30s

scrape_configs:
  - job_name: vllm
    static_configs:
      - targets:
          - "host.docker.internal:8000"
```

### 什麼時候適合用這條？

- 你要先驗證 vLLM metrics 有沒有出來
- 你想快速 demo 一套 dashboard
- 你還沒進 Kubernetes

### 限制

- 沒有 GPU/host metrics
- 沒有高可用
- 沒有正式環境的 dashboard provisioning / alerting / retention 設計

## 6. 方案二：通用正式方案

這是我最推薦的 **非 K8s 正式架構**：

```text
vLLM + Prometheus + Grafana + dcgm-exporter + node_exporter
```

### 為什麼這條最實用？

- 架構簡單
- 與官方 metric 模型完全一致
- 不綁 Kubernetes
- 很容易把單機方案逐步擴大成正式環境

### 最小可行 Prometheus scrape 設定

下面不是官方逐字配置，而是依官方 `/metrics` 暴露方式與 Prometheus 標準設定整理出的實務版本：

```yaml
global:
  scrape_interval: 5s
  evaluation_interval: 30s

scrape_configs:
  - job_name: vllm
    static_configs:
      - targets:
          - "vllm-host-1:8000"
          - "vllm-host-2:8000"

  - job_name: dcgm-exporter
    static_configs:
      - targets:
          - "vllm-host-1:9400"
          - "vllm-host-2:9400"

  - job_name: node-exporter
    static_configs:
      - targets:
          - "vllm-host-1:9100"
          - "vllm-host-2:9100"
```

### 這條方案的建議 dashboard 組合

1. vLLM dashboard
2. GPU dashboard
3. Host dashboard
4. Alerting dashboard

不要把所有東西塞進同一張圖，不然排障會很痛苦。

## 7. 方案三：Kubernetes 正式方案

如果你已經在 K8s 上跑 vLLM，官方最成熟的路線是 `production-stack`。

### 官方 production-stack 提供什麼？

- vLLM serving engine
- request router
- observability stack
- Grafana dashboard
- Helm chart

官方 README 明確把 observability stack 定義成：

```text
Prometheus + Grafana
```

### 官方 dashboard 目前能看什麼？

production-stack README 明列 Grafana dashboard 會提供：

1. Available vLLM Instances
2. Request Latency Distribution
3. TTFT Distribution
4. Number of Running Requests
5. Number of Pending Requests
6. GPU KV Usage Percent
7. GPU KV Cache Hit Rate

這已經是很像 production SRE 會先看的第一層視角。

### 在已有 Prometheus Operator 的叢集上

官方 Helm README 建議：

```yaml
servingEngineSpec:
  serviceMonitor:
    enabled: true
routerSpec:
  serviceMonitor:
    enabled: true
grafanaDashboards:
  enabled: true
```

### 在空白叢集上

官方 Helm README 指出 `vllm-stack` 可以直接嵌入 `kube-prometheus-stack`：

```yaml
servingEngineSpec:
  serviceMonitor:
    enabled: true
routerSpec:
  serviceMonitor:
    enabled: true
grafanaDashboards:
  enabled: true

kube-prometheus-stack:
  enabled: true
```

### 這條方案的優點

- 官方支援最完整
- `ServiceMonitor` 與 dashboard 走 K8s 原生配置
- 很適合多模型、多副本、路由器前置的正式場景
- 後續能直接接 autoscaling

### 什麼情況我會優先選它？

- 你要在 K8s 跑正式 workload
- 你要多個 serving engine
- 你要 router
- 你希望 dashboard / monitor / metric export 都跟 Helm values 一起管理

## 8. 官方 dashboard 現況

目前官方 dashboard 不只一份。

## 8.1 較早的官方範例 dashboard

- 路徑：`examples/observability/prometheus_grafana/grafana.json`
- 用途：搭配單機 Prometheus/Grafana 範例

## 8.2 現行官方 observability dashboards

- 路徑：`examples/observability/dashboards/grafana/`
- 內容：
  - `performance_statistics.json`
  - `query_statistics.json`

官方文件對兩份 JSON 的描述是：

- `performance_statistics.json`：延遲、吞吐、效能指標
- `query_statistics.json`：查詢表現、request volume、KPI

### 建議

如果你是新建 dashboard，我會優先考慮 **新版 `performance_statistics.json` + `query_statistics.json`**，因為這套是官方目前專門維護的 observability dashboards 目錄。

## 8.3 production-stack dashboard

- 路徑：`production-stack/helm/dashboards/vllm-dashboard.json`
- Grafana.com dashboard ID：`25043`

這份特別適合 K8s / production-stack 場景。

## 9. Grafana 端怎麼接

官方 Grafana 文件指出：

- Grafana **內建** Prometheus data source
- **不需要額外安裝 plugin**
- dashboard 可用 UI 或 HTTP API 匯入

### 基本步驟

1. 在 Grafana 新增 `Prometheus` data source
2. 填入 Prometheus URL
3. `Save & Test`
4. 匯入 vLLM dashboard JSON

### 常見 Prometheus URL

- 同 Docker network：`http://prometheus:9090`
- 同主機安裝：`http://127.0.0.1:9090`
- K8s service：`http://prometheus-operated.<namespace>.svc:9090`

實際 URL 依你的部署方式調整。

## 10. 正式環境應該重點盯哪些指標

我會把 vLLM 監控分成 5 個面向。

## 10.1 可用性

- `up{job="vllm"}`
- `http_requests_total` 的 5xx 比例
- 實例數量 / healthy instances

### 代表什麼？

- API server 是否活著
- Prometheus 是否真的抓得到
- 錯誤是否開始上升

## 10.2 使用量與吞吐

- `vllm:request_success_total`
- `vllm:prompt_tokens_total`
- `vllm:generation_tokens_total`

### 代表什麼？

- 每秒 request 數
- 每秒 prompt tokens
- 每秒 generation tokens

## 10.3 使用者體感延遲

- `vllm:time_to_first_token_seconds`
- `vllm:inter_token_latency_seconds`
- `vllm:e2e_request_latency_seconds`
- `vllm:request_queue_time_seconds`

### 代表什麼？

- TTFT：第一個 token 出現多久
- TPOT / inter-token latency：後續 token 吐出速度
- E2E latency：整體完成時間
- Queue time：是否已經開始排隊

## 10.4 排程與容量壓力

- `vllm:num_requests_running`
- `vllm:num_requests_waiting`
- `vllm:kv_cache_usage_perc`

### 代表什麼？

- 目前正在執行多少請求
- 排隊是否持續升高
- KV cache 是否逼近上限

## 10.5 快取效率

- `vllm:prefix_cache_queries`
- `vllm:prefix_cache_hits`

### 代表什麼？

- prefix cache hit rate 好不好
- 為什麼同樣 QPS 下，有時 TTFT 會突然變差

## 11. 建議的 PromQL

以下查詢是依官方 metric 名稱整理的實務版 PromQL。

## 11.1 每秒 request 完成數

```promql
sum(rate(vllm:request_success_total[5m]))
```

## 11.2 每秒 prompt tokens

```promql
sum(rate(vllm:prompt_tokens_total[5m]))
```

## 11.3 每秒 generation tokens

```promql
sum(rate(vllm:generation_tokens_total[5m]))
```

## 11.4 TTFT p95

```promql
histogram_quantile(
  0.95,
  sum by (le, model_name) (
    rate(vllm:time_to_first_token_seconds_bucket[5m])
  )
)
```

## 11.5 E2E latency p95

```promql
histogram_quantile(
  0.95,
  sum by (le, model_name) (
    rate(vllm:e2e_request_latency_seconds_bucket[5m])
  )
)
```

## 11.6 Queue time p95

```promql
histogram_quantile(
  0.95,
  sum by (le, model_name) (
    rate(vllm:request_queue_time_seconds_bucket[5m])
  )
)
```

## 11.7 TPOT / inter-token latency p95

```promql
histogram_quantile(
  0.95,
  sum by (le, model_name) (
    rate(vllm:inter_token_latency_seconds_bucket[5m])
  )
)
```

## 11.8 正在執行與等待中的 requests

```promql
sum by (model_name) (vllm:num_requests_running)
```

```promql
sum by (model_name) (vllm:num_requests_waiting)
```

## 11.9 KV cache 使用率

```promql
max by (model_name) (vllm:kv_cache_usage_perc) * 100
```

## 11.10 Prefix cache hit rate

```promql
sum(rate(vllm:prefix_cache_hits[5m]))
/
sum(rate(vllm:prefix_cache_queries[5m]))
```

## 11.11 HTTP 5xx rate

```promql
sum(rate(http_requests_total{status=~"5.."}[5m]))
```

## 12. 告警建議

Grafana 官方文件把 alert rule 拆成：

- query
- condition
- evaluation interval / duration
- labels / annotations / routing

對 vLLM 我會先做這幾種告警。

## 12.1 可用性告警

### vLLM target down

```promql
up{job="vllm"} == 0
```

### HTTP 5xx 持續升高

```promql
sum(rate(http_requests_total{status=~"5.."}[5m])) > 0.1
```

## 12.2 延遲告警

### TTFT p95 過高

```promql
histogram_quantile(
  0.95,
  sum by (le) (
    rate(vllm:time_to_first_token_seconds_bucket[5m])
  )
) > 1
```

### E2E latency p95 過高

```promql
histogram_quantile(
  0.95,
  sum by (le) (
    rate(vllm:e2e_request_latency_seconds_bucket[5m])
  )
) > 10
```

閾值要依模型大小、batching 策略、QPS 重新調整。

## 12.3 容量告警

### Queue 持續堆高

```promql
sum(vllm:num_requests_waiting) > 0
```

更實務一點的做法是搭配 `for: 5m`，避免瞬時尖峰誤報。

### KV cache 快滿

```promql
max(vllm:kv_cache_usage_perc) > 0.9
```

## 12.4 GPU / 主機告警

這部分來自 `dcgm-exporter` / `node_exporter`，不是 vLLM 本身提供，但正式環境強烈建議一起做：

- GPU memory usage 高
- GPU utilization 長期 100%
- CPU steal / load 高
- host memory 不足
- disk latency / io wait 升高

## 13. 常見坑

## 13.1 誤以為 vLLM 直接內建 Grafana

不是。vLLM 原生是 `/metrics`，Grafana 是透過 Prometheus 對接。

## 13.2 只畫 latency，不畫 queue / KV cache

這樣你只會知道「慢了」，但不知道是：

- request 堆積
- cache 快滿
- prefix cache 命中下降
- GPU 打滿

## 13.3 只抓 vLLM metrics，不抓 GPU metrics

這是最常見的不完整監控。因為 LLM serving 很多問題其實是 GPU 資源問題。

## 13.4 升版後 dashboard 壞掉

原因通常是 metric deprecation / rename。升版前先檢查：

- 你的 dashboard 用到哪些 metric
- 官方 release / docs 是否提到 deprecation
- 必要時用 `--show-hidden-metrics-for-version=X.Y` 暫時過渡

## 13.5 `--api-server-count > 1` 後 process metrics 不見

這是官方文件已有說明的 multi-process 行為，不一定是 bug。

## 13.6 舊 metric 與新 metric 混用

官方 metrics 文件特別提到 queue time 曾有重複命名情況。如果你同時看到舊 queue metric 與 `vllm:request_queue_time_seconds`，新建 dashboard 時應優先用 `vllm:request_queue_time_seconds`。

## 14. 我會怎麼選

## 14.1 如果你現在只是要先把監控架起來

先用：

```text
vLLM + Prometheus + Grafana
```

直接照官方 `Prometheus and Grafana` 範例起來，確認 `/metrics`、datasource、dashboard 都通。

## 14.2 如果你要在 VM / bare metal 正式上線

我會選：

```text
vLLM + Prometheus + Grafana + dcgm-exporter + node_exporter
```

原因是：

- 架構簡單
- 可觀測性完整
- 不被 K8s 綁住
- 跟官方 metric 模型完全對齊

## 14.3 如果你本來就在 Kubernetes

我會直接選：

```text
vLLM production-stack + kube-prometheus-stack
```

然後把這幾個打開：

- `servingEngineSpec.serviceMonitor.enabled`
- `routerSpec.serviceMonitor.enabled`
- `grafanaDashboards.enabled`
- 視情況 `kube-prometheus-stack.enabled`

這條是目前最像「官方 production blueprint」的路線。

## 15. 補充：如果你還想做 traces / logs

vLLM 也有官方 `Setup OpenTelemetry POC` 文件，但從 metrics 設計文件看得很清楚，官方目前仍然是 **優先以 Prometheus 作為 production monitoring 主線**。

所以我的建議是：

1. 先把 metrics 監控做完整
2. 再補 traces
3. 最後才補更進階的 logging correlation

不要一開始就把觀測面做得太散。

## 16. 結論

### 最務實的結論

- **vLLM + Grafana 是成熟方案**
- 但它的正確架構是 **vLLM `/metrics` -> Prometheus -> Grafana**
- 官方現在已經提供：
  - 單機 Prometheus/Grafana 範例
  - 官方 dashboard JSON
  - Kubernetes production-stack observability 方案

### 我的推薦順序

1. **單機驗證**：官方 `Prometheus and Grafana` 範例
2. **正式非 K8s**：`vLLM + Prometheus + Grafana + dcgm-exporter + node_exporter`
3. **正式 K8s**：`production-stack + kube-prometheus-stack + ServiceMonitor + Grafana dashboards`

如果只選一句話總結：

> 現在最成熟、最標準、和官方最對齊的 vLLM 監控方式，就是把 vLLM 的 Prometheus metrics 接進 Grafana，而正式環境一定要把 GPU 與主機層 metrics 一起納入。

## 17. 來源

### vLLM 官方

- vLLM Metrics design: <https://docs.vllm.ai/en/stable/design/metrics/>
- vLLM Production metrics: <https://docs.vllm.ai/en/v0.20.0/usage/metrics/>
- vLLM Prometheus and Grafana example: <https://docs.vllm.ai/en/stable/examples/observability/prometheus_grafana/>
- vLLM Monitoring Dashboards: <https://docs.vllm.ai/en/stable/examples/observability/dashboards/>
- vLLM examples index: <https://docs.vllm.ai/en/latest/examples/>
- vLLM production-stack integration page: <https://docs.vllm.ai/en/latest/deployment/integrations/production-stack/>
- vLLM production-stack repository: <https://github.com/vllm-project/production-stack>
- vLLM production-stack Helm README: <https://github.com/vllm-project/production-stack/blob/main/helm/README.md>

### Grafana / Prometheus 官方

- Grafana Prometheus data source config: <https://grafana.com/docs/grafana/latest/datasources/prometheus/configure/>
- Grafana dashboard import: <https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/import-dashboards/>
- Grafana alert rules: <https://grafana.com/docs/grafana/latest/alerting/fundamentals/alert-rules/>
- Prometheus configuration: <https://prometheus.io/docs/prometheus/latest/configuration/configuration/>

### GPU / host metrics 官方

- NVIDIA DCGM-Exporter docs: <https://docs.nvidia.com/datacenter/dcgm/latest/gpu-telemetry/dcgm-exporter.html>
- Prometheus node_exporter: <https://github.com/prometheus/node_exporter>

