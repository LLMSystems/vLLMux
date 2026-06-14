from typing import Any, Dict, Optional, Set

from fastapi import HTTPException
import logging

from src.llm_router.backend_runtime_state import (FAIL_OPEN_PENALTY,
                                                  INFLIGHT_WEIGHT,
                                                  get_inflight,
                                                  is_backend_in_cooldown)

logger = logging.getLogger(__name__)


async def select_instance_least_load(
    app,
    model_key: str,
    model_cfg: Dict[str, Any],
    exclude: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Pick the least-loaded instance for a model group.

    `exclude` is the set of instance ids already tried this request; it lets the
    proxy fail over to the next-best backend without re-picking a dead one.
    """
    instances = model_cfg.get("instances", [])
    if not instances:
        raise HTTPException(status_code=500, detail=f"Model '{model_key}' has no instances configured.")

    exclude = exclude or set()
    candidates = [i for i in instances if i["id"] not in exclude]
    if not candidates:
        raise HTTPException(
            status_code=503,
            detail=f"No remaining instance to try for model '{model_key}'.",
        )

    if len(candidates) == 1:
        return candidates[0]

    metrics_map = app.state.metrics_cache.get(model_key, {})
    best_instance = None
    best_score = float("inf")

    for instance in candidates:
        instance_id = instance["id"]
        metric = metrics_map.get(instance_id)

        # metric is None only before the first scrape lands (fresh start or a
        # just-reloaded group). Don't skip — that would 500 the whole request
        # during the cold-start window. Treat unknown load as idle (0) and let
        # the inflight penalty spread requests until real metrics arrive.
        if metric is None:
            logger.warning(
                "No cached metrics for model=%s backend=%s; assuming idle.",
                model_key,
                instance_id,
            )
            base_score = 0.0
        else:
            base_score = metric.compute_load_score()

        inflight = get_inflight(app, model_key, instance_id)
        inflight_penalty = inflight * INFLIGHT_WEIGHT

        cooldown_penalty = FAIL_OPEN_PENALTY if is_backend_in_cooldown(app, model_key, instance_id) else 0.0

        final_score = base_score + inflight_penalty + cooldown_penalty

        # debug, not info: this runs per-instance per-request — at INFO it floods
        # the single event loop with string formatting + stdout writes under load.
        logger.debug(
            "Instance %s has load score %.4f, base_score=%.4f, inflight_penalty=%.4f, cooldown_penalty=%.4f",
            instance_id,
            final_score,
            base_score,
            inflight_penalty,
            cooldown_penalty,
        )
        
        if final_score < best_score:
            best_score = final_score
            best_instance = instance

    if best_instance is None:
        raise HTTPException(status_code=500, detail=f"No suitable instance found for model '{model_key}'.")

    return best_instance