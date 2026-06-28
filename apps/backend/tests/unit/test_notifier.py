"""Lifecycle notifier: payload formatting, severity floor, cooldown dedup, and
event classification (model_failed / model_gave_up / model_recovered)."""
import asyncio
from types import SimpleNamespace

import pytest

from app.core.settings import BackendSettings
from app.llmops.events import classify_event, emit_transition
from app.llmops.notifier import (
    AlertEvent,
    Notifier,
    Sink,
    build_notifier,
    format_payload,
)
from app.llmops.state import Desired, ModelState

pytestmark = pytest.mark.unit


class FakeClient:
    def __init__(self):
        self.calls = []

    async def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))


def _ev(event="model_failed", severity="error"):
    return AlertEvent(event=event, severity=severity, model="Qwen::a", kind="llm",
                      detail="boom", restart_count=2)


# ---- formatting ----------------------------------------------------------

def test_format_slack_and_discord_and_webhook():
    ev = _ev()
    slack = format_payload("slack", ev)
    assert "text" in slack and "model_failed" in slack["text"] and "Qwen::a" in slack["text"]
    discord = format_payload("discord", ev)
    assert "content" in discord and "Qwen::a" in discord["content"]
    webhook = format_payload("webhook", ev)
    assert webhook["event"] == "model_failed" and webhook["model"] == "Qwen::a"
    assert webhook["error"] == "boom"  # back-compat alias


# ---- severity floor + cooldown -------------------------------------------

async def _drain():
    await asyncio.sleep(0.02)


async def test_per_sink_floor_filters_below_threshold():
    # Each sink decides by its own floor (no global gate): an `error`-floor sink
    # drops info but takes critical.
    c = FakeClient()
    n = Notifier(c, [Sink("webhook", "http://h", "error")])
    await n.notify(_ev(event="model_recovered", severity="info"))
    await _drain()
    assert c.calls == []  # info < sink floor (error)
    await n.notify(_ev(severity="critical"))
    await _drain()
    assert len(c.calls) == 1


async def test_no_global_gate_info_sink_receives_info():
    # A sink set to `info` receives info events even though the notifier default
    # is stricter — the default is not a hard gate.
    c = FakeClient()
    n = Notifier(c, [Sink("webhook", "http://h", "info")], min_severity="error")
    await n.notify(_ev(event="model_recovered", severity="info"))
    await _drain()
    assert len(c.calls) == 1


async def test_cooldown_suppresses_duplicate():
    c = FakeClient()
    n = Notifier(c, [Sink("webhook", "http://h")], min_severity="info", cooldown_s=999)
    await n.notify(_ev())
    await n.notify(_ev())  # same (model, event) within cooldown
    await _drain()
    assert len(c.calls) == 1


async def test_distinct_events_not_suppressed():
    c = FakeClient()
    n = Notifier(c, [Sink("webhook", "http://h")], min_severity="info", cooldown_s=999)
    await n.notify(_ev(event="model_failed"))
    await n.notify(_ev(event="model_gave_up", severity="critical"))
    await _drain()
    assert len(c.calls) == 2


async def test_per_sink_min_severity():
    c = FakeClient()
    sinks = [Sink("webhook", "http://low", "info"), Sink("slack", "http://high", "critical")]
    n = Notifier(c, sinks, min_severity="info")
    await n.notify(_ev(severity="error"))  # >= info, < critical
    await _drain()
    assert [u for u, _ in c.calls] == ["http://low"]


def test_build_notifier_from_settings():
    s = BackendSettings(alert_webhook="http://w", alert_slack_webhook="http://s",
                        alert_discord_webhook="http://d", alert_min_severity="warning")
    n = build_notifier(FakeClient(), s)
    assert {sink.type for sink in n.sinks} == {"webhook", "slack", "discord"}
    assert n.min_severity == "warning"
    assert not build_notifier(FakeClient(), BackendSettings()).enabled  # nothing configured


# ---- event classification ------------------------------------------------

def _inst(**kw):
    base = dict(key="Qwen::a", kind=SimpleNamespace(value="llm"), managed=True,
                desired=Desired.RUNNING, restart_count=0, next_restart_at=123.0,
                last_error="boom", was_failed=False)
    base.update(kw)
    return SimpleNamespace(**base)


SETTINGS = BackendSettings(auto_restart=True, max_restarts=3)


def test_classify_failed_within_budget():
    ev = classify_event(_inst(restart_count=1, next_restart_at=123.0),
                        ModelState.STARTING, ModelState.FAILED, "rc=1", SETTINGS)
    assert ev.event == "model_failed" and ev.severity == "error"


def test_classify_gave_up_when_budget_exhausted():
    ev = classify_event(_inst(restart_count=3, next_restart_at=None),
                        ModelState.STARTING, ModelState.FAILED, "rc=1", SETTINGS)
    assert ev.event == "model_gave_up" and ev.severity == "critical"


def test_classify_unmanaged_failure_is_not_gave_up():
    ev = classify_event(_inst(managed=False, restart_count=9, next_restart_at=None),
                        ModelState.READY, ModelState.FAILED, "died", SETTINGS)
    assert ev.event == "model_failed"


def test_classify_recovered_only_after_failure():
    assert classify_event(_inst(was_failed=False), ModelState.STARTING, ModelState.READY,
                          None, SETTINGS) is None
    ev = classify_event(_inst(was_failed=True), ModelState.STARTING, ModelState.READY,
                        None, SETTINGS)
    assert ev.event == "model_recovered" and ev.severity == "info"


def test_classify_benign_transition_is_none():
    assert classify_event(_inst(), ModelState.STOPPED, ModelState.STARTING,
                          None, SETTINGS) is None


# ---- emit_transition funnel ----------------------------------------------

class FakeStore:
    def __init__(self):
        self.events = []

    async def record_model_event(self, key, kind, frm, to, detail):
        self.events.append((key, frm, to, detail))


async def test_emit_persists_and_clears_was_failed_on_ready():
    store, c = FakeStore(), FakeClient()
    n = Notifier(c, [Sink("webhook", "http://h")], min_severity="info")
    inst = _inst(was_failed=True)
    await emit_transition(store, n, SETTINGS, inst, ModelState.STARTING, ModelState.READY, None)
    await _drain()
    assert store.events and store.events[0][2] == "ready"  # persisted
    assert inst.was_failed is False                        # cleared on READY
    assert len(c.calls) == 1                               # recovered alert sent
