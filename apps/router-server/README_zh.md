<div align="center" xmlns="http://www.w3.org/1999/html">

# LLM Router Server

<p align="center">
  <img src="assets/structure.png" width="1200px" style="vertical-align:middle;">
</p>

<p align="center">
  LLM Router Server 是一個專為多模型部署場景設計的高效能路由服務，用於統一管理和調度多個本地大型語言模型（LLM）、Embedding 模型、Re-ranking 模型等推理服務。
</p>

</div>

### 主要功能

- **統一路由管理**：整合多個獨立的 vLLM 服務、Embedding 服務與 Reranker 服務
- **OpenAI 兼容 API**：提供完整相容的 OpenAI API 介面（`/v1/chat/completions`、`/v1/completions`、`/v1/embeddings`）
- **配置式部署**：透過 YAML 配置檔輕鬆管理多個模型的啟動參數、埠口、GPU 分配等
- **多模型並行**：支援多個模型實例同時運行，每個模型使用獨立的進程與 GPU 資源
- **智能負載均衡**：基於即時指標自動選擇負載最低的實例（運行中請求數、等待中請求數、KV 快取使用率）
- **高效能轉發**：基於 FastAPI + Gunicorn + Uvloop 的高效能異步架構
- **流式響應優化**：對流式請求進行優化，確保低延遲、穩定的 Token 輸出

### 使用場景

- **多模型服務部署**：在單台或多台伺服器上部署多個 LLM 模型
- **模型負載均衡**：根據業務需求動態選擇不同的模型
- **統一 API 介面**：為不同模型提供統一的 API 端點
- **RAG 應用**：整合 Embedding 與 Reranking 服務，構建完整的檢索增強生成系統

---

## 特色

### 多模型獨立運行
- 每個 LLM 模型透過獨立進程啟動，使用不同的埠與 CUDA 裝置
- 支援動態配置模型數量、GPU 記憶體分配、並行請求數等參數
- 模型之間互不干擾，單一模型故障不影響其他服務

### 智能負載均衡
- **即時指標監控**：持續輪詢每個實例的 vLLM `/metrics` 端點，收集：
  - 運行中的請求數
  - 等待中的請求數
  - KV 快取使用率
  - 總提示詞與生成 token 數
- **最低負載選擇**：自動將請求路由到負載分數最低的實例
- **負載分數計算**：結合多個指標與可配置的權重：
  - 等待請求權重：10.0
  - 運行請求權重：3.0
  - KV 快取使用率權重：100.0
- **健康監控**：追蹤後端健康狀態，對失敗的實例套用冷卻期
- **飛行中請求追蹤**：監控飛行中的請求，防止單一實例過載

### Embedding 與 Reranker 整合
- 內建 Embedding 伺服器與 Reranker 伺服器
- 支援多個 Embedding 模型（m3e-base、bge-m3 等）
- 支援多個 Reranking 模型（bge-reranker-large 等）
- 統一轉發 `/v1/embeddings` 請求

### 完全相容 OpenAI SDK
- 支援使用 OpenAI Python SDK 直接調用
- 無需修改現有代碼，僅需更改 `base_url`
- 支援所有標準參數（temperature、top_p、max_tokens 等）

### 工作流程

1. **客戶端請求**：客戶端透過 OpenAI SDK 或 HTTP 客戶端向 Router Server 發送請求
2. **路由解析**：Router 根據請求中的 `model` 參數查找對應的後端服務配置
3. **基於負載的實例選擇**：對於具有多個實例的模型：
   - 從所有實例取得即時指標
   - 計算每個實例的負載分數
   - 選擇負載最低的實例
   - 考慮健康狀態和冷卻期
4. **請求轉發**：將請求轉發至選定的 vLLM 或 Embedding 服務實例
5. **流式處理**：對流式響應進行優化處理，確保低延遲傳輸
6. **健康追蹤**：監控請求成功/失敗並更新實例健康狀態
7. **響應返回**：將後端服務的響應原樣返回給客戶端

---

## 目錄結構

