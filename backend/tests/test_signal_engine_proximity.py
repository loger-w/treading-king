"""驗 _eval_cdp_proximity / _eval_ma_proximity 回 (bool, level)。"""
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine


def _tick(price: float, t: float = 1700000000.0) -> Tick:
    return Tick(price=price, size=1, time=t)


def test_cdp_proximity_returns_level_when_hit():
    engine = SignalEngine()
    engine._field_cache["2330"] = {
        "cdp_ah": 100.0, "cdp_nh": 99.0, "cdp": 98.0,
        "cdp_nl": 97.0, "cdp_al": 96.0,
    }
    prox = {"levels": ["ah", "nh"], "tolerance_ticks": 0}
    ok, level = engine._eval_cdp_proximity("2330", _tick(100.0), prox)
    assert ok is True
    assert level == "ah"


def test_cdp_proximity_no_hit_returns_none_level():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"cdp_ah": 100.0}
    prox = {"levels": ["ah"], "tolerance_ticks": 0}
    ok, level = engine._eval_cdp_proximity("2330", _tick(95.0), prox)
    assert ok is False
    assert level is None


def test_ma_proximity_returns_level_when_hit_within_tolerance():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"sma_5": 100.47, "sma_20": 105.20}
    prox = {"levels": ["sma_5", "sma_20"], "tolerance_ticks": 1}
    # 100.5 within 1 tick of 100.47 (tick_size at 100 = 0.05)
    ok, level = engine._eval_ma_proximity("2330", _tick(100.5), prox)
    assert ok is True
    assert level == "sma_5"


def test_ma_proximity_no_hit_returns_false():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"sma_5": 100.0}
    prox = {"levels": ["sma_5"], "tolerance_ticks": 0}
    ok, level = engine._eval_ma_proximity("2330", _tick(102.0), prox)
    assert ok is False
    assert level is None


def test_ma_proximity_missing_cache_returns_false():
    engine = SignalEngine()
    engine._field_cache["2330"] = {}  # no sma
    prox = {"levels": ["sma_5"], "tolerance_ticks": 5}
    ok, level = engine._eval_ma_proximity("2330", _tick(100.0), prox)
    assert ok is False
    assert level is None
