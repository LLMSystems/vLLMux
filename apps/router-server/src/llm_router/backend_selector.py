from typing import Any, Dict

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
    model_cfg: Dict[str, Any]
) -> Dict[str, Any]:
    instances = model_cfg.get("instances", [])
    if not instances:
        raise HTTPException(status_code=500, detail=f"Model '{model_key}' has no instances configured.")

    if len(instances) == 1:
        return instances[0]

    metrics_map = app.state.metrics_cache.get(model_key, {})
    best_instance = None
    best_score = float("inf")
    
    for instance in instances:
        instance_id = instance["id"]
        metric = metrics_map.get(instance_id)

        if metric is None:
            logger.warning(
                "No cached metrics for model=%s backend=%s; skipping for now.",
                model_key,
                instance_id,
            )
            continue

        base_score = metric.compute_load_score()
        inflight = get_inflight(app, model_key, instance_id)
        inflight_penalty = inflight * INFLIGHT_WEIGHT

        cooldown_penalty = FAIL_OPEN_PENALTY if is_backend_in_cooldown(app, model_key, instance_id) else 0.0

        final_score = base_score + inflight_penalty + cooldown_penalty

        logger.info(
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