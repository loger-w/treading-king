"""GET / POST /api/screen — 篩股 endpoint。

Phase 2b：POST 接收 Filter DSL，跑通用篩股引擎；
保留 GET /api/screen/rsi-oversold 為 thin wrapper（phase 2a 兼容）。

讀取策略：永遠讀「最後一次成功 cache_runs.run_date」對應的 indicator_cache。
未跑過 cache → 503。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from models.condition import Condition, Filter
from services.screener import screen as run_screen
from services.supabase_client import SupabaseStatus, get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


def _ensure_supabase():
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(
            503,
            detail={"error": "supabase_unavailable", "last_error": sb.last_error},
        )
    return sb


@router.post("/api/screen")
async def screen_api(filter_: Filter) -> dict:
    """跑自定 Filter 篩股。

    Body 範例：
    {
      "conditions": [
        {"field": "rsi_14", "operator": "lt", "value": 30},
        {"field": "change_pct", "operator": "gte", "value": 0}
      ],
      "logic": "AND",
      "limit": 200
    }
    """
    sb = _ensure_supabase()
    try:
        result = run_screen(sb.client, filter_)
    except RuntimeError as e:
        raise HTTPException(
            503,
            detail={"error": "cache_not_ready", "message": str(e)},
        )
    result["filter"] = filter_.model_dump()
    return result


@router.get("/api/screen/rsi-oversold")
async def screen_rsi_oversold(
    limit: int = Query(200, ge=1, le=500),
) -> dict:
    """Phase 2a 兼容 endpoint：寫死 RSI 超賣 + 紅 K。新前端用 POST /api/screen。"""
    sb = _ensure_supabase()
    f = Filter(
        conditions=[
            Condition(field="rsi_14", operator="lt", value=30),
            Condition(field="change_pct", operator="gte", value=0),
        ],
        limit=limit,
    )
    try:
        result = run_screen(sb.client, f)
    except RuntimeError as e:
        raise HTTPException(
            503,
            detail={"error": "cache_not_ready", "message": str(e)},
        )
    return {
        "rule": "rsi-oversold",
        "criteria": "rsi_14 < 30 AND change_pct >= 0",
        **result,
    }
