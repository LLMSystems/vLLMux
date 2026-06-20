"""Backward-compatible least-load selector.

The routing policy is now pluggable (see routing_strategies.py). This module keeps
the original entry point as a thin wrapper over the `least_load` strategy so
existing imports and tests keep working unchanged.
"""
from typing import Any, Dict, Optional, Set

from src.llm_router.routing_strategies import select_instance


async def select_instance_least_load(
    app,
    model_key: str,
    model_cfg: Dict[str, Any],
    exclude: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Pick the least-loaded instance for a model group (the historical default).

    Equivalent to `select_instance(..., strategy="least_load")`.
    """
    return await select_instance(
        app, model_key, model_cfg, strategy="least_load", exclude=exclude
    )
