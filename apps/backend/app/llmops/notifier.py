"""Lifecycle-event notifier: pushes discrete alerts to Slack / Discord / a
generic webhook.

Complements Grafana (which handles metric/threshold alerts); this side handles
discrete state events — a model crashing, the auto-restart budget being
exhausted, a model recovering. Best-effort and fire-and-forget: a failed send is
logged, never raised, and never blocks the state machine.

See docs/alerting-design_zh-CN.md.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field

logger = logging.getLogger("llmops.notifier")

# Ordered severities; a sink/notifier only emits events at or above its floor.
_SEVERITY = {"info": 0, "warning": 1, "error": 2, "critical": 3}
_EMOJI = {"info": "✅", "warning": "⚠️", "error": "🔴", "critical": "🚨"}


def severity_rank(s: str) -> int:
    return _SEVERITY.get(s, 0)


@dataclass
class AlertEvent:
    event: str          # model_failed | model_gave_up | model_recovered | ...
    severity: str       # info | warning | error | critical
    model: str
    kind: str
    detail: str | None = None
    restart_count: int = 0
    ts: float = field(default_factory=time.time)


@dataclass
class Sink:
    type: str           # slack | discord | webhook
    url: str
    min_severity: str = "info"


# ---- payload formatters --------------------------------------------------

def _summary(ev: AlertEvent) -> str:
    line = f"{_EMOJI.get(ev.severity, '')} {ev.event} — {ev.model} ({ev.kind})"
    if ev.restart_count:
        line += f"  · restarts={ev.restart_count}"
    return line


def format_payload(sink_type: str, ev: AlertEvent) -> dict:
    """Render an event for a sink type. Slack/Discord get a text message; a
    generic webhook gets the raw event JSON (back-compatible with the old
    LLMOPS_ALERT_WEBHOOK payload, plus `severity`)."""
    if sink_type == "slack":
        text = f"*{_summary(ev)}*"
        if ev.detail:
            text += f"\n```{ev.detail[:1500]}```"
        return {"text": text}
    if sink_type == "discord":
        content = _summary(ev)
        if ev.detail:
            content += f"\n```{ev.detail[:1500]}```"
        return {"content": content[:1900]}
    # generic webhook → raw event (keep `error` alias for the old payload shape)
    payload = asdict(ev)
    payload["error"] = ev.detail
    return payload


# ---- notifier ------------------------------------------------------------

class Notifier:
    """Dispatches AlertEvents to all configured sinks, with a global severity
    floor and per-(model, event) cooldown to tame crash-loop alert storms."""

    def __init__(self, http_client, sinks: list[Sink],
                 min_severity: str = "error", cooldown_s: float = 300.0) -> None:
        self.http = http_client
        self.sinks = sinks
        # The *default* severity floor — applied to env sinks and offered as the
        # default for new sinks. Each sink then decides for itself (no global
        # gate), so a sink set to `info` really does receive info events.
        self.min_severity = min_severity
        self.cooldown_s = cooldown_s
        self._last_sent: dict[tuple[str, str], float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.sinks)

    def set_sinks(self, sinks: list[Sink]) -> None:
        """Swap the active sink set (called after env+DB sinks are (re)loaded)."""
        self.sinks = sinks

    async def send_test(self, sinks: list[Sink] | None = None) -> list[dict]:
        """Send a synthetic alert to the given sinks (or all), bypassing the
        severity floor and cooldown. Returns a per-sink {type, ok, error?}."""
        targets = self.sinks if sinks is None else sinks
        ev = AlertEvent(
            event="test_alert", severity="info", model="(test)", kind="-",
            detail="Test alert from vLLMux — your notification sink is wired up.",
        )
        results: list[dict] = []
        for sink in targets:
            row = {"type": sink.type, "ok": True}
            try:
                await self.http.post(sink.url, json=format_payload(sink.type, ev), timeout=5.0)
            except Exception as e:  # report, don't raise
                row["ok"] = False
                row["error"] = str(e)
            results.append(row)
        return results

    def _suppressed(self, ev: AlertEvent) -> bool:
        """True if an identical (model, event) fired within the cooldown."""
        key = (ev.model, ev.event)
        now = time.monotonic()
        last = self._last_sent.get(key)
        if last is not None and now - last < self.cooldown_s:
            return True
        self._last_sent[key] = now
        return False

    async def notify(self, ev: AlertEvent) -> None:
        # Each sink decides by its own floor — there is no global gate. Dedup is
        # only consumed once at least one sink would actually receive the event,
        # so a below-threshold event doesn't burn the cooldown slot.
        eligible = [s for s in self.sinks
                    if severity_rank(ev.severity) >= severity_rank(s.min_severity)]
        if not eligible or self._suppressed(ev):
            return
        for sink in eligible:
            asyncio.create_task(self._send(sink, ev))

    async def _send(self, sink: Sink, ev: AlertEvent) -> None:
        try:
            await self.http.post(sink.url, json=format_payload(sink.type, ev), timeout=5.0)
        except Exception:
            logger.warning("alert send to %s sink failed (%s)", sink.type, ev.event)


def env_sinks(settings) -> list[Sink]:
    """The built-in sinks configured via environment (not editable at runtime).
    They adopt the global ``alert_min_severity`` as their own per-sink floor."""
    floor = getattr(settings, "alert_min_severity", "error")
    sinks: list[Sink] = []
    if getattr(settings, "alert_slack_webhook", ""):
        sinks.append(Sink("slack", settings.alert_slack_webhook, floor))
    if getattr(settings, "alert_discord_webhook", ""):
        sinks.append(Sink("discord", settings.alert_discord_webhook, floor))
    if getattr(settings, "alert_webhook", ""):
        sinks.append(Sink("webhook", settings.alert_webhook, floor))
    return sinks


def build_notifier(http_client, settings) -> Notifier:
    """Assemble a Notifier from env-configured sinks. Empty when nothing is
    configured (notify() then no-ops), preserving today's silent behaviour.
    DB-backed sinks are layered on at startup via ``refresh_sinks``."""
    return Notifier(
        http_client, env_sinks(settings),
        min_severity=getattr(settings, "alert_min_severity", "error"),
        cooldown_s=getattr(settings, "alert_cooldown_s", 300.0),
    )


async def refresh_sinks(notifier: Notifier, settings, store) -> None:
    """Recompute the active sink set = env sinks + DB sinks. Call at startup and
    after any DB sink mutation so the live Notifier reflects the change."""
    sinks = env_sinks(settings)
    if store is not None and hasattr(store, "list_alert_sinks"):
        for row in await store.list_alert_sinks():
            sinks.append(Sink(row["type"], row["url"], row.get("min_severity", "info")))
    notifier.set_sinks(sinks)
