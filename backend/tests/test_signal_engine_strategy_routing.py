"""驗 strategy 路由:既有規則照常觸發;strategy 規則走 _eval_strategy(目前 stub 不發)。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from models.condition import (
    ActiveFilter, ActiveSignalOut, CdpProximityCondition, LimitUpOpenTouchStrategy,
)
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine

POST_OPEN = datetime(2026, 5, 20, 10, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()


def _cdp_active() -> ActiveSignalOut:
    return ActiveSignalOut(
        id="g", name="generic",
        filter_json=ActiveFilter(cdp_proximity=CdpProximityCondition(levels=["ah"], tolerance_ticks=0)),
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=60, enabled=True, created_at="2026-06-05",
    )


def _strategy_active() -> ActiveSignalOut:
    return ActiveSignalOut(
        id="s", name="strat",
        filter_json=ActiveFilter(strategy=LimitUpOpenTouchStrategy(type="limit_up_open_touch")),
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=60, enabled=True, created_at="2026-06-05",
    )


@pytest.mark.asyncio
async def test_generic_rule_still_fires_with_routing():
    engine = SignalEngine()
    engine._active = [_cdp_active()]
    engine._field_cache["2330"] = {"cdp_ah": 100.0}
    engine._prev_tick["2330"] = Tick(price=99.0, size=1, time=0.0)

    captured: dict = {}
    async def fake_broadcast(payload): captured.update(payload)

    with patch("services.signal_engine.time.time", return_value=POST_OPEN), \
         patch("services.signal_engine.get_broadcaster") as bc, \
         patch("services.signal_engine.get_signal_writer") as sw:
        bc.return_value.broadcast = fake_broadcast
        sw.return_value = MagicMock()
        await engine._evaluate("2330", Tick(price=100.0, size=1, time=1.0))

    assert captured.get("event") == "signal"


@pytest.mark.asyncio
async def test_strategy_rule_no_fire_with_stub():
    engine = SignalEngine()
    engine._active = [_strategy_active()]
    engine._field_cache["2330"] = {"prev_close": 100.0, "cdp_ah": 100.0}
    engine._prev_tick["2330"] = Tick(price=99.0, size=1, time=0.0)

    fired = False
    async def fake_broadcast(payload):
        nonlocal fired; fired = True

    with patch("services.signal_engine.time.time", return_value=POST_OPEN), \
         patch("services.signal_engine.get_broadcaster") as bc, \
         patch("services.signal_engine.get_signal_writer") as sw:
        bc.return_value.broadcast = fake_broadcast
        sw.return_value = MagicMock()
        await engine._evaluate("2330", Tick(price=100.0, size=1, time=1.0))

    assert fired is False   # stub _eval_strategy 回 None
