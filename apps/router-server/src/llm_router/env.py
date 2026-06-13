"""Environment setup applied before spawning vLLM subprocesses.

Launchers call env_setup() once before Popen(["vllm", ...]); the child vLLM
processes inherit os.environ, so anything set here reaches every model. Kept in
sync with apps/backend/app/launcher/env.py.
"""
import logging
import os

logger = logging.getLogger(__name__)


def _is_wsl() -> bool:
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def env_setup() -> None:
    os.environ.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")

    # WSL + CUDA 13 / flashinfer 0.6.12 compatibility workaround:
    # - VLLM_USE_V2_MODEL_RUNNER=0 : the V2 runner needs UVA, which WSL disables.
    # - VLLM_USE_FLASHINFER_SAMPLER=0 + VLLM_ATTENTION_BACKEND=FLASH_ATTN :
    #   flashinfer 0.6.12's bundled CUB source is incompatible with CUDA 13, so
    #   fall back to vLLM's prebuilt FlashAttention + native Torch sampler.
    # Applied only on WSL by default, so a normal Linux/CUDA box keeps the faster
    # V2 runner + flashinfer path. Override with LLM_ROUTER_VLLM_COMPAT=on|off.
    # setdefault throughout: an explicit export by the caller always wins.
    compat = os.environ.get("LLM_ROUTER_VLLM_COMPAT", "auto").lower()
    if compat == "on" or (compat == "auto" and _is_wsl()):
        os.environ.setdefault("VLLM_USE_V2_MODEL_RUNNER", "0")
        os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")
        os.environ.setdefault("VLLM_ATTENTION_BACKEND", "FLASH_ATTN")
        logger.info("Applied WSL/CUDA13 vLLM compat env vars")
