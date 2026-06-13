import asyncio
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def poll_metrics_forever(app, interval: float = 1.0):
    logger.info("Starting metrics poller with interval=%.2fs", interval)

    try:
        while True:
            config = app.state.config
            metrics_client = app.state.metrics_client

            new_cache: Dict[str, Dict[str, Any]] = {}

            llm_engines = config.get("LLM_engines", {})
            for model_key, model_cfg in llm_engines.items():
                instances = model_cfg.get("instances", [])
                if not instances:
                    continue

                backends = {
                    instance["id"]: f"http://{instance.get('host', 'localhost')}:{instance['port']}"
                    for instance in instances
                }

                metrics = await metrics_client.fetch_many(backends)
                new_cache[model_key] = metrics

            app.state.metrics_cache = new_cache
            await asyncio.sleep(interval)

    except asyncio.CancelledError:
        logger.info("Metrics poller cancelled.")
        raise
    except Exception:
        logger.exception("Metrics poller crashed.")
        raise