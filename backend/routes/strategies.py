"""/api/strategies — strategies CRUD。

Plan §Phase 2b。預設策略名稱以 "[預設]" 前綴（前端用此判斷是否可刪）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from models.condition import Filter
from services.supabase_client import SupabaseStatus, get_supabase
from services.user_context import get_user_label

logger = logging.getLogger(__name__)
router = APIRouter()


class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    filter_json: Filter


def _ensure_supabase():
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(
            503,
            detail={"error": "supabase_unavailable", "last_error": sb.last_error},
        )
    return sb


@router.get("/api/strategies")
async def list_strategies() -> dict:
    sb = _ensure_supabase()
    res = (
        sb.client.table("strategies")
        .select("id, name, description, filter_json, created_at")
        .eq("user_label", get_user_label())
        .order("created_at", desc=True)
        .execute()
    )
    return {"strategies": res.data or []}


@router.post("/api/strategies", status_code=201)
async def create_strategy(payload: StrategyCreate) -> dict:
    sb = _ensure_supabase()
    res = (
        sb.client.table("strategies")
        .insert(
            {
                "name": payload.name,
                "description": payload.description,
                "filter_json": payload.filter_json.model_dump(),
                "user_label": get_user_label(),
            }
        )
        .execute()
    )
    if not res.data:
        raise HTTPException(500, detail={"error": "insert_failed"})
    return res.data[0]


@router.delete("/api/strategies/{strategy_id}", status_code=204)
async def delete_strategy(strategy_id: str) -> None:
    sb = _ensure_supabase()
    (
        sb.client.table("strategies")
        .delete()
        .eq("user_label", get_user_label())
        .eq("id", strategy_id)
        .execute()
    )
    return None
