"""GET/POST/DELETE /api/watchlist — 自選清單 CRUD。

POST 順手:
  - ws_pool.subscribe(owner='watchlist')
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

logger = logging.getLogger(__name__)
router = APIRouter()


class WatchlistAdd(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    note: str | None = None


def _ensure_supabase():
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable", "last_error": sb.last_error})
    return sb


@router.get("/api/watchlist")
async def list_watchlist() -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("watchlist")
        .select("symbol, added_at, note, symbols(name, market, is_etf)")
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
    # symbol 必須存在 symbols 表（FK 會擋但前端體驗差，主動驗）
    sym_res = await asyncio.to_thread(
        lambda: sb.client.table("symbols").select("symbol").eq("symbol", payload.symbol).limit(1).execute()
    )
    if not (sym_res.data or []):
        raise HTTPException(404, detail={"error": "symbol_not_found", "symbol": payload.symbol})

    try:
        await asyncio.to_thread(
            lambda: sb.client.table("watchlist").insert({
                "symbol": payload.symbol, "note": payload.note,
            }).execute()
        )
    except Exception as e:
        # 可能 unique violation（已存在）
        raise HTTPException(409, detail={"error": "already_in_watchlist", "detail": str(e)})

    # WS subscribe (sync, fast)
    try:
        await get_ws_pool().subscribe(payload.symbol, owner_id="watchlist")
    except RuntimeError as e:
        logger.warning("watchlist add: ws subscribe failed: %s", e)
        # 不 rollback watchlist，user 可看到加進來但無即時資料

    # CDP backfill 背景跑（不 block response）
    asyncio.create_task(get_cdp_service().backfill_from_fubon(payload.symbol))

    # refresh signal_engine field_cache so scope=watchlist signals start evaluating this symbol
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("watchlist add: refresh signal_engine failed: %s", e)

    return {"symbol": payload.symbol, "status": "added"}


@router.delete("/api/watchlist/{symbol}", status_code=204)
async def remove_watchlist(symbol: str) -> None:
    sb = _ensure_supabase()
    await asyncio.to_thread(
        lambda: sb.client.table("watchlist").delete().eq("symbol", symbol).execute()
    )
    # WS unsubscribe（其他 owner 可能還在）
    try:
        await get_ws_pool().unsubscribe(symbol, owner_id="watchlist")
    except Exception as e:
        logger.warning("watchlist remove: ws unsubscribe failed: %s", e)
    # cdp cache 也清
    get_cdp_service().discard(symbol)

    # refresh signal_engine: remove this symbol from any cached scope
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("watchlist remove: refresh signal_engine failed: %s", e)

    return None
