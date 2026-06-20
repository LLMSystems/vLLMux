import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
import uvloop
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.llm_router.overlay import load_config_with_overlay
from src.llm_router.metrics_poller import poll_metrics_forever
from src.llm_router.router import router
from src.llm_router.routing_strategies import DEFAULT_STRATEGY
from src.llm_router.store import LLMOpsStore
from src.llm_router.vllm_metrics_client import VLLMMetricsClient

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


setup_logging()
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting application lifespan...")
    # read=None: LLM generations can take far longer than any fixed timeout, and
    # for non-streaming requests no bytes arrive until generation completes — a
    # read timeout here would kill long completions. Per-request callers that
    # need a bound (metrics poller, embeddings) pass their own timeout.
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=10.0),
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
        ),
    )
    app.state.metrics_client = VLLMMetricsClient(
        http_client=app.state.http_client,
        timeout=2
    )
    
    app.state.metrics_cache = {}
    app.state.backend_inflight = {}
    app.state.backend_health = {}
    # Routing policy: global default (per-group overrides ride model_config), plus
    # the round-robin cursor map. See routing_strategies.py.
    app.state.routing_strategy = os.environ.get("LLMOPS_ROUTING_STRATEGY", DEFAULT_STRATEGY)
    app.state.rr_counters = {}

    # Shared telemetry DB (same file the dashboard backend reads). LLMOPS_DB_PATH
    # must match the backend; default resolves to <repo>/data/llmops.db.
    app.state.store = await LLMOpsStore(os.environ.get("LLMOPS_DB_PATH")).init()

    metrics_task = asyncio.create_task(poll_metrics_forever(app, interval=1.0))
    app.state.metrics_task = metrics_task
    try:
        yield
    finally:
        logger.info("Shutting down application lifespan...")
        metrics_task.cancel()
        try:
            await metrics_task
        except asyncio.CancelledError:
            pass

        await app.state.http_client.aclose()
        await app.state.store.close()

def create_app(config: dict) -> FastAPI:
    app = FastAPI(title="LLM Router API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.config = config
    app.include_router(router)

    return app

config_path = os.environ.get("CONFIG_PATH", "../../packages/config-schema/config.yaml")
# Base config.yaml + dynamic-model overlay (POST /reload re-reads both).
config = load_config_with_overlay(config_path)
app = create_app(config)
app.state.config_path = config_path