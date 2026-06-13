import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
import uvloop
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.llm_router.config_loader import load_config
from src.llm_router.metrics_poller import poll_metrics_forever
from src.llm_router.router import router
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
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=2.0),
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

config_path = os.environ.get("CONFIG_PATH", "configs/config.yaml")
config = load_config(config_path)
app = create_app(config)