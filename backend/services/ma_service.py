"""SMA fetch service — 共用給 routes/ma.py 跟 signal_engine。

對應富邦 tech.sma,當日不變(用上一交易日 close 算)。失敗欄位回 None。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.fubon_client import FubonStatus, get_fubon
from services.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)


def _extract_latest(result: Any) -> float | None:
    if not isinstance(result, dict):
        return None
    data = result.get("data")
    if not isinstance(data, list) or not data:
        return None
    last = data[-1]
    if not isinstance(last, dict):
        return None
    v = last.get("sma")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def fetch_sma(symbol: str, period: int) -> float | None:
    """單一 period 的 SMA fetch — 失敗回 None。"""
    fubon = get_fubon()
    if fubon.status != FubonStatus.OK or fubon.sdk is None:
        return None
    try:
        await asyncio.to_thread(get_rate_limiter().acquire)
        res = await asyncio.to_thread(
            fubon.sdk.marketdata.rest_client.stock.technical.sma,
            symbol=symbol, period=period,
        )
        return _extract_latest(res)
    except Exception as e:
        logger.warning("ma fetch failed: %s period=%d — %s: %s",
                       symbol, period, type(e).__name__, e)
        return None


async def fetch_sma_5_20(symbol: str) -> tuple[float | None, float | None]:
    """並行打 SMA5 + SMA20,回 (sma_5, sma_20)。"""
    sma_5, sma_20 = await asyncio.gather(
        fetch_sma(symbol, 5),
        fetch_sma(symbol, 20),
    )
    return sma_5, sma_20
