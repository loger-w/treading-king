"""GET /api/quote/{symbol} — 即時報價（透過富邦 intraday/quote）."""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException

from services.fubon_client import get_fubon

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
        return result
    except Exception as e:
        logger.warning("intraday_quote(%s) failed: %s", symbol, e)
        raise HTTPException(502, detail={"error": "fubon_call_failed", "detail": str(e)})
