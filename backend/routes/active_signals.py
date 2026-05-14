"""GET/POST/PUT/DELETE /api/active_signals — 即時訊號規則 CRUD。

POST/PUT 後呼叫 signal_engine.refresh_active_signals 重新載入規則 + 對 scope 內的 symbols
做 ws_pool.subscribe(owner=active_signal_id)。
DELETE 反過來 unsubscribe。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from models.condition import ActiveSignalCreate
from services.fubon_ws import get_ws_pool
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


async def _scope_symbols(scope: dict) -> list[str]:
    """解析 scope dict → symbols list."""
    sb = get_supabase()
    if scope.get("type") == "symbols":
        return list(scope.get("symbols", []))
    if scope.get("type") == "watchlist":
        res = await asyncio.to_thread(
            lambda: sb.client.table("watchlist")
            .select("symbol")
            .eq("user_label", get_user_label())
            .execute()
        )
        return [r["symbol"] for r in (res.data or [])]
    return []


@router.get("/api/active_signals")
async def list_active() -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("active_signals")
        .select("id, name, filter_json, scope, cooldown_seconds, enabled, created_at")
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
            "user_label": get_user_label(),
        }).execute()
    )
    if not res.data:
        raise HTTPException(500, detail={"error": "insert_failed"})
    new_row = res.data[0]
    # subscribe scope 內 symbols
    if payload.enabled:
        symbols = await _scope_symbols(payload.scope.model_dump())
        for sym in symbols:
            try:
                await get_ws_pool().subscribe(sym, owner_id=new_row["id"])
            except RuntimeError as e:
                logger.warning("active_signal create: ws sub %s failed: %s", sym, e)
    await get_signal_engine().refresh_active_signals()
    return new_row


@router.put("/api/active_signals/{sid}")
async def update_active(sid: str, payload: ActiveSignalCreate) -> dict:
    sb = _ensure_supabase()
    # 拿舊的 scope 算 diff（簡化：先全 unsub 再全 sub）
    old = await asyncio.to_thread(
        lambda: sb.client.table("active_signals")
        .select("scope, enabled")
        .eq("user_label", get_user_label())
        .eq("id", sid)
        .single()
        .execute()
    )
    if not old.data:
        raise HTTPException(404, detail={"error": "not_found"})

    old_syms = await _scope_symbols(old.data.get("scope", {})) if old.data.get("enabled") else []
    for sym in old_syms:
        await get_ws_pool().unsubscribe(sym, owner_id=sid)

    res = await asyncio.to_thread(
        lambda: sb.client.table("active_signals").update({
            "name": payload.name,
            "filter_json": payload.filter_json.model_dump(),
            "scope": payload.scope.model_dump(),
            "cooldown_seconds": payload.cooldown_seconds,
            "enabled": payload.enabled,
        })
        .eq("user_label", get_user_label())
        .eq("id", sid)
        .execute()
    )

    if payload.enabled:
        new_syms = await _scope_symbols(payload.scope.model_dump())
        for sym in new_syms:
            try:
                await get_ws_pool().subscribe(sym, owner_id=sid)
            except RuntimeError as e:
                logger.warning("update: ws sub %s failed: %s", sym, e)
    await get_signal_engine().refresh_active_signals()
    return res.data[0] if res.data else {}


@router.delete("/api/active_signals/{sid}", status_code=204)
async def delete_active(sid: str) -> None:
    sb = _ensure_supabase()
    old = await asyncio.to_thread(
        lambda: sb.client.table("active_signals")
        .select("scope, enabled")
        .eq("user_label", get_user_label())
        .eq("id", sid)
        .single()
        .execute()
    )
    if old.data and old.data.get("enabled"):
        for sym in await _scope_symbols(old.data.get("scope", {})):
            await get_ws_pool().unsubscribe(sym, owner_id=sid)

    await asyncio.to_thread(
        lambda: sb.client.table("active_signals")
        .delete()
        .eq("user_label", get_user_label())
        .eq("id", sid)
        .execute()
    )
    await get_signal_engine().refresh_active_signals()
    return None
