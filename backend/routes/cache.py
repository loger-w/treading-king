"""POST /api/cache/refresh, GET /api/cache/status — indicator cache job control.

Plan §Phase 2a：前端按鈕「跑 cache job」打 refresh；「狀態」顯示 status row。

refresh 是 fire-and-forget 背景 task：response 立刻回 202，job 之後在背景跑。
重複呼叫會被 indicator_cache_job._running 擋掉，回 409。

cache job 跑全市場 ~2363 檔 × 8 API call / 5 req/s ≈ 60 分鐘。dev 階段帶 ?limit=N
先驗證流程通了再跑全量。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query

from services.indicator_cache_job import (
    get_latest_run,
    is_cache_job_running,
    run_cache_job,
)
from services.supabase_client import SupabaseStatus, get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


def _on_job_done(task: asyncio.Task) -> None:
    """Background-task done callback — log result/error."""
    if task.cancelled():
        logger.warning("cache job task cancelled")
        return
    exc = task.exception()
    if exc is not None:
        logger.error("cache job task failed: %s: %s", type(exc).__name__, exc)
    else:
        logger.info("cache job task finished: %s", task.result())


@router.post("/api/cache/refresh", status_code=202)
async def refresh_cache(
    limit: int | None = Query(
        None,
        ge=1,
        description="Dev: only process first N active symbols (None = all)",
    ),
) -> dict:
    if is_cache_job_running():
        raise HTTPException(
            409,
            detail={"error": "already_running", "message": "cache job already in progress"},
        )

    task = asyncio.create_task(run_cache_job(limit=limit))
    task.add_done_callback(_on_job_done)

    return {
        "status": "accepted",
        "limit": limit,
        "message": "cache job started; poll /api/cache/status",
    }


@router.get("/api/cache/status")
async def cache_status() -> dict:
    """Latest cache_runs row + whether a job is currently running."""
    supabase = get_supabase()
    if supabase.status != SupabaseStatus.OK or supabase.client is None:
        raise HTTPException(
            503,
            detail={"error": "supabase_unavailable", "last_error": supabase.last_error},
        )

    latest = get_latest_run(supabase.client)
    return {
        "running": is_cache_job_running(),
        "latest_run": latest,  # None if cache_runs is empty
    }
