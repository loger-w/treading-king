"""auto_monitor_scheduler 篩選邏輯。"""
import pytest

from jobs.auto_monitor_scheduler import (
    _amplitude_pct,
    _passes_screen,
    AUTO_MONITOR_CAP,
    MIN_CHANGE_PCT,
    MAX_CHANGE_PCT,
    MIN_AMP_PCT,
    MIN_VOLUME_LOTS,
)


def _make_item(symbol="2330", changePct=5.0, highPrice=105.0, lowPrice=100.0,
               tradeVolume=5000, **kw):
    return {"symbol": symbol, "changePercent": changePct,
            "highPrice": highPrice, "lowPrice": lowPrice,
            "tradeVolume": tradeVolume, **kw}


def test_amplitude_pct_normal():
    assert _amplitude_pct(105.0, 100.0) == pytest.approx(5.0)


def test_amplitude_pct_zero_low():
    assert _amplitude_pct(10.0, 0.0) == 0.0


def test_amplitude_pct_none():
    assert _amplitude_pct(None, 100.0) == 0.0


def test_passes_screen_happy_path():
    item = _make_item(changePct=5.0, highPrice=106.0, lowPrice=100.0, tradeVolume=5000)
    assert _passes_screen(item) is True


def test_fails_screen_low_change_pct():
    item = _make_item(changePct=2.5)
    assert _passes_screen(item) is False


def test_fails_screen_high_change_pct():
    item = _make_item(changePct=9.5)
    assert _passes_screen(item) is False


def test_fails_screen_boundary_change_pct_eq_3():
    item = _make_item(changePct=3.0)
    assert _passes_screen(item) is False


def test_fails_screen_low_amplitude():
    item = _make_item(highPrice=101.0, lowPrice=100.0)
    assert _passes_screen(item) is False


def test_fails_screen_low_volume():
    item = _make_item(tradeVolume=2000)
    assert _passes_screen(item) is False


def test_fails_screen_non_4digit_symbol():
    item = _make_item(symbol="00878")
    assert _passes_screen(item) is False


def test_fails_screen_missing_fields():
    assert _passes_screen({"symbol": "2330"}) is False


def test_cap_is_100():
    assert AUTO_MONITOR_CAP == 100


def test_thresholds():
    assert MIN_CHANGE_PCT == 3.0
    assert MAX_CHANGE_PCT == 9.0
    assert MIN_AMP_PCT == 3.0
    assert MIN_VOLUME_LOTS == 3000
