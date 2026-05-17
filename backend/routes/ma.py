"""GET /api/ma/{symbol} — 回日 K SMA5 / SMA20。

從 indicator_cache 拿最後一次成功 cache run 那天的 sma_5 / sma_20。
缺值(剛加入自選、indicator_cache 還沒跑到)欄位回 null,前端會靜默不畫。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from services.indicator_cache_job import get_latest_done_run
from services.supabase_client import SupabaseStatus, get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/ma/{symbol}")
async def get_ma(symbol: str) -> dict:
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(
            503,
            detail={"error": "supabase_unavailable", "last_error": sb.last_error},
        )

    latest = await asyncio.to_thread(get_latest_done_run, sb.client)
    if latest is None:
        # cache_runs 是空的(初次部署 / 沒跑過 cache job)
        return {"symbol": symbol, "sma_5": None, "sma_20": None, "as_of_date": None}

    run_date = latest["run_date"]
    # 用 limit(1) 而不是 maybe_single() — 跟 codebase 其他地方(cdp.py 等)一致,
    # 避免 supabase-py 在「沒 row」時行為跨版本不一致
    res = await asyncio.to_thread(
        lambda: sb.client.table("indicator_cache")
        .select("sma_5, sma_20")
        .eq("symbol", symbol)
        .eq("date", run_date)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    row = rows[0] if rows else {}
    return {
        "symbol": symbol,
        "sma_5": row.get("sma_5"),
        "sma_20": row.get("sma_20"),
        "as_of_date": run_date,
    }
