"""驗 跨日 heartbeat 清掉前一日的 touch_count key。"""
from datetime import date, timedelta

from services.signal_engine import SignalEngine


def test_gc_keeps_today_drops_yesterday():
    engine = SignalEngine()
    today = date.today()
    yesterday = today - timedelta(days=1)

    engine._cdp_touch_count = {
        ("2330", "ah", today):     3,
        ("2330", "ah", yesterday): 5,
        ("2454", "cdp", yesterday): 2,
    }
    engine._ma_touch_count = {
        ("2330", "sma_5", today):     1,
        ("2330", "sma_5", yesterday): 7,
    }

    engine._gc_touch_counts()

    assert engine._cdp_touch_count == {("2330", "ah", today): 3}
    assert engine._ma_touch_count  == {("2330", "sma_5", today): 1}
