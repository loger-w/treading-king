"""GET/POST/PUT/DELETE /api/active_signals — 即時訊號規則 CRUD。

POST/PUT/DELETE 後呼叫 signal_engine.refresh_active_signals 重新載入規則。
WS 訂閱由 monitor_list owner 統一管,active_signal 不再自己訂閱。
儲存層從 Supabase 改為本機 ConfigStore;無 user_label 隔離。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from models.condition import ActiveSignalCreate
from services.local_store import get_local_store
from services.signal_engine import get_signal_engine

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/active_signals")
async def list_active() -> dict:
    store = get_local_store()
    return {"active_signals": store.config.list_active_signals()}


@router.post("/api/active_signals", status_code=201)
async def create_active(payload: ActiveSignalCreate) -> dict:
    store = get_local_store()
    new_row = store.config.create_active_signal({
        "name": payload.name,
        "filter_json": payload.filter_json.model_dump(),
        "scope": payload.scope.model_dump(),
        "cooldown_seconds": payload.cooldown_seconds,
        "enabled": payload.enabled,
        "notify_discord": payload.notify_discord,
    })
    # ws 訂閱由 monitor_list owner 統一管,active_signal 不再自己訂閱
    await get_signal_engine().refresh_active_signals()
    return new_row


@router.put("/api/active_signals/{sid}")
async def update_active(sid: str, payload: ActiveSignalCreate) -> dict:
    store = get_local_store()
    updated = store.config.update_active_signal(sid, {
        "name": payload.name,
        "filter_json": payload.filter_json.model_dump(),
        "scope": payload.scope.model_dump(),
        "cooldown_seconds": payload.cooldown_seconds,
        "enabled": payload.enabled,
        "notify_discord": payload.notify_discord,
    })
    if updated is None:
        raise HTTPException(404, detail={"error": "not_found"})
    await get_signal_engine().refresh_active_signals()
    return updated


@router.delete("/api/active_signals/{sid}", status_code=204)
async def delete_active(sid: str) -> None:
    store = get_local_store()
    deleted = store.config.delete_active_signal(sid)
    if not deleted:
        raise HTTPException(404, detail={"error": "not_found"})
    await get_signal_engine().refresh_active_signals()
    return None
