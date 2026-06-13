"""FastAPI dependencies: pull singletons off app.state."""
from __future__ import annotations

from fastapi import Request

from app.llmops.manager import ModelManager


def get_manager(request: Request) -> ModelManager:
    return request.app.state.manager
