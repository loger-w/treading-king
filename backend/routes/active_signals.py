"""GET/POST/PUT/DELETE /api/active_signals — 即時訊號規則 CRUD。

POST/PUT 後呼叫 signal_engine.refresh_active_signals 重新載入規則。
WS 訂閱由 monitor_list owner 統一管,active_signal 不再自己訂閱。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from models.condition import ActiveSignalCreate
from services.signal_engine import get_signal_engine
from services.supabase_client import SupabaseStatus, get_supabase
from services.user_context import get_user_label

logger = logging.getLogger(__name__)
router = APIRouter()


def _ensure_supabase():
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable", "last_error": sb.last_error})
    return sb


@router.get("/api/active_signals")
async def list_active() -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("active_signals")
        .select("id, name, filter_json, scope, cooldown_seconds, enabled, notify_discord, created_at")
        .eq("user_label", get_user_label())
        .order("created_at", desc=True).execute()
    )
    return {"active_signals": res.data or []}


@router.post("/api/active_signals", status_code=201)
async def create_active(payload: ActiveSignalCreate) -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("active_signals").insert({
            "name": payload.name,
            "filter_json": payload.filter_json.model_dump(),
            "scope": payload.scope.model_dump(),
            "cooldown_seconds": payload.cooldown_seconds,
            "enabled": payload.enabled,
            "notify_discord": payload.notify_discord,
            "user_label": get_user_label(),
        }).execute()
    )
    if not res.data:
        raise HTTPException(500, detail={"error": "insert_failed"})
    new_row = res.data[0]
    # ws 訂閱由 monitor_list owner 統一管,active_signal 不再自己訂閱
    await get_signal_engine().refresh_active_signals()
    return new_row


@router.put("/api/active_signals/{sid}")
async def update_active(sid: str, payload: ActiveSignalCreate) -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("active_signals").update({
            "name": payload.name,
            "filter_json": payload.filter_json.model_dump(),
            "scope": payload.scope.model_dump(),
            "cooldown_seconds": payload.cooldown_seconds,
            "enabled": payload.enabled,
            "notify_discord": payload.notify_discord,
        })
        .eq("user_label", get_user_label())
        .eq("id", sid)
        .execute()
    )
    await get_signal_engine().refresh_active_signals()
    return res.data[0] if res.data else {}


@router.delete("/api/active_signals/{sid}", status_code=204)
async def delete_active(sid: str) -> None:
    sb = _ensure_supabase()
    await asyncio.to_thread(
        lambda: sb.client.table("active_signals")
        .delete()
        .eq("user_label", get_user_label())
        .eq("id", sid)
        .execute()
    )
    await get_signal_engine().refresh_active_signals()
    return None
