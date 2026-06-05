"""驗 /api/bookmarks + /api/watchlist route 改 ConfigStore 後行為不變。

重點:回應 shape 不變、item 的 name/market/is_etf 由 MarketCache.get_symbol 補回、
新增 / 刪除股票會帶動 WS subscribe / unsubscribe 副作用。

注意:真實 add-item contract 是 POST {"symbols": [...]}(批次),
回應 {added, skipped, count};items 包在 {items, count} 內 — 測試對齊真實 contract。
"""
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from main import app
from services.local_store import get_local_store

client = TestClient(app)


def test_list_bookmarks_shape(local_store_tmp):
    r = client.get("/api/bookmarks")
    assert r.status_code == 200
    body = r.json()
    assert "groups" in body and "count" in body
    assert any(g["is_system"] and g["source_type"] == "top_gainers" for g in body["groups"])
    g = body["groups"][0]
    assert set(g) == {"id", "name", "sort_order", "is_system", "source_type", "count"}


def test_add_item_subscribes_and_enriches_name(local_store_tmp, monkeypatch):
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.bookmarks.get_ws_pool", lambda: fake_pool)
    monkeypatch.setattr("routes.bookmarks.get_cdp_service",
                        lambda: AsyncMock())
    get_local_store().market.replace_symbols(
        [{"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True}])
    gid = next(g["id"] for g in client.get("/api/bookmarks").json()["groups"] if not g["is_system"])
    # 真實 contract:批次 symbols
    r = client.post(f"/api/bookmarks/{gid}/items", json={"symbols": ["2330"]})
    assert r.status_code in (200, 201)
    fake_pool.subscribe.assert_awaited()
    items = client.get(f"/api/bookmarks/{gid}/items").json()
    rows = items if isinstance(items, list) else items.get("items", items)
    assert any(it.get("name") == "台積電" for it in rows)  # name 由 get_symbol 補回


def test_add_unknown_symbol_returns_404(local_store_tmp, monkeypatch):
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.bookmarks.get_ws_pool", lambda: fake_pool)
    get_local_store().market.replace_symbols(
        [{"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True}])
    gid = next(g["id"] for g in client.get("/api/bookmarks").json()["groups"] if not g["is_system"])
    r = client.post(f"/api/bookmarks/{gid}/items", json={"symbols": ["9999"]})
    assert r.status_code == 404
    fake_pool.subscribe.assert_not_awaited()


def test_system_group_items_empty_until_scheduler(local_store_tmp):
    sid = next(g["id"] for g in client.get("/api/bookmarks").json()["groups"] if g["is_system"])
    items = client.get(f"/api/bookmarks/{sid}/items").json()
    assert items == {"items": [], "count": 0}


def test_system_group_rejects_write(local_store_tmp):
    sid = next(g["id"] for g in client.get("/api/bookmarks").json()["groups"] if g["is_system"])
    r = client.post(f"/api/bookmarks/{sid}/items", json={"symbols": ["2330"]})
    assert r.status_code == 403


def test_delete_item_unsubscribes(local_store_tmp, monkeypatch):
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.bookmarks.get_ws_pool", lambda: fake_pool)
    monkeypatch.setattr("routes.bookmarks.get_cdp_service", lambda: AsyncMock())
    get_local_store().market.replace_symbols(
        [{"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True}])
    gid = next(g["id"] for g in client.get("/api/bookmarks").json()["groups"] if not g["is_system"])
    client.post(f"/api/bookmarks/{gid}/items", json={"symbols": ["2330"]})
    r = client.delete(f"/api/bookmarks/{gid}/items/2330")
    assert r.status_code == 204
    fake_pool.unsubscribe.assert_awaited_with("2330", owner_id=f"bookmark:{gid}")


def test_watchlist_add_subscribes_default_group(local_store_tmp, monkeypatch):
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.watchlist.get_ws_pool", lambda: fake_pool)
    monkeypatch.setattr("routes.watchlist.get_cdp_service", lambda: AsyncMock())
    get_local_store().market.replace_symbols(
        [{"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True}])
    r = client.post("/api/watchlist", json={"symbol": "2330"})
    assert r.status_code == 201
    assert r.json() == {"symbol": "2330", "status": "added"}
    fake_pool.subscribe.assert_awaited()
    body = client.get("/api/watchlist").json()
    assert "watchlist" in body and body["count"] == 1
    assert body["watchlist"][0]["name"] == "台積電"


def test_delete_item_does_not_refresh_signal_engine(local_store_tmp, monkeypatch):
    """刪書籤股票不該重載訊號引擎。

    signal scope 用 monitor_list(_field_cache.keys()),watchlist(書籤)增刪
    不改 monitor_list → refresh_active_signals 是多餘的。它會對 monitor_list
    全部 symbol 序列 await cdp.get()(實測 ~2.2s)→ 刪除卡頓 bug。
    """
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.bookmarks.get_ws_pool", lambda: fake_pool)
    monkeypatch.setattr("routes.bookmarks.get_cdp_service", lambda: AsyncMock())
    fake_engine = AsyncMock()
    monkeypatch.setattr("services.signal_engine.get_signal_engine", lambda: fake_engine)
    get_local_store().market.replace_symbols(
        [{"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True}])
    gid = next(g["id"] for g in client.get("/api/bookmarks").json()["groups"] if not g["is_system"])
    client.post(f"/api/bookmarks/{gid}/items", json={"symbols": ["2330"]})
    fake_engine.reset_mock()  # 清掉 add 階段的呼叫,只看 delete
    r = client.delete(f"/api/bookmarks/{gid}/items/2330")
    assert r.status_code == 204
    fake_engine.refresh_active_signals.assert_not_awaited()


def test_add_item_does_not_refresh_signal_engine(local_store_tmp, monkeypatch):
    """加書籤股票同理不該重載訊號引擎(watchlist 不影響 signal scope)。"""
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.bookmarks.get_ws_pool", lambda: fake_pool)
    monkeypatch.setattr("routes.bookmarks.get_cdp_service", lambda: AsyncMock())
    fake_engine = AsyncMock()
    monkeypatch.setattr("services.signal_engine.get_signal_engine", lambda: fake_engine)
    get_local_store().market.replace_symbols(
        [{"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True}])
    gid = next(g["id"] for g in client.get("/api/bookmarks").json()["groups"] if not g["is_system"])
    client.post(f"/api/bookmarks/{gid}/items", json={"symbols": ["2330"]})
    fake_engine.refresh_active_signals.assert_not_awaited()


def test_delete_bookmark_does_not_refresh_signal_engine(local_store_tmp, monkeypatch):
    """刪整個書籤同理不該重載訊號引擎(同 root cause:多餘的 CDP 重算)。"""
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.bookmarks.get_ws_pool", lambda: fake_pool)
    fake_engine = AsyncMock()
    monkeypatch.setattr("services.signal_engine.get_signal_engine", lambda: fake_engine)
    bid = client.post("/api/bookmarks", json={"name": "臨時書籤"}).json()["id"]
    fake_engine.reset_mock()
    r = client.delete(f"/api/bookmarks/{bid}")
    assert r.status_code == 204
    fake_engine.refresh_active_signals.assert_not_awaited()


def test_move_items_does_not_refresh_signal_engine(local_store_tmp, monkeypatch):
    """跨書籤搬移股票同理不該重載訊號引擎。"""
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.bookmarks.get_ws_pool", lambda: fake_pool)
    monkeypatch.setattr("routes.bookmarks.get_cdp_service", lambda: AsyncMock())
    fake_engine = AsyncMock()
    monkeypatch.setattr("services.signal_engine.get_signal_engine", lambda: fake_engine)
    get_local_store().market.replace_symbols(
        [{"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True}])
    g1 = client.post("/api/bookmarks", json={"name": "來源"}).json()["id"]
    g2 = client.post("/api/bookmarks", json={"name": "目的"}).json()["id"]
    client.post(f"/api/bookmarks/{g1}/items", json={"symbols": ["2330"]})
    fake_engine.reset_mock()
    r = client.patch("/api/bookmarks/items/move",
                     json={"symbols": ["2330"], "from_group_id": g1,
                           "to_group_ids": [g2], "op": "move"})
    assert r.status_code == 200
    fake_engine.refresh_active_signals.assert_not_awaited()