```
LLM-Router-Server/
├── configs/                    # 配置檔目錄
│   ├── config.yaml            # 主要配置檔（模型、伺服器設定）
│   └── gunicorn.conf.py       # Gunicorn 配置檔
├── docker/                     # Docker 相關檔案
│   ├── Dockerfile             # Docker 映像構建檔
│   └── docker-compose.yaml    # Docker Compose 配置
├── logs/                       # 日誌目錄
├── scripts/                    # 啟動腳本目錄
│   ├── start_all_models.py    # 啟動所有模型的 Python 腳本
│   └── start_all.sh           # 一鍵啟動腳本（模型 + Router）
├── src/                        # 主要源碼目錄
│   ├── embedding_reranker/    # Embedding 與 Reranker 模組
│   │   ├── __init__.py
│   │   ├── embedding_reranker_launcher.py  # 啟動器
│   │   ├── schema.py          # 資料結構定義
│   │   └── embedding_engine/  # 推理引擎
│   │       ├── baseinferencer.py  # 基礎推理類
│   │       ├── embed_rerank.py    # Embedding/Rerank 實作
│   │       ├── generator.py       # 生成器
│   │       └── optimize.py        # 優化工具
│   ├── llm_router/            # LLM 路由模組
│   │   ├── __init__.py
│   │   ├── config_loader.py   # 配置載入器
│   │   ├── env.py             # 環境變數管理
│   │   ├── main.py            # FastAPI 應用入口
│   │   ├── router.py          # 路由處理邏輯
│   │   └── vllm_launcher.py   # vLLM 啟動器
│   └── metrics/               # 監控與指標
│       └── basic_metrics.py   # 基礎指標收集
├── test/                       # 測試檔案目錄
│   └── test_router_server.py  # Router 伺服器測試
├── requirements.txt            # Python 依賴清單
└── README.md                   # 專案說明文件
```

---

## 安裝指南

### 安裝依賴

```bash
pip install -r requirements.txt
```

## 配置說明

### 1. 編輯配置檔

主要配置檔位於 `configs/config.yaml`，包含兩個主要部分：

#### LLM 引擎配置

配置一個或多個 LLM 模型，支援多實例部署：
```yaml
LLM_engines:
  # 模型配置（支援多實例）
  Qwen3-0.6B:
    instances:
      # 第一個實例
      - id: "qwen3-1"                         # 實例 ID
        host: "localhost"                     # 服務主機
        port: 8002                            # 服務埠口
        cuda_device: 0                        # CUDA 裝置編號
      
      # 第二個實例
      - id: "qwen3-2"                         # 實例 ID
        host: "localhost"                     # 服務主機
        port: 8004                            # 服務埠口
        cuda_device: 0                        # CUDA 裝置編號
    
    # 模型配置（所有實例共享）
    model_config:
      model_tag: "Qwen/Qwen3-0.6B"           # 模型路徑或 HuggingFace ID
      dtype: "float16"                       # 資料類型
      max_model_len: 500                     # 最大序列長度
      gpu_memory_utilization: 0.35           # GPU 記憶體使用率
      tensor_parallel_size: 1                # Tensor 並行大小

# Embedding 與 Reranking 伺服器配置
embedding_server:
  host: "localhost"
  port: 8005
  cuda_device: 1

  # Embedding 模型列表
  embedding_models:
    m3e-base:
      model_name: "moka-ai/m3e-base"
      model_path: "./models/embedding_engine/model/embedding_model/m3e-base-model"
      tokenizer_path: "./models/embedding_engine/model/embedding_model/m3e-base-tokenizer"
      max_length: 512
      use_gpu: true
      use_float16: true
    
    bge-m3:
      model_name: "BAAI/bge-m3"
      model_path: "./models/embedding_engine/model/embedding_model/bge-m3-model"
      tokenizer_path: "./models/embedding_engine/model/embedding_model/bge-m3-tokenizer"
      max_length: 512
      use_gpu: true
      use_float16: true

  # Reranking 模型列表
  reranking_models:
    bge-reranker-large:
      model_name: "BAAI/bge-reranker-large"
      model_path: "./models/embedding_engine/model/reranking_model/bge-reranker-large-model"
      tokenizer_path: "./models/embedding_engine/model/reranking_model/bge-reranker-large-tokenizer"
      max_length: 512
      use_gpu: true
      use_float16: true
```

#### 配置參數說明

**LLM 引擎參數：**

*實例配置：*
- `id`：實例的唯一識別碼
- `host`：vLLM 服務監聽的主機地址
- `port`：vLLM 服務監聽的埠口
- `cuda_device`：指定使用的 GPU 裝置編號

