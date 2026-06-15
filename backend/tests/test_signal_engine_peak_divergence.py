"""驗造山積木:急拉偵測 + 山頂追蹤 + N 根沒創新高確認。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from services.ring_buffer import Tick
from services.signal_engine import MinuteCandle, SignalEngine

TZ = timezone(timedelta(hours=8))
MORNING = datetime(2026, 6, 15, 9, 30, tzinfo=TZ).timestamp()


def _candle(high, close, volume=100, minute=0, low=None, open_=None):
    o = open_ if open_ is not None else close
    lo = low if low is not None else min(o, close, high)
    return MinuteCandle(minute=minute, open=o, high=high, low=lo, close=close, volume=volume)


def _strat(surge_pct=3.0, surge_window_bars=5, surge_volume_ratio=2.5):
    return {"surge_pct": surge_pct, "surge_window_bars": surge_window_bars,
            "surge_volume_ratio": surge_volume_ratio}


# ---- _detect_surge 積木單測 ----

def test_detect_surge_steep_and_high_volume_true():
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000  # 均量 100/min @09:30
    # 5 根前 close=100, 當根 close=104(漲 4% ≥ 3%), vol=300 → vr=3.0 ≥ 2.5
    candle = _candle(high=104, close=104, volume=300)
    closes = [100, 100, 100, 100, 100, 104]  # 6 個(5+1)
    assert engine._detect_surge("2330", candle, closes, MORNING, _strat()) is True


def test_detect_surge_steep_but_low_volume_false():
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000
    candle = _candle(high=104, close=104, volume=50)  # vr=0.5 < 2.5
    closes = [100, 100, 100, 100, 100, 104]
    assert engine._detect_surge("2330", candle, closes, MORNING, _strat()) is False


def test_detect_surge_high_volume_but_not_steep_false():
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000
    candle = _candle(high=101, close=101, volume=300)  # 漲 1% < 3%
    closes = [100, 100, 100, 100, 100, 101]
    assert engine._detect_surge("2330", candle, closes, MORNING, _strat()) is False


def test_detect_surge_insufficient_history_false():
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000
    candle = _candle(high=104, close=104, volume=300)
    closes = [100, 104]  # 只有 2 個 ≤ window=5
    assert engine._detect_surge("2330", candle, closes, MORNING, _strat()) is False
