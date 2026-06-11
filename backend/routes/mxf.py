"""MXF 期貨即時行情 REST endpoints。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from services.fubon_futures import (
    SUPPORTED_TIMEFRAMES,
    FubonUnavailableError,
    determine_current_session,
    fetch_candles,
    resolve_active_symbol,
)

router = APIRouter()
TPE = ZoneInfo("Asia/Taipei")


@router.get("/api/mxf/symbol/active")
async def get_active_symbol() -> dict:
    symbol = await resolve_active_symbol()
    if not symbol:
        raise HTTPException(503, detail={"error": "mxf_symbol_unavailable"})
    return {"symbol": symbol}


@router.get("/api/mxf/candles")
async def get_mxf_candles(
    tf: int = Query(5, description="timeframe in minutes"),
    symbol: str | None = Query(None, description="若指定就用,否則自動取近月"),
) -> dict:
    if tf not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(400, detail={"error": "unsupported_timeframe", "supported": list(SUPPORTED_TIMEFRAMES)})

    use_symbol = symbol or await resolve_active_symbol()
    if not use_symbol:
        raise HTTPException(503, detail={"error": "mxf_symbol_unavailable"})

    try:
        candles = await fetch_candles(use_symbol, tf)
    except FubonUnavailableError:
        # 與股票 candles 口徑一致:行情源死亡回 503,讓前端能與「休市無資料」區分
        raise HTTPException(503, detail={"error": "fubon_unavailable"})
    return {
        "symbol": use_symbol,
        "timeframe": tf,
        "candles": candles,
        "current_session": determine_current_session(datetime.now(TPE)),
    }
