"""GET /api/camarilla/{symbol} — 回 Camarilla 8 線值。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.camarilla import get_camarilla_service

router = APIRouter()


@router.get("/api/camarilla/{symbol}")
async def get_camarilla(symbol: str) -> dict:
    levels = await get_camarilla_service().get(symbol)
    if levels is None:
        ok = await get_camarilla_service().backfill_from_fubon(symbol)
        if not ok:
            raise HTTPException(503, detail={"error": "camarilla_data_unavailable", "symbol": symbol})
        levels = await get_camarilla_service().get(symbol)
        if levels is None:
            raise HTTPException(503, detail={"error": "camarilla_data_unavailable_after_backfill"})
    return levels  # type: ignore[return-value]
