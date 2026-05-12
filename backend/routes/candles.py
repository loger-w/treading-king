"""GET /api/candles/{symbol}/intraday — proxy 富邦 intraday.candles。

回 266 筆 1m K + average (= VWAP)，給前端 IntradayChart 用。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from services.fubon_client import FubonStatus, get_fubon

router = APIRouter()


@router.get("/api/candles/{symbol}/intraday")
async def intraday_candles(symbol: str) -> dict:
    fubon = get_fubon()
    if fubon.status != FubonStatus.OK or fubon.sdk is None:
        raise HTTPException(503, detail={"error": "fubon_unavailable", "last_error": fubon.last_error})

    try:
        r = await asyncio.to_thread(
            fubon.sdk.marketdata.rest_client.stock.intraday.candles,
            symbol=symbol,
        )
    except Exception as e:
        raise HTTPException(502, detail={"error": "fubon_call_failed", "detail": str(e)})

    return r or {}
