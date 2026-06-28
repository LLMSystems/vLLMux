"""Benchmark dataset management: ModelScope dataset cache inventory + downloads."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.auth import require_operator
from app.services import dataset_service

router = APIRouter(prefix="/datasets", tags=["datasets"])


def _manager(request: Request):
    return request.app.state.dataset_download_manager


class DatasetDownloadRequest(BaseModel):
    key: str = Field(min_length=1)


@router.get("")
async def list_datasets(request: Request):
    """Curated dataset catalog (cached flag + size) + free space on the cache volume."""
    loop = asyncio.get_event_loop()
    datasets = await loop.run_in_executor(None, dataset_service.scan)
    disk = await loop.run_in_executor(None, dataset_service.disk_usage)
    return {"disk": disk, "datasets": datasets}


@router.get("/downloads")
async def list_downloads(request: Request):
    return _manager(request).list()


@router.post("/download", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_operator)])
async def start_download(body: DatasetDownloadRequest, request: Request):
    try:
        return _manager(request).start(body.key)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_operator)])
async def delete_dataset(key: str, request: Request):
    loop = asyncio.get_event_loop()
    try:
        deleted = await loop.run_in_executor(None, dataset_service.delete, key)
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"delete failed: {e}")
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"not cached: {key}")
