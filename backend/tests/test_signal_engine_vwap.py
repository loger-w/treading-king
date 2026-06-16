"""驗引擎 VWAP 即時計算。"""
from services.signal_engine import MinuteCandle, SignalEngine


def _candle(high, low, close, volume, minute=0):
    return MinuteCandle(minute=minute, open=close, high=high, low=low,
                        close=close, volume=volume)


def test_vwap_single_candle():
    e = SignalEngine()
    c = _candle(high=102, low=98, close=100, volume=1000, minute=1)
    e._update_vwap("2330", c)
    assert abs(e._field_cache["2330"]["vwap"] - 100.0) < 0.01


def test_vwap_two_candles_weighted():
    e = SignalEngine()
    c1 = _candle(high=102, low=98, close=100, volume=1000, minute=1)
    c2 = _candle(high=110, low=106, close=108, volume=3000, minute=2)
    e._update_vwap("2330", c1)
    e._update_vwap("2330", c2)
    # tp1=100, tp2=(110+106+108)/3=108
    # vwap = (100*1000 + 108*3000) / (1000+3000) = 424000/4000 = 106.0
    assert abs(e._field_cache["2330"]["vwap"] - 106.0) < 0.01


def test_vwap_zero_volume_skipped():
    e = SignalEngine()
    c = _candle(high=100, low=100, close=100, volume=0, minute=1)
    e._update_vwap("2330", c)
    assert "vwap" not in e._field_cache.get("2330", {})


def test_vwap_cleared_on_reset():
    e = SignalEngine()
    c = _candle(high=102, low=98, close=100, volume=1000, minute=1)
    e._update_vwap("2330", c)
    assert "vwap" in e._field_cache.get("2330", {})
    e._vwap_state.clear()
    # After clear, next candle starts fresh accumulation
    e._update_vwap("2330", _candle(high=50, low=46, close=48, volume=500, minute=2))
    assert abs(e._field_cache["2330"]["vwap"] - 48.0) < 0.01
