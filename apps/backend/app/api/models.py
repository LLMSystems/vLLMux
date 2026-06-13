"""The unified /models resource: LLM and embedding backends alike.

The route layer is intentionally thin — it translates ModelManager outcomes and
exceptions into HTTP, and holds no process/state logic of its own.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.api.deps import get_manager
from app.api.schemas import ModelView
from app.llmops.manager import ModelAlreadyRunning, ModelConflict, ModelManager, ModelNotFound
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


@router.post("/parse")
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


@router.post("", response_model=ModelView, status_code=status.HTTP_201_CREATED)
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


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(key: str, manager: ModelManager = Depends(get_manager)):
    """Remove a dynamically-added model (must be overlay-owned and stopped)."""
    try:
        await manager.delete_overlay_model(key)
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


@router.post("/{key}/start", response_model=ModelView, status_code=status.HTTP_202_ACCEPTED)
async def start_model(key: str, manager: ModelManager = Depends(get_manager)):
    try:
        return ModelView.from_instance(await manager.start(key))
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")
    except ModelAlreadyRunning:
        raise HTTPException(status.HTTP_409_CONFLICT, f"model already running: {key}")


@router.post("/{key}/stop", response_model=ModelView, status_code=status.HTTP_202_ACCEPTED)
async def stop_model(key: str, manager: ModelManager = Depends(get_manager)):
    try:
        return ModelView.from_instance(await manager.stop(key))
    except ModelNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model: {key}")
