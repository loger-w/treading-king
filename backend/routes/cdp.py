"""GET /api/cdp/{symbol} — 回 CDP 5 線值。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.cdp import get_cdp_service

router = APIRouter()


@router.get("/api/cdp/{symbol}")
async def get_cdp(symbol: str) -> dict:
    levels = await get_cdp_service().get(symbol)
    if levels is None:
        # lazy backfill 一次
        ok = await get_cdp_service().backfill_from_fubon(symbol)
        if not ok:
            raise HTTPException(503, detail={"error": "cdp_data_unavailable", "symbol": symbol})
        levels = await get_cdp_service().get(symbol)
        if levels is None:
            raise HTTPException(503, detail={"error": "cdp_data_unavailable_after_backfill"})
    return levels  # type: ignore[return-value]
