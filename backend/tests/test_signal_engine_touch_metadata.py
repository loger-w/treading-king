"""驗 fanout payload 帶 cdp_touch / ma_touch 含 direction、role、touch_index。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.condition import (
    ActiveFilter, ActiveSignalOut, CdpProximityCondition,
)
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine

# 固定 wall-clock 在試撮外、9:00 後,避免 gate 隨真實時間 flaky
POST_OPEN = datetime(2026, 5, 20, 10, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()


def _make_active() -> ActiveSignalOut:
    # rearm_ticks=0:本檔驗 touch metadata / touch_index(需要重複觸發),
    # re-arm 行為由 test_signal_engine_rearm.py 覆蓋
    return ActiveSignalOut(
        id="x", name="t",
        filter_json=ActiveFilter(
            cdp_proximity=CdpProximityCondition(
                levels=["ah"], tolerance_ticks=0, rearm_ticks=0,
            ),
        ),
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=60,
        enabled=True,
        created_at="2026-05-19",
    )


@pytest.mark.asyncio
async def test_fanout_payload_includes_cdp_touch_from_below():
    engine = SignalEngine()
    engine._active = [_make_active()]
    engine._field_cache["2330"] = {"cdp_ah": 100.0}
    engine._prev_tick["2330"] = Tick(price=99.0, size=1, time=0.0)

    captured: dict = {}
    async def fake_broadcast(payload):
        captured.update(payload)

    with patch("services.signal_engine.time.time", return_value=POST_OPEN), \
         patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_signal_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        sw = MagicMock()
        mock_sw.return_value = sw

        await engine._evaluate("2330", Tick(price=100.0, size=1, time=1.0))

    assert captured["event"] == "signal"
    assert captured["data"]["cdp_touch"] == {
        "level": "ah", "direction": "from_below",
        "role": "resistance", "touch_index": 1,
    }


@pytest.mark.asyncio
async def test_touch_index_increments_on_repeat_trigger():
    engine = SignalEngine()
    active = _make_active()
    active.cooldown_seconds = 0  # 不檔重複觸發
    engine._active = [active]
    engine._field_cache["2330"] = {"cdp_ah": 100.0}

    captured_indices: list[int] = []
    async def fake_broadcast(payload):
        captured_indices.append(payload["data"]["cdp_touch"]["touch_index"])

    with patch("services.signal_engine.time.time", return_value=POST_OPEN), \
         patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_signal_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()

        # 3 次連續從下往上跨越 100
        for i in range(3):
            engine._prev_tick["2330"] = Tick(price=99.0, size=1, time=float(i * 2))
            await engine._evaluate("2330", Tick(price=100.0, size=1, time=float(i * 2 + 1)))

    assert captured_indices == [1, 2, 3]
