import time

INFLIGHT_WEIGHT = 5.0
FAIL_OPEN_PENALTY = 1e9


def make_backend_key(model_key: str, instance_id: str) -> str:
    return f"{model_key}::{instance_id}"


def get_inflight(app, model_key: str, instance_id: str) -> int:
    key = make_backend_key(model_key, instance_id)
    return app.state.backend_inflight.get(key, 0)


def incr_inflight(app, model_key: str, instance_id: str) -> None:
    key = make_backend_key(model_key, instance_id)
    app.state.backend_inflight[key] = app.state.backend_inflight.get(key, 0) + 1


def decr_inflight(app, model_key: str, instance_id: str) -> None:
    key = make_backend_key(model_key, instance_id)
    current = app.state.backend_inflight.get(key, 0)
    if current <= 1:
        app.state.backend_inflight.pop(key, None)
    else:
        app.state.backend_inflight[key] = current - 1


def is_backend_in_cooldown(app, model_key: str, instance_id: str) -> bool:
    key = make_backend_key(model_key, instance_id)
    state = app.state.backend_health.get(key, {})
    cooldown_until = state.get("cooldown_until", 0.0)
    return cooldown_until > time.time()


def mark_backend_failure(
    app,
    model_key: str,
    instance_id: str,
    error: str,
    cooldown_seconds: float = 10.0,
) -> None:
    key = make_backend_key(model_key, instance_id)
    state = app.state.backend_health.get(key, {})
    fail_count = state.get("fail_count", 0) + 1

    app.state.backend_health[key] = {
        "fail_count": fail_count,
        "cooldown_until": time.time() + cooldown_seconds,
        "last_error": error,
    }


def mark_backend_success(app, model_key: str, instance_id: str) -> None:
    key = make_backend_key(model_key, instance_id)
    if key in app.state.backend_health:
        app.state.backend_health[key]["fail_count"] = 0
        app.state.backend_health[key]["cooldown_until"] = 0.0
        app.state.backend_health[key]["last_error"] = None