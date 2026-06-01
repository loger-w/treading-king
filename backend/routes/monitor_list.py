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

from routes._item_enrich import enrich_item
from services.cdp import get_cdp_service
from services.fubon_ws import get_ws_pool
from services.local_store import get_local_store

logger = logging.getLogger(__name__)
router = APIRouter()

OWNER_ID = "monitor_list"


class MonitorListAdd(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)


@router.get("/api/monitor_list")
async def list_monitor() -> dict:
    store = get_local_store()
    items = store.config.list_monitor()
    out = [enrich_item(m, store.market) for m in items]
    return {"items": out, "count": len(out)}


@router.post("/api/monitor_list", status_code=201)
async def add_monitor(payload: MonitorListAdd) -> dict:
    store = get_local_store()

    # symbol 必須存在於本機 market cache(若尚未載入則跳過,讓資料稍後同步)
    if store.market.symbols_loaded() and not store.market.has_symbol(payload.symbol):
        raise HTTPException(404, detail={"error": "symbol_not_found"})

    # 先試 ws subscribe;失敗就不寫 store,避免狀態不一致
    try:
        await get_ws_pool().subscribe(payload.symbol, owner_id=OWNER_ID)
    except RuntimeError as e:
        raise HTTPException(503, detail={"error": "ws_capacity_full", "detail": str(e)})

    # 寫 local store(add_monitor 已是 idempotent)
    store.config.add_monitor(payload.symbol)

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
    store = get_local_store()
    store.config.remove_monitor(symbol)
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
