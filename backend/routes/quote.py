"""GET /api/quote/{symbol} — 即時報價（透過富邦 intraday/quote）。
POST /api/quotes/snapshot — 批次拿多檔的 prev_close / last_price。
"""
from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.fubon_client import get_fubon
from services.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)
router = APIRouter()

# 台股代碼：4 碼數字 OR 4 碼數字 + 1-2 字母（例：2330, 0050, 00878, 2330B）
SYMBOL_RE = re.compile(r"^[0-9]{4,6}[A-Z]{0,2}$")


@router.get("/api/quote/{symbol}")
async def get_quote(symbol: str) -> dict:
    if not SYMBOL_RE.match(symbol):
        raise HTTPException(400, f"Invalid symbol format: {symbol!r}")

    fubon = get_fubon()
    if fubon.status.value != "ok":
        raise HTTPException(
            503,
            detail={
                "error": "fubon_unavailable",
                "fubon_status": fubon.status.value,
                "last_error": fubon.last_error,
            },
        )

    try:
        result = await fubon.intraday_quote(symbol)
        return {
            "bids": result.get("bids", []),
            "asks": result.get("asks", []),
            "is_limit_up_bid":   result.get("isLimitUpBid", False),
            "is_limit_up_ask":   result.get("isLimitUpAsk", False),
            "is_limit_down_bid": result.get("isLimitDownBid", False),
            "is_limit_down_ask": result.get("isLimitDownAsk", False),
            "reference_price": result.get("referencePrice"),
        }
    except Exception as e:
        logger.warning("intraday_quote(%s) failed: %s", symbol, e)
        raise HTTPException(502, detail={"error": "fubon_call_failed", "detail": str(e)})


class SnapshotRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1, max_length=50)


@router.post("/api/quotes/snapshot")
async def snapshot_quotes(req: SnapshotRequest) -> dict:
    """批次拿多檔的 prev_close / last_price。

    - 任一 symbol 格式錯：整批 400
    - Fubon SDK degraded：整批 503
    - 單一 symbol 富邦失敗：該 row 兩欄都為 None，其他正常回
    """
    for sym in req.symbols:
        if not SYMBOL_RE.match(sym):
            raise HTTPException(400, f"Invalid symbol format: {sym!r}")

    fubon = get_fubon()
    if fubon.status.value != "ok":
        raise HTTPException(
            503,
            detail={
                "error": "fubon_unavailable",
                "fubon_status": fubon.status.value,
                "last_error": fubon.last_error,
            },
        )

    # 併發數對齊 limiter 速率 — 50 檔一次全下會同時占用共用執行緒池
    # (min(32, cpu+4) 條)跑 SDK 呼叫,期間其他富邦呼叫全部排隊;這層上限
    # 讓 snapshot 不依賴 client 內部等 token 的實作也塞不滿池子
    sem = asyncio.Semaphore(max(1, int(get_rate_limiter().rate)))

    async def one(sym: str) -> dict:
        async with sem:
            try:
                r = await fubon.intraday_quote(sym)
                return {
                    "symbol": sym,
                    "prev_close": r.get("previousClose"),
                    "last_price": r.get("lastPrice"),
                }
            except Exception as e:
                logger.warning("snapshot intraday_quote(%s) failed: %s", sym, e)
                return {"symbol": sym, "prev_close": None, "last_price": None}

    results = await asyncio.gather(*(one(s) for s in req.symbols))
    return {"quotes": results}
