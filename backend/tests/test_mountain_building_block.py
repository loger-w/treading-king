"""驗造山積木 v4:high+swing low surge、分級確認、re-surge margin。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from services.ring_buffer import Tick
from services.signal_engine import MinuteCandle, SignalEngine, _find_surge_base

TZ = timezone(timedelta(hours=8))
MORNING = datetime(2026, 6, 15, 9, 30, tzinfo=TZ).timestamp()


def _candle(high, close, volume=100, minute=0, low=None, open_=None):
    o = open_ if open_ is not None else close
    lo = low if low is not None else min(o, close, high)
    return MinuteCandle(minute=minute, open=o, high=high, low=lo, close=close, volume=volume)


def _strat(surge_pct=3.0, surge_window_bars=10, surge_volume_ratio=1.5):
    return {"surge_pct": surge_pct, "surge_window_bars": surge_window_bars,
            "surge_volume_ratio": surge_volume_ratio}


# ---- _find_surge_base 單測 ----

def test_find_surge_base_simple_trough():
    closes = [100, 98, 95, 93, 94, 95, 96, 97, 96, 98]
    assert _find_surge_base(closes) == 96


def test_find_surge_base_monotonic_rise():
    closes = [90, 91, 92, 93, 94]
    assert _find_surge_base(closes) == 90


def test_find_surge_base_monotonic_decline():
    closes = [95, 94, 93, 92]
    assert _find_surge_base(closes) == 92


def test_find_surge_base_single_element():
    assert _find_surge_base([100]) == 100


def test_find_surge_base_empty():
    assert _find_surge_base([]) == 0.0


def test_find_surge_base_v_shape():
    closes = [100, 97, 95, 97, 99]
    assert _find_surge_base(closes) == 95


def test_find_surge_base_ignores_early_global_low():
    closes = [90, 91, 95, 98, 97, 96, 97, 98]
    assert _find_surge_base(closes) == 96


# ---- _detect_surge v4 單測 ----

def test_detect_surge_high_reaches_threshold_true():
    """v4: high(非 close)達 3% 就觸發。"""
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000
    candle = _candle(high=103, close=101, volume=200)
    closes = [100] * 10 + [101]
    assert engine._detect_surge("2330", candle, closes, MORNING, _strat()) is True


def test_detect_surge_close_below_threshold_but_high_above():
    """v4 關鍵:close 未達 3% 但 high 達 3% → 仍觸發。"""
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000
    candle = _candle(high=104, close=101.5, volume=200)
    closes = [100] * 10 + [101.5]
    assert engine._detect_surge("2330", candle, closes, MORNING, _strat()) is True


def test_detect_surge_low_volume_false():
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000
    candle = _candle(high=104, close=104, volume=50)
    closes = [100] * 10 + [104]
    assert engine._detect_surge("2330", candle, closes, MORNING, _strat()) is False


def test_detect_surge_high_not_steep_false():
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000
    candle = _candle(high=101, close=101, volume=200)
    closes = [100] * 10 + [101]
    assert engine._detect_surge("2330", candle, closes, MORNING, _strat()) is False


def test_detect_surge_min_bars_insufficient():
    """v4: min_bars=3 → 需要 4 個 recent_closes。"""
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000
    candle = _candle(high=104, close=104, volume=200)
    closes = [100, 100, 104]
    assert engine._detect_surge("2330", candle, closes, MORNING, _strat()) is False


def test_detect_surge_min_bars_just_enough():
    """4 個 recent_closes 剛好夠。"""
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000
    candle = _candle(high=104, close=104, volume=200)
    closes = [100, 100, 100, 104]
    assert engine._detect_surge("2330", candle, closes, MORNING, _strat()) is True


def test_detect_surge_uses_swing_low_not_global_min():
    """v4 關鍵:近期相對低點,不是全域最低。"""
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000
    # closes[:-1] = [100, 90, 91, 95, 98, 97, 96, 97, 98, 99]
    # swing low = 96, high=99 → (99-96)/96 = 3.125% ≥ 3%
    candle = _candle(high=99, close=99, volume=200)
    closes = [100, 90, 91, 95, 98, 97, 96, 97, 98, 99, 99]
    assert engine._detect_surge("2330", candle, closes, MORNING, _strat()) is True


def test_detect_surge_exact_threshold():
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000
    candle = _candle(high=103, close=100, volume=200)
    closes = [100] * 10 + [100]
    assert engine._detect_surge("2330", candle, closes, MORNING, _strat()) is True


# ---- _update_mountain 造山狀態機 ----

def _mountain_feed(engine, candles, confirm_bars=3):
    """逐根餵 candle 到 _update_mountain,回最後的 mountain state for '2330'。"""
    engine._day_volume["2330"] = 3000
    for c in candles:
        engine._update_mountain("2330", c, MORNING + c.minute * 60, confirm_bars=confirm_bars)
    return engine._mountain_state.get("2330")


# 10 根暖機 + 急拉根(m10: close=104 漲 4%, vol=2000 → vr~22)
_WARMUP = [_candle(high=100, close=100, volume=10, minute=i) for i in range(10)]
_SURGE = _candle(high=104, close=104, volume=2000, minute=10)


def test_surge_starts_tracking():
    engine = SignalEngine()
    st = _mountain_feed(engine, _WARMUP + [_SURGE])
    assert st["phase"] == "surge_tracking"
    assert st["peak_high"] == 104
    assert st["peak_vr"] > 2.5


def test_tracking_follows_higher_high():
    engine = SignalEngine()
    st = _mountain_feed(engine, _WARMUP + [
        _SURGE,                                              # m10: peak=104
        _candle(high=106, close=105, volume=10, minute=11),  # m11: high=106 創更高
    ])
    assert st["phase"] == "surge_tracking"
    assert st["peak_high"] == 106


def test_no_surge_stays_idle():
    engine = SignalEngine()
    st = _mountain_feed(engine, _WARMUP + [
        _candle(high=101, close=101, volume=10, minute=10),  # 漲 1% < 3%, 無量
    ])
    assert st["phase"] == "idle"
    assert st["peak_high"] == 0.0


def test_confirm_after_n_bars_no_new_high():
    engine = SignalEngine()
    st = _mountain_feed(engine, _WARMUP + [
        _SURGE,                                              # m10: peak=104
        _candle(high=103, close=103, volume=10, minute=11),  # no new high (1)
        _candle(high=102, close=102, volume=10, minute=12),  # no new high (2)
        _candle(high=101, close=101, volume=10, minute=13),  # no new high (3) → confirmed
    ])
    assert st["phase"] == "confirmed"
    assert st["peak_high"] == 104
    assert st["confirmed_minute"] == 13


def test_new_high_resets_confirm_counter():
    engine = SignalEngine()
    st = _mountain_feed(engine, _WARMUP + [
        _SURGE,                                              # m10: peak=104
        _candle(high=103, close=103, volume=10, minute=11),  # no new high (1)
        _candle(high=102, close=102, volume=10, minute=12),  # no new high (2)
        _candle(high=105, close=105, volume=10, minute=13),  # NEW HIGH → reset, peak=105
        _candle(high=104, close=104, volume=10, minute=14),  # no new high (1)
        _candle(high=103, close=103, volume=10, minute=15),  # no new high (2)
    ])
    assert st["phase"] == "surge_tracking"                   # 只 2 根,還沒到 3
    assert st["peak_high"] == 105


def test_new_surge_after_confirmed_upgrades_mountain():
    engine = SignalEngine()
    st = _mountain_feed(engine, _WARMUP + [
        _SURGE,                                              # m10: peak=104
        _candle(high=103, close=103, volume=10, minute=11),
        _candle(high=102, close=102, volume=10, minute=12),
        _candle(high=101, close=101, volume=10, minute=13),  # confirmed: peak=104
        # 暖機湊 10 根 recent_closes 讓新急拉可偵測
        *[_candle(high=100, close=100, volume=10, minute=14+i) for i in range(10)],
        _candle(high=108, close=108, volume=2000, minute=24), # 新急拉 high=108>104
    ])
    assert st["phase"] == "surge_tracking"
    assert st["peak_high"] == 108


def test_new_surge_lower_peak_keeps_old_mountain():
    engine = SignalEngine()
    st = _mountain_feed(engine, _WARMUP + [
        _SURGE,                                              # m10: peak=104
        _candle(high=103, close=103, volume=10, minute=11),
        _candle(high=102, close=102, volume=10, minute=12),
        _candle(high=101, close=101, volume=10, minute=13),  # confirmed: peak=104
        *[_candle(high=97, close=97, volume=10, minute=14+i) for i in range(10)],
        _candle(high=103, close=103, volume=2000, minute=24), # 急拉 high=103<104 → 保留舊山
    ])
    assert st["phase"] == "confirmed"
    assert st["peak_high"] == 104


def test_equal_high_counts_as_no_new_high():
    engine = SignalEngine()
    st = _mountain_feed(engine, _WARMUP + [
        _SURGE,                                              # m10: peak=104
        _candle(high=104, close=103, volume=10, minute=11),  # equal high (1)
        _candle(high=104, close=103, volume=10, minute=12),  # equal high (2)
        _candle(high=104, close=103, volume=10, minute=13),  # equal high (3) → confirmed
    ])
    assert st["phase"] == "confirmed"
    assert st["peak_high"] == 104


def test_confirm_bars_parameter_overrides_default():
    engine = SignalEngine()
    st = _mountain_feed(engine, _WARMUP + [
        _SURGE,                                              # m10: peak=104
        _candle(high=103, close=103, volume=10, minute=11),  # no new high (1) → confirm_bars=1 觸發
    ], confirm_bars=1)
    assert st["phase"] == "confirmed"
    assert st["peak_high"] == 104


def test_daily_reset_clears_mountain_state():
    engine = SignalEngine()
    engine._mountain_state["2330"] = {"phase": "confirmed", "peak_high": 134}
    engine._reset_daily_strategy_state()
    assert "2330" not in engine._mountain_state


@pytest.mark.asyncio
async def test_evaluate_updates_mountain_state_on_settled_candle():
    """整合:逐 tick 跨分鐘結算 → _update_mountain 被呼叫 → mountain_state 更新。"""
    engine = SignalEngine()
    engine._active = []
    engine._field_cache["2330"] = {}

    ticks = (
        [(100.0, 10, i) for i in range(10)]
        + [(104.0, 2000, 10)]
        + [(103.0, 10, 11), (102.0, 10, 12), (101.0, 10, 13)]
        + [(100.0, 10, 14)]
    )
    with patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_signal_writer") as mock_sw:
        mock_bc.return_value.broadcast = MagicMock()
        mock_sw.return_value = MagicMock()
        for price, size, minute in ticks:
            ts = MORNING + minute * 60
            with patch("services.signal_engine.time.time", return_value=ts):
                await engine._evaluate("2330", Tick(price=price, size=size, time=ts))

    st = engine._mountain_state.get("2330")
    assert st is not None
    assert st["phase"] == "confirmed"
    assert st["peak_high"] == 104.0
    assert st["peak_vr"] > 1.5
