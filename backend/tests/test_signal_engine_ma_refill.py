"""驗 _refill_field_cache 會把 MA 寫進 field_cache。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.condition import (
    ActiveFilter, ActiveSignalOut, Condition,
)
from services.signal_engine import SignalEngine


@pytest.mark.asyncio
async def test_refill_writes_sma_into_field_cache():
    engine = SignalEngine()
    # _refill_field_cache 現在透過 _load_monitor_symbols 拿 symbol 集合,
    # 直接 monkeypatch 該方法讓它回傳測試目標 symbol,不需要 active scope 設定。
    engine._load_monitor_symbols = AsyncMock(return_value={"2330"})

    with patch("services.signal_engine.get_cdp_service") as mock_cdp, \
         patch("services.signal_engine.ma_service") as mock_ma:
        mock_cdp.return_value.get = AsyncMock(return_value=None)
        mock_ma.fetch_sma_5_20 = AsyncMock(return_value=(100.5, 105.2))

        await engine._refill_field_cache()

    assert engine._field_cache["2330"]["sma_5"]  == 100.5
    assert engine._field_cache["2330"]["sma_20"] == 105.2


@pytest.mark.asyncio
async def test_refill_skips_ma_when_both_none():
    """ma_service 失敗 (兩個都 None) 時不應該寫進 cache。"""
    engine = SignalEngine()
    engine._active = [
        ActiveSignalOut(
            id="x", name="t",
            filter_json=ActiveFilter(
                conditions=[Condition(field="close", operator="gte", value=100)],
            ),
            scope={"type": "symbols", "symbols": ["2330"]},
            cooldown_seconds=60, enabled=True, created_at="2026-05-19",
        )
    ]

    with patch("services.signal_engine.get_cdp_service") as mock_cdp, \
         patch("services.signal_engine.ma_service") as mock_ma, \
         patch("services.signal_engine.get_supabase") as mock_sb:
        mock_cdp.return_value.get = AsyncMock(return_value=None)
        mock_ma.fetch_sma_5_20 = AsyncMock(return_value=(None, None))
        mock_sb.return_value.client = None

        await engine._refill_field_cache()

    assert "sma_5"  not in engine._field_cache.get("2330", {})
    assert "sma_20" not in engine._field_cache.get("2330", {})
