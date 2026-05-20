"""驗試撮期間(08:30 ≤ t < 09:00,台北時間)不觸發訊號。

Gate 判斷依據是 wall-clock(現在時間),不是 tick.time —
heartbeat path 拿 ring_buffer.latest 可能是昨日舊 tick,用 tick.time 會漏擋。
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


def test_in_pre_open_08_45_weekday_is_blocked():
    # 2026-05-20 是 Wed
    assert SignalEngine._in_pre_open_period(_ts(2026, 5, 20, 8, 45)) is True


def test_in_pre_open_08_30_weekday_is_blocked():
    """試撮起始邊界 08:30:00 含在內。"""
    assert SignalEngine._in_pre_open_period(_ts(2026, 5, 20, 8, 30)) is True


def test_in_pre_open_09_00_weekday_is_not_blocked():
    """09:00 正式開盤,gate 不該擋。"""
    assert SignalEngine._in_pre_open_period(_ts(2026, 5, 20, 9, 0)) is False


def test_in_pre_open_08_29_weekday_is_not_blocked():
    """08:30 之前(試撮還沒開始)gate 不擋。"""
    assert SignalEngine._in_pre_open_period(_ts(2026, 5, 20, 8, 29)) is False


def test_in_pre_open_09_15_weekday_is_not_blocked():
    """盤中 09:15 完全不該被擋。"""
    assert SignalEngine._in_pre_open_period(_ts(2026, 5, 20, 9, 15)) is False


def test_in_pre_open_weekend_not_blocked():
    # 2026-05-23 是 Sat
    assert SignalEngine._in_pre_open_period(_ts(2026, 5, 23, 8, 45)) is False


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
         patch("services.supabase_writer.get_supabase_writer") as mock_sw:
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
         patch("services.supabase_writer.get_supabase_writer") as mock_sw:
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
         patch("services.supabase_writer.get_supabase_writer") as mock_sw:
        mock_bc.return_value.broadcast = MagicMock()
        mock_sw.return_value = MagicMock()
        await engine._evaluate("2330", pre_open_tick)

    assert engine._prev_tick["2330"] is original_prev


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
         patch("services.supabase_writer.get_supabase_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()

        await engine._evaluate(
            "2330",
            Tick(price=100.0, size=1, time=_ts(2026, 5, 20, 9, 0)),
        )

    assert captured.get("event") == "signal"
    assert captured["data"]["cdp_touch"]["level"] == "ah"
