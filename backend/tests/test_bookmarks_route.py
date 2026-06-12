"""驗 /api/bookmarks route 改 ConfigStore 後行為不變。

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


def test_add_item_subscribe_failure_rolls_back(local_store_tmp, monkeypatch):
    """subscribe 失敗(WS pool 容量滿)要回滾 store 並回報 failed。

    否則書籤看得到該股票、卻整個 session 收不到即時行情(靜默不一致)—
    與 monitor_list 的「訂閱失敗就不寫 store」原則對齊。
    """
    fake_pool = AsyncMock()
    fake_pool.subscribe.side_effect = RuntimeError("capacity full")
    monkeypatch.setattr("routes.bookmarks.get_ws_pool", lambda: fake_pool)
    monkeypatch.setattr("routes.bookmarks.get_cdp_service", lambda: AsyncMock())
    get_local_store().market.replace_symbols(
        [{"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True}])
    gid = next(g["id"] for g in client.get("/api/bookmarks").json()["groups"] if not g["is_system"])
    r = client.post(f"/api/bookmarks/{gid}/items", json={"symbols": ["2330"]})
    assert r.status_code == 201
    body = r.json()
    assert body["added"] == [] and body["failed"] == ["2330"] and body["count"] == 0
    # store 已回滾 — 不會留下「有股票、沒行情」的殘骸
    assert client.get(f"/api/bookmarks/{gid}/items").json()["count"] == 0


def test_move_keeps_source_when_all_targets_fail(local_store_tmp, monkeypatch):
    """move 時目的群組全部訂閱失敗 → symbol 留在來源,不可兩頭皆空。"""
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.bookmarks.get_ws_pool", lambda: fake_pool)
    monkeypatch.setattr("routes.bookmarks.get_cdp_service", lambda: AsyncMock())
    get_local_store().market.replace_symbols(
        [{"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True}])
    g1 = client.post("/api/bookmarks", json={"name": "來源"}).json()["id"]
    g2 = client.post("/api/bookmarks", json={"name": "目的"}).json()["id"]
    client.post(f"/api/bookmarks/{g1}/items", json={"symbols": ["2330"]})
    fake_pool.subscribe.side_effect = RuntimeError("capacity full")
    r = client.patch("/api/bookmarks/items/move",
                     json={"symbols": ["2330"], "from_group_id": g1,
                           "to_group_ids": [g2], "op": "move"})
    assert r.status_code == 200
    assert r.json()["failed"] == [{"symbol": "2330", "group_id": g2}]
    assert client.get(f"/api/bookmarks/{g1}/items").json()["count"] == 1  # 留在來源
    assert client.get(f"/api/bookmarks/{g2}/items").json()["count"] == 0  # 目的已回滾


def _setup_group_with_items(monkeypatch, symbols):
    """共用前置:mock WS/CDP、註冊 symbols、批次加入預設書籤,回傳 group id。"""
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.bookmarks.get_ws_pool", lambda: fake_pool)
    monkeypatch.setattr("routes.bookmarks.get_cdp_service", lambda: AsyncMock())
    get_local_store().market.replace_symbols(
        [{"symbol": s, "name": s, "market": "TWSE", "is_etf": False, "is_active": True}
         for s in symbols])
    gid = next(g["id"] for g in client.get("/api/bookmarks").json()["groups"]
               if not g["is_system"])
    client.post(f"/api/bookmarks/{gid}/items", json={"symbols": symbols})
    return gid


def test_reorder_items_changes_get_order(local_store_tmp, monkeypatch):
    # 為何重要:這就是「自訂排序」的核心 contract — reorder 後 GET 要照新順序回
    gid = _setup_group_with_items(monkeypatch, ["2330", "2317", "2454"])
    r = client.patch(f"/api/bookmarks/{gid}/items/reorder",
                     json={"symbols": ["2317", "2454", "2330"]})
    assert r.status_code == 200
    got = [it["symbol"] for it in client.get(f"/api/bookmarks/{gid}/items").json()["items"]]
    assert got == ["2317", "2454", "2330"]


def test_reorder_items_mismatch_400(local_store_tmp, monkeypatch):
    gid = _setup_group_with_items(monkeypatch, ["2330"])
    r = client.patch(f"/api/bookmarks/{gid}/items/reorder",
                     json={"symbols": ["2330", "9999"]})
    assert r.status_code == 400


def test_new_item_appears_on_top(local_store_tmp, monkeypatch):
    # 為何重要:user 已確認「新加入排最上面」— 批次加入後再加一檔,新檔要在第一位
    gid = _setup_group_with_items(monkeypatch, ["2330", "2317"])
    get_local_store().market.replace_symbols(
        [{"symbol": s, "name": s, "market": "TWSE", "is_etf": False, "is_active": True}
         for s in ["2330", "2317", "2454"]])
    client.post(f"/api/bookmarks/{gid}/items", json={"symbols": ["2454"]})
    got = [it["symbol"] for it in client.get(f"/api/bookmarks/{gid}/items").json()["items"]]
    assert got[0] == "2454"


def test_items_without_position_fallback_added_at_desc(local_store_tmp, monkeypatch):
    # 為何重要:既有 config.json 沒有 position(spec 明定不遷移)— 舊資料要維持原本
    # 「加入時間新→舊」的順序、且排在有 position 的項目後面
    gid = _setup_group_with_items(monkeypatch, ["2330"])
    cfg = get_local_store().config
    cfg._data["watchlist_items"].extend([
        {"id": "old1", "group_id": gid, "symbol": "1101",
         "added_at": "2026-01-01T00:00:00+00:00", "note": None},
        {"id": "old2", "group_id": gid, "symbol": "1102",
         "added_at": "2026-02-01T00:00:00+00:00", "note": None},
    ])
    got = [it["symbol"] for it in client.get(f"/api/bookmarks/{gid}/items").json()["items"]]
    assert got == ["2330", "1102", "1101"]  # 有 position 在前;舊資料 added_at 新→舊


def test_reorder_groups_changes_list_order(local_store_tmp):
    g1 = client.post("/api/bookmarks", json={"name": "甲"}).json()["id"]
    g2 = client.post("/api/bookmarks", json={"name": "乙"}).json()["id"]
    g0 = next(g["id"] for g in client.get("/api/bookmarks").json()["groups"]
              if g["name"] == "自選")
    r = client.patch("/api/bookmarks/reorder", json={"ids": [g2, g1, g0]})
    assert r.status_code == 200
    user_ids = [g["id"] for g in client.get("/api/bookmarks").json()["groups"]
                if not g["is_system"]]
    assert user_ids == [g2, g1, g0]
    # 系統書籤(大漲股)永遠在最後、不受影響
    assert client.get("/api/bookmarks").json()["groups"][-1]["is_system"] is True


def test_reorder_groups_mismatch_400(local_store_tmp):
    g0 = next(g["id"] for g in client.get("/api/bookmarks").json()["groups"]
              if not g["is_system"])
    r = client.patch("/api/bookmarks/reorder", json={"ids": [g0, "no-such-id"]})
    assert r.status_code == 400


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
