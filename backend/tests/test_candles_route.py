"""驗 /api/candles/{symbol}/intraday 過共用 rate limiter。

candles 與 quote 同屬富邦日內行情配額(300/min)— 不記帳的話輪詢流量繞道,
尖峰時會讓守規矩的 quote/sma 呼叫吃 429。
"""
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from main import app
from services.fubon_client import FubonStatus

client = TestClient(app)


def test_intraday_candles_acquires_rate_limit(monkeypatch):
    fake_limiter = MagicMock()
    fake_limiter.acquire_async = AsyncMock(return_value=True)
    monkeypatch.setattr("routes.candles.get_rate_limiter", lambda: fake_limiter)

    fubon = MagicMock()
    fubon.status = FubonStatus.OK
    fubon.sdk.marketdata.rest_client.stock.intraday.candles = MagicMock(
        return_value={"data": []})
    fubon.intraday_quote = AsyncMock(return_value={"previousClose": 100.0})
    monkeypatch.setattr("routes.candles.get_fubon", lambda: fubon)

    r = client.get("/api/candles/2330/intraday")
    assert r.status_code == 200
    fake_limiter.acquire_async.assert_awaited_once()
    assert r.json()["prev_close"] == 100.0
