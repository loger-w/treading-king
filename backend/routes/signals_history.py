"""GET /api/signals/history?... — 訊號歷史查詢。
GET /api/signals/today_counts — 今日累計命中數（給前端 chip 上標）。

儲存層為本機 SignalsLog。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query

from services.local_store import get_local_store

router = APIRouter()


@router.get("/api/signals/history")
async def signals_history(
    symbol: str | None = Query(None),
    active_signal_id: str | None = Query(None),
    since: str | None = Query(None, description="ISO datetime"),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    rows = get_local_store().signals.query(
        symbol=symbol,
        active_signal_id=active_signal_id,
        since=since,
        limit=limit,
    )
    return {"signals": rows, "count": len(rows)}


@router.get("/api/signals/today_counts")
async def today_counts() -> dict:
    """回 today (Asia/Taipei) 的 signals_log raw rows (symbol + active_signal_id)。
    前端 group by (symbol, active_signal_id) 算 count。
    Row 量小（cooldown ≥ 1800s × N 規則 × N 自選），不需 backend aggregate。
    """
    tz_tw = ZoneInfo("Asia/Taipei")
    today_start_tw = datetime.now(tz_tw).replace(hour=0, minute=0, second=0, microsecond=0)

    rows = get_local_store().signals.today_rows()
    counts = [
        {"symbol": r.get("symbol"), "active_signal_id": r.get("active_signal_id")}
        for r in rows
    ]
    return {
        "as_of": datetime.now(tz_tw).isoformat(),
        "today_start": today_start_tw.isoformat(),
        "counts": counts,
    }
