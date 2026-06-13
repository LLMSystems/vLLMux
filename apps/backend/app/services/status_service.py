"""Aggregate health/status of every configured LLM instance + embedding server."""
import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


async def _check_status(
    http_client: httpx.AsyncClient,
    starting_models: set,
    name: str,
    host: str,
    port: int,
) -> dict:
    url = f"http://{host}:{port}/health"
    if name in starting_models:
        return {"name": name, "status": "啟動中", "port": port}
    try:
        resp = await http_client.get(url)
        if resp.status_code == 200:
            return {"name": name, "status": "已啟動", "port": port}
    except Exception:
        logger.error(f"無法連接模型 {name} : {url}")
    return {"name": name, "status": "未啟動", "port": port}


async def collect_model_status(
    config: dict,
    http_client: httpx.AsyncClient,
    starting_models: set,
) -> list[dict]:
    llm_engines = config.get("LLM_engines", {})
    embedding = config.get("embedding_server", None)

    tasks = []
    for name, cfg in llm_engines.items():
        for instance in cfg.get("instances", []):
            instance_id = instance.get("id")
            if not instance_id:
                continue
            host = instance.get("host", cfg.get("host", "localhost"))
            port = instance.get("port", cfg.get("port"))
            if port:
                tasks.append(
                    _check_status(
                        http_client, starting_models, f"{name}::{instance_id}", host, port
                    )
                )

    if embedding:
        host = embedding.get("host", "localhost")
        port = embedding.get("port")
        if port:
            tasks.append(
                _check_status(
                    http_client, starting_models, "Embedding & reranking Server", host, port
                )
            )

    return list(await asyncio.gather(*tasks))
