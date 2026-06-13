"""Config inspection endpoint."""
from fastapi import APIRouter

from app.services.config_service import summarize_config
from app.services.overlay import build_merged_config

router = APIRouter(tags=["config"])


@router.get("/config")
def get_config():
    # Merge base config.yaml with the dynamic-model overlay so the summary
    # reflects models added at runtime, not just what's on disk.
    return summarize_config(build_merged_config())
