"""Load-test (evalscope perf) endpoints."""
from __future__ import annotations

import os
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.auth import require_operator
from app.perf.manager import PerfBusy, PerfError

router = APIRouter(prefix="/perf", tags=["perf"])


def _pm(request: Request):
    return request.app.state.perf_manager


def _store(request: Request):
    return request.app.state.store


class PerfRequest(BaseModel):
    model: str  # model group
    name: Optional[str] = None
    mode: Literal["sweep", "openloop", "multiturn", "sla", "embedding", "rerank", "speed"] = "sweep"
    target: Literal["router", "instance"] = "router"
    instance_key: Optional[str] = None
    dataset: Literal["random", "openqa"] = "random"
    endpoint: Literal["chat", "completions"] = "chat"
    max_tokens: int = Field(default=256, ge=1)
    min_prompt_length: int = Field(default=512, ge=1)
    max_prompt_length: int = Field(default=512, ge=1)
    prefix_length: int = Field(default=0, ge=0)  # shared prefix per point (prefix-cache observation)
    duration: Optional[int] = Field(default=None, ge=1)  # soft time limit (multi-turn / open-loop)
    speed_long: bool = False  # speed mode: use speed_benchmark_long (63k/129k prompts)
    stream: bool = True
    # sweep / multiturn (parallel) — and number is shared by all closed-loop modes
    parallel: Optional[list[int]] = None
    number: Optional[list[int]] = None
    warmup_num: Optional[float] = None
    # open-loop mode (arrival rate sweep)
    rate: Optional[list[int]] = None
    # multi-turn mode
    mt_dataset: Literal["share_gpt_zh_multi_turn", "random_multi_turn", "custom_multi_turn"] = (
        "share_gpt_zh_multi_turn"
    )
    mt_dataset_path: Optional[str] = None  # for custom_multi_turn (server-side JSONL path)
    min_turns: int = Field(default=2, ge=1)
    max_turns: int = Field(default=4, ge=1)
    # sla auto-tune mode
    sla_variable: Literal["parallel", "rate"] = "parallel"
    sla_params: Optional[list[dict[str, str]]] = None  # [{metric: "op value"}], groups OR-ed
    sla_lower_bound: int = Field(default=1, ge=1)
    sla_upper_bound: int = Field(default=64, ge=1)
    sla_num_runs: int = Field(default=1, ge=1)
    sla_fixed_parallel: Optional[int] = Field(default=None, ge=1)
    # embedding / rerank mode (concurrency sweep, shares parallel/number)
    rerank_documents: int = Field(default=10, ge=1)


@router.get("")
async def list_runs(request: Request, limit: int = 50):
    return {"busy": _pm(request).busy, "runs": await _store(request).list_perf_runs(limit)}


@router.post("", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_operator)])
async def start_run(body: PerfRequest, request: Request):
    if body.mode == "sla":
        if not body.sla_params:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "sla_params is required in SLA mode")
    elif body.mode == "openloop":
        if not body.rate or not body.number:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "rate and number are required in open-loop mode")
        if len(body.rate) != len(body.number):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "rate and number must be the same length")
    elif body.mode == "multiturn":
        if not body.parallel or not body.number:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "parallel and number are required in multi-turn mode")
        if body.mt_dataset == "custom_multi_turn" and not body.mt_dataset_path:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "mt_dataset_path is required for custom_multi_turn")
    elif body.mode in ("embedding", "rerank"):
        if not body.parallel or not body.number:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"parallel and number are required in {body.mode} mode")
        if len(body.parallel) != len(body.number):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "parallel and number must be the same length")
    elif body.mode == "speed":
        pass  # parallel/number are fixed by the speed_benchmark dataset
    else:  # sweep
        if not body.parallel or not body.number:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "parallel and number are required in sweep mode")
        if len(body.parallel) != len(body.number):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "parallel and number must be the same length")
    # A load test measures latency/throughput and must own the GPU — refuse to
    # start while any eval is running or queued (evals share the GPU and would
    # pollute the numbers).
    if request.app.state.eval_manager.busy:
        raise HTTPException(status.HTTP_409_CONFLICT, "an eval is running or queued; try again later")
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
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown perf run: {run_id}")
    # output_dir is only persisted when the run finishes; while it's still running
    # derive the run dir from the manager root so the live log streams.
    out_dir = run.get("output_dir") or os.path.join(_pm(request).perf_root, str(run_id))
    path = os.path.join(out_dir, "run.log")
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


@router.post("/{run_id}/cancel", dependencies=[Depends(require_operator)])
async def cancel_run(run_id: int, request: Request, force: bool = False):
    """Stop a running load test. force=true SIGKILLs immediately (for a wedged
    run) instead of the default SIGTERM-then-SIGKILL grace."""
    if not await _pm(request).cancel(run_id, force=force):
        raise HTTPException(status.HTTP_409_CONFLICT, "run is not active")
    return {"ok": True}


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_operator)])
async def delete_run(run_id: int, request: Request):
    if not await _store(request).delete_perf_run(run_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown perf run: {run_id}")
