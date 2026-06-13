"""The unified /models resource: LLM and embedding backends alike.

The route layer is intentionally thin — it translates ModelManager outcomes and
exceptions into HTTP, and holds no process/state logic of its own.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_manager
from app.api.schemas import ModelView
from app.llmops.manager import ModelAlreadyRunning, ModelManager, ModelNotFound

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=list[ModelView])
async def list_models(manager: ModelManager = Depends(get_manager)):
    return [ModelView.from_instance(i) for i in await manager.list()]


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
