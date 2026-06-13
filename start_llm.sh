# WSL 下 pinned memory 不支援，新版 V2 Model Runner 會因 UVA 不可用而啟動失敗，
# 強制退回 V1 Model Runner 即可（V1 不需要 UVA/pinned memory）。
export VLLM_USE_V2_MODEL_RUNNER=0

# 繞開 flashinfer：打包的 0.6.12 與 CUDA 13.0 的 CUB 不相容，runtime JIT 編譯會失敗。
# 改用 vLLM 內建預編譯的 FlashAttention，sampler 改用原生 Torch。
export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_ATTENTION_BACKEND=FLASH_ATTN

vllm serve Qwen/Qwen3-0.6B \
  --served-model-name qwen3-0.6b \
  --host 0.0.0.0 \
  --port 8000 \
  --dtype auto \
  --gpu-memory-utilization 0.7 \
  --max-model-len 8192