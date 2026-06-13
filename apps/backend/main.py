import asyncio
import logging
from asyncio import subprocess
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_config_path, load_config
from app.core.logging import setup_logging
from app.routes import config, embedding, llm, status, system
from app.services.gpu_service import get_gpu_processes_with_info

CONFIG_PATH = get_config_path()

setup_logging()
logger = logging.getLogger(__name__)


async def update_gpu_processes_task(app: FastAPI):
    """後台任務：定期更新 GPU 進程信息"""
    while True:
        try:
            loop = asyncio.get_event_loop()
            processes = await loop.run_in_executor(None, get_gpu_processes_with_info)
            app.state.gpu_processes = processes
            logger.debug(f"Updated GPU processes: {len(processes)} processes")
        except Exception as e:
            logger.error(f"Error updating GPU processes: {e}")
            app.state.gpu_processes = []

        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.config = load_config(CONFIG_PATH)
    app.state.config_path = CONFIG_PATH
    app.state.starting_models = set()
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=2.0),
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
        ),
    )

    app.state.gpu_processes = []

    gpu_task = asyncio.create_task(update_gpu_processes_task(app))

    logger.info(f"{CONFIG_PATH} 載入完成")
    logger.info("GPU 進程監控任務已啟動")

    app.state.running_llm_procs = {}

    try:
        yield
    finally:
        if hasattr(app.state, "running_llm_procs"):
            for key, proc in app.state.running_llm_procs.items():
                if proc and proc.poll() is None:
                    logger.info(f"正在關閉模型 {key}...")
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                        logger.info(f"模型 {key} 已成功關閉")
                    except subprocess.TimeoutExpired:
                        logger.warning(f"模型 {key} 關閉逾時，強制終止")
                        proc.kill()
        gpu_task.cancel()
        try:
            await gpu_task
        except asyncio.CancelledError:
            pass

        await app.state.http_client.aclose()
        logger.info("FastAPI app 正在關閉")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(config.router, prefix="/api")
app.include_router(status.router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(embedding.router, prefix="/api")
app.include_router(llm.router, prefix="/api")
