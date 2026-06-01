from fastapi.testclient import TestClient
from main import app
from services.local_store import get_local_store

client = TestClient(app)


def test_search_reads_from_market_cache(local_store_tmp):
    get_local_store().market.replace_symbols([
        {"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True},
    ])
    r = client.get("/api/symbols", params={"search": "23", "limit": 10})
    assert r.status_code == 200
    assert r.json() == {"results": [
        {"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False}]}
