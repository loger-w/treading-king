"""Signal engine auto-monitor symbol 整合。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.signal_engine import SignalEngine


@pytest.mark.asyncio
async def test_load_monitor_symbols_includes_auto(local_store_tmp):
    """_load_monitor_symbols 回傳 config monitor_list ∪ _auto_monitor_symbols。"""
    local_store_tmp.config.add_monitor("2330")
    engine = SignalEngine()
    engine._auto_monitor_symbols = {"3008"}
    syms = await engine._load_monitor_symbols()
    assert syms == {"2330", "3008"}


@pytest.mark.asyncio
async def test_add_auto_symbols_populates_field_cache(local_store_tmp):
    """add_auto_symbols 後新 symbol 出現在 field_cache。"""
    engine = SignalEngine()

    with patch("services.signal_engine.get_cdp_service") as mock_cdp, \
         patch("services.signal_engine.ma_service") as mock_ma:
        mock_cdp.return_value.get = AsyncMock(return_value={
            "ah": 110, "nh": 105, "cdp": 100, "nl": 95, "al": 90, "prev_close": 99,
        })
        mock_ma.fetch_sma_5_20 = AsyncMock(return_value=(100.0, 98.0))
        await engine.add_auto_symbols({"2330"})

    assert "2330" in engine._field_cache
    assert engine._field_cache["2330"]["cdp"] == 100
    assert "2330" in engine._auto_monitor_symbols


@pytest.mark.asyncio
async def test_add_auto_symbols_skips_already_added(local_store_tmp):
    """已在 auto set 裡的 symbol 不重複載入 field_cache。"""
    engine = SignalEngine()
    engine._auto_monitor_symbols = {"2330"}
    engine._field_cache = {"2330": {"cdp": 100}}

    with patch("services.signal_engine.get_cdp_service") as mock_cdp, \
         patch("services.signal_engine.ma_service") as mock_ma:
        mock_cdp.return_value.get = AsyncMock(return_value=None)
        mock_ma.fetch_sma_5_20 = AsyncMock(return_value=(None, None))
        await engine.add_auto_symbols({"2330"})

    assert engine._field_cache["2330"]["cdp"] == 100
    mock_cdp.return_value.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_auto_symbols_evicts_auto_only(local_store_tmp):
    """clear_auto_symbols 只逐出 auto-only 的 symbol,不動手動 monitor 的。"""
    local_store_tmp.config.add_monitor("2330")

    engine = SignalEngine()
    engine._auto_monitor_symbols = {"3008", "2330"}
    engine._field_cache = {"2330": {"cdp": 100}, "3008": {"cdp": 200}}

    await engine.clear_auto_symbols()

    assert "2330" in engine._field_cache
    assert "3008" not in engine._field_cache
    assert engine._auto_monitor_symbols == set()


@pytest.mark.asyncio
async def test_clear_auto_symbols_idempotent(local_store_tmp):
    """空 auto set 時 clear 不炸。"""
    engine = SignalEngine()
    await engine.clear_auto_symbols()
    assert engine._auto_monitor_symbols == set()


@pytest.mark.asyncio
async def test_daily_reset_clears_auto_symbols():
    """_reset_daily_strategy_state 應清空 _auto_monitor_symbols。"""
    engine = SignalEngine()
    engine._auto_monitor_symbols = {"2330", "3008"}
    engine._reset_daily_strategy_state()
    assert engine._auto_monitor_symbols == set()
