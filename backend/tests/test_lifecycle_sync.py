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
