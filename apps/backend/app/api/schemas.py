"""API response models for the /models resource."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.llmops.instance import ModelInstance
from app.llmops.state import Desired, ModelKind, ModelState


class ModelView(BaseModel):
    """Public view of one model instance's lifecycle state."""

    key: str
    kind: ModelKind
    # Which inference engine backs this instance ("vllm" / "sglang" / …).
    engine: str = "vllm"
    model_tag: Optional[str] = None
    host: str
    port: int
    state: ModelState
    desired: Desired
    managed: bool
    pid: Optional[int] = None
    last_error: Optional[str] = None
    started_at: Optional[float] = None
    ready_at: Optional[float] = None
    updated_at: float
    restart_count: int = 0
    # HA Phase 3d: which node-agent runs this instance (multi-node deploys). None
    # on a single host / when unknown.
    node_id: Optional[str] = None

    @classmethod
    def from_instance(cls, inst: ModelInstance) -> "ModelView":
        return cls(
            key=inst.key,
            kind=inst.kind,
            engine=inst.engine,
            model_tag=inst.model_tag,
            host=inst.host,
            port=inst.port,
            state=inst.state,
            desired=inst.desired,
            managed=inst.managed,
            pid=inst.pid,
            last_error=inst.last_error,
            started_at=inst.started_at,
            ready_at=inst.ready_at,
            updated_at=inst.updated_at,
            restart_count=inst.restart_count,
        )
