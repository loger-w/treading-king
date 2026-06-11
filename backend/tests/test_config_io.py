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


def test_import_malformed_payload_returns_400_and_keeps_config(local_store_tmp, monkeypatch):
    """壞 payload(清單形狀錯)→ 400,且現有設定完全不動 — 不能半套用寫穿磁碟。"""
    monkeypatch.setattr("services.lifecycle_sync.get_ws_pool", lambda: AsyncMock())
    monkeypatch.setattr("services.lifecycle_sync.get_signal_engine", lambda: AsyncMock())
    get_local_store().config.create_group("原有")
    r = client.post("/api/config/import", json={
        "schema_version": 1,
        "bookmark_groups": "oops",  # 非 list
    })
    assert r.status_code == 400
    assert "原有" in [g["name"] for g in get_local_store().config.list_groups()]
    # 之後的正常寫入不會把壞資料落盤
    get_local_store().config.add_monitor("2330")
    assert [m["symbol"] for m in get_local_store().config.list_monitor()] == ["2330"]


def test_import_resync_failure_still_reports_imported(local_store_tmp, monkeypatch):
    """resync 失敗時 config 已落盤 — 回 200 標明 resync failed。

    回 500 會讓 client 誤判匯入失敗而重匯;實際設定已持久化,只是訂閱
    需重啟由 startup resync 補齊。
    """
    monkeypatch.setattr("services.lifecycle_sync.get_ws_pool", lambda: AsyncMock())
    monkeypatch.setattr("services.lifecycle_sync.get_signal_engine", lambda: AsyncMock())
    get_local_store().config.create_group("帶走的")
    snap = client.get("/api/config/export").json()

    async def boom(prev_owners=None):
        raise RuntimeError("resync exploded")

    monkeypatch.setattr("routes.config_io.resync_from_config", boom)
    r = client.post("/api/config/import", json=snap)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "imported" and body["resync"] == "failed"
    # 匯入本身已生效
    assert "帶走的" in [g["name"] for g in get_local_store().config.list_groups()]
