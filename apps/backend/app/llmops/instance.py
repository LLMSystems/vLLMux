"""The single record the rest of the system reasons about: ModelInstance.

`LaunchSpec` is the resolved "how to start this" recipe a launcher produces from
config; `ModelInstance` is the live record the registry holds, combining that
recipe with the runtime state (process handle, observed state, timestamps).
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from app.llmops.state import Desired, ModelKind, ModelState


@dataclass
class LaunchSpec:
    """Everything needed to spawn one backend process, resolved from config.

    Produced by a Launcher.build_spec(); consumed by process.spawn_process().
    """

    key: str
    kind: ModelKind
    command: list[str]
    env: dict[str, str]
    log_path: str
    host: str
    port: int
    probe_url: str
    model_tag: Optional[str] = None
    # True when launched with --enable-sleep-mode + VLLM_SERVER_DEV_MODE=1, so the
    # /sleep, /wake_up and /is_sleeping dev endpoints are available.
    sleep_enabled: bool = False


@dataclass
class ModelInstance:
    """Live record for one model/instance — the unit the registry tracks.

    `state` is the observed truth maintained solely by the reconciler.
    `desired` is what the user asked for. `managed` is True only when *this*
    backend spawned the process (and therefore may kill it); adopted external
    processes are managed=False.
    """

    key: str
    kind: ModelKind
    host: str
    port: int
    spec: LaunchSpec
    model_tag: Optional[str] = None

    desired: Desired = Desired.STOPPED
    state: ModelState = ModelState.STOPPED
    proc: Optional[subprocess.Popen] = None
    managed: bool = False
    pid: Optional[int] = None
    last_error: Optional[str] = None
    log_path: Optional[str] = None

    started_at: Optional[float] = None
    ready_at: Optional[float] = None
    updated_at: float = field(default_factory=time.time)

    # Auto-restart bookkeeping (managed by the reconciler).
    restart_count: int = 0
    next_restart_at: Optional[float] = None

    # Startup progress tracking: the reconciler watches the log file growing while
    # STARTING so a slow weight download/load isn't mistaken for a hang. The
    # timeout fires only after start_timeout of *no* progress, not total elapsed.
    last_log_size: int = 0
    last_progress_at: Optional[float] = None

    def touch(self) -> None:
        self.updated_at = time.time()

    def set_state(self, state: ModelState) -> None:
        """Transition observed state, stamping ready_at on entry to READY."""
        if state == ModelState.READY and self.state != ModelState.READY:
            self.ready_at = time.time()
        self.state = state
        self.touch()
