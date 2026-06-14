"""Model-weight management: HF cache inventory + downloads."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.auth import require_admin
from app.services import hf_service

router = APIRouter(tags=["downloads"])


def _manager(request: Request):
    return request.app.state.download_manager


class DownloadRequest(BaseModel):
    repo_id: str = Field(min_length=1)


@router.get("/cache")
async def get_cache(request: Request):
    """Cached models + free space on the volume holding the HF cache."""
    import asyncio

    loop = asyncio.get_event_loop()
    models = await loop.run_in_executor(None, hf_service.scan_cache)
    disk = await loop.run_in_executor(None, hf_service.disk_usage)
    return {"disk": disk, "models": models}


@router.delete("/cache/{repo_id:path}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_admin)])
async def delete_cached_model(repo_id: str, request: Request):
    import asyncio

    loop = asyncio.get_event_loop()
    try:
        deleted = await loop.run_in_executor(None, hf_service.delete_repo, repo_id)
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"delete failed: {e}")
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"not cached: {repo_id}")


@router.get("/downloads")
async def list_downloads(request: Request):
    return _manager(request).list()


@router.post("/downloads", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_admin)])
async def start_download(body: DownloadRequest, request: Request):
    try:
        return _manager(request).start(body.repo_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
