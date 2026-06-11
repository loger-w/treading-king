"""GET /api/camarilla/{symbol} — 回 Camarilla 8 線值。

流程與 /api/cdp 同構,共用 routes/cdp.py 的 get_levels_or_503。
"""
from __future__ import annotations

from fastapi import APIRouter

from routes.cdp import get_levels_or_503
from services.camarilla import get_camarilla_service

router = APIRouter()


@router.get("/api/camarilla/{symbol}")
async def get_camarilla(symbol: str) -> dict:
    return await get_levels_or_503(get_camarilla_service(), symbol, "camarilla")
