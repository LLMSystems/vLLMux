from fastapi import APIRouter, Request

from app.services import model_service

router = APIRouter()


@router.post("/embedding/start")
def start_embedding_server(request: Request):
    try:
        model_service.start_embedding(request.app, request.app.state.config_path)
        return {"status": "啟動中", "message": "Embedding Server 已啟動"}
    except Exception as e:
        return {"status": "錯誤", "message": str(e)}


@router.post("/embedding/stop")
def stop_embedding():
    try:
        model_service.stop_embedding()
        return {"status": "未啟動", "message": "Embedding Server 已關閉"}
    except Exception as e:
        return {"status": "錯誤", "message": str(e)}
