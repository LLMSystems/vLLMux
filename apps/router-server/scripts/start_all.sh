#!/bin/bash
# Starts the Router Server only. Model processes (vLLM, embedding/reranker) are
# now owned by the Dashboard backend's llmops layer and started via its
# /api/models/{key}/start endpoint — the router no longer launches models.

CONFIG_PATH=$1
GUNICORN_CONFIG_PATH=$2

if [ -z "$CONFIG_PATH" ]; then
  echo "請提供 config.yaml 路徑，例如: ./start_all.sh ./configs/config_formal.yaml ./configs/gunicorn.conf.py"
  exit 1
fi

if [ -z "$GUNICORN_CONFIG_PATH" ]; then
  echo "請提供 gunicorn 配置檔路徑，例如: ./start_all.sh ./configs/config_formal.yaml ./configs/gunicorn.conf.py"
  exit 1
fi

echo "使用配置檔: $CONFIG_PATH"
export CONFIG_PATH="$CONFIG_PATH"

echo "使用 gunicorn 配置檔: $GUNICORN_CONFIG_PATH"
export GUNICORN_CONFIG_PATH="$GUNICORN_CONFIG_PATH"

export TORCH_CUDA_ARCH_LIST="8.0"

echo "啟動 Router Server（gunicorn + uvloop）..."
PYTHONPATH=. gunicorn src.llm_router.main:app \
  -c "$GUNICORN_CONFIG_PATH" \
  --env CONFIG_PATH="$CONFIG_PATH"
