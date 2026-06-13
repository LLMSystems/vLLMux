from fastapi import APIRouter, Request

from app.services import model_service

router = APIRouter()


@router.post("/llm/start/{model_name}")
def start_llm_model(model_name: str, request: Request):
    try:
        model_service.start_llm(request.app, model_name, request.app.state.config_path)
        return {"status": "啟動中", "message": f"模型 {model_name} 啟動中"}
    except ValueError as ve:
        return {"status": "錯誤", "message": str(ve)}
    except Exception as e:
        return {"status": "錯誤", "message": f"啟動模型時發生錯誤: {e}"}


@router.post("/llm/stop/{model_name}")
def stop_llm_model(model_name: str, request: Request):
    try:
        model_service.stop_llm(request.app, model_name)
        return {"status": "未啟動", "message": f"模型 {model_name} 已關閉"}
    except Exception as e:
        return {"status": "錯誤", "message": f"停止模型時發生錯誤: {e}"}
