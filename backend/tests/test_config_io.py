"""GET /api/config/export、POST /api/config/import 路由整合測試。"""
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from main import app
from services.local_store import get_local_store

client = TestClient(app)


def test_export_then_import_roundtrip(local_store_tmp, monkeypatch):
    monkeypatch.setattr("services.lifecycle_sync.get_ws_pool", lambda: AsyncMock())
    monkeypatch.setattr("services.lifecycle_sync.get_signal_engine", lambda: AsyncMock())
    get_local_store().config.create_group("帶走的")
    snap = client.get("/api/config/export").json()
    assert snap["schema_version"] == 1 and snap["exported_at"]

    get_local_store().config.create_group("臨時")
    r = client.post("/api/config/import", json=snap)
    assert r.status_code == 200
    names = [g["name"] for g in get_local_store().config.list_groups()]
    assert "帶走的" in names and "臨時" not in names


def test_import_rejects_bad_schema(local_store_tmp, monkeypatch):
    monkeypatch.setattr("services.lifecycle_sync.get_ws_pool", lambda: AsyncMock())
    monkeypatch.setattr("services.lifecycle_sync.get_signal_engine", lambda: AsyncMock())
    r = client.post("/api/config/import", json={"schema_version": 999})
    assert r.status_code == 400
