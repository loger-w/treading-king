"""GET /api/me — 回傳當前 backend 的 USER_LABEL + cache owner 旗標。

前端用來顯示「You are: <label>」徽章，避免 .env 設錯卻不自知。
"""
from __future__ import annotations

from fastapi import APIRouter

from services.user_context import get_user_label, is_cache_job_owner

router = APIRouter()


@router.get("/api/me")
async def me() -> dict:
    return {
        "user_label": get_user_label(),
        "is_cache_owner": is_cache_job_owner(),
    }
