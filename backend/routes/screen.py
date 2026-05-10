"""GET /api/screen/* — read-only screener endpoints (Phase 2a hard-coded rules).

Phase 2a 寫死一條 RSI 超賣（rsi_14 < 30 AND change_pct >= 0），證明端到端通了。
Phase 2b 才升級成 DSL（filter_json）+ POST /api/screen + 策略 CRUD。

讀取策略：永遠讀「最後一次成功的 cache_runs.run_date」對應的 indicator_cache 列。
這就是 plan §Phase 2a 「同表 + date filter staging」路線——
任意時刻 kill 半截 job，reader 還是讀上一個成功 date，沒 inconsistent 列。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from services.indicator_cache_job import get_latest_done_run
from services.supabase_client import SupabaseStatus, get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/screen/rsi-oversold")
async def screen_rsi_oversold(
    limit: int = Query(200, ge=1, le=500),
) -> dict:
    """RSI 超賣 + 紅 K：rsi_14 < 30 AND change_pct >= 0。

    回傳「最後一次成功 cache 跑」當下符合的 symbols，含 join 後的 name/market。
    若還沒有成功的 cache run，回 503。
    """
    supabase = get_supabase()
    if supabase.status != SupabaseStatus.OK or supabase.client is None:
        raise HTTPException(
            503,
            detail={"error": "supabase_unavailable", "last_error": supabase.last_error},
        )

    client = supabase.client

    last_done = get_latest_done_run(client)
    if not last_done:
        raise HTTPException(
            503,
            detail={
                "error": "cache_not_ready",
                "message": "no successful cache run yet — POST /api/cache/refresh first",
            },
        )

    target_date = last_done["run_date"]

    # 拉符合條件的 cache 列 + join symbols 拿 name/market
    res = (
        client.table("indicator_cache")
        .select(
            "symbol, date, close, change_pct, volume, rsi_14, "
            "symbols(name, market, is_etf)"
        )
        .eq("date", target_date)
        .lt("rsi_14", 30)
        .gte("change_pct", 0)
        .order("rsi_14")
        .limit(limit)
        .execute()
    )

    rows = res.data or []
    results = []
    for r in rows:
        sym_meta = r.get("symbols") or {}
        results.append(
            {
                "symbol": r["symbol"],
                "name": sym_meta.get("name"),
                "market": sym_meta.get("market"),
                "is_etf": sym_meta.get("is_etf"),
                "close": r.get("close"),
                "change_pct": r.get("change_pct"),
                "volume": r.get("volume"),
                "rsi_14": r.get("rsi_14"),
            }
        )

    return {
        "rule": "rsi-oversold",
        "criteria": "rsi_14 < 30 AND change_pct >= 0",
        "as_of_date": target_date,
        "count": len(results),
        "results": results,
    }
