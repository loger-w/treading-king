"""GET /api/auto_monitor 回 auto_monitor 快取。"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(local_store_tmp):
    from main import app
    return TestClient(app)


def test_get_auto_monitor_empty(client, local_store_tmp):
    r = client.get("/api/auto_monitor")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["count"] == 0


def test_get_auto_monitor_with_data(client, local_store_tmp):
    local_store_tmp.market.replace_symbols([
        {"symbol": "2330", "name": "台積電", "market": "TWSE",
         "is_etf": False, "is_active": True},
    ])
    local_store_tmp.market.replace_auto_monitor([
        {"symbol": "2330", "change_pct": 5.0, "amplitude_pct": 4.2,
         "volume_lots": 8000, "market": "TSE", "rank": 1,
         "captured_at": "2026-06-16T02:00:00Z"},
    ])
    r = client.get("/api/auto_monitor")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["symbol"] == "2330"
    assert item["name"] == "台積電"
    assert item["change_pct"] == 5.0
    assert item["amplitude_pct"] == 4.2
    assert item["market"] == "TWSE"  # enrich_item 用 MarketCache 的正規值,不是 Fubon API 的 "TSE"
