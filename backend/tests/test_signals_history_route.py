"""驗 /api/signals/history + /api/signals/today_counts 讀本機 SignalsLog。

response 形狀:
- history    → {"signals": [...], "count": N}
- today_counts → {"counts": [{symbol, active_signal_id}...]}(前端只讀 counts)
"""
from fastapi.testclient import TestClient

from main import app
from services.local_store import get_local_store

client = TestClient(app)


def test_history_reads_jsonl(local_store_tmp):
    get_local_store().signals.append({
        "active_signal_id": "a", "symbol": "2330",
        "trigger_price": 1.0, "trigger_volume": 1, "context_json": {},
    })
    r = client.get("/api/signals/history", params={"symbol": "2330", "limit": 50})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    rows = body["signals"]
    assert any(x["symbol"] == "2330" for x in rows)


def test_history_filters_by_symbol(local_store_tmp):
    log = get_local_store().signals
    log.append({"active_signal_id": "a", "symbol": "2330"})
    log.append({"active_signal_id": "b", "symbol": "2454"})
    r = client.get("/api/signals/history", params={"symbol": "2330"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert [s["symbol"] for s in body["signals"]] == ["2330"]


def test_history_filters_by_active_signal_id(local_store_tmp):
    log = get_local_store().signals
    log.append({"active_signal_id": "a", "symbol": "2330"})
    log.append({"active_signal_id": "b", "symbol": "2454"})
    r = client.get("/api/signals/history", params={"active_signal_id": "b"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["signals"][0]["active_signal_id"] == "b"


def test_history_empty_when_no_rows(local_store_tmp):
    r = client.get("/api/signals/history")
    assert r.status_code == 200
    assert r.json() == {"signals": [], "count": 0}


def test_today_counts_shape(local_store_tmp):
    get_local_store().signals.append({
        "active_signal_id": "a", "symbol": "2330",
        "trigger_price": 1.0, "trigger_volume": 1, "context_json": {},
    })
    r = client.get("/api/signals/today_counts")
    assert r.status_code == 200
    body = r.json()
    # 只回 counts — as_of / today_start 無消費者,已移除
    assert set(body) == {"counts"}
    counts = body["counts"]
    assert isinstance(counts, list)
    # 前端 group by (symbol, active_signal_id):row 只需含這兩個 key
    assert any(c["symbol"] == "2330" and c["active_signal_id"] == "a" for c in counts)
