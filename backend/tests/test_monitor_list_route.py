"""驗 /api/monitor_list CRUD 行為。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app(monkeypatch):
    from routes import monitor_list as ml
    from services.supabase_client import SupabaseStatus

    fake_table = MagicMock()
    fake_table.select.return_value = fake_table
    fake_table.eq.return_value = fake_table
    fake_table.order.return_value = fake_table
    fake_table.limit.return_value = fake_table
    fake_table.insert.return_value = fake_table
    fake_table.delete.return_value = fake_table
    fake_table.execute.return_value = MagicMock(data=[])

    fake_sb = MagicMock()
    fake_sb.status = SupabaseStatus.OK
    fake_sb.client.table.return_value = fake_table

    monkeypatch.setattr(ml, "get_supabase", lambda: fake_sb)
    monkeypatch.setattr(ml, "get_user_label", lambda: "test")

    fake_pool = MagicMock()
    fake_pool.subscribe = AsyncMock()
    fake_pool.unsubscribe = AsyncMock()
    monkeypatch.setattr(ml, "get_ws_pool", lambda: fake_pool)

    fake_cdp = MagicMock()
    fake_cdp.backfill_from_fubon = AsyncMock()
    monkeypatch.setattr(ml, "get_cdp_service", lambda: fake_cdp)

    fake_engine = MagicMock()
    fake_engine.refresh_active_signals = AsyncMock()
    with patch("services.signal_engine.get_signal_engine", lambda: fake_engine):
        a = FastAPI()
        a.include_router(ml.router)
        yield a, fake_table, fake_pool


def test_list_returns_empty(app):
    a, table, _ = app
    table.execute.return_value = MagicMock(data=[])
    client = TestClient(a)
    r = client.get("/api/monitor_list")
    assert r.status_code == 200
    assert r.json() == {"items": [], "count": 0}


def test_add_unknown_symbol_returns_404(app):
    a, table, _ = app
    # symbols lookup 回空 → 404
    table.execute.return_value = MagicMock(data=[])
    client = TestClient(a)
    r = client.post("/api/monitor_list", json={"symbol": "9999"})
    assert r.status_code == 404


def test_add_success_subscribes_and_inserts(app):
    a, table, pool = app
    # 第一次 execute = symbols 查;第二次 execute = insert
    table.execute.side_effect = [
        MagicMock(data=[{"symbol": "2330"}]),
        MagicMock(data=[{"user_label": "test", "symbol": "2330"}]),
    ]
    client = TestClient(a)
    r = client.post("/api/monitor_list", json={"symbol": "2330"})
    assert r.status_code == 201
    pool.subscribe.assert_awaited_once_with("2330", owner_id="monitor_list")


def test_add_ws_capacity_full_returns_503_no_db_write(app):
    a, table, pool = app
    table.execute.return_value = MagicMock(data=[{"symbol": "2330"}])
    pool.subscribe.side_effect = RuntimeError("WS pool capacity full")
    client = TestClient(a)
    r = client.post("/api/monitor_list", json={"symbol": "2330"})
    assert r.status_code == 503
    # insert 不該被呼叫
    assert not table.insert.called


def test_delete_unsubscribes(app):
    a, _, pool = app
    client = TestClient(a)
    r = client.delete("/api/monitor_list/2330")
    assert r.status_code == 204
    pool.unsubscribe.assert_awaited_once_with("2330", owner_id="monitor_list")
