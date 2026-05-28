"""GET/POST/DELETE /api/monitor_list — 監聽清單 CRUD。

POST 順手:
  - ws_pool.subscribe(owner='monitor_list')
  - cdp_service.backfill_from_fubon(symbol) 背景
  - signal_engine.refresh_active_signals()
DELETE 反過來 unsubscribe + refresh。
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

OWNER_ID = "monitor_list"


class MonitorListAdd(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)


def _ensure_supabase():
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable"})
    return sb


@router.get("/api/monitor_list")
async def list_monitor() -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("monitor_list")
        .select("symbol, added_at, symbols(name, market, is_etf)")
        .eq("user_label", get_user_label())
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
            "name": meta.get("name"),
            "market": meta.get("market"),
            "is_etf": meta.get("is_etf"),
        })
    return {"items": out, "count": len(out)}


@router.post("/api/monitor_list", status_code=201)
async def add_monitor(payload: MonitorListAdd) -> dict:
    sb = _ensure_supabase()
    label = get_user_label()

    # symbol 必須存在 symbols 表
    sym_res = await asyncio.to_thread(
        lambda: sb.client.table("symbols").select("symbol")
        .eq("symbol", payload.symbol).limit(1).execute()
    )
    if not (sym_res.data or []):
        raise HTTPException(404, detail={"error": "symbol_not_found"})

    # 先試 ws subscribe;失敗就不寫 DB,避免狀態不一致
    try:
        await get_ws_pool().subscribe(payload.symbol, owner_id=OWNER_ID)
    except RuntimeError as e:
        raise HTTPException(503, detail={"error": "ws_capacity_full", "detail": str(e)})

    # 寫 DB
    try:
        await asyncio.to_thread(
            lambda: sb.client.table("monitor_list").insert({
                "user_label": label,
                "symbol": payload.symbol,
            }).execute()
        )
    except Exception as e:
        # 寫失敗 → rollback ws subscribe
        try:
            await get_ws_pool().unsubscribe(payload.symbol, owner_id=OWNER_ID)
        except Exception:
            pass
        raise HTTPException(409, detail={"error": "already_in_monitor_list", "detail": str(e)})

    # CDP backfill 背景跑
    asyncio.create_task(get_cdp_service().backfill_from_fubon(payload.symbol))

    # signal_engine refresh
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("monitor_list add: refresh signal_engine failed: %s", e)

    return {"symbol": payload.symbol, "status": "added"}


@router.delete("/api/monitor_list/{symbol}", status_code=204)
async def remove_monitor(symbol: str) -> None:
    sb = _ensure_supabase()
    await asyncio.to_thread(
        lambda: sb.client.table("monitor_list").delete()
        .eq("user_label", get_user_label())
        .eq("symbol", symbol)
        .execute()
    )
    try:
        await get_ws_pool().unsubscribe(symbol, owner_id=OWNER_ID)
    except Exception as e:
        logger.warning("monitor_list remove: ws unsubscribe failed: %s", e)
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("monitor_list remove: refresh signal_engine failed: %s", e)
    return None
