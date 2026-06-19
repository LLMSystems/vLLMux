# 配置說明

> [English](configuration.md)

配置文件位於 `packages/config-schema/config.yaml`——單一來源，由
`packages/config-schema/schema.py` 驗證，前端、後端與 router 都讀同一份。它控制所有
模型的啟動參數。

> 通常**不需要**手動編輯：從前端貼上 `vllm serve …` 指令新增模型，會以動態 overlay 疊加。
> 只有要維護「正式、手寫」的模型清單時才改 `config.yaml`。

## `config.yaml` 結構

```yaml
# 路由服務器配置
server:
  host: "0.0.0.0"
  port: 8887
  uvicorn_log_level: "info"

# LLM 模型配置
LLM_engines:
  Qwen3-0.6B:
    instances:
      - id: "qwen3"
        host: "localhost"
        port: 8002
        cuda_device: 0
      - id: "qwen3-2"
        host: "localhost"
        port: 8004
        cuda_device: 0

    model_config:
      model_tag: "Qwen/Qwen3-0.6B"
      dtype: "float16"
      max_model_len: 500
      gpu_memory_utilization: 0.35
      tensor_parallel_size: 1

# Embedding 服務器配置（可選）
embedding_server:
  host: "localhost"
  port: 8005
  cuda_device: 1

  embedding_models:
    m3e-base:
      model_name: "moka-ai/m3e-base"
      model_path: "./models/embedding_engine/model/embedding_model/m3e-base-model"
      tokenizer_path: "./models/embedding_engine/model/embedding_model/m3e-base-tokenizer"
      max_length: 512
      use_gpu: true
      use_float16: true

  reranking_models:
    bge-reranker-large:
      model_name: "BAAI/bge-reranker-large"
      model_path: "./models/embedding_engine/model/reranking_model/bge-reranker-large-model"
      tokenizer_path: "./models/embedding_engine/model/reranking_model/bge-reranker-large-tokenizer"
      max_length: 512
      use_gpu: true
      use_float16: true
```

## 關鍵參數

| 參數 | 說明 | 建議值 |
|------|------|--------|
| `gpu_memory_utilization` | GPU 記憶體使用比例 | 0.6–0.9 |
| `max_model_len` | 最大上下文長度 | 依模型能力 |
| `tensor_parallel_size` | 多 GPU 並行數 | GPU 數量 |
| `dtype` | 推理精度 | float16（速度快） / bfloat16（更穩定） |
| `cuda_device` | GPU 設備編號 | 0, 1, 2… |

## 同時啟動多個模型

可以——只要顯存放得下。**VRAM 預檢防呆**會擋下會撐爆目標 GPU 的啟動（可用 *Force start*
逐次覆寫），未指定 `cuda_device` 的實例會**自動擺放**到剩餘顯存最多的 GPU。單張小卡通常
能跑一顆中型模型加幾顆小模型；模型是按需啟動的，所以可以設定一大批而不必全部同時運行。

可在後端用環境變數調整防呆／重啟策略：

| 環境變數 | 用途 |
|---|---|
| `LLMOPS_VRAM_GUARD` | 啟用／關閉 VRAM 預檢防呆 |
| `LLMOPS_AUTO_RESTART` | 崩潰的 managed 模型自動重啟 |
| `LLMOPS_MAX_RESTARTS` | 放棄前的重啟次數上限 |
| `LLMOPS_RESTART_BACKOFF` | 指數退避基數 |
