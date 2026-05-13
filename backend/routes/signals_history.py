"""GET /api/signals/history?... — 訊號歷史查詢。
GET /api/signals/today_counts — 今日累計命中數（給前端 chip 上標）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from services.supabase_client import SupabaseStatus, get_supabase
from services.user_context import get_user_label

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
        ).eq("user_label", get_user_label()).order("triggered_at", desc=True).limit(limit)
        if symbol:
            q = q.eq("symbol", symbol)
        if active_signal_id:
            q = q.eq("active_signal_id", active_signal_id)
        if since:
            q = q.gte("triggered_at", since)
        return q.execute()

    res = await asyncio.to_thread(_q)
    return {"signals": res.data or [], "count": len(res.data or [])}


@router.get("/api/signals/today_counts")
async def today_counts() -> dict:
    """回 today (Asia/Taipei) 的 signals_log raw rows (symbol + active_signal_id)。
    前端 group by (symbol, active_signal_id) 算 count。
    Row 量小（cooldown ≥ 1800s × N 規則 × N 自選），不需 backend aggregate。
    """
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable"})

    tz_tw = ZoneInfo("Asia/Taipei")
    today_start_tw = datetime.now(tz_tw).replace(hour=0, minute=0, second=0, microsecond=0)

    def _q():
        return (
            sb.client.table("signals_log")
            .select("symbol, active_signal_id")
            .eq("user_label", get_user_label())
            .gte("triggered_at", today_start_tw.isoformat())
            .execute()
        )

    res = await asyncio.to_thread(_q)
    return {
        "as_of": datetime.now(tz_tw).isoformat(),
        "today_start": today_start_tw.isoformat(),
        "counts": res.data or [],
    }
