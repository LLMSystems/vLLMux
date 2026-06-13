"""System resource + GPU inspection endpoints."""
import psutil
from fastapi import APIRouter, HTTPException, Request

from app.services import gpu_service

router = APIRouter(tags=["system"])


@router.get("/resources")
def get_system_resources():
    return {
        "cpu": psutil.cpu_percent(interval=0.2),
        "memory": psutil.virtual_memory()._asdict(),
        "gpus": gpu_service.get_gpu_info(),
    }


@router.get("/gpu/processes")
def get_gpu_processes(request: Request):
    """Read the GPU-process inventory cached by the background poller."""
    if hasattr(request.app.state, "gpu_processes"):
        return request.app.state.gpu_processes
    try:
        return gpu_service.get_gpu_processes_with_info()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get GPU processes: {e}")
