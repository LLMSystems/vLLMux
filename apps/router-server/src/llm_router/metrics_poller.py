import asyncio
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def _probe_sleeping(http_client, backends: Dict[str, str]) -> Dict[str, bool]:
    """GET /is_sleeping for each backend; returns {composite_key: is_sleeping}.

    Any error / missing endpoint counts as not-sleeping, so a transient blip can
    never wrongly evict a serving instance from routing.
    """
    async def one(name: str, base_url: str) -> tuple[str, bool]:
        try:
            resp = await http_client.get(base_url.rstrip("/") + "/is_sleeping", timeout=2.0)
            if resp.status_code != 200:
                return name, False
            return name, bool(resp.json().get("is_sleeping", False))
        except Exception:
            return name, False

    pairs = await asyncio.gather(*(one(n, u) for n, u in backends.items()))
    return dict(pairs)


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

            # Sleep-aware routing: for groups launched with sleep mode, also probe
            # /is_sleeping so the router can skip a level-1-asleep instance (whose
            # /metrics may still answer with low load and otherwise look idle).
            # Only sleep-capable groups are probed, so non-sleep deployments pay
            # nothing extra.
            sleep_backends: Dict[str, str] = {}
            for model_key, model_cfg in llm_engines.items():
                if not model_cfg.get("model_config", {}).get("enable_sleep_mode"):
                    continue
                for instance in model_cfg.get("instances", []):
                    composite = f"{model_key}\x00{instance['id']}"
                    if composite in backends:
                        sleep_backends[composite] = backends[composite]
            sleeping = (
                await _probe_sleeping(app.state.http_client, sleep_backends)
                if sleep_backends else {}
            )

            new_cache: Dict[str, Dict[str, Any]] = {}
            for model_key, instance_id, composite in index:
                metric = flat[composite]
                if sleeping.get(composite):
                    metric.is_sleeping = True
                new_cache.setdefault(model_key, {})[instance_id] = metric

            app.state.metrics_cache = new_cache
            await asyncio.sleep(interval)

    except asyncio.CancelledError:
        logger.info("Metrics poller cancelled.")
        raise
    except Exception:
        logger.exception("Metrics poller crashed.")
        raise