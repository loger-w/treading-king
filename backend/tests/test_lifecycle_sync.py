from unittest.mock import AsyncMock
import pytest
from services.local_store import get_local_store
from services.lifecycle_sync import resync_from_config


@pytest.mark.asyncio
async def test_resync_subscribes_bookmarks_and_monitor(local_store_tmp, monkeypatch):
    cfg = get_local_store().config
    g = cfg.list_groups()[0]
    cfg.add_item(g["id"], "2330")
    cfg.add_monitor("2454")
    fake_pool, fake_engine = AsyncMock(), AsyncMock()
    monkeypatch.setattr("services.lifecycle_sync.get_ws_pool", lambda: fake_pool)
    monkeypatch.setattr("services.lifecycle_sync.get_signal_engine", lambda: fake_engine)
    await resync_from_config(prev_owners={"bookmark:old": ["9999"]})
    fake_pool.unsubscribe.assert_any_await("9999", "bookmark:old")
    fake_pool.subscribe.assert_any_await("2330", owner_id=f"bookmark:{g['id']}")
    fake_pool.subscribe.assert_any_await("2454", owner_id="monitor_list")
    fake_engine.refresh_active_signals.assert_awaited_once()


@pytest.mark.asyncio
async def test_resync_preserves_overlapping_subscriptions(local_store_tmp, monkeypatch):
    """匯入的 config 與現況重疊是常態 — 重疊 (owner, symbol) 不可退訂再重訂:
    refcount 落 0 會真打富邦退訂 + 清掉 ring_buffer 的 tick 視窗(window 條件全盲),
    退訂到重訂之間還有漏 tick 空窗。只動差集。"""
    cfg = get_local_store().config
    g = cfg.list_groups()[0]
    cfg.add_item(g["id"], "2330")          # 重疊:舊有、新也有
    cfg.add_monitor("2454")                # 新增:舊無、新有
    fake_pool, fake_engine = AsyncMock(), AsyncMock()
    monkeypatch.setattr("services.lifecycle_sync.get_ws_pool", lambda: fake_pool)
    monkeypatch.setattr("services.lifecycle_sync.get_signal_engine", lambda: fake_engine)

    owner = f"bookmark:{g['id']}"
    await resync_from_config(prev_owners={
        owner: ["2330", "8888"],           # 8888:舊有、新無 → 該退訂
        "monitor_list": [],
    })

    fake_pool.unsubscribe.assert_awaited_once_with("8888", owner)
    fake_pool.subscribe.assert_awaited_once_with("2454", owner_id="monitor_list")
