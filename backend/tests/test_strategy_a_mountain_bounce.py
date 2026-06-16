"""驗策略 A：造山後碰 CDP 無力。"""
from models.condition import ActiveFilter, ActiveSignalOut, MountainBounceStrategy
from services.signal_engine import MinuteCandle, SignalEngine

NH = 100.0
AH = 105.0
CDP_MID = 97.0


def _active(confirm_bars=2, levels=("nh",), tolerance_pct=0.0, require_below_vwap=False):
    return ActiveSignalOut(
        id="mb", name="造山碰CDP",
        filter_json=ActiveFilter(strategy=MountainBounceStrategy(
            type="mountain_bounce", levels=list(levels),
            confirm_bars=confirm_bars, tolerance_pct=tolerance_pct,
            require_below_vwap=require_below_vwap,
        )),
        scope={"type": "watchlist"},
        cooldown_seconds=1800, enabled=True, created_at="2026-06-16",
        notify_discord=False,
    )


def _engine():
    e = SignalEngine()
    e._field_cache["2330"] = {"cdp_nh": NH, "cdp_ah": AH, "cdp": CDP_MID}
    return e


def _candle(close, high=None, volume=100, minute=0, open_=None):
    h = high if high is not None else close
    o = open_ if open_ is not None else close
    lo = min(o, close, h)
    return MinuteCandle(minute=minute, open=o, high=h, low=lo, close=close, volume=volume)


def _set_mountain_confirmed(engine, symbol="2330", peak_high=110.0):
    engine._mountain_state[symbol] = {
        "phase": "confirmed", "recent_closes": [],
        "peak_high": peak_high, "peak_vr": 2.0, "peak_minute": 5,
        "confirmed_minute": 8, "no_new_high_count": 0,
    }


def test_no_signal_without_mountain():
    engine = _engine()
    active = _active()
    strat = engine._strategy_of(active)
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    assert r is None


def test_arm_and_confirm_2_bars():
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(confirm_bars=2)
    strat = engine._strategy_of(active)
    r1 = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    assert r1 is None
    r2 = engine._eval_mountain_bounce(strat, active, "2330", _candle(98, high=99, minute=11), 660)
    assert r2 is not None
    assert r2["level"] == "nh"
    assert r2["direction"] == "from_below"
    assert r2["role"] == "mountain_bounce"
    assert r2["confirm_bars"] == 2
    assert r2["peak_high"] == 110.0


def test_disarm_on_close_above():
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(confirm_bars=2)
    strat = engine._strategy_of(active)
    engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    engine._eval_mountain_bounce(strat, active, "2330", _candle(101, high=102, minute=11), 660)
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=100.5, minute=12), 720)
    assert r is None


def test_confirm_bars_3():
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(confirm_bars=3)
    strat = engine._strategy_of(active)
    engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    r2 = engine._eval_mountain_bounce(strat, active, "2330", _candle(98, minute=11), 660)
    assert r2 is None
    r3 = engine._eval_mountain_bounce(strat, active, "2330", _candle(97, minute=12), 720)
    assert r3 is not None


def test_tolerance_pct():
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(confirm_bars=1, tolerance_pct=0.5)
    strat = engine._strategy_of(active)
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=99.8, minute=10), 600)
    assert r is not None


def test_require_below_vwap_blocks():
    engine = _engine()
    _set_mountain_confirmed(engine)
    engine._field_cache["2330"]["vwap"] = 98.0
    active = _active(confirm_bars=1, require_below_vwap=True)
    strat = engine._strategy_of(active)
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    assert r is None


def test_require_below_vwap_allows():
    engine = _engine()
    _set_mountain_confirmed(engine)
    engine._field_cache["2330"]["vwap"] = 100.0
    active = _active(confirm_bars=1, require_below_vwap=True)
    strat = engine._strategy_of(active)
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    assert r is not None


def test_require_below_vwap_no_vwap_skips():
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(confirm_bars=1, require_below_vwap=True)
    strat = engine._strategy_of(active)
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    assert r is None


def test_multiple_levels_fire_independently():
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(confirm_bars=1, levels=("nh", "ah"))
    strat = engine._strategy_of(active)
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(104, high=106, minute=10), 600)
    assert r is not None
    assert r["level"] == "ah"


def test_mountain_not_confirmed_returns_none():
    engine = _engine()
    engine._mountain_state["2330"] = {
        "phase": "surge_tracking", "recent_closes": [],
        "peak_high": 110.0, "peak_vr": 2.0, "peak_minute": 5,
        "confirmed_minute": 0, "no_new_high_count": 0,
    }
    active = _active(confirm_bars=1)
    strat = engine._strategy_of(active)
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    assert r is None


def test_below_vwap_metadata():
    engine = _engine()
    _set_mountain_confirmed(engine)
    engine._field_cache["2330"]["vwap"] = 100.0
    active = _active(confirm_bars=1, require_below_vwap=False)
    strat = engine._strategy_of(active)
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    assert r is not None
    assert r["below_vwap"] is True


def test_vwap_blocked_still_disarms():
    """require_below_vwap=True + close >= vwap must still disarm if close >= CDP."""
    engine = _engine()
    _set_mountain_confirmed(engine)
    engine._field_cache["2330"]["vwap"] = 98.0
    active = _active(confirm_bars=2, require_below_vwap=True)
    strat = engine._strategy_of(active)
    # Bar 1: close=97 < vwap=98, high=101 >= NH=100, close < NH → arm + count=1
    engine._eval_mountain_bounce(strat, active, "2330", _candle(97, high=101, minute=10), 600)
    assert ("mb", "2330", "nh") in engine._mountain_bounce_armed
    # Bar 2: close=101 >= vwap=98 → vwap blocked. BUT close=101 >= NH=100 → should disarm
    engine._eval_mountain_bounce(strat, active, "2330", _candle(101, high=102, minute=11), 660)
    assert ("mb", "2330", "nh") not in engine._mountain_bounce_armed


def test_multi_level_second_level_not_lost():
    """When two levels fire on same candle, second level stays armed (not deleted)."""
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(confirm_bars=1, levels=("ah", "nh"))
    strat = engine._strategy_of(active)
    # high=106 touches AH(105) and NH(100). close=104 < AH but > NH.
    # AH should fire. NH should NOT fire (104 > 100) but should remain armed.
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(104, high=106, minute=10), 600)
    assert r is not None
    assert r["level"] == "ah"
    # NH should still be armed (close=104 >= NH=100 → disarmed by close check)
    # Actually 104 >= 100 so NH gets disarmed. That's correct.
    # Test case where both confirm:
    engine2 = _engine()
    _set_mountain_confirmed(engine2)
    active2 = _active(confirm_bars=1, levels=("ah", "nh"))
    strat2 = engine2._strategy_of(active2)
    # high=106 touches both. close=99 < both AH(105) and NH(100).
    r2 = engine2._eval_mountain_bounce(strat2, active2, "2330", _candle(99, high=106, minute=10), 600)
    assert r2 is not None
    assert r2["level"] == "ah"  # first level wins
    # NH should still be armed with reset count (not deleted)
    nh_key = ("mb", "2330", "nh")
    assert nh_key in engine2._mountain_bounce_armed
    armed_nh = engine2._mountain_bounce_armed[nh_key]
    assert armed_nh["confirm_count"] == 0  # reset, not deleted
