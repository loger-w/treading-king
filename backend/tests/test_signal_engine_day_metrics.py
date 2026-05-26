"""驗 day_change_pct / day_volume 兩個動態 field 行為。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from models.condition import ActiveFilter, ActiveSignalOut, Condition
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine

TW = timezone(timedelta(hours=8))


def _ts(year: int, month: int, day: int, hour: int, minute: int) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=TW).timestamp()


def _active(cond: Condition) -> ActiveSignalOut:
    return ActiveSignalOut(
        id="x", name="t",
        filter_json=ActiveFilter(conditions=[cond]),
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=60,
        enabled=True,
        created_at="2026-05-20",
    )


def test_resolve_day_change_pct_uses_prev_close():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"prev_close": 100.0}
    # +6% → 106.0
    tick = Tick(price=106.0, size=0, time=_ts(2026, 5, 20, 10, 0))
    assert engine._resolve_field("2330", tick, "day_change_pct") == pytest.approx(6.0)


def test_resolve_day_change_pct_negative():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"prev_close": 100.0}
    tick = Tick(price=92.5, size=0, time=_ts(2026, 5, 20, 10, 0))
    assert engine._resolve_field("2330", tick, "day_change_pct") == pytest.approx(-7.5)


def test_resolve_day_change_pct_missing_prev_close_returns_none():
    engine = SignalEngine()
    engine._field_cache["2330"] = {}  # no prev_close
    tick = Tick(price=100.0, size=0, time=_ts(2026, 5, 20, 10, 0))
    assert engine._resolve_field("2330", tick, "day_change_pct") is None


def test_resolve_day_volume_returns_accumulator():
    engine = SignalEngine()
    engine._day_volume["2330"] = 1234
    tick = Tick(price=100.0, size=0, time=_ts(2026, 5, 20, 10, 0))
    assert engine._resolve_field("2330", tick, "day_volume") == 1234.0


def test_resolve_day_volume_zero_when_not_seen():
    engine = SignalEngine()
    tick = Tick(price=100.0, size=0, time=_ts(2026, 5, 20, 10, 0))
    assert engine._resolve_field("2330", tick, "day_volume") == 0.0


# 共用 wall-clock — 10:00 已開盤,讓 evaluate 路徑跑得到 fanout
POST_OPEN = _ts(2026, 5, 20, 10, 0)


@pytest.mark.asyncio
async def test_evaluate_does_not_accumulate_day_volume_during_pre_open():
    """試撮期間(08:30-09:00)沒實際成交,不該污染今日總量。"""
    engine = SignalEngine()
    pre_open_tick = Tick(price=100.0, size=3, time=_ts(2026, 5, 20, 8, 45))

    with patch("services.signal_engine.time.time", return_value=_ts(2026, 5, 20, 8, 45)):
        await engine._evaluate("2330", pre_open_tick)

    assert engine._day_volume.get("2330", 0) == 0


@pytest.mark.asyncio
async def test_evaluate_accumulates_day_volume_after_open():
    """09:00 後 tick 的 size 要進 _day_volume 累積器。"""
    engine = SignalEngine()
    with patch("services.signal_engine.time.time", return_value=POST_OPEN):
        await engine._evaluate("2330", Tick(price=100.0, size=4, time=POST_OPEN))
        await engine._evaluate("2330", Tick(price=101.0, size=7, time=POST_OPEN))

    assert engine._day_volume["2330"] == 11


@pytest.mark.asyncio
async def test_evaluate_day_change_pct_triggers_signal_above_threshold():
    engine = SignalEngine()
    engine._active = [_active(Condition(field="day_change_pct", operator="gt", value=6))]
    engine._field_cache["2330"] = {"prev_close": 100.0}

    captured: dict = {}
    async def fake_broadcast(payload):
        captured.update(payload)

    with patch("services.signal_engine.time.time", return_value=POST_OPEN), \
         patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.supabase_writer.get_supabase_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()

        # +7% → 高於 6 → 觸發
        await engine._evaluate(
            "2330",
            Tick(price=107.0, size=1, time=POST_OPEN),
        )

    assert captured.get("event") == "signal"


@pytest.mark.asyncio
async def test_evaluate_day_change_pct_does_not_trigger_below_threshold():
    engine = SignalEngine()
    engine._active = [_active(Condition(field="day_change_pct", operator="gt", value=6))]
    engine._field_cache["2330"] = {"prev_close": 100.0}

    called = MagicMock()
    async def fake_broadcast(payload):
        called(payload)

    with patch("services.signal_engine.time.time", return_value=POST_OPEN), \
         patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.supabase_writer.get_supabase_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()

        # +5% → 低於 6 → 不觸發
        await engine._evaluate(
            "2330",
            Tick(price=105.0, size=1, time=POST_OPEN),
        )

    called.assert_not_called()


@pytest.mark.asyncio
async def test_evaluate_day_volume_triggers_when_cumulative_exceeds_threshold():
    engine = SignalEngine()
    engine._active = [_active(Condition(field="day_volume", operator="gte", value=10))]
    # _scope_includes 現在只看 _field_cache,必須預先放入 symbol 才能通過 scope filter
    engine._field_cache["2330"] = {}

    captured: list = []
    async def fake_broadcast(payload):
        captured.append(payload)

    with patch("services.signal_engine.time.time", return_value=POST_OPEN), \
         patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.supabase_writer.get_supabase_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()

        # 4 + 3 = 7,未達 10 → 不觸發
        await engine._evaluate("2330", Tick(price=100.0, size=4, time=POST_OPEN))
        await engine._evaluate("2330", Tick(price=100.0, size=3, time=POST_OPEN))
        assert len(captured) == 0

        # +5 = 12 ≥ 10 → 觸發
        await engine._evaluate("2330", Tick(price=100.0, size=5, time=POST_OPEN))
        assert len(captured) == 1
