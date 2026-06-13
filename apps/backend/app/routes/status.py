from fastapi import APIRouter, Request

from app.services.status_service import collect_model_status

router = APIRouter()


@router.get("/status/all")
async def get_all_model_status(request: Request):
    results = await collect_model_status(
        request.app.state.config,
        request.app.state.http_client,
        getattr(request.app.state, "starting_models", set()),
    )
    return {"models": results}
