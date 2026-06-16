"""OS-level process primitives for model backends.

Three concerns live here, deliberately free of any registry/state knowledge so
they stay trivially testable:
  - env_setup(): vLLM env tweaks applied before spawning (WSL/CUDA13 compat).
  - spawn_process(): launch a LaunchSpec as a new-session subprocess with logs.
  - terminate_process_group(): kill the *whole* process group, not just the
    parent pid — vLLM spawns worker subprocesses that otherwise leak GPU memory.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
from typing import Optional

from app.llmops.instance import LaunchSpec

logger = logging.getLogger(__name__)


def _is_wsl() -> bool:
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def env_setup() -> None:
    """Apply vLLM env vars before spawning. Child processes inherit os.environ.

    Kept in sync with apps/router-server/src/llm_router/env.py. setdefault
    throughout so an explicit export by the operator always wins.
    """
    os.environ.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")
    # Unbuffered child stdout/stderr so a hard crash's traceback reaches the log
    # file instead of being lost in a never-flushed block buffer.
    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    # WSL + CUDA 13 / flashinfer 0.6.12 workaround:
    # - VLLM_USE_V2_MODEL_RUNNER=0 : the V2 runner needs UVA, which WSL disables.
    # - VLLM_USE_FLASHINFER_SAMPLER=0 + VLLM_ATTENTION_BACKEND=FLASH_ATTN :
    #   flashinfer 0.6.12's bundled CUB is incompatible with CUDA 13, so fall
    #   back to vLLM's prebuilt FlashAttention + native Torch sampler.
    # Applied only on WSL by default; override with LLM_ROUTER_VLLM_COMPAT=on|off.
    compat = os.environ.get("LLM_ROUTER_VLLM_COMPAT", "auto").lower()
    if compat == "on" or (compat == "auto" and _is_wsl()):
        os.environ.setdefault("VLLM_USE_V2_MODEL_RUNNER", "0")
        os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")
        os.environ.setdefault("VLLM_ATTENTION_BACKEND", "FLASH_ATTN")
        logger.info("Applied WSL/CUDA13 vLLM compat env vars")


def spawn_process(spec: LaunchSpec) -> subprocess.Popen:
    """Spawn the spec's command, redirecting stdout+stderr to its log file.

    start_new_session=True puts the child in its own process group so that
    terminate_process_group() can later reap the whole tree. Blocking: call via
    run_in_executor from async code.
    """
    env_setup()

    os.makedirs(os.path.dirname(spec.log_path), exist_ok=True)
    # Fresh log per launch so reconciler log-tails reflect *this* run only.
    log_file = open(spec.log_path, "w", encoding="utf-8")

    full_env = os.environ.copy()
    full_env.update(spec.env)

    logger.info("Launching [%s]: %s", spec.key, " ".join(spec.command))
    proc = subprocess.Popen(
        spec.command,
        env=full_env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    # The Popen keeps the fd; close our handle so we don't leak it.
    log_file.close()
    return proc


def terminate_process_group(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    """SIGTERM the process group, then SIGKILL if it doesn't exit in time.

    Blocking (proc.wait): call via run_in_executor from async code.
    """
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return

    try:
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=timeout)
        logger.info("Process group %s terminated gracefully", pgid)
    except subprocess.TimeoutExpired:
        logger.warning("Process group %s did not exit, sending SIGKILL", pgid)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
    except ProcessLookupError:
        pass


def kill_process_group(proc: subprocess.Popen) -> None:
    """SIGKILL the whole process group immediately, no graceful grace period.

    For force-stopping a wedged run that ignores SIGTERM. Blocking (proc.wait):
    call via run_in_executor from async code.
    """
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
        logger.warning("Process group %s force-killed (SIGKILL)", pgid)
        proc.wait()
    except ProcessLookupError:
        pass


def read_log_tail(log_path: str, max_bytes: int = 2000) -> str:
    """Return the last ~max_bytes of a log file, for surfacing failure reasons."""
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            return f.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def read_log_lines(log_path: str, n: int = 200) -> Optional[str]:
    """Return the last `n` lines of a log file, or None if it doesn't exist."""
    if not log_path or not os.path.exists(log_path):
        return None
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return None


def read_log_head(log_path: str, max_bytes: int = 65536) -> Optional[str]:
    """Return the first ~max_bytes of a log file, or None if it doesn't exist.

    vLLM's startup capacity/memory/compile metrics are all printed near the top,
    so this avoids slurping a multi-MB serving log just to parse them."""
    if not log_path or not os.path.exists(log_path):
        return None
    try:
        with open(log_path, "rb") as f:
            return f.read(max_bytes).decode("utf-8", errors="replace")
    except OSError:
        return None
