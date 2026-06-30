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
            # HA Phase 3a: refresh live instance addresses from the shared store so
            # scraping (and, via app.state.live_addrs, routing) follows where each
            # instance actually runs rather than assuming the backend's localhost.
            # Falls back to the config address when an instance hasn't published one
            # yet — so collapsed single-host deploys behave exactly as before.
            live_addrs: Dict[tuple, tuple] = {}
            store = getattr(app.state, "store", None)
            if store is not None and hasattr(store, "list_instances_live"):
                try:
                    for r in await store.list_instances_live():
                        live_addrs[(r["group_key"], r["instance_id"])] = (r["host"], r["port"])
                except Exception:
                    pass
            app.state.live_addrs = live_addrs

            index = []  # (model_key, instance_id, composite_key)
            backends: Dict[str, tuple] = {}  # composite -> (url, engine)
            for model_key, model_cfg in llm_engines.items():
                # Pick the engine's metric parser (sglang exposes sglang:* names,
                # not vllm:*). Default vllm so existing configs are unchanged.
                engine = (model_cfg.get("model_config") or {}).get("engine", "vllm")
                for instance in model_cfg.get("instances", []):
                    composite = f"{model_key}\x00{instance['id']}"
                    host, port = live_addrs.get(
                        (model_key, instance["id"]),
                        (instance.get("host", "localhost"), instance["port"]),
                    )
                    url = f"http://{host}:{port}"
                    index.append((model_key, instance["id"], composite))
                    backends[composite] = (url, engine)

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
                        sleep_backends[composite] = backends[composite][0]  # url only
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

            # HA: refresh the shared drain set so a backend's drain (written via any
            # router's /drain) is honoured by *every* router replica, and expired
            # marks heal. No-op when there's no store.
            store = getattr(app.state, "store", None)
            if store is not None and hasattr(store, "list_draining"):
                try:
                    app.state.draining = await store.list_draining()
                except Exception:
                    pass

            await asyncio.sleep(interval)

    except asyncio.CancelledError:
        logger.info("Metrics poller cancelled.")
        raise
    except Exception:
        logger.exception("Metrics poller crashed.")
        raise