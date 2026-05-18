"""GET /api/ma/{symbol} — 即時打富邦 tech.sma,回最新一根日 K 的 SMA5 / SMA20。

不走 indicator_cache(已廢棄)。並行打 2 個 tech.sma call,失敗欄位回 null。
富邦 daily SMA 用上一交易日的 close 算,當日盤中不變,前端不必加 cache 也夠。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from services.fubon_client import FubonStatus, get_fubon
from services.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)
router = APIRouter()


def _extract_latest(result: Any) -> tuple[float | None, str | None]:
    """從富邦 tech.sma 回應拿最後一筆 (sma_value, date)。"""
    if not isinstance(result, dict):
        return None, None
    data = result.get("data")
    if not isinstance(data, list) or not data:
        return None, None
    last = data[-1]
    if not isinstance(last, dict):
        return None, None
    v = last.get("sma")
    d = last.get("date")
    try:
        return (float(v) if v is not None else None, d if isinstance(d, str) else None)
    except (TypeError, ValueError):
        return None, d if isinstance(d, str) else None


async def _fetch_sma(sdk: Any, symbol: str, period: int) -> tuple[float | None, str | None]:
    """單一 period 的 SMA fetch — 失敗回 (None, None)。"""
    try:
        await asyncio.to_thread(get_rate_limiter().acquire)
        res = await asyncio.to_thread(
            sdk.marketdata.rest_client.stock.technical.sma,
            symbol=symbol,
            period=period,
        )
        return _extract_latest(res)
    except Exception as e:
        logger.warning("ma fetch failed: %s period=%d — %s: %s",
                       symbol, period, type(e).__name__, e)
        return None, None


@router.get("/api/ma/{symbol}")
async def get_ma(symbol: str) -> dict:
    fubon = get_fubon()
    if fubon.status != FubonStatus.OK or fubon.sdk is None:
        raise HTTPException(
            503,
            detail={"error": "fubon_unavailable", "last_error": fubon.last_error},
        )

    (sma_5, date_5), (sma_20, date_20) = await asyncio.gather(
        _fetch_sma(fubon.sdk, symbol, 5),
        _fetch_sma(fubon.sdk, symbol, 20),
    )

    # as_of_date 取兩邊都有的那個(通常相同);都沒有就 None
    as_of_date = date_5 or date_20

    return {
        "symbol": symbol,
        "sma_5": sma_5,
        "sma_20": sma_20,
        "as_of_date": as_of_date,
    }
