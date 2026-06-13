"""驗 SignalEngine 的 per-symbol 1 分鐘 candle 聚合。"""
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine

BASE = 1716166860.0
MIN0 = BASE
MIN1 = BASE + 60
MIN2 = BASE + 120


def test_first_tick_creates_candle_no_settlement():
    engine = SignalEngine()
    tick = Tick(price=100.0, size=10, time=MIN0)
    settled = engine._update_candle("2330", tick, MIN0, is_new_tick=True)
    assert settled is None
    candle = engine._minute_candle["2330"]
    assert candle.open == 100.0
    assert candle.close == 100.0
    assert candle.volume == 10


def test_same_minute_updates_ohlcv():
    engine = SignalEngine()
    engine._update_candle("2330", Tick(100.0, 10, MIN0), MIN0, True)
    engine._update_candle("2330", Tick(102.0, 5, MIN0 + 10), MIN0 + 10, True)
    engine._update_candle("2330", Tick(98.0, 8, MIN0 + 20), MIN0 + 20, True)
    engine._update_candle("2330", Tick(101.0, 3, MIN0 + 30), MIN0 + 30, True)
    c = engine._minute_candle["2330"]
    assert c.open == 100.0
    assert c.high == 102.0
    assert c.low == 98.0
    assert c.close == 101.0
    assert c.volume == 26


def test_tick_driven_settlement_on_minute_cross():
    engine = SignalEngine()
    engine._update_candle("2330", Tick(100.0, 10, MIN0), MIN0, True)
    engine._update_candle("2330", Tick(105.0, 5, MIN0 + 30), MIN0 + 30, True)
    settled = engine._update_candle("2330", Tick(106.0, 7, MIN1), MIN1, True)
    assert settled is not None
    assert settled.close == 105.0
    assert settled.volume == 15
    new = engine._minute_candle["2330"]
    assert new.open == 106.0
    assert new.volume == 7


def test_heartbeat_refeed_no_volume_update():
    engine = SignalEngine()
    tick = Tick(100.0, 10, MIN0)
    engine._update_candle("2330", tick, MIN0, is_new_tick=True)
    engine._update_candle("2330", tick, MIN0 + 1, is_new_tick=False)
    assert engine._minute_candle["2330"].volume == 10


def test_heartbeat_settles_on_wall_clock_advance():
    engine = SignalEngine()
    tick = Tick(100.0, 10, MIN0)
    engine._update_candle("2330", tick, MIN0, is_new_tick=True)
    settled = engine._update_candle("2330", tick, MIN1, is_new_tick=False)
    assert settled is not None
    assert settled.close == 100.0
    assert "2330" not in engine._minute_candle


def test_heartbeat_before_any_tick_no_candle():
    engine = SignalEngine()
    tick = Tick(100.0, 10, MIN0)
    settled = engine._update_candle("2330", tick, MIN0, is_new_tick=False)
    assert settled is None
    assert "2330" not in engine._minute_candle


def test_daily_reset_clears_candle():
    engine = SignalEngine()
    engine._update_candle("2330", Tick(100.0, 10, MIN0), MIN0, True)
    assert "2330" in engine._minute_candle
    engine._reset_daily_strategy_state()
    assert "2330" not in engine._minute_candle


def test_out_of_session_tick_no_candle_created():
    engine = SignalEngine()
    tick = Tick(price=100.0, size=10, time=MIN0)
    settled = engine._update_candle("2330", tick, MIN0, is_new_tick=True, in_session=False)
    assert settled is None
    assert "2330" not in engine._minute_candle


def test_out_of_session_tick_settles_existing_candle():
    engine = SignalEngine()
    engine._update_candle("2330", Tick(100.0, 10, MIN0), MIN0, True, in_session=True)
    tick2 = Tick(price=105.0, size=5, time=MIN1)
    settled = engine._update_candle("2330", tick2, MIN1, is_new_tick=True, in_session=False)
    assert settled is not None
    assert settled.close == 100.0
    assert "2330" not in engine._minute_candle
