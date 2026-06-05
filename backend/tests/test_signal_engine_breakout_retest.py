"""驗策略 2:早盤爆拉由下突破 CDP 線 → arm → M 分內由上而下回踩同線 → 發。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from models.condition import ActiveFilter, ActiveSignalOut, BreakoutRetestStrategy
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine

TZ = timezone(timedelta(hours=8))
EARLY = datetime(2026, 5, 20, 9, 5, tzinfo=TZ).timestamp()    # 開盤後 5 分(早盤內)
LATE = datetime(2026, 5, 20, 9, 20, tzinfo=TZ).timestamp()    # 開盤後 20 分(早盤外)
POST_OPEN = datetime(2026, 5, 20, 10, 0, tzinfo=TZ).timestamp()


def _active(levels=("nh",)):
    return ActiveSignalOut(
        id="b", name="爆拉突破回踩",
        filter_json=ActiveFilter(strategy=BreakoutRetestStrategy(
            type="breakout_retest", early_window_minutes=10, surge_pct=3.0,
            surge_window_seconds=60, retest_within_minutes=10,
            levels=list(levels), tolerance_ticks=1)),
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=60, enabled=True, created_at="2026-06-05",
    )


def test_arm_on_early_surge_cross_then_retest_fires():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"cdp_nh": 100.0}
    engine._eval_window = lambda symbol, tick, wc: True       # 模擬爆拉成立
    active = _active()
    strat = engine._strategy_of(active)

    # 早盤由下穿越 nh=100 → arm(穿越當下不發)
    r = engine._eval_breakout_retest(
        strat, active, "2330", Tick(100.0, 1, EARLY), prev=Tick(99.0, 1, EARLY - 1), now=EARLY)
    assert r is None
    assert "nh" in engine._breakout_armed[(active.id, "2330")]

    # 稍後由上而下回踩 nh → 發
    later = EARLY + 60
    r2 = engine._eval_breakout_retest(
        strat, active, "2330", Tick(100.0, 1, later), prev=Tick(101.0, 1, later - 1), now=later)
    assert r2 == {"level": "nh", "direction": "from_above", "role": "support"}
    assert "nh" not in engine._breakout_armed[(active.id, "2330")]   # 觸發後 disarm


def test_no_arm_when_surge_below_threshold():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"cdp_nh": 100.0}
    engine._eval_window = lambda symbol, tick, wc: False      # 爆拉不足
    active = _active()
    strat = engine._strategy_of(active)
    engine._eval_breakout_retest(
        strat, active, "2330", Tick(100.0, 1, EARLY), prev=Tick(99.0, 1, EARLY - 1), now=EARLY)
    assert engine._breakout_armed.get((active.id, "2330"), {}) == {}


def test_no_arm_outside_early_window():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"cdp_nh": 100.0}
    engine._eval_window = lambda symbol, tick, wc: True
    active = _active()
    strat = engine._strategy_of(active)
    engine._eval_breakout_retest(
        strat, active, "2330", Tick(100.0, 1, LATE), prev=Tick(99.0, 1, LATE - 1), now=LATE)
    assert engine._breakout_armed.get((active.id, "2330"), {}) == {}


def test_retest_after_window_expires_no_fire():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"cdp_nh": 100.0}
    engine._eval_window = lambda symbol, tick, wc: False
    active = _active()
    strat = engine._strategy_of(active)
    engine._breakout_armed[(active.id, "2330")] = {"nh": EARLY}    # 5 分時 arm
    late = EARLY + 11 * 60                                          # 11 分後才回踩(> 10 分)
    r = engine._eval_breakout_retest(
        strat, active, "2330", Tick(100.0, 1, late), prev=Tick(101.0, 1, late - 1), now=late)
    assert r is None
    assert "nh" not in engine._breakout_armed[(active.id, "2330")]  # 逾時 disarm


def test_multi_line_arm_retest_any():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"cdp_nh": 100.0, "cdp_ah": 102.0}
    engine._eval_window = lambda symbol, tick, wc: True
    active = _active(levels=("nh", "ah"))
    strat = engine._strategy_of(active)
    # 一次爆拉穿越 nh(100) 與 ah(102):prev 99 → curr 103,兩條都 from_below
    engine._eval_breakout_retest(
        strat, active, "2330", Tick(103.0, 1, EARLY), prev=Tick(99.0, 1, EARLY - 1), now=EARLY)
    assert set(engine._breakout_armed[(active.id, "2330")]) == {"nh", "ah"}
    # 回踩 nh → 發
    later = EARLY + 60
    r = engine._eval_breakout_retest(
        strat, active, "2330", Tick(100.0, 1, later), prev=Tick(101.0, 1, later - 1), now=later)
    assert r["level"] == "nh"


def test_reset_daily_clears_breakout_armed():
    engine = SignalEngine()
    engine._breakout_armed[("x", "2330")] = {"nh": 1.0}
    engine._reset_daily_strategy_state()
    assert engine._breakout_armed == {}


@pytest.mark.asyncio
async def test_retest_fires_through_evaluate():
    engine = SignalEngine()
    active = _active()
    engine._active = [active]
    engine._field_cache["2330"] = {"cdp_nh": 100.0}
    engine._breakout_armed[(active.id, "2330")] = {"nh": POST_OPEN - 60}  # 1 分前突破
    engine._prev_tick["2330"] = Tick(101.0, 1, 0.0)

    captured: dict = {}
    async def fake_broadcast(payload): captured.update(payload)

    with patch("services.signal_engine.time.time", return_value=POST_OPEN), \
         patch("services.signal_engine.get_broadcaster") as bc, \
         patch("services.signal_engine.get_signal_writer") as sw:
        bc.return_value.broadcast = fake_broadcast
        sw.return_value = MagicMock()
        await engine._evaluate("2330", Tick(price=100.0, size=1, time=1.0))

    assert captured["data"]["cdp_touch"] == {
        "level": "nh", "direction": "from_above", "role": "support", "touch_index": 1,
    }
