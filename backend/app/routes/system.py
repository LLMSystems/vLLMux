import psutil
from fastapi import APIRouter, HTTPException, Request

from app.services import gpu_service

router = APIRouter()


@router.get("/resources")
def get_system_resources():
    return {
        "cpu": psutil.cpu_percent(interval=0.2),
        "memory": psutil.virtual_memory()._asdict(),
        "gpus": gpu_service.get_gpu_info(),
    }


@router.get("/gpu/processes")
def get_gpu_processes(request: Request):
    """從內存中獲取 GPU 進程信息（由後台任務定期更新）"""
    if hasattr(request.app.state, "gpu_processes"):
        return request.app.state.gpu_processes
    try:
        return gpu_service.get_gpu_processes_with_info()
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get GPU processes: {str(e)}"
        )
