"""FastAPI app factory + lifespan wiring.

Lifespan responsibilities:
  - load + validate config (typed RootConfig)
  - build the ModelRegistry (one record per configured instance, all STOPPED)
  - build the ModelManager and stash it + the registry on app.state
  - adopt any already-running backends, then start the reconcile + GPU loops
  - on shutdown: cancel loops, reap managed processes, close the HTTP client
"""
import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import config as config_routes
from app.api import models as model_routes
from app.api import observability as observability_routes
from app.api import system as system_routes
from app.core.config import get_config_path
from app.core.logging import setup_logging
from app.core.settings import BackendSettings
from app.core.store import LLMOpsStore
from app.llmops.launchers import EmbeddingLauncher, VllmLauncher
from app.llmops.manager import ModelManager, build_registry
from app.llmops.reconciler import adopt_running, reconcile_loop
from app.llmops.state import ModelState
from app.services.gpu_service import get_gpu_processes_with_info
from app.services.overlay import build_merged_config, overlay_path

setup_logging()
logger = logging.getLogger(__name__)


async def _gpu_poll_loop(app: FastAPI, interval: float) -> None:
    """Refresh the GPU-process inventory in the background."""
    loop = asyncio.get_event_loop()
    while True:
        try:
            app.state.gpu_processes = await loop.run_in_executor(
                None, get_gpu_processes_with_info
            )
        except Exception:
            logger.exception("GPU process poll failed")
            app.state.gpu_processes = []
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config_path = get_config_path()
    ov_path = overlay_path()
    # Base config.yaml + dynamically-added models (overlay), merged into one view.
    config = build_merged_config(config_path)
    settings = BackendSettings.from_env()

    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=2.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )

    store = await LLMOpsStore(settings.db_path).init()

    launchers = [VllmLauncher(), EmbeddingLauncher()]
    registry = build_registry(config, config_path, launchers)
    manager = ModelManager(
        registry, launchers, http_client, config, config_path, settings, store=store,
        overlay_path=ov_path,
    )

    app.state.config = config
    app.state.config_path = config_path
    app.state.settings = settings
    app.state.http_client = http_client
    app.state.store = store
    app.state.registry = registry
    app.state.manager = manager
    app.state.gpu_processes = []

    logger.info("Config loaded from %s (%d instances)", config_path, len(registry.keys()))
    logger.info("Telemetry store at %s", store.db_path)

    # Adopt anything already healthy before starting the loops, so state is
    # honest from the first response.
    await adopt_running(registry, http_client, settings, store)

    tasks = [
        asyncio.create_task(reconcile_loop(registry, http_client, settings, store)),
        asyncio.create_task(_gpu_poll_loop(app, settings.gpu_poll_interval)),
    ]
    logger.info("Reconciler + GPU poller started")

    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        await manager.stop_all()
        await http_client.aclose()
        await store.close()
        logger.info("Backend shut down")


def create_app() -> FastAPI:
    app = FastAPI(title="LLM Router Dashboard Backend", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(model_routes.router, prefix="/api")
    app.include_router(system_routes.router, prefix="/api")
    app.include_router(config_routes.router, prefix="/api")
    app.include_router(observability_routes.router, prefix="/api")

    @app.get("/healthz", tags=["health"])
    async def healthz():
        """Liveness probe for the backend itself + a model state-count summary."""
        counts: dict[str, int] = {}
        if hasattr(app.state, "registry"):
            for inst in await app.state.registry.snapshot():
                counts[inst.state.value] = counts.get(inst.state.value, 0) + 1
        return {"status": "ok", "models": counts}

    return app


app = create_app()
