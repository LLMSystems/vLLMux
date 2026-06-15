"""Load-test (evalscope perf) endpoints."""
from __future__ import annotations

import os
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.auth import require_admin
from app.perf.manager import PerfBusy, PerfError

router = APIRouter(prefix="/perf", tags=["perf"])


def _pm(request: Request):
    return request.app.state.perf_manager


def _store(request: Request):
    return request.app.state.store


class PerfRequest(BaseModel):
    model: str  # model group
    name: Optional[str] = None
    target: Literal["router", "instance"] = "router"
    instance_key: Optional[str] = None
    dataset: Literal["random", "openqa"] = "random"
    endpoint: Literal["chat", "completions"] = "chat"
    parallel: list[int] = Field(min_length=1)
    number: list[int] = Field(min_length=1)
    max_tokens: int = Field(default=256, ge=1)
    min_prompt_length: int = Field(default=512, ge=1)
    max_prompt_length: int = Field(default=512, ge=1)
    stream: bool = True
    warmup_num: Optional[float] = None


@router.get("")
async def list_runs(request: Request, limit: int = 50):
    return {"busy": _pm(request).busy, "runs": await _store(request).list_perf_runs(limit)}


@router.post("", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_admin)])
async def start_run(body: PerfRequest, request: Request):
    if len(body.parallel) != len(body.number):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "parallel and number must be the same length")
    try:
        return await _pm(request).start(body.model_dump())
    except PerfBusy as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except PerfError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/{run_id}")
async def get_run(run_id: int, request: Request):
    run = await _store(request).get_perf_run(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown perf run: {run_id}")
    return run


@router.get("/{run_id}/log")
async def get_run_log(run_id: int, request: Request, tail: int = 200):
    run = await _store(request).get_perf_run(run_id)
    if run is None or not run.get("output_dir"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown perf run: {run_id}")
    path = os.path.join(run["output_dir"], "run.log")
    if not os.path.exists(path):
        return {"content": ""}
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return {"content": "".join(lines[-tail:])}


@router.get("/{run_id}/report", response_class=FileResponse)
async def get_run_report(run_id: int, request: Request):
    """The full evalscope HTML report for a run (interactive charts + detail)."""
    run = await _store(request).get_perf_run(run_id)
    if run is None or not run.get("output_dir"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown perf run: {run_id}")
    path = os.path.join(run["output_dir"], "run", "perf_report.html")
    if not os.path.exists(path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not available for this run")
    return FileResponse(path, media_type="text/html")


@router.post("/{run_id}/cancel", dependencies=[Depends(require_admin)])
async def cancel_run(run_id: int, request: Request):
    if not await _pm(request).cancel(run_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "run is not active")
    return {"ok": True}


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_admin)])
async def delete_run(run_id: int, request: Request):
    if not await _store(request).delete_perf_run(run_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown perf run: {run_id}")
