"""Backend runtime tunables, sourced from environment variables.

Kept as a plain dataclass (rather than pulling in pydantic-settings) so there is
no extra dependency. All values have sensible defaults; override via env for
deployment without code changes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class BackendSettings:
    # How often the reconciler derives observed state from process + health.
    poll_interval: float = 2.0
    # STARTING -> FAILED if not READY within this many seconds.
    start_timeout: float = 300.0
    # Grace period for SIGTERM before SIGKILL when stopping.
    stop_timeout: float = 10.0
    # How often the GPU-process inventory is refreshed.
    gpu_poll_interval: float = 5.0
    # SQLite telemetry DB path. None -> let LLMOpsStore use its shared default.
    db_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "BackendSettings":
        return cls(
            poll_interval=_env_float("LLMOPS_POLL_INTERVAL", 2.0),
            start_timeout=_env_float("LLMOPS_START_TIMEOUT", 300.0),
            stop_timeout=_env_float("LLMOPS_STOP_TIMEOUT", 10.0),
            gpu_poll_interval=_env_float("LLMOPS_GPU_POLL_INTERVAL", 5.0),
            db_path=os.environ.get("LLMOPS_DB_PATH"),
        )
