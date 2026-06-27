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
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth as auth_routes
from app.api import config as config_routes
from app.api import datasets as dataset_routes
from app.api import downloads as download_routes
from app.api import embedding as embedding_routes
from app.api import eval as eval_routes
from app.api import lora as lora_routes
from app.api import models as model_routes
from app.api import perf as perf_routes
from app.api import observability as observability_routes
from app.api import system as system_routes
from app.core.config import get_config_path
from app.core.logging import setup_logging
from app.core.settings import BackendSettings
from app.core.store import LLMOpsStore
from app.llmops.launchers import EmbeddingLauncher, VllmLauncher
from app.llmops.manager import ModelManager, build_registry
from app.llmops.autoscaler import autoscaler_loop
from app.llmops.load_monitor import load_monitor_loop
from app.llmops.reconciler import adopt_running, reconcile_loop
from app.llmops.state import ModelState
from app.eval.manager import EvalManager
from app.perf.manager import PerfManager
from app.services.dataset_downloads import DatasetDownloadManager
from app.services.downloads import DownloadManager
from app.services.lora_downloads import LoraDownloadManager
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
    # `or` (not get's default) so an env var set-but-empty (as the compose env
    # passes it) still falls back instead of yielding "" — an empty router_url
    # would no-op router reloads and the load-monitor scrape.
    router_url = os.environ.get("LLMOPS_ROUTER_URL") or "http://127.0.0.1:8887"
    manager = ModelManager(
        registry, launchers, http_client, config, config_path, settings, store=store,
        overlay_path=ov_path, router_url=router_url,
    )

    app.state.config = config
    app.state.config_path = config_path
    app.state.settings = settings
    app.state.http_client = http_client
    app.state.store = store
    app.state.registry = registry
    app.state.manager = manager
    app.state.gpu_processes = []
    app.state.download_manager = DownloadManager()
    app.state.dataset_download_manager = DatasetDownloadManager()
    app.state.lora_download_manager = LoraDownloadManager()
    perf_root = os.path.join(os.path.dirname(store.db_path), "perf")
    app.state.perf_manager = PerfManager(store, manager, settings, perf_root, router_url)
    eval_root = os.path.join(os.path.dirname(store.db_path), "eval")
    app.state.eval_manager = EvalManager(store, manager, settings, eval_root, router_url)
    # A load test and an eval both contend for the GPU — they stay mutually
    # exclusive, so each manager needs a handle to the other.
    app.state.eval_manager.perf_manager = app.state.perf_manager
    app.state.perf_manager.eval_manager = app.state.eval_manager
    await store.mark_stale_perf_runs()  # orphaned 'running' rows from a prior crash
    await store.mark_stale_eval_runs()

    logger.info("Config loaded from %s (%d instances)", config_path, len(registry.keys()))
    logger.info("Telemetry store at %s", store.db_path)
    if not settings.auth_enabled:
        logger.warning(
            "LLMOPS_ADMIN_TOKEN not set — control endpoints are UNAUTHENTICATED. "
            "Set it to require an admin token for start/stop/add/edit/remove."
        )

    # Adopt anything already healthy before starting the loops, so state is
    # honest from the first response.
    await adopt_running(registry, http_client, settings, store)

    # Seed the Prometheus file_sd targets file (covering adopted-ready instances)
    # so monitoring has a valid file from t=0, before the first state transition.
    await manager.write_prometheus_targets()

    app.state.load_stats = {}
    tasks = [
        asyncio.create_task(reconcile_loop(registry, http_client, settings, store, manager)),
        asyncio.create_task(_gpu_poll_loop(app, settings.gpu_poll_interval)),
        asyncio.create_task(
            load_monitor_loop(app, registry, http_client, router_url, settings.load_poll_interval)
        ),
        asyncio.create_task(autoscaler_loop(app, manager, settings.autoscale_interval)),
    ]
    logger.info("Reconciler + GPU poller + load monitor + autoscaler started")

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
    app = FastAPI(title="vLLMux Backend", lifespan=lifespan)
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
    app.include_router(auth_routes.router, prefix="/api")
    app.include_router(download_routes.router, prefix="/api")
    app.include_router(lora_routes.router, prefix="/api")
    app.include_router(dataset_routes.router, prefix="/api")
    app.include_router(embedding_routes.router, prefix="/api")
    app.include_router(perf_routes.router, prefix="/api")
    app.include_router(eval_routes.router, prefix="/api")

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
