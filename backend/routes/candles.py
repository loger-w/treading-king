"""GET /api/candles/{symbol}/intraday — proxy 富邦 intraday.candles。

回 266 筆 1m K + average (= VWAP) + prev_close（昨日收盤，給前端算漲跌% 用），
給前端 IntradayChart 用。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from services.fubon_client import FubonStatus, get_fubon
from services.supabase_client import get_supabase

router = APIRouter()


@router.get("/api/candles/{symbol}/intraday")
async def intraday_candles(symbol: str) -> dict:
    fubon = get_fubon()
    if fubon.status != FubonStatus.OK or fubon.sdk is None:
        raise HTTPException(503, detail={"error": "fubon_unavailable", "last_error": fubon.last_error})

    # 並行抓 candles 跟昨日收盤
    sb = get_supabase()

    async def fetch_candles() -> dict:
        return await asyncio.to_thread(
            fubon.sdk.marketdata.rest_client.stock.intraday.candles,
            symbol=symbol,
        )

    async def fetch_prev_close() -> float | None:
        if sb.client is None:
            return None
        try:
            res = await asyncio.to_thread(
                lambda: sb.client.table("daily_ohlc")
                .select("close")
                .eq("symbol", symbol)
                .order("date", desc=True)
                .limit(1)
                .execute()
            )
            rows = res.data or []
            return float(rows[0]["close"]) if rows else None
        except Exception:
            return None

    try:
        candles_r, prev_close = await asyncio.gather(
            fetch_candles(), fetch_prev_close()
        )
    except Exception as e:
        raise HTTPException(502, detail={"error": "fubon_call_failed", "detail": str(e)})

    result = candles_r or {}
    result["prev_close"] = prev_close
    return result