*模型配置（所有實例共享）：*
- `model_tag`：模型檔案路徑或 HuggingFace 模型 ID
- `dtype`：模型精度類型（`float16`、`bfloat16` 等）
- `max_model_len`：最大上下文長度
- `gpu_memory_utilization`：GPU 記憶體使用率（0.0-1.0）
- `tensor_parallel_size`：Tensor 並行度（多 GPU 推理）

**Embedding 伺服器參數：**
- `host`、`port`：伺服器監聽地址與埠口
- `cuda_device`：使用的 GPU 裝置
- `model_path`：模型權重檔案路徑
- `tokenizer_path`：Tokenizer 檔案路徑
- `max_length`：最大序列長度
- `use_gpu`：是否使用 GPU
- `use_float16`：是否使用 FP16 精度

### 2. 配置 Gunicorn

編輯 `configs/gunicorn.conf.py`：

```python
# gunicorn.conf.py
import os

# 綁定地址與埠口
bind = "0.0.0.0:8947"

# Worker 數量（建議設為 CPU 核心數）
workers = 4

# Worker 類別（使用 Uvicorn Worker 支援 ASGI）
worker_class = "uvicorn.workers.UvicornWorker"

# 超時時間（0 表示無限）
timeout = 0

# 日誌等級
loglevel = "info"

# 訪問日誌輸出到標準輸出
accesslog = "-"

# 錯誤日誌輸出到標準輸出
errorlog = "-"

# 是否預載入應用
preload_app = False
```

---

## 使用指南

### 1. 啟動所有服務

使用一鍵啟動腳本：

```bash
sh scripts/start_all.sh ./configs/config.yaml ./configs/gunicorn.conf.py
```

這個腳本會依序執行：
1. 啟動所有配置的 vLLM 模型服務
2. 啟動 Embedding 與 Reranker 服務（如果已配置）
3. 啟動 Router Server（使用 Gunicorn + 多 Worker）

### 3. 驗證服務狀態

檢查所有可用模型：

```bash
curl http://localhost:8947/v1/models
```

### 4. 使用 OpenAI SDK 調用

#### Chat Completions（對話生成）

```python
from openai import OpenAI

client = OpenAI(
    api_key="EMPTY",
    base_url="http://localhost:8947/v1"
)

# 非流式請求
response = client.chat.completions.create(
    model="Qwen2.5-14B-Instruct",
    messages=[
        {"role": "system", "content": "你是一個有幫助的助手。"},
        {"role": "user", "content": "請介紹一下 Python 的優點。"}
    ],
    temperature=0.7,
    max_tokens=500
)

print(response.choices[0].message.content)

# 流式請求
stream = client.chat.completions.create(
    model="Qwen2.5-14B-Instruct",
    messages=[
        {"role": "user", "content": "寫一首關於春天的詩。"}
    ],
    temperature=0.8,
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

#### Embeddings（文本嵌入）

```python
response = client.embeddings.create(
    model="m3e-base",
    input=["這是第一段文本", "這是第二段文本"]
)

# 取得嵌入向量
embedding_1 = response.data[0].embedding
embedding_2 = response.data[1].embedding

print(f"嵌入向量維度: {len(embedding_1)}")
```

#### Reranking（重排序）

```python
documents = [
    "機器學習最好透過專案來學習。",
    "理論對於理解機器學習至關重要。",
    "實作教程是學習機器學習的最佳方式。"
]

response = client.embeddings.create(
    model="bge-reranker-large",
    input=documents,
    extra_body={"query": "如何學習機器學習？"}
)

# 取得重排序分數
for idx, item in enumerate(response.data):
    print(f"文檔 {idx}: 分數 {item.embedding}")
```

---

## API 文檔

### 端點列表

| 端點 | 方法 | 描述 |
|------|------|------|
| `/v1/chat/completions` | POST | 對話生成（支援流式） |
| `/v1/completions` | POST | 文本補全（支援流式） |
| `/v1/embeddings` | POST | 文本嵌入 / 重排序 |
| `/v1/models` | GET | 列出所有可用模型 |

### 專案內部文檔

- `LLM Router Streaming 問題紀錄與解法.md`：流式響應優化說明
- `LLM Router 吞吐優化.md`：吞吐量優化指南

---

## License
This project is licensed under the MIT License.
