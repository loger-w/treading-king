"""驗 signal_engine 改成讀 monitor_list 評估(不再讀 active.scope)。"""
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from services.signal_engine import SignalEngine


@pytest.mark.asyncio
async def test_load_monitor_symbols_returns_set(local_store_tmp):
    local_store_tmp.config.add_monitor("2330")
    local_store_tmp.config.add_monitor("2317")

    engine = SignalEngine()
    syms = await engine._load_monitor_symbols()
    assert syms == {"2330", "2317"}


@pytest.mark.asyncio
async def test_load_monitor_symbols_empty_when_no_monitor(local_store_tmp):
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
    monkeypatch.setattr(se, "get_signal_writer", lambda: fake_writer)

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
    monkeypatch.setattr(se, "get_signal_writer", lambda: MagicMock(append=MagicMock()))

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
    """discord 推送丟錯不該影響 ws broadcast + 歷史寫入。"""
    from services import signal_engine as se
    from services.ring_buffer import Tick
    from models.condition import ActiveSignalOut, ActiveFilter, Condition

    async def raising_send_signal(**kwargs):
        raise RuntimeError("discord down")
    monkeypatch.setattr(se, "discord_notifier", MagicMock(send_signal=raising_send_signal))
    broadcaster = MagicMock(broadcast=AsyncMock())
    monkeypatch.setattr(se, "get_broadcaster", lambda: broadcaster)
    writer = MagicMock(append=MagicMock())
    monkeypatch.setattr(se, "get_signal_writer", lambda: writer)

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


@pytest.mark.asyncio
async def test_refill_evicts_symbol_removed_from_monitor():
    """從 monitor_list 移除的 symbol,refill 後不該殘留在 field_cache。

    field_cache 是 _scope_includes / _scope_symbols 的唯一閘門;殘留會讓已刪除的
    股票仍通過 scope 檢查 → tick-driven 與 heartbeat 兩條路徑都繼續評估、繼續
    觸發訊號(對應使用者回報「刪除監聽後訊號仍跳」)。
    """
    engine = SignalEngine()
    # 模擬先前 refill 後 cache 內有兩檔
    engine._field_cache = {"2330": {"cdp": 600.0}, "2317": {"cdp": 100.0}}
    # monitor_list 現在只剩 2317(2330 已被刪除)
    engine._load_monitor_symbols = AsyncMock(return_value={"2317"})

    with patch("services.signal_engine.get_cdp_service") as mock_cdp, \
         patch("services.signal_engine.ma_service") as mock_ma:
        mock_cdp.return_value.get = AsyncMock(return_value=None)
        mock_ma.fetch_sma_5_20 = AsyncMock(return_value=(None, None))
        await engine._refill_field_cache()

    assert "2330" not in engine._field_cache   # 被刪的要被逐出
    assert "2317" in engine._field_cache       # 還在 monitor 的要保留
