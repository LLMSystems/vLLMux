# Monitoring (Prometheus + Grafana)

> [中文](monitoring_zh-CN.md)

The stack bundles a full **Prometheus → Grafana** pipeline, no manual setup.

- The **backend** writes a Prometheus file-based service-discovery file
  (`LLMOPS_PROMETHEUS_SD_PATH`) listing every *ready* vLLM instance, refreshed as
  models start/stop — so a dynamic fleet is scraped with zero config edits.
- **Prometheus** (`:9090`) scrapes those instances' `/metrics` plus `dcgm-exporter`
  (GPU) and `node-exporter` (host).
- **Grafana** is served single-origin at **`http://localhost:8884/grafana`**
  (anonymous read-only; log in as `admin` / `GRAFANA_ADMIN_PASSWORD` to edit).
  Datasource and dashboards are auto-provisioned from
  [`deploy/grafana`](../deploy/grafana):
  - **Overview** — single pane: health, latency SLO, capacity, GPU/host
  - **vLLM Scheduling & Capacity** (custom)
  - **Performance** / **Query** (official vLLM dashboards)
  - **GPU** (DCGM) and **Host** (Node Exporter)

  The same dashboards are embedded in the dashboard's **Monitoring** tab, with SLO
  threshold lines and model-lifecycle annotations.
- **Alerting (two complementary channels)**:
  - **Grafana** (metric/threshold): provisioned vLLM rules (target down, TTFT p95,
    KV cache, request queueing, autoscaling VRAM-blocked / saturated) route to a
    webhook contact point — set `GRAFANA_ALERT_WEBHOOK` in `deploy/.env`
    (Slack/Discord/generic) and restart Grafana.
  - **Backend Notifier** (discrete lifecycle events): model crash, restart-budget
    exhausted, recovered — pushed to Slack/Discord/webhook, configured via
    `LLMOPS_ALERT_*` env or the console's **Notifications** page (with a test push).
    See [alerting-design_zh-CN.md](alerting-design_zh-CN.md). Both can target the
    same Slack channel.

```bash
curl http://localhost:9090/api/v1/targets        # prometheus: scrape target health
# open http://localhost:8884/grafana             # dashboards + alerts
```

For background on the metrics and the design rationale, see
[vllm_grafana_monitoring_guide.md](vllm_grafana_monitoring_guide.md).
