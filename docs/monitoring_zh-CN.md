# 監控（Prometheus + Grafana）

> [English](monitoring.md)

整套內建完整的 **Prometheus → Grafana** 流程，免手動設定。

- **後端**寫出 Prometheus file-based service-discovery 檔（`LLMOPS_PROMETHEUS_SD_PATH`），
  列出每個 *ready* 的 vLLM 實例，並隨模型啟停刷新——所以動態艦隊免改設定即被抓取。
- **Prometheus**（`:9090`）抓取這些實例的 `/metrics`，外加 `dcgm-exporter`（GPU）與
  `node-exporter`（主機）。
- **Grafana** 以單一來源服務於 **`http://localhost:8884/grafana`**（匿名唯讀；以
  `admin` / `GRAFANA_ADMIN_PASSWORD` 登入可編輯）。datasource 與 dashboards 由
  [`deploy/grafana`](../deploy/grafana) 自動 provision：
  - **總覽** — 單一頁面：健康、延遲 SLO、容量、GPU/主機
  - **vLLM 排程與容量**（自訂）
  - **Performance** / **Query**（官方 vLLM dashboards）
  - **GPU**（DCGM）與 **Host**（Node Exporter）

  同一批 dashboards 也嵌入控制台的 **監控** 分頁，含 SLO 門檻線與模型生命週期標註。
- **告警**：已 provision 的 vLLM 告警規則（target down、TTFT p95、KV cache、請求排隊）
  路由到一個 webhook contact point —— 在 `deploy/.env` 設 `GRAFANA_ALERT_WEBHOOK`
  （Slack/Discord/通用）並重啟 Grafana 即可收到通知。

```bash
curl http://localhost:9090/api/v1/targets        # prometheus：scrape target 健康狀態
# 開啟 http://localhost:8884/grafana             # dashboards 與告警
```

指標背景與設計理念見 [vllm_grafana_monitoring_guide.md](vllm_grafana_monitoring_guide.md)。
