"""驗 /api/cdp、/api/camarilla 共用的 get_levels_or_503 — lazy backfill 行為。

重點:富邦無歷史資料的 symbol(下市/打錯)backfill 永遠 False,
當日只能真打一次 historical API(negative cache),不可每個請求重打。
"""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import routes.cdp as cdp_route_module
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_failed_backfill(monkeypatch):
    monkeypatch.setattr(cdp_route_module, "_failed_backfill", {})


def _fake_service(get_results, backfill_ok):
    svc = AsyncMock()
    svc.get.side_effect = get_results
    svc.backfill_from_fubon.return_value = backfill_ok
    return svc


def test_lazy_backfill_then_success(monkeypatch):
    levels = {"ah": 1.0}
    svc = _fake_service([None, levels], backfill_ok=True)
    monkeypatch.setattr("routes.cdp.get_cdp_service", lambda: svc)
    r = client.get("/api/cdp/2330")
    assert r.status_code == 200 and r.json() == levels
    svc.backfill_from_fubon.assert_awaited_once()


def test_failed_backfill_negative_cached_for_the_day(monkeypatch):
    svc = _fake_service(lambda *a: None, backfill_ok=False)
    svc.get.side_effect = None
    svc.get.return_value = None
    monkeypatch.setattr("routes.cdp.get_cdp_service", lambda: svc)
    assert client.get("/api/cdp/9999").status_code == 503
    assert client.get("/api/cdp/9999").status_code == 503
    # 當日第二個請求不可再真打 historical backfill
    assert svc.backfill_from_fubon.await_count == 1


def test_camarilla_shares_helper_with_own_error_prefix(monkeypatch):
    svc = _fake_service([None, None], backfill_ok=False)
    monkeypatch.setattr("routes.camarilla.get_camarilla_service", lambda: svc)
    r = client.get("/api/camarilla/9999")
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "camarilla_data_unavailable"
