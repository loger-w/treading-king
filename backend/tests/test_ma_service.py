"""驗 ma_service.fetch_sma_5_20 — 拉 SMA5/20、失敗欄位回 None。"""
from unittest.mock import MagicMock

import pytest

from services import ma_service


@pytest.mark.asyncio
async def test_fetch_sma_5_20_returns_both_floats(monkeypatch):
    fake_sdk = MagicMock()
    fake_sdk.marketdata.rest_client.stock.technical.sma = MagicMock(
        side_effect=[
            {"data": [{"sma": 100.5, "date": "2026-05-19"}]},
            {"data": [{"sma": 105.2, "date": "2026-05-19"}]},
        ]
    )
    fubon = MagicMock()
    fubon.status = ma_service.FubonStatus.OK
    fubon.sdk = fake_sdk
    monkeypatch.setattr(ma_service, "get_fubon", lambda: fubon)

    sma_5, sma_20 = await ma_service.fetch_sma_5_20("2330")
    assert sma_5 == 100.5
    assert sma_20 == 105.2


@pytest.mark.asyncio
async def test_fetch_sma_5_20_handles_partial_failure(monkeypatch):
    """SMA5 OK、SMA20 失敗時 SMA20 回 None,不 raise。"""
    fake_sdk = MagicMock()
    fake_sdk.marketdata.rest_client.stock.technical.sma = MagicMock(
        side_effect=[
            {"data": [{"sma": 100.5, "date": "2026-05-19"}]},
            Exception("network error"),
        ]
    )
    fubon = MagicMock()
    fubon.status = ma_service.FubonStatus.OK
    fubon.sdk = fake_sdk
    monkeypatch.setattr(ma_service, "get_fubon", lambda: fubon)

    sma_5, sma_20 = await ma_service.fetch_sma_5_20("2330")
    assert sma_5 == 100.5
    assert sma_20 is None
