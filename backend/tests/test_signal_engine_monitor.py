"""驗 signal_engine 改成讀 monitor_list 評估(不再讀 active.scope)。"""
from unittest.mock import MagicMock, AsyncMock

import pytest

from services.signal_engine import SignalEngine


@pytest.mark.asyncio
async def test_load_monitor_symbols_returns_set(monkeypatch):
    from services import signal_engine as se
    fake_table = MagicMock()
    fake_table.select.return_value = fake_table
    fake_table.eq.return_value = fake_table
    fake_table.execute.return_value = MagicMock(data=[{"symbol": "2330"}, {"symbol": "2317"}])
    fake_sb = MagicMock()
    fake_sb.client.table.return_value = fake_table
    monkeypatch.setattr(se, "get_supabase", lambda: fake_sb)
    monkeypatch.setattr(se, "get_user_label", lambda: "loger")

    engine = SignalEngine()
    syms = await engine._load_monitor_symbols()
    assert syms == {"2330", "2317"}
    fake_table.eq.assert_called_with("user_label", "loger")


@pytest.mark.asyncio
async def test_load_monitor_symbols_empty_when_supabase_none(monkeypatch):
    from services import signal_engine as se
    fake_sb = MagicMock(client=None)
    monkeypatch.setattr(se, "get_supabase", lambda: fake_sb)
    engine = SignalEngine()
    assert await engine._load_monitor_symbols() == set()


def test_scope_includes_uses_field_cache_membership():
    """field_cache 由 monitor_list refill,_scope_includes 不再讀 active.scope。"""
    engine = SignalEngine()
    engine._field_cache = {"2330": {}, "2317": {}}
    active = MagicMock()
    assert engine._scope_includes(active, "2330") is True
    assert engine._scope_includes(active, "9999") is False


def test_scope_symbols_returns_field_cache_keys():
    """heartbeat 用 _scope_symbols 拿 monitor_list 全部 symbol。"""
    engine = SignalEngine()
    engine._field_cache = {"2330": {}, "2317": {}}
    active = MagicMock()
    assert set(engine._scope_symbols(active)) == {"2330", "2317"}
