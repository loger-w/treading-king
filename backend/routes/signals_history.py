"""GET /api/signals/history?symbol=&since=&active_signal_id=&limit= — 訊號歷史查詢。"""
from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from services.supabase_client import SupabaseStatus, get_supabase

router = APIRouter()


@router.get("/api/signals/history")
async def signals_history(
    symbol: str | None = Query(None),
    active_signal_id: str | None = Query(None),
    since: str | None = Query(None, description="ISO datetime"),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable"})

    def _q():
        q = sb.client.table("signals_log").select(
            "id, active_signal_id, symbol, triggered_at, trigger_price, trigger_volume, context_json"
        ).order("triggered_at", desc=True).limit(limit)
        if symbol:
            q = q.eq("symbol", symbol)
        if active_signal_id:
            q = q.eq("active_signal_id", active_signal_id)
        if since:
            q = q.gte("triggered_at", since)
        return q.execute()

    res = await asyncio.to_thread(_q)
    return {"signals": res.data or [], "count": len(res.data or [])}
