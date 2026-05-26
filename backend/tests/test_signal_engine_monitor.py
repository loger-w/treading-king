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


@pytest.mark.asyncio
async def test_fanout_calls_discord_when_notify_enabled(monkeypatch):
    """rule.notify_discord=True → discord_notifier.send_signal 被叫一次。"""
    from services import signal_engine as se
    from services.ring_buffer import Tick
    from models.condition import ActiveSignalOut, ActiveFilter, Condition

    sent = []
    async def fake_send_signal(**kwargs):
        sent.append(kwargs)
    monkeypatch.setattr(se, "discord_notifier", MagicMock(send_signal=fake_send_signal))
    monkeypatch.setattr(se, "get_broadcaster", lambda: MagicMock(broadcast=AsyncMock()))
    fake_writer = MagicMock(append=MagicMock())
    monkeypatch.setattr("services.supabase_writer.get_supabase_writer", lambda: fake_writer)
    monkeypatch.setattr(se, "get_user_label", lambda: "loger")

    engine = SignalEngine()
    active = ActiveSignalOut(
        id="r1", name="r1",
        filter_json=ActiveFilter(conditions=[Condition(field="close", operator="gt", value=0)]),
        scope={"type": "watchlist"}, cooldown_seconds=60, enabled=True,
        created_at="2026-05-26", notify_discord=True,
    )
    tick = Tick(price=600.0, size=10, time=1700000000.0)
    await engine._fanout(active, "2330", tick)

    assert len(sent) == 1
    assert sent[0]["rule_name"] == "r1"
    assert sent[0]["symbol"] == "2330"


@pytest.mark.asyncio
async def test_fanout_skips_discord_when_notify_disabled(monkeypatch):
    from services import signal_engine as se
    from services.ring_buffer import Tick
    from models.condition import ActiveSignalOut, ActiveFilter, Condition

    sent = []
    async def fake_send_signal(**kwargs):
        sent.append(kwargs)
    monkeypatch.setattr(se, "discord_notifier", MagicMock(send_signal=fake_send_signal))
    monkeypatch.setattr(se, "get_broadcaster", lambda: MagicMock(broadcast=AsyncMock()))
    monkeypatch.setattr("services.supabase_writer.get_supabase_writer", lambda: MagicMock(append=MagicMock()))
    monkeypatch.setattr(se, "get_user_label", lambda: "loger")

    engine = SignalEngine()
    active = ActiveSignalOut(
        id="r2", name="r2",
        filter_json=ActiveFilter(conditions=[Condition(field="close", operator="gt", value=0)]),
        scope={"type": "watchlist"}, cooldown_seconds=60, enabled=True,
        created_at="2026-05-26", notify_discord=False,
    )
    tick = Tick(price=600.0, size=10, time=1700000000.0)
    await engine._fanout(active, "2330", tick)

    assert sent == []


@pytest.mark.asyncio
async def test_fanout_continues_when_discord_raises(monkeypatch):
    """discord 推送丟錯不該影響 ws broadcast + supabase append。"""
    from services import signal_engine as se
    from services.ring_buffer import Tick
    from models.condition import ActiveSignalOut, ActiveFilter, Condition

    async def raising_send_signal(**kwargs):
        raise RuntimeError("discord down")
    monkeypatch.setattr(se, "discord_notifier", MagicMock(send_signal=raising_send_signal))
    broadcaster = MagicMock(broadcast=AsyncMock())
    monkeypatch.setattr(se, "get_broadcaster", lambda: broadcaster)
    writer = MagicMock(append=MagicMock())
    monkeypatch.setattr("services.supabase_writer.get_supabase_writer", lambda: writer)
    monkeypatch.setattr(se, "get_user_label", lambda: "loger")

    engine = SignalEngine()
    active = ActiveSignalOut(
        id="r3", name="r3",
        filter_json=ActiveFilter(conditions=[Condition(field="close", operator="gt", value=0)]),
        scope={"type": "watchlist"}, cooldown_seconds=60, enabled=True,
        created_at="2026-05-26", notify_discord=True,
    )
    tick = Tick(price=600.0, size=10, time=1700000000.0)
    # 不該 raise
    await engine._fanout(active, "2330", tick)

    broadcaster.broadcast.assert_awaited_once()
    writer.append.assert_called_once()
