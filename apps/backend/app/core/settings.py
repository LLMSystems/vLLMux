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


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


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
    # How often per-group live load (queue depth) is aggregated from the router scrape.
    load_poll_interval: float = 5.0
    # How often the autoscaler evaluates each autoscale-enabled group.
    autoscale_interval: float = 5.0
    # SQLite telemetry DB path. None -> let LLMOpsStore use its shared default.
    db_path: Optional[str] = None
    # Pre-flight VRAM check before starting a model (blocks likely-OOM starts).
    vram_guard: bool = True
    # Auto-restart a managed model that crashes while desired=running.
    auto_restart: bool = True
    # Max consecutive auto-restarts before giving up (budget resets once READY).
    max_restarts: int = 3
    # Exponential backoff base (seconds) between auto-restart attempts.
    restart_backoff_base: float = 5.0
    # Shared admin token gating control/write operations + API-key management.
    # Empty -> auth disabled (dev mode): writes are open and a warning is logged.
    admin_token: str = ""
    # Optional webhook URL; a JSON alert is POSTed when a model enters FAILED.
    alert_webhook: str = ""
    # Optional path for the Prometheus file_sd targets file. The backend rewrites
    # it whenever the set of ready vLLM instances changes, so Prometheus can
    # scrape a dynamic fleet without config edits. Empty -> feature disabled.
    prometheus_sd_path: str = ""
    # Total concurrency budget shared across running evals (sum of their
    # eval_batch_size). Evals run in parallel as long as the sum stays within
    # this; the rest queue. Maps to vLLM's max-num-seqs pressure. Runtime-editable
    # via the eval API (not persisted across restart).
    eval_concurrency_budget: int = 32

    @property
    def auth_enabled(self) -> bool:
        return bool(self.admin_token)

    @classmethod
    def from_env(cls) -> "BackendSettings":
        return cls(
            admin_token=os.environ.get("LLMOPS_ADMIN_TOKEN", "").strip(),
            alert_webhook=os.environ.get("LLMOPS_ALERT_WEBHOOK", "").strip(),
            prometheus_sd_path=os.environ.get("LLMOPS_PROMETHEUS_SD_PATH", "").strip(),
            poll_interval=_env_float("LLMOPS_POLL_INTERVAL", 2.0),
            start_timeout=_env_float("LLMOPS_START_TIMEOUT", 300.0),
            stop_timeout=_env_float("LLMOPS_STOP_TIMEOUT", 10.0),
            gpu_poll_interval=_env_float("LLMOPS_GPU_POLL_INTERVAL", 5.0),
            load_poll_interval=_env_float("LLMOPS_LOAD_POLL_INTERVAL", 5.0),
            autoscale_interval=_env_float("LLMOPS_AUTOSCALE_INTERVAL", 5.0),
            db_path=os.environ.get("LLMOPS_DB_PATH"),
            vram_guard=_env_bool("LLMOPS_VRAM_GUARD", True),
            auto_restart=_env_bool("LLMOPS_AUTO_RESTART", True),
            max_restarts=int(_env_float("LLMOPS_MAX_RESTARTS", 3)),
            restart_backoff_base=_env_float("LLMOPS_RESTART_BACKOFF", 5.0),
            eval_concurrency_budget=int(_env_float("LLMOPS_EVAL_CONCURRENCY_BUDGET", 32)),
        )
