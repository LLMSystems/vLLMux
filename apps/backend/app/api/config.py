"""Config inspection endpoint."""
from fastapi import APIRouter

from app.core.config import load_config
from app.services.config_service import summarize_config

router = APIRouter(tags=["config"])


@router.get("/config")
def get_config():
    return summarize_config(load_config())
