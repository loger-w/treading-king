"""GET /api/candles/{symbol}/intraday — proxy 富邦 intraday.candles。

回 266 筆 1m K + average (= VWAP) + prev_close（昨日收盤，給前端算漲跌% 用），
給前端 IntradayChart 用。

prev_close 來源：富邦 intraday quote 的 previousClose 欄位（權威值，跟券商
app 顯示一致）。不從 daily_ohlc 拿是因為 daily cache job 偶爾會漏單檔
（觀察到 6531 缺 2026-05-12），用 quote API 比較可靠。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from services.fubon_client import FubonStatus, get_fubon
from services.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/candles/{symbol}/intraday")
async def intraday_candles(symbol: str) -> dict:
    fubon = get_fubon()
    if fubon.status != FubonStatus.OK or fubon.sdk is None:
        raise HTTPException(503, detail={"error": "fubon_unavailable", "last_error": fubon.last_error})

    async def fetch_candles() -> dict:
        # candles 與 quote 同屬富邦日內行情配額(300/min)— 先過共用 limiter
        # 記帳,否則輪詢流量繞道、尖峰時會害守規矩的 quote/sma 吃 429。
        # 用 async 版等 token — 排隊時不占 thread pool worker
        await get_rate_limiter().acquire_async()
        return await asyncio.to_thread(
            fubon.sdk.marketdata.rest_client.stock.intraday.candles,
            symbol=symbol,
        )

    async def fetch_prev_close() -> float | None:
        try:
            q = await fubon.intraday_quote(symbol)
            pc = q.get("previousClose")
            return float(pc) if pc is not None else None
        except Exception as e:
            logger.warning("intraday_quote(%s) for prev_close failed: %s", symbol, e)
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
