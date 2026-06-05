"""驗策略 1:鎖死漲停 ≥N 秒 → 打開後由上而下碰 CDP 線 → 發(測支撐)。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from models.condition import ActiveFilter, ActiveSignalOut, LimitUpOpenTouchStrategy
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine

POST_OPEN = datetime(2026, 5, 20, 10, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()

STRAT = {
    "type": "limit_up_open_touch", "lock_seconds": 60,
    "levels": ["cdp"], "tolerance_ticks": 1,
}


def _cache(engine):
    # 昨收 100 → 漲停價 110;CDP 中線設 100(供回踩碰線)
    engine._field_cache["2330"] = {"prev_close": 100.0, "cdp": 100.0}


def test_lock_accumulates_over_pinned_ticks():
    engine = SignalEngine()
    _cache(engine)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=0.0)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=70.0)
    assert engine._limit_lock_best["2330"] >= 60.0


def test_fires_after_lock_then_open_then_touch_from_above():
    engine = SignalEngine()
    _cache(engine)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=0.0)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=70.0)
    touch = engine._eval_limit_up_open_touch(
        STRAT, "2330", Tick(100.0, 1, 71.0), prev=Tick(101.0, 1, 70.5),
    )
    assert touch == {"level": "cdp", "direction": "from_above", "role": "support"}


def test_no_fire_if_lock_too_short():
    engine = SignalEngine()
    _cache(engine)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=0.0)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=30.0)   # 只 30s
    engine._update_limit_up_state("2330", Tick(109.0, 1, 0.0), now=31.0)   # 打開
    touch = engine._eval_limit_up_open_touch(
        STRAT, "2330", Tick(100.0, 1, 40.0), prev=Tick(101.0, 1, 39.0),
    )
    assert touch is None


def test_no_fire_on_upward_touch():
    # 鎖死夠久但反彈由下往上碰線(回升穿線)→ 不發(只認由上而下測支撐)
    engine = SignalEngine()
    _cache(engine)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=0.0)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=70.0)
    touch = engine._eval_limit_up_open_touch(
        STRAT, "2330", Tick(100.0, 1, 80.0), prev=Tick(99.0, 1, 79.0),
    )
    assert touch is None


def test_reset_daily_clears_lock_state():
    engine = SignalEngine()
    engine._limit_at_since["2330"] = 5.0
    engine._limit_lock_best["2330"] = 120.0
    engine._reset_daily_strategy_state()
    assert "2330" not in engine._limit_lock_best
    assert "2330" not in engine._limit_at_since


@pytest.mark.asyncio
async def test_fires_through_evaluate():
    engine = SignalEngine()
    engine._active = [ActiveSignalOut(
        id="s", name="漲停打開碰CDP",
        filter_json=ActiveFilter(strategy=LimitUpOpenTouchStrategy(
            type="limit_up_open_touch", lock_seconds=60, levels=["cdp"], tolerance_ticks=1)),
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=60, enabled=True, created_at="2026-06-05",
    )]
    engine._limit_up_active = True
    _cache(engine)
    engine._limit_lock_best["2330"] = 65.0        # 已鎖死夠久
    engine._prev_tick["2330"] = Tick(101.0, 1, 0.0)

    captured: dict = {}
    async def fake_broadcast(payload): captured.update(payload)

    with patch("services.signal_engine.time.time", return_value=POST_OPEN), \
         patch("services.signal_engine.get_broadcaster") as bc, \
         patch("services.signal_engine.get_signal_writer") as sw:
        bc.return_value.broadcast = fake_broadcast
        sw.return_value = MagicMock()
        await engine._evaluate("2330", Tick(price=100.0, size=1, time=1.0))

    assert captured["data"]["cdp_touch"]["level"] == "cdp"
    assert captured["data"]["cdp_touch"]["role"] == "support"
