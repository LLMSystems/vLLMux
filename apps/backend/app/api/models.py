"""The unified /models resource: LLM and embedding backends alike.

The route layer is intentionally thin — it translates ModelManager outcomes and
exceptions into HTTP, and holds no process/state logic of its own.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_manager
from app.api.schemas import ModelView
from app.core.auth import require_admin
from app.llmops.manager import (
    GpuUnavailable,
    LoraRuntimeError,
    ModelAlreadyRunning,
    ModelConflict,
    ModelManager,
    ModelNotFound,
    VRAMInsufficient,
)
from app.services.vllm_command import parse_vllm_command

router = APIRouter(prefix="/models", tags=["models"])


class ParseRequest(BaseModel):
    command: str


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


@router.get("", response_model=list[ModelView])
async def list_models(manager: ModelManager = Depends(get_manager)):
    return [ModelView.from_instance(i) for i in await manager.list()]


@router.post("/parse", dependencies=[Depends(require_admin)])
async def parse_command(body: ParseRequest, manager: ModelManager = Depends(get_manager)):
    """Parse a pasted vLLM command into editable fields + conflict hints."""
    try:
        parsed = parse_vllm_command(body.command)
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
             dependencies=[Depends(require_admin)])
async def create_model(body: CreateModelRequest, manager: ModelManager = Depends(get_manager)):
    """Add a dynamic LLM model (overlay). Appears immediately as STOPPED."""
    settings = dict(body.settings)
    if not settings.get("model_tag"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "settings.model_tag is required")
    try:
        inst = await manager.create_overlay_model(
            body.group, body.instance.model_dump(), settings
        )
    except ModelConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except Exception as e:  # pydantic ValidationError etc. -> bad request
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid model config: {e}")
    return ModelView.from_instance(inst)


@router.put("/{key}", response_model=ModelView, dependencies=[Depends(require_admin)])
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
               dependencies=[Depends(require_admin)])
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


@router.post("/{key}/lora", dependencies=[Depends(require_admin)])
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


@router.delete("/{key}/lora/{name}", dependencies=[Depends(require_admin)])
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
    try:
        return ModelView.from_instance(await manager.get(key))
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")


@router.post("/{key}/start", response_model=ModelView, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_admin)])
async def start_model(key: str, force: bool = False, manager: ModelManager = Depends(get_manager)):
    try:
        return ModelView.from_instance(await manager.start(key, force=force))
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")
    except ModelAlreadyRunning:
        raise HTTPException(status.HTTP_409_CONFLICT, f"model already running: {key}")
    except (VRAMInsufficient, GpuUnavailable) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.post("/{key}/stop", response_model=ModelView, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_admin)])
async def stop_model(key: str, manager: ModelManager = Depends(get_manager)):
    try:
        return ModelView.from_instance(await manager.stop(key))
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")
