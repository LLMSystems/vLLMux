"""Embedding/reranking model parameter editing (overlay override)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_manager
from app.core.auth import require_operator
from app.llmops.manager import ModelConflict, ModelManager, ModelNotFound

router = APIRouter(prefix="/embedding", tags=["embedding"])


class UpdateEmbeddingModel(BaseModel):
    model_type: Literal["embedding", "reranking"]
    name: str
    settings: dict[str, Any]


@router.put("/models", dependencies=[Depends(require_operator)])
async def update_embedding_model(
    body: UpdateEmbeddingModel, manager: ModelManager = Depends(get_manager)
):
    """Edit one embedding/reranking model's params; effective on next launch."""
    try:
        await manager.update_embedding_model(body.model_type, body.name, body.settings)
    except ModelNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    except ModelConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid embedding config: {e}")
    return {"ok": True, "name": body.name, "model_type": body.model_type}
