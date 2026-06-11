"""驗 /api/preview 訂閱失敗後的狀態回復。

subscribe 失敗(pool 容量滿)時舊 owner 已退訂,_current_preview 不可殘留
舊 symbol — 否則重 POST 舊 symbol 會命中 noop 分支、實際已退訂卻收不到 tick。
"""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import routes.preview as preview_module
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_preview_state():
    preview_module._current_preview = None
    yield
    preview_module._current_preview = None


def test_set_and_noop(monkeypatch):
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.preview.get_ws_pool", lambda: fake_pool)
    r = client.post("/api/preview", json={"symbol": "2330"})
    assert r.status_code == 200 and r.json()["current"] == "2330"
    fake_pool.subscribe.reset_mock()
    r = client.post("/api/preview", json={"symbol": "2330"})  # 同檔 noop
    assert r.status_code == 200
    fake_pool.subscribe.assert_not_awaited()


def test_subscribe_failure_clears_current_so_retry_resubscribes(monkeypatch):
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.preview.get_ws_pool", lambda: fake_pool)
    assert client.post("/api/preview", json={"symbol": "2330"}).status_code == 200

    # 換檔失敗:2330 的 preview owner 已退訂,狀態必須是「無 preview」
    fake_pool.subscribe.side_effect = RuntimeError("capacity full")
    r = client.post("/api/preview", json={"symbol": "2454"})
    assert r.status_code == 503
    assert preview_module._current_preview is None

    # 重 POST 回 2330 不可命中 noop — 要真的重 subscribe
    fake_pool.subscribe.side_effect = None
    fake_pool.subscribe.reset_mock()
    r = client.post("/api/preview", json={"symbol": "2330"})
    assert r.status_code == 200 and r.json()["current"] == "2330"
    fake_pool.subscribe.assert_awaited_with("2330", owner_id="preview")
