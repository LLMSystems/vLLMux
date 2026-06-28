"""The single funnel for model state transitions: persist + classify + alert.

Both the reconciler (which detects crashes / startup timeouts) and the manager
(user-initiated transitions) route every transition through ``emit_transition``,
so alert-worthy events fire no matter which path produced them. This closes the
old gap where reconciler-detected crashes were persisted but never alerted.

See docs/alerting-design_zh-CN.md.
"""
from __future__ import annotations

import logging

from app.llmops.notifier import AlertEvent
from app.llmops.state import Desired, ModelState

logger = logging.getLogger(__name__)


def classify_event(inst, frm, to, detail, settings) -> AlertEvent | None:
    """Decide whether a transition is alert-worthy and at what severity.
    Pure / read-only — no side effects on ``inst``."""
    if to == ModelState.FAILED:
        # "Gave up" = we want it running and managed, but the restart budget is
        # spent and nothing is armed — the moment a human needs to step in.
        gave_up = (
            inst.managed
            and inst.desired == Desired.RUNNING
            and settings.auto_restart
            and inst.restart_count >= settings.max_restarts
            and inst.next_restart_at is None
        )
        return AlertEvent(
            event="model_gave_up" if gave_up else "model_failed",
            severity="critical" if gave_up else "error",
            model=inst.key,
            kind=inst.kind.value,
            detail=detail or inst.last_error,
            restart_count=inst.restart_count,
        )
    if to == ModelState.READY and getattr(inst, "was_failed", False):
        return AlertEvent(
            event="model_recovered",
            severity="info",
            model=inst.key,
            kind=inst.kind.value,
            restart_count=inst.restart_count,
        )
    return None


async def emit_transition(store, notifier, settings, inst, frm, to, detail=None) -> None:
    """Persist a transition to the event store, then (best-effort) dispatch an
    alert if it is alert-worthy. Telemetry/alerts never break ops."""
    if store is not None:
        try:
            await store.record_model_event(
                inst.key, inst.kind.value,
                frm.value if frm else None, to.value, detail,
            )
        except Exception:
            logger.exception("Failed to record model event for %s", inst.key)

    ev = classify_event(inst, frm, to, detail, settings) if notifier is not None else None
    # A healthy READY clears the failed flag so the *next* failure→ready pair is
    # what triggers the following recovery alert.
    if to == ModelState.READY:
        inst.was_failed = False
    if ev is not None:
        try:
            await notifier.notify(ev)
        except Exception:
            logger.warning("notifier.notify raised for %s (%s)", inst.key, ev.event)


async def emit_alert(notifier, ev: AlertEvent) -> None:
    """Dispatch a standalone alert not tied to a persisted transition
    (e.g. a synthetic event). Best-effort."""
    if notifier is None:
        return
    try:
        await notifier.notify(ev)
    except Exception:
        logger.warning("notifier.notify raised for standalone %s", ev.event)
