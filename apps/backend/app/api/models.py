"""The unified /models resource: LLM and embedding backends alike.

The route layer is intentionally thin — it translates ModelManager outcomes and
exceptions into HTTP, and holds no process/state logic of its own.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_manager
from app.api.schemas import ModelView
from app.core.auth import require_operator
from app.llmops.manager import (
    GpuUnavailable,
    LoraRuntimeError,
    ModelAlreadyRunning,
    ModelConflict,
    ModelManager,
    ModelNotFound,
    SleepError,
    VRAMInsufficient,
)
from app.services.vllm_command import parse_command as parse_engine_command

router = APIRouter(prefix="/models", tags=["models"])


class ParseRequest(BaseModel):
    command: str
    # Which engine's CLI to parse ('vllm' | 'sglang'). Omitted = sniff from the
    # command (sglang.launch_server -> sglang, else vLLM).
    engine: Optional[str] = None


class InstanceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    host: str = "localhost"
    port: int
    cuda_device: Optional[int] = None


class CreateModelRequest(BaseModel):
    group: str
    instance: InstanceSpec
    # Full vLLM model_config (model_tag + any flags); extra keys allowed downstream.
    settings: dict[str, Any] = {}
    # Group-level autoscaling policy. None clears it (the editor always sends the
    # current state, so an off-toggle removes the block).
    autoscale: dict[str, Any] | None = None


@router.get("", response_model=list[ModelView])
async def list_models(request: Request, manager: ModelManager = Depends(get_manager)):
    # HA: in Postgres/multi-node mode the fleet view comes from the shared store
    # (each node backfills its *owned* observed state) — on leader and follower
    # alike, since with per-node actuation (Phase 7) no single registry is complete.
    # SQLite collapsed: the local registry is the truth — identical to before.
    return [ModelView(**v) for v in await manager.fleet_views(prefer_store=manager.prefer_store_view())]


@router.post("/parse", dependencies=[Depends(require_operator)])
async def parse_command(body: ParseRequest, manager: ModelManager = Depends(get_manager)):
    """Parse a pasted vLLM / SGLang command into editable fields + conflict hints."""
    try:
        parsed = parse_engine_command(body.command, body.engine)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    inst = parsed["instance"]
    key = f"{parsed['group']}::{inst['id']}"
    parsed["conflicts"] = {
        "key_exists": manager.registry.get(key) is not None,
        "port_in_use": inst.get("port") in manager._used_ports(),
    }
    return parsed


@router.post("", response_model=ModelView, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_operator)])
async def create_model(body: CreateModelRequest, manager: ModelManager = Depends(get_manager)):
    """Add a dynamic LLM model (overlay). Appears immediately as STOPPED."""
    settings = dict(body.settings)
    if not settings.get("model_tag"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "settings.model_tag is required")
    try:
        inst = await manager.create_overlay_model(
            body.group, body.instance.model_dump(), settings, autoscale=body.autoscale
        )
    except ModelConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except Exception as e:  # pydantic ValidationError etc. -> bad request
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid model config: {e}")
    return ModelView.from_instance(inst)


@router.put("/{key}", response_model=ModelView, dependencies=[Depends(require_operator)])
async def update_model(
    key: str, body: CreateModelRequest, manager: ModelManager = Depends(get_manager)
):
    """Edit a stopped model's instance fields / model_config (overlay override).

    `group` / instance `id` are fixed by the key and ignored if changed."""
    settings = dict(body.settings)
    if not settings.get("model_tag"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "settings.model_tag is required")
    try:
        inst = await manager.update_overlay_model(key, body.instance.model_dump(), settings)
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")
    except ModelConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except Exception as e:  # pydantic ValidationError etc. -> bad request
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid model config: {e}")
    return ModelView.from_instance(inst)


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_operator)])
async def delete_model(key: str, manager: ModelManager = Depends(get_manager)):
    """Remove a dynamically-added model (must be overlay-owned and stopped)."""
    try:
        await manager.delete_overlay_model(key)
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")
    except ModelConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


class LoadLoraRequest(BaseModel):
    name: str = Field(min_length=1)  # served name (request `model` field)
    path: str = Field(min_length=1)  # in-container adapter path, e.g. /lora/<name>
    base_model_name: Optional[str] = None


def _group_of(key: str) -> str:
    """Hot-LoRA ops are group-level; accept a model key or a bare group."""
    return key.split("::")[0]


@router.post("/{key}/lora", dependencies=[Depends(require_operator)])
async def load_lora(key: str, body: LoadLoraRequest, manager: ModelManager = Depends(get_manager)):
    """Hot-load a LoRA into every ready instance of the group + persist it to the
    overlay. Call POST /v1/reload on the router afterwards so it routes the new
    adapter (the frontend does this)."""
    try:
        return await manager.load_lora(_group_of(key), body.name, body.path, body.base_model_name)
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")
    except ModelConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except LoraRuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LoRA load failed (rolled back): {e}")


@router.delete("/{key}/lora/{name}", dependencies=[Depends(require_operator)])
async def unload_lora(key: str, name: str, manager: ModelManager = Depends(get_manager)):
    """Hot-unload a LoRA from every ready instance + drop it from the overlay."""
    try:
        return await manager.unload_lora(_group_of(key), name)
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")
    except ModelConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.get("/{key}", response_model=ModelView)
async def get_model(key: str, manager: ModelManager = Depends(get_manager)):
    # HA: like list_models, prefer the shared store's observed state in multi-node
    # mode so a model owned by another node shows its real state (not this node's
    # idle registry). Falls back to the local registry (collapsed / not in store).
    if manager.prefer_store_view():
        for v in await manager.fleet_views(prefer_store=True):
            if v.get("key") == key:
                return ModelView(**v)
    try:
        return ModelView.from_instance(await manager.get(key))
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")


def _reject_if_autoscaled(manager: ModelManager, key: str) -> None:
    """Block manual lifecycle control on an autoscale-owned group — the autoscaler
    holds the desired state, so a manual start/stop would just be undone. Turning
    off the group's autoscale returns control to the operator."""
    group = key.partition("::")[0]
    engine = manager.config.LLM_engines.get(group)
    autoscale = getattr(engine, "autoscale", None) if engine else None
    if autoscale is not None and autoscale.enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"'{group}' is autoscaled — manual start/stop/sleep is disabled. "
            f"Turn off autoscale for the group to control it by hand.",
        )


