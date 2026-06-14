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

            llm_engines = config.get("LLM_engines", {})

            # Flatten every (group, instance) into a single batch so one slow or
            # down backend can't delay scraping the others. Previously groups
            # were scraped sequentially, so a poll cycle could balloon to
            # (num_groups_with_a_dead_backend x timeout) and the cache went
            # stale, degrading routing. A composite key keeps instance ids that
            # collide across groups distinct.
            index = []  # (model_key, instance_id, composite_key)
            backends: Dict[str, str] = {}
            for model_key, model_cfg in llm_engines.items():
                for instance in model_cfg.get("instances", []):
                    composite = f"{model_key}\x00{instance['id']}"
                    url = f"http://{instance.get('host', 'localhost')}:{instance['port']}"
                    index.append((model_key, instance["id"], composite))
                    backends[composite] = url

            flat = await metrics_client.fetch_many(backends) if backends else {}

            new_cache: Dict[str, Dict[str, Any]] = {}
            for model_key, instance_id, composite in index:
                new_cache.setdefault(model_key, {})[instance_id] = flat[composite]

            app.state.metrics_cache = new_cache
            await asyncio.sleep(interval)

    except asyncio.CancelledError:
        logger.info("Metrics poller cancelled.")
        raise
    except Exception:
        logger.exception("Metrics poller crashed.")
        raise