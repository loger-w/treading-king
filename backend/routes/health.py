"""GET /api/health — liveness + dependency status.

Phase 1 minimal version. Phase 2a 加 session/cache_*；Phase 3 加 ws_*/signal_engine。
"""
from __future__ import annotations

from fastapi import APIRouter

from services.fubon_client import get_fubon
from services.supabase_client import get_supabase

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

    return {
        "status": overall,
        "fubon_status": fubon.status.value,
        "fubon_last_error": fubon.last_error,
        "supabase_status": supabase.status.value,
        "supabase_last_error": supabase.last_error,
    }
