"""GET /api/health — liveness + dependency status.

Phase 1: status, fubon_status, supabase_status.
Phase 2a: + is_trading_day, cache_last_success_at, cache_last_run_status.
Phase 3: + session, ws_*, signal_engine.
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter

from services.fubon_client import get_fubon
from services.indicator_cache_job import get_latest_done_run, get_latest_run
from services.supabase_client import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/health")
async def health() -> dict:
    fubon = get_fubon()
    supabase = get_supabase()

    fubon_ok = fubon.status.value == "ok"
    supabase_ok = supabase.status.value == "ok"

    if fubon_ok and supabase_ok:
        overall = "ok"
    elif fubon_ok or supabase_ok:
        overall = "degraded"
    else:
        overall = "error"

    today = date.today()
    is_trading_day = today.weekday() < 5  # TODO: 真實 TWSE 行事曆

    cache_last_success_at: str | None = None
    cache_last_run_status: str | None = None
    if supabase_ok and supabase.client is not None:
        try:
            done = get_latest_done_run(supabase.client)
            if done:
                cache_last_success_at = done.get("finished_at") or done.get("started_at")
            latest = get_latest_run(supabase.client)
            if latest:
                cache_last_run_status = latest.get("status")
        except Exception as e:
            logger.warning("health: cache_runs lookup failed: %s", e)

    return {
        "status": overall,
        "fubon_status": fubon.status.value,
        "fubon_last_error": fubon.last_error,
        "supabase_status": supabase.status.value,
        "supabase_last_error": supabase.last_error,
        "is_trading_day": is_trading_day,
        "cache_last_success_at": cache_last_success_at,
        "cache_last_run_status": cache_last_run_status,
    }
