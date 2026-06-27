"""Autoscaler policy (Phase 2): the pure decide() function — scale-up on sustained
queue pressure (wake-first), idle scale-down along the warm ladder, cooldowns and
the min_ready / min_warm / max_ready floors and caps."""
import pytest

from app.llmops.autoscaler import GroupTiming, _Inst, decide
from app.llmops.state import ModelState
from schema import AutoscaleConfig

pytestmark = pytest.mark.unit

NOW = 100_000.0


def cfg(**kw):
    base = dict(enabled=True, min_ready=1, min_warm=1, max_ready=4,
                scale_up_waiting=4.0, scale_up_window_s=20.0,
                sleep_after_s=180.0, stop_after_s=900.0, cooldown_s=60.0)
    base.update(kw)
    return AutoscaleConfig(**base)


def insts(**counts) -> list[_Inst]:
    """e.g. insts(ready=1, asleep=1, stopped=2) -> distinct keyed instances."""
    out, n = [], 0
    for state, key in [(ModelState.READY, "ready"), (ModelState.SLEEPING, "asleep"),
                       (ModelState.STOPPED, "stopped"), (ModelState.STARTING, "starting")]:
        for _ in range(counts.get(key, 0)):
            out.append(_Inst(f"M::{key}{n}", state))
            n += 1
    return out


def load(wpr=0.0, running=0.0, waiting=0.0):
    return {"waiting_per_replica": wpr, "running_total": running, "waiting_total": waiting}


def test_disabled_does_nothing():
    assert decide(cfg(enabled=False), load(wpr=99), insts(ready=1), True, GroupTiming(), NOW) == []


def test_floor_starts_to_reach_min_ready_at_zero_load():
    # min_ready=1 but nothing ready -> cold-start one even with no queue.
    assert decide(cfg(min_ready=1), load(), insts(stopped=2), True, GroupTiming(), NOW) == \
        [("start", "M::stopped0")]


def test_floor_prefers_wake_to_reach_min_ready():
    assert decide(cfg(min_ready=1), load(), insts(asleep=1, stopped=1), True, GroupTiming(), NOW) == \
        [("wake", "M::asleep0")]


def test_floor_satisfied_counts_starting():
    # one already STARTING meets min_ready=1 -> no action.
    assert decide(cfg(min_ready=1), load(), insts(starting=1, stopped=1), True, GroupTiming(), NOW) == []


def test_scale_up_prefers_wake_over_cold_start():
    t = GroupTiming(over_since=NOW - 25, last_scale_up=NOW - 100)  # sustained + cooled
    out = decide(cfg(), load(wpr=10), insts(ready=1, asleep=1, stopped=1), True, t, NOW)
    assert out == [("wake", "M::asleep1")]
    assert t.last_scale_up == NOW and t.over_since is None


def test_scale_up_cold_starts_when_nothing_asleep():
    t = GroupTiming(over_since=NOW - 25, last_scale_up=NOW - 100)
    out = decide(cfg(), load(wpr=10), insts(ready=1, stopped=1), True, t, NOW)
    assert out == [("start", "M::stopped1")]


def test_scale_up_waits_for_sustained_window():
    t = GroupTiming()
    assert decide(cfg(), load(wpr=10), insts(ready=1, stopped=1), True, t, NOW) == []
    assert t.over_since == NOW  # armed, but not yet sustained


def test_scale_up_blocked_by_cooldown():
    t = GroupTiming(over_since=NOW - 25, last_scale_up=NOW - 10)  # 10s < 60s cooldown
    assert decide(cfg(), load(wpr=10), insts(ready=1, stopped=1), True, t, NOW) == []


def test_scale_up_blocked_at_max_ready():
    t = GroupTiming(over_since=NOW - 25, last_scale_up=NOW - 100)
    # effective_ready (ready+starting) already at cap
    out = decide(cfg(max_ready=2), load(wpr=10), insts(ready=1, starting=1, stopped=1), True, t, NOW)
    assert out == []


def test_scale_down_sleeps_idle_ready_beyond_min():
    t = GroupTiming(idle_since=NOW - 200)  # idle past sleep_after (180)
    out = decide(cfg(min_ready=1), load(), insts(ready=2), True, t, NOW)
    assert out == [("sleep", "M::ready1")]  # keeps ready0, sleeps the last


def test_scale_down_stops_when_not_sleep_capable():
    t = GroupTiming(idle_since=NOW - 200)
    out = decide(cfg(min_ready=1), load(), insts(ready=2), False, t, NOW)
    assert out == [("stop", "M::ready1")]


def test_scale_down_keeps_min_ready():
    t = GroupTiming(idle_since=NOW - 5000)
    assert decide(cfg(min_ready=1), load(), insts(ready=1), True, t, NOW) == []


def test_scale_down_stop_tier_drops_asleep_beyond_min_warm():
    t = GroupTiming(idle_since=NOW - 1000)  # past stop_after (900)
    out = decide(cfg(min_ready=1, min_warm=1), load(), insts(ready=1, asleep=2), True, t, NOW)
    assert out == [("stop", "M::asleep2")]  # resident 3 > min_warm 1


def test_busy_group_resets_idle_and_does_not_scale_down():
    t = GroupTiming(idle_since=NOW - 5000)
    assert decide(cfg(), load(running=3), insts(ready=2), True, t, NOW) == []
    assert t.idle_since is None


def test_idle_arms_timer_without_acting():
    t = GroupTiming()
    assert decide(cfg(), load(), insts(ready=2), True, t, NOW) == []
    assert t.idle_since == NOW  # armed; not yet past sleep_after
