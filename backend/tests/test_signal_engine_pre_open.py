"""驗只有正盤(週一~五 09:00 ≤ t < 13:30,台北時間)才評估訊號。

Gate 判斷依據是 wall-clock(現在時間),不是 tick.time — heartbeat path 拿
ring_buffer.latest 可能是收盤 / 昨日舊 tick,用 tick.time 會誤把盤後算成盤中,
導致收盤價落在 CDP proximity 內時每 cooldown 一直重複觸發訊號。
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from models.condition import (
    ActiveFilter, ActiveSignalOut, CdpProximityCondition,
)
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine

TW = timezone(timedelta(hours=8))


def _ts(year: int, month: int, day: int, hour: int, minute: int) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=TW).timestamp()


def _make_active() -> ActiveSignalOut:
    return ActiveSignalOut(
        id="x", name="t",
        filter_json=ActiveFilter(
            cdp_proximity=CdpProximityCondition(levels=["ah"], tolerance_ticks=0),
        ),
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=60,
        enabled=True,
        created_at="2026-05-20",
    )


def test_pre_open_08_45_weekday_blocked():
    """試撮期間 08:45 不在 trading session。2026-05-20 是 Wed。"""
    assert SignalEngine._in_trading_session(_ts(2026, 5, 20, 8, 45)) is False


def test_pre_open_08_30_weekday_blocked():
    """試撮起始邊界 08:30 仍不在 trading session。"""
    assert SignalEngine._in_trading_session(_ts(2026, 5, 20, 8, 30)) is False


def test_market_open_09_00_weekday_in_session():
    """09:00 正式開盤,gate 放行。"""
    assert SignalEngine._in_trading_session(_ts(2026, 5, 20, 9, 0)) is True


def test_pre_open_08_29_weekday_blocked():
    """08:30 之前的早盤前也不在 trading session(沒成交)。"""
    assert SignalEngine._in_trading_session(_ts(2026, 5, 20, 8, 29)) is False


def test_intraday_09_15_in_session():
    """盤中 09:15 應在 trading session。"""
    assert SignalEngine._in_trading_session(_ts(2026, 5, 20, 9, 15)) is True


def test_intraday_13_29_in_session():
    """收盤前最後一分鐘 13:29 仍應評估(收盤集合競價最後一筆訊號)。"""
    assert SignalEngine._in_trading_session(_ts(2026, 5, 20, 13, 29)) is True


def test_market_close_13_30_blocked():
    """收盤瞬間 13:30 起 gate 開始擋 — 收盤 stale tick 不再重複觸發。"""
    assert SignalEngine._in_trading_session(_ts(2026, 5, 20, 13, 30)) is False


def test_post_close_13_35_blocked():
    """盤後 13:35 不在 trading session — 修這個 bug 的主場景。"""
    assert SignalEngine._in_trading_session(_ts(2026, 5, 20, 13, 35)) is False


def test_post_close_18_00_blocked():
    """盤後傍晚 18:00 仍不在 trading session(heartbeat 還在跑,擋住才不會 fire)。"""
    assert SignalEngine._in_trading_session(_ts(2026, 5, 20, 18, 0)) is False


def test_overnight_03_00_blocked():
    """凌晨 3:00 不在 trading session。"""
    assert SignalEngine._in_trading_session(_ts(2026, 5, 20, 3, 0)) is False


def test_weekend_blocked():
    """週末整天不評估。2026-05-23 是 Sat。"""
    assert SignalEngine._in_trading_session(_ts(2026, 5, 23, 10, 0)) is False
    assert SignalEngine._in_trading_session(_ts(2026, 5, 24, 11, 0)) is False  # Sun


@pytest.mark.asyncio
async def test_evaluate_skips_fanout_during_pre_open():
    engine = SignalEngine()
    engine._active = [_make_active()]
    engine._field_cache["2330"] = {"cdp_ah": 100.0}
    engine._prev_tick["2330"] = Tick(price=99.0, size=1, time=_ts(2026, 5, 20, 8, 44))

    called = MagicMock()
    async def fake_broadcast(payload):
        called(payload)

    with patch("services.signal_engine.time.time", return_value=_ts(2026, 5, 20, 8, 45)), \
         patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_signal_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()

        # wall clock 08:45(試撮),觸到 cdp_ah 也不應 fanout
        await engine._evaluate(
            "2330",
            Tick(price=100.0, size=1, time=_ts(2026, 5, 20, 8, 45)),
        )

    called.assert_not_called()


@pytest.mark.asyncio
async def test_evaluate_blocks_stale_tick_when_wall_clock_in_pre_open():
    """regression: heartbeat 拿到昨日舊 tick(tick.time 在盤中),wall clock 在試撮內,gate 仍要擋。"""
    engine = SignalEngine()
    engine._active = [_make_active()]
    engine._field_cache["2330"] = {"cdp_ah": 100.0}
    engine._prev_tick["2330"] = Tick(price=99.0, size=1, time=_ts(2026, 5, 19, 13, 24))

    # 模擬 heartbeat 拿到「昨日 13:30 收盤」的 stale tick(時間在 trading hours)
    stale_tick = Tick(price=100.0, size=1, time=_ts(2026, 5, 19, 13, 30))

    called = MagicMock()
    async def fake_broadcast(payload):
        called(payload)

    with patch("services.signal_engine.time.time", return_value=_ts(2026, 5, 20, 8, 45)), \
         patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_signal_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()

        await engine._evaluate("2330", stale_tick)

    called.assert_not_called()


@pytest.mark.asyncio
async def test_evaluate_does_not_touch_prev_tick_during_pre_open():
    """試撮期間 prev_tick 保持原值 — 試撮 indicative 不該影響 09:00 後方向計算。"""
    engine = SignalEngine()
    engine._active = [_make_active()]
    engine._field_cache["2330"] = {"cdp_ah": 100.0}
    original_prev = Tick(price=99.0, size=1, time=_ts(2026, 5, 19, 13, 25))
    engine._prev_tick["2330"] = original_prev

    pre_open_tick = Tick(price=98.0, size=1, time=_ts(2026, 5, 20, 8, 45))

    with patch("services.signal_engine.time.time", return_value=_ts(2026, 5, 20, 8, 45)), \
         patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_signal_writer") as mock_sw:
        mock_bc.return_value.broadcast = MagicMock()
        mock_sw.return_value = MagicMock()
        await engine._evaluate("2330", pre_open_tick)

    assert engine._prev_tick["2330"] is original_prev


@pytest.mark.asyncio
async def test_evaluate_blocks_stale_tick_after_close():
    """regression: 收盤後 heartbeat 拿 13:30 最後一筆 stale tick(剛好落在 CDP 附近)
    一直重評估,gate 必須擋住 — 不擋的話每 cooldown 重發訊號(本次修這個 bug 的場景)。"""
    engine = SignalEngine()
    engine._active = [_make_active()]
    engine._field_cache["2330"] = {"cdp_ah": 100.0}
    engine._prev_tick["2330"] = Tick(price=99.5, size=1, time=_ts(2026, 5, 20, 13, 29))

    # 模擬「收盤價剛好停在 cdp_ah 100.0 上」的 stale tick — heartbeat 反覆拿到
    stale_tick = Tick(price=100.0, size=1, time=_ts(2026, 5, 20, 13, 30))

    called = MagicMock()
    async def fake_broadcast(payload):
        called(payload)

    # wall clock 在盤後 14:30(cooldown 過期後 heartbeat 重評估)
    with patch("services.signal_engine.time.time", return_value=_ts(2026, 5, 20, 14, 30)), \
         patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_signal_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()

        await engine._evaluate("2330", stale_tick)

    called.assert_not_called()


@pytest.mark.asyncio
async def test_evaluate_triggers_at_market_open():
    """09:00:00 第一秒 gate 就該放行。"""
    engine = SignalEngine()
    engine._active = [_make_active()]
    engine._field_cache["2330"] = {"cdp_ah": 100.0}
    engine._prev_tick["2330"] = Tick(price=99.0, size=1, time=_ts(2026, 5, 19, 13, 30))

    captured: dict = {}
    async def fake_broadcast(payload):
        captured.update(payload)

    with patch("services.signal_engine.time.time", return_value=_ts(2026, 5, 20, 9, 0)), \
         patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_signal_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()

        await engine._evaluate(
            "2330",
            Tick(price=100.0, size=1, time=_ts(2026, 5, 20, 9, 0)),
        )

    assert captured.get("event") == "signal"
    assert captured["data"]["cdp_touch"]["level"] == "ah"