@router.post("/{key}/start", response_model=ModelView, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_operator)])
async def start_model(key: str, force: bool = False, manager: ModelManager = Depends(get_manager)):
    _reject_if_autoscaled(manager, key)
    try:
        return ModelView.from_instance(await manager.start(key, force=force))
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")
    except ModelAlreadyRunning:
        raise HTTPException(status.HTTP_409_CONFLICT, f"model already running: {key}")
    except (VRAMInsufficient, GpuUnavailable) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.post("/{key}/stop", response_model=ModelView, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_operator)])
async def stop_model(key: str, manager: ModelManager = Depends(get_manager)):
    _reject_if_autoscaled(manager, key)
    try:
        return ModelView.from_instance(await manager.stop(key))
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")


class AutoscaleRequest(BaseModel):
    """Group autoscale policy from the UI. enabled=false (or omitted) turns it off.
    Basic knobs (min/max replicas) plus the advanced timing/threshold fields; any
    field left None keeps the AutoscaleConfig default."""
    enabled: bool = False
    min_ready: int = Field(default=1, ge=0)
    max_ready: int | None = Field(default=None, ge=1)
    min_warm: int | None = Field(default=None, ge=0)
    # Advanced (optional) — see AutoscaleConfig for semantics & defaults.
    scale_up_waiting: float | None = Field(default=None, gt=0)
    scale_up_window_s: float | None = Field(default=None, ge=0)
    sleep_after_s: float | None = Field(default=None, ge=0)
    stop_after_s: float | None = Field(default=None, ge=0)
    cooldown_s: float | None = Field(default=None, ge=0)


@router.put("/{group}/autoscale", dependencies=[Depends(require_operator)])
async def set_group_autoscale(
    group: str, body: AutoscaleRequest, manager: ModelManager = Depends(get_manager)
):
    """Enable/disable + tune a group's autoscaling. Does not require the group
    stopped (autoscale is not a launch parameter)."""
    payload = None
    if body.enabled:
        payload = {"enabled": True, "min_ready": body.min_ready}
        for field in (
            "max_ready", "min_warm", "scale_up_waiting", "scale_up_window_s",
            "sleep_after_s", "stop_after_s", "cooldown_s",
        ):
            value = getattr(body, field)
            if value is not None:
                payload[field] = value
    try:
        return {"group": group, "autoscale": await manager.set_autoscale(group, payload)}
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown group: {group}")
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid autoscale config: {e}")


class FallbackRequest(BaseModel):
    """Group cross-model fallback chain (other group names, in order)."""
    fallback: list[str] = Field(default_factory=list)


@router.put("/{group}/fallback", dependencies=[Depends(require_operator)])
async def set_group_fallback(
    group: str, body: FallbackRequest, manager: ModelManager = Depends(get_manager)
):
    """Set/clear a group's cross-model fallback chain. No stop required."""
    try:
        return {"group": group, "fallback": await manager.set_fallback(group, body.fallback)}
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown group: {group}")
    except ModelConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid fallback config: {e}")


@router.post("/{key}/sleep", response_model=ModelView,
             dependencies=[Depends(require_operator)])
async def sleep_model(key: str, level: int = 1, manager: ModelManager = Depends(get_manager)):
    """Level-1 sleep a ready instance: free its VRAM but keep it warm for a
    seconds-fast wake. Requires the model to be launched with enable_sleep_mode."""
    _reject_if_autoscaled(manager, key)
    try:
        return ModelView.from_instance(await manager.sleep(key, level=level))
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")
    except ModelConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except SleepError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))


@router.post("/{key}/wake", response_model=ModelView,
             dependencies=[Depends(require_operator)])
async def wake_model(key: str, manager: ModelManager = Depends(get_manager)):
    """Wake a sleeping instance back to ready (reloads weights to GPU)."""
    _reject_if_autoscaled(manager, key)
    try:
        return ModelView.from_instance(await manager.wake(key))
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")
    except ModelConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except SleepError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
