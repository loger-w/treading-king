"""re-arm 狀態機:觸發→抑制→離線→再武裝;horizontal 不推。

field_cache cdp_nh=100.0 → tick_size(100.0)=0.5,預設 rearm 5 ticks = 離線 2.5 元。
"""
from models.condition import (
    ActiveFilter, ActiveSignalOut, CdpProximityCondition, MAProximityCondition,
)
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine


def _tick(price: float, t: float = 1.0) -> Tick:
    return Tick(price=price, size=1, time=t)


def _active(**prox_kwargs) -> ActiveSignalOut:
    kwargs = {"levels": ["nh"], "tolerance_ticks": 0, **prox_kwargs}
    return ActiveSignalOut(
        id="a1", name="t",
        filter_json=ActiveFilter(cdp_proximity=CdpProximityCondition(**kwargs)),
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=60, enabled=True, created_at="2026-06-12",
    )


def _engine() -> SignalEngine:
    engine = SignalEngine()
    engine._field_cache["2330"] = {"cdp_nh": 100.0}
    return engine


def test_touch_marks_level_suppressed_and_blocks_repeat():
    engine, a = _engine(), _active()
    touch, _ = engine._eval_with_touch_meta(a, "2330", _tick(100.0), _tick(99.5))
    assert touch == {"level": "nh", "direction": "from_below", "role": "resistance"}
    assert ("a1", "2330", "nh") in engine._prox_suppressed
    # 黏線重評 → 不再回 touch
    touch2, _ = engine._eval_with_touch_meta(a, "2330", _tick(100.0), _tick(100.0))
    assert touch2 is None


def test_leaving_less_than_rearm_distance_stays_suppressed():
    engine, a = _engine(), _active()
    engine._eval_with_touch_meta(a, "2330", _tick(100.0), _tick(99.5))
    # 離線 2.0 < 2.5 → 仍抑制
    engine._eval_with_touch_meta(a, "2330", _tick(102.0), _tick(100.0))
    assert ("a1", "2330", "nh") in engine._prox_suppressed


def test_rearm_after_leaving_far_enough_then_retouch_triggers():
    engine, a = _engine(), _active()
    engine._eval_with_touch_meta(a, "2330", _tick(100.0), _tick(99.5))
    # 離線 2.5 = 5 ticks → 解除抑制
    engine._eval_with_touch_meta(a, "2330", _tick(102.5), _tick(102.0))
    assert ("a1", "2330", "nh") not in engine._prox_suppressed
    # 回頭碰 → 新觸發,方向 from_above
    touch, _ = engine._eval_with_touch_meta(a, "2330", _tick(100.0), _tick(102.5))
    assert touch == {"level": "nh", "direction": "from_above", "role": "support"}


def test_horizontal_touch_dropped_and_not_suppressed():
    engine, a = _engine(), _active()
    # prev=None → direction horizontal → 不推、也不消耗 armed 狀態
    touch, _ = engine._eval_with_touch_meta(a, "2330", _tick(100.0), None)
    assert touch is None
    assert engine._prox_suppressed == set()


def test_rearm_zero_disables_suppression():
    engine, a = _engine(), _active(rearm_ticks=0)
    t1, _ = engine._eval_with_touch_meta(a, "2330", _tick(100.0), _tick(99.5))
    t2, _ = engine._eval_with_touch_meta(a, "2330", _tick(100.0), _tick(99.5))
    assert t1 is not None and t2 is not None
    assert engine._prox_suppressed == set()


def test_ma_touch_uses_same_rearm_mechanism():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"sma_5": 100.0}
    a = ActiveSignalOut(
        id="a1", name="t",
        filter_json=ActiveFilter(
            ma_proximity=MAProximityCondition(levels=["sma_5"], tolerance_ticks=1),
        ),
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=60, enabled=True, created_at="2026-06-12",
    )
    _, ma = engine._eval_with_touch_meta(a, "2330", _tick(100.0), _tick(99.0))
    assert ma is not None
    assert ("a1", "2330", "sma_5") in engine._prox_suppressed
    _, ma2 = engine._eval_with_touch_meta(a, "2330", _tick(100.0), _tick(100.0))
    assert ma2 is None


def test_daily_reset_clears_suppressed():
    engine, a = _engine(), _active()
    engine._eval_with_touch_meta(a, "2330", _tick(100.0), _tick(99.5))
    engine._reset_daily_strategy_state()
    assert engine._prox_suppressed == set()
