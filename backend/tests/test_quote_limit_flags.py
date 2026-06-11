"""驗 quote endpoint forward 富邦 isLimitUp/Down flags。"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_forward_limit_up_bid_flag(client):
    """鎖漲停時 isLimitUpBid=True 要 forward 到 response。"""
    fake_result = {
        "bids": [{"price": 100.0, "size": 5000}],
        "asks": [{"price": 0.0, "size": 0}],
        "isLimitUpBid": True,
        "isLimitUpAsk": False,
        "isLimitDownBid": False,
        "isLimitDownAsk": False,
    }
    with patch("routes.quote.get_fubon") as mock_get:
        fubon = mock_get.return_value
        fubon.status.value = "ok"
        fubon.intraday_quote = AsyncMock(return_value=fake_result)
        r = client.get("/api/quote/2330")
    assert r.status_code == 200
    body = r.json()
    assert body["is_limit_up_bid"] is True
    assert body["is_limit_down_ask"] is False
    assert body["bids"] == [{"price": 100.0, "size": 5000}]


def test_forward_reference_price(client):
    """平盤參考價要透傳 — 前端漲跌停快捷與閃電階梯夾界都靠它。"""
    fake_result = {"bids": [], "asks": [], "referencePrice": 1000.0}
    with patch("routes.quote.get_fubon") as mock_get:
        fubon = mock_get.return_value
        fubon.status.value = "ok"
        fubon.intraday_quote = AsyncMock(return_value=fake_result)
        r = client.get("/api/quote/2330")
    assert r.status_code == 200
    assert r.json()["reference_price"] == 1000.0


def test_missing_reference_price_is_null(client):
    fake_result = {"bids": [], "asks": []}
    with patch("routes.quote.get_fubon") as mock_get:
        fubon = mock_get.return_value
        fubon.status.value = "ok"
        fubon.intraday_quote = AsyncMock(return_value=fake_result)
        r = client.get("/api/quote/2330")
    assert r.json()["reference_price"] is None


def test_missing_flags_default_to_false(client):
    """富邦 response 沒帶 flag 時要預設 False(向後相容)。"""
    fake_result = {"bids": [], "asks": []}
    with patch("routes.quote.get_fubon") as mock_get:
        fubon = mock_get.return_value
        fubon.status.value = "ok"
        fubon.intraday_quote = AsyncMock(return_value=fake_result)
        r = client.get("/api/quote/2330")
    assert r.status_code == 200
    body = r.json()
    assert body["is_limit_up_bid"] is False
    assert body["is_limit_up_ask"] is False
    assert body["is_limit_down_bid"] is False
    assert body["is_limit_down_ask"] is False
