"""驗 /api/monitor_list CRUD 行為(local-store 版)。

GET  → {"items": [...enriched...], "count": N}
POST → 201 {"symbol": ..., "status": "added"}
DELETE → 204
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from main import app
from services.local_store import get_local_store

client = TestClient(app)

# ── 每支測試都取到 autouse local_store_tmp fixture ──


def test_list_returns_empty(local_store_tmp):
    r = client.get("/api/monitor_list")
    assert r.status_code == 200
    assert r.json() == {"items": [], "count": 0}


def test_add_unknown_symbol_returns_404_when_symbols_loaded(local_store_tmp, monkeypatch):
    """symbols 已載入但查無此 symbol → 404。"""
    store = get_local_store()
    # 模擬已載入狀態但沒有該 symbol
    monkeypatch.setattr(store.market, "symbols_loaded", lambda: True)
    monkeypatch.setattr(store.market, "has_symbol", lambda s: False)

    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.monitor_list.get_ws_pool", lambda: fake_pool)

    r = client.post("/api/monitor_list", json={"symbol": "9999"})
    assert r.status_code == 404
    fake_pool.subscribe.assert_not_awaited()


def test_add_success_subscribes_and_persists(local_store_tmp, monkeypatch):
    """symbols 未載入(跳過檢查) → subscribe → 寫入 local store → 201。"""
    store = get_local_store()
    monkeypatch.setattr(store.market, "symbols_loaded", lambda: False)

    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.monitor_list.get_ws_pool", lambda: fake_pool)

    with patch("services.signal_engine.get_signal_engine") as mock_engine_getter:
        mock_engine = AsyncMock()
        mock_engine_getter.return_value = mock_engine
        r = client.post("/api/monitor_list", json={"symbol": "2330"})

    assert r.status_code == 201
    assert r.json() == {"symbol": "2330", "status": "added"}
    fake_pool.subscribe.assert_awaited_once_with("2330", owner_id="monitor_list")
    assert [m["symbol"] for m in get_local_store().config.list_monitor()] == ["2330"]


def test_add_ws_capacity_full_returns_503_no_store_write(local_store_tmp, monkeypatch):
    """ws subscribe 噴 RuntimeError → 503,store 不寫入。"""
    store = get_local_store()
    monkeypatch.setattr(store.market, "symbols_loaded", lambda: False)

    fake_pool = AsyncMock()
    fake_pool.subscribe.side_effect = RuntimeError("WS pool capacity full")
    monkeypatch.setattr("routes.monitor_list.get_ws_pool", lambda: fake_pool)

    r = client.post("/api/monitor_list", json={"symbol": "2330"})
    assert r.status_code == 503
    assert get_local_store().config.list_monitor() == []


def test_delete_unsubscribes_and_removes(local_store_tmp, monkeypatch):
    """DELETE → 204,store 移除,unsubscribe 用 owner_id keyword。"""
    get_local_store().config.add_monitor("2330")

    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.monitor_list.get_ws_pool", lambda: fake_pool)

    with patch("services.signal_engine.get_signal_engine") as mock_engine_getter:
        mock_engine = AsyncMock()
        mock_engine_getter.return_value = mock_engine
        r = client.delete("/api/monitor_list/2330")

    assert r.status_code == 204
    fake_pool.unsubscribe.assert_awaited_once_with("2330", owner_id="monitor_list")
    assert get_local_store().config.list_monitor() == []


def test_list_enriches_with_name_market_isetf(local_store_tmp, monkeypatch):
    """GET 回應包含 name/market/is_etf(查不到全 None)。"""
    store = get_local_store()
    store.config.add_monitor("2330")

    # 模擬 market.get_symbol 回傳 metadata
    monkeypatch.setattr(
        store.market, "get_symbol",
        lambda s: {"name": "台積電", "market": "TSE", "is_etf": False} if s == "2330" else None,
    )

    r = client.get("/api/monitor_list")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["symbol"] == "2330"
    assert item["name"] == "台積電"
    assert item["market"] == "TSE"
    assert item["is_etf"] is False


def test_list_returns_newest_first(local_store_tmp, monkeypatch):
    """GET 回傳順序為最新加入在前(parity with old Supabase .order desc)。"""
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.monitor_list.get_ws_pool", lambda: fake_pool)
    # CDP/signal_engine 靜音,不影響斷言
    monkeypatch.setattr("routes.monitor_list.get_cdp_service", lambda: AsyncMock())
    with patch("services.signal_engine.get_signal_engine") as mock_engine_getter:
        mock_engine_getter.return_value = AsyncMock()
        client.post("/api/monitor_list", json={"symbol": "2330"})
        client.post("/api/monitor_list", json={"symbol": "2454"})

    syms = [it["symbol"] for it in client.get("/api/monitor_list").json()["items"]]
    # 2454 是後加的,應排在前面
    assert syms == ["2454", "2330"]


def test_add_triggers_cdp_backfill(local_store_tmp, monkeypatch):
    """POST 時應呼叫 get_cdp_service().backfill_from_fubon(symbol)。"""
    store = get_local_store()
    monkeypatch.setattr(store.market, "symbols_loaded", lambda: False)

    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.monitor_list.get_ws_pool", lambda: fake_pool)

    fake_cdp = AsyncMock()
    monkeypatch.setattr("routes.monitor_list.get_cdp_service", lambda: fake_cdp)

    with patch("services.signal_engine.get_signal_engine") as mock_engine_getter:
        mock_engine_getter.return_value = AsyncMock()
        r = client.post("/api/monitor_list", json={"symbol": "2330"})

    assert r.status_code == 201
    # backfill_from_fubon 已被呼叫(透過 create_task,coroutine 物件已建立)
    fake_cdp.backfill_from_fubon.assert_called_once_with("2330")
