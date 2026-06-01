"""驗 /api/active_signals route 改 LocalStore(ConfigStore)後行為不變。

重點:
- GET 回 {"active_signals": [...]}
- POST 回新建的 row dict (status 201), 含 id / created_at
- PUT 回更新後的 row dict (status 200), 找不到 id → 404
- DELETE status 204, 找不到 id → 404
- 每次 write(POST / PUT / DELETE)都 await signal_engine.refresh_active_signals()
"""
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

# 最小合法 payload — filter_json 需含 conditions(至少一個),scope 型別符合 WatchlistScope
PAYLOAD = {
    "name": "突破",
    "filter_json": {
        "conditions": [{"field": "close", "operator": "gt", "value": 100}],
        "logic": "AND",
    },
    "scope": {"type": "watchlist"},
    "cooldown_seconds": 1800,
    "enabled": True,
    "notify_discord": True,
}


def test_create_returns_row_with_id(local_store_tmp):
    r = client.post("/api/active_signals", json=PAYLOAD)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "突破"
    assert "id" in body and body["id"]
    assert "created_at" in body


def test_create_refreshes_engine(local_store_tmp, monkeypatch):
    fake_engine = AsyncMock()
    monkeypatch.setattr("routes.active_signals.get_signal_engine", lambda: fake_engine)
    r = client.post("/api/active_signals", json=PAYLOAD)
    assert r.status_code in (200, 201)
    assert r.json()["name"] == "突破"
    fake_engine.refresh_active_signals.assert_awaited_once()


def test_list_shape(local_store_tmp):
    client.post("/api/active_signals", json=PAYLOAD)
    r = client.get("/api/active_signals")
    assert r.status_code == 200
    body = r.json()
    assert "active_signals" in body
    assert isinstance(body["active_signals"], list)
    assert len(body["active_signals"]) == 1
    assert body["active_signals"][0]["name"] == "突破"


def test_list_then_delete_refreshes(local_store_tmp, monkeypatch):
    fake_engine = AsyncMock()
    monkeypatch.setattr("routes.active_signals.get_signal_engine", lambda: fake_engine)
    sid = client.post("/api/active_signals", json=PAYLOAD).json()["id"]
    listed = client.get("/api/active_signals")
    assert listed.status_code == 200
    r = client.delete(f"/api/active_signals/{sid}")
    assert r.status_code in (200, 204)
    assert fake_engine.refresh_active_signals.await_count >= 2  # create + delete


def test_update_refreshes_engine(local_store_tmp, monkeypatch):
    fake_engine = AsyncMock()
    monkeypatch.setattr("routes.active_signals.get_signal_engine", lambda: fake_engine)
    sid = client.post("/api/active_signals", json=PAYLOAD).json()["id"]
    updated_payload = {**PAYLOAD, "name": "更新後"}
    r = client.put(f"/api/active_signals/{sid}", json=updated_payload)
    assert r.status_code == 200
    assert r.json()["name"] == "更新後"
    assert fake_engine.refresh_active_signals.await_count == 2  # create + put


def test_update_unknown_id_returns_404(local_store_tmp, monkeypatch):
    monkeypatch.setattr("routes.active_signals.get_signal_engine", lambda: AsyncMock())
    r = client.put("/api/active_signals/nonexistent-id", json=PAYLOAD)
    assert r.status_code == 404


def test_delete_unknown_id_returns_404(local_store_tmp, monkeypatch):
    monkeypatch.setattr("routes.active_signals.get_signal_engine", lambda: AsyncMock())
    r = client.delete("/api/active_signals/nonexistent-id")
    assert r.status_code == 404


def test_delete_removes_from_list(local_store_tmp, monkeypatch):
    monkeypatch.setattr("routes.active_signals.get_signal_engine", lambda: AsyncMock())
    sid = client.post("/api/active_signals", json=PAYLOAD).json()["id"]
    client.delete(f"/api/active_signals/{sid}")
    listed = client.get("/api/active_signals").json()["active_signals"]
    assert not any(s["id"] == sid for s in listed)
