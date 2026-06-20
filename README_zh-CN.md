<div align="center">

<p align="center">
  <img src="assets/icon.png" width="500px" style="vertical-align:middle;">
</p>

**一站式部署、路由、監控與評測你的 vLLM 集群**

[English](README.md) · [中文](README_zh-CN.md)

![vLLMux](https://img.shields.io/badge/vLLM-multiplexed-5b8def)
![license](https://img.shields.io/badge/license-MIT-green)
![stack](https://img.shields.io/badge/FastAPI%20·%20Vue%203%20·%20Grafana-informational)

![Main Console](assets/image0.png)
![Main Console](assets/image1.png)
![Main Console](assets/image2.png)

</div>

---

**vLLMux** 是一個自架的 LLM 服務控制平台，基於
[vLLM](https://github.com/vllm-project/vllm)。
內建的 Prometheus + Grafana 監控——全都在同一個 Vue 控制台之後。


## 功能亮點

- **貼上 `vllm serve …` 即可新增模型** — 解析成表單、以動態 overlay 疊加；router 熱重載。
- **生命週期** — 每實例狀態機（`stopped → starting → ready → failed`）、VRAM 預檢防呆、GPU 自動擺放、崩潰指數退避自動重啟。
- **可插拔路由策略** — 每個模型群組或全域各自選負載平衡策略：`least_load`（預設）、`round_robin`、`random`、`least_inflight`、`p2c`,以及 `session_affinity` / `prefix_affinity`(多輪對話與共用 prompt 的快取重用）。可在控制台即時切換;失效轉移與每後端冷卻對所有策略一體適用。
- **即時觀測** — SSE 狀態、動畫系統拓撲圖與 router 負載平衡圖、每模型用量／延遲／錯誤統計。
- **內建 Grafana 監控** — Prometheus 自動發現每個運行中的實例；總覽／容量／效能／GPU／主機 dashboards 嵌入應用內，含 SLO 門檻線與告警。
- **Playground** — OpenAI 相容的 chat（串流）／completions／embeddings／reranking。
- **壓測與評測** — LLM 壓測（並發、到達率、SLA 自動調優）＋ 30+ 個準確度資料集與 LLM-as-judge。
- **資料庫** — 在 UI 瀏覽／預下載 HF 權重與資料集；工具調用 parser 助手；LoRA 支援。
- **安全性** — 管理員權杖控管操作，並可發行／撤銷帶 per-key 用量歸屬的 API 金鑰。

完整說明見 [docs/features_zh-CN.md](docs/features_zh-CN.md)。

## 快速開始

需要安裝 Docker 與 NVIDIA Container Toolkit（WSL2 請在 Docker Desktop 開啟 GPU 支援）。

```bash
cp deploy/.env.example deploy/.env   # 填 HF_TOKEN、要用的 GPU、管理員權杖
make up                              # 建置並啟動整套服務
# 瀏覽器開 http://localhost:8884
```

`make down` 停止 · `make logs` 追蹤所有服務日誌 · `make ps` 看狀態。

```bash
curl http://localhost:8887/v1/models     # router：列出設定的模型群組
curl http://localhost:5000/api/models    # 後端：每個實例的生命週期狀態
# http://localhost:8884/grafana          # dashboards 與告警
```

完整架構、共用 netns 的原理、volumes 與手動啟動見
[docs/deployment_zh-CN.md](docs/deployment_zh-CN.md)。

## 架構

```mermaid
flowchart LR
    Client([Clients 用戶端])
    FE["<b>frontend</b><br/>nginx · :8884<br/>單一來源"]
    VLLM["<b>vLLM 實例</b><br/>:800x"]
    GF["<b>grafana</b><br/>/grafana"]
    DCGM["dcgm-exporter<br/>:9400 · GPU"]
    NODE["node-exporter<br/>:9100 · 主機"]

    subgraph netns["共用 network namespace"]
        BE["<b>backend</b> · :5000<br/>模型生命週期"]
        RT["<b>router</b> · :8887<br/>OpenAI 相容負載平衡"]
        PR["<b>prometheus</b> · :9090"]
    end

    Client --> FE
    FE -->|/api| BE
    FE -->|/v1| RT
    FE -->|/grafana| GF
    BE -->|按需拉起| VLLM
    RT -->|路由 + 平衡| VLLM
    PR -->|抓取 /metrics| VLLM
    PR --> DCGM
    PR --> NODE
    GF -->|查詢| PR
```

**router 只負責路由**——**模型生命週期由 backend 掌管**。frontend、router、backend 與
Grafana 都在 nginx 之後以單一來源對外；backend、router、Prometheus 共用一個 network
namespace，所以被拉起的 vLLM 實例可在 `localhost` 互相連到。

## 文件

| 主題 | |
|---|---|
| 部署與架構 | [docs/deployment_zh-CN.md](docs/deployment_zh-CN.md) |
| 配置（`config.yaml`） | [docs/configuration_zh-CN.md](docs/configuration_zh-CN.md) |
| 功能特色（詳細） | [docs/features_zh-CN.md](docs/features_zh-CN.md) |
| 監控（Prometheus + Grafana） | [docs/monitoring_zh-CN.md](docs/monitoring_zh-CN.md) |
| HTTP API | [docs/API.md](docs/API.md) |

## 環境需求

NVIDIA GPU（建議 CUDA 13.1+）· 16GB+ RAM · 50GB+ 磁碟。

> **提示 — RAM 有限時跑多個 instance。** 每個 vLLM instance 啟動時都會做
> `torch.compile` + CUDA-graph capture，這非常吃**系統 RAM**（不是 VRAM）。在小機器上
> （例如 WSL2 只有 ~8GB RAM），對同一顆模型開第二個 instance 很容易把 RAM 吃光、swap
> 抖動，讓新 instance 一直卡在 `starting`。在啟動指令加上 **`--enforce-eager`** 即可跳過
> 編譯：啟動時間從數分鐘降到數秒、RAM/CPU 壓力大幅下降，代價只是推理延遲略增。多 instance
> 的瓶頸通常是 **RAM 而非 VRAM**，擴展前先把 WSL 記憶體加大（`.wslconfig` →
> `memory=12GB`，再 `wsl --shutdown`）。

## 授權

MIT — 見 [LICENSE](LICENSE)。
