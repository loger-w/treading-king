"""GET/POST/DELETE /api/watchlist — 「自選」書籤的 thin alias。

Legacy 入口:書籤群組化後,/api/watchlist 改為對 user 的「自選」書籤 (預設書籤)
操作。內部呼叫新 schema (bookmark_groups + watchlist_items),但 API 簽名不變,
讓既有前端與 active_signals scope=watchlist 邏輯持續運作。

POST 順手:
  - ws_pool.subscribe(owner=f"bookmark:{自選_group_id}")
  - 背景 task: cdp.backfill_from_fubon
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.cdp import get_cdp_service
from services.fubon_ws import get_ws_pool
from services.supabase_client import SupabaseStatus, get_supabase
from services.user_context import get_user_label

logger = logging.getLogger(__name__)
router = APIRouter()

DEFAULT_BOOKMARK_NAME = "自選"


class WatchlistAdd(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    note: str | None = None


def _ensure_supabase():
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable", "last_error": sb.last_error})
    return sb


async def _get_or_create_default_group_id(sb, label: str) -> str:
    """取 user 的「自選」書籤 id;若不存在自動建一個。"""
    res = await asyncio.to_thread(
        lambda: sb.client.table("bookmark_groups")
        .select("id")
        .eq("user_label", label)
        .eq("name", DEFAULT_BOOKMARK_NAME)
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]["id"]

    # 沒有就建
    ins = await asyncio.to_thread(
        lambda: sb.client.table("bookmark_groups").insert({
            "user_label": label,
            "name": DEFAULT_BOOKMARK_NAME,
            "sort_order": 0,
            "is_system": False,
        }).execute()
    )
    if not ins.data:
        raise HTTPException(500, detail={"error": "create_default_bookmark_failed"})
    return ins.data[0]["id"]


def _owner_id(group_id: str) -> str:
    return f"bookmark:{group_id}"


@router.get("/api/watchlist")
async def list_watchlist() -> dict:
    sb = _ensure_supabase()
    gid = await _get_or_create_default_group_id(sb, get_user_label())

    res = await asyncio.to_thread(
        lambda: sb.client.table("watchlist_items")
        .select("symbol, added_at, note, symbols(name, market, is_etf)")
        .eq("group_id", gid)
        .order("added_at", desc=True)
        .execute()
    )
    rows = res.data or []
    out = []
    for r in rows:
        meta = r.get("symbols") or {}
        out.append({
            "symbol": r["symbol"],
            "added_at": r.get("added_at"),
            "note": r.get("note"),
            "name": meta.get("name"),
            "market": meta.get("market"),
            "is_etf": meta.get("is_etf"),
        })
    return {"watchlist": out, "count": len(out)}


@router.post("/api/watchlist", status_code=201)
async def add_watchlist(payload: WatchlistAdd) -> dict:
    sb = _ensure_supabase()
    label = get_user_label()
    gid = await _get_or_create_default_group_id(sb, label)

    # symbol 必須存在 symbols 表(FK 會擋但前端體驗差,主動驗)
    sym_res = await asyncio.to_thread(
        lambda: sb.client.table("symbols").select("symbol").eq("symbol", payload.symbol).limit(1).execute()
    )
    if not (sym_res.data or []):
        raise HTTPException(404, detail={"error": "symbol_not_found", "symbol": payload.symbol})

    try:
        await asyncio.to_thread(
            lambda: sb.client.table("watchlist_items").insert({
                "group_id": gid,
                "symbol": payload.symbol,
                "note": payload.note,
            }).execute()
        )
    except Exception as e:
        raise HTTPException(409, detail={"error": "already_in_watchlist", "detail": str(e)})

    # WS subscribe (owner_id 用 bookmark:{gid})
    try:
        await get_ws_pool().subscribe(payload.symbol, owner_id=_owner_id(gid))
    except RuntimeError as e:
        logger.warning("watchlist add: ws subscribe failed: %s", e)

    # CDP backfill 背景跑
    asyncio.create_task(get_cdp_service().backfill_from_fubon(payload.symbol))

    # signal_engine refresh
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("watchlist add: refresh signal_engine failed: %s", e)

    return {"symbol": payload.symbol, "status": "added"}


@router.delete("/api/watchlist/{symbol}", status_code=204)
async def remove_watchlist(symbol: str) -> None:
    sb = _ensure_supabase()
    gid = await _get_or_create_default_group_id(sb, get_user_label())

    await asyncio.to_thread(
        lambda: sb.client.table("watchlist_items")
        .delete()
        .eq("group_id", gid)
        .eq("symbol", symbol)
        .execute()
    )

    # WS unsubscribe (refcount 自動處理同檔多書籤;此處只取消「自選」這個 owner)
    try:
        await get_ws_pool().unsubscribe(symbol, owner_id=_owner_id(gid))
    except Exception as e:
        logger.warning("watchlist remove: ws unsubscribe failed: %s", e)

    # cdp cache 不主動 discard — 其他書籤可能還有,留給 lazy eviction
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("watchlist remove: refresh signal_engine failed: %s", e)
    return None
