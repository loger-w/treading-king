"""GET/POST/DELETE /api/watchlist — 「自選」書籤的 thin alias。

Legacy 入口:書籤群組化後,/api/watchlist 改為對 user 的「自選」書籤 (預設書籤)
操作。內部走本機 ConfigStore(bookmark_groups + watchlist_items),但 API 簽名不變,
讓既有前端與 active_signals scope=watchlist 邏輯持續運作。

「自選」書籤由 ConfigStore.load() 的 _seed_defaults 保證存在(name == "自選")。

POST 順手:
  - ws_pool.subscribe(owner=f"bookmark:{自選_group_id}")
  - 背景 task: cdp.backfill_from_fubon
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

DEFAULT_BOOKMARK_NAME = "自選"


class WatchlistAdd(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    note: str | None = None


def _default_group_id() -> str:
    """取 user 的「自選」書籤 id;seed 保證存在,缺了就建一個。"""
    store = get_local_store()
    g = next((x for x in store.config.list_groups()
              if x["name"] == DEFAULT_BOOKMARK_NAME), None)
    if g is None:
        g = store.config.create_group(DEFAULT_BOOKMARK_NAME, sort_order=0)
    return g["id"]


def _owner_id(group_id: str) -> str:
    return f"bookmark:{group_id}"


@router.get("/api/watchlist")
async def list_watchlist() -> dict:
    store = get_local_store()
    gid = _default_group_id()

    items = store.config.list_items(gid)
    items = sorted(items, key=lambda it: it.get("added_at") or "", reverse=True)
    out = [
        enrich_item(
            {"symbol": it["symbol"], "added_at": it.get("added_at"), "note": it.get("note")},
            store.market,
        )
        for it in items
    ]
    return {"watchlist": out, "count": len(out)}


@router.post("/api/watchlist", status_code=201)
async def add_watchlist(payload: WatchlistAdd) -> dict:
    store = get_local_store()
    gid = _default_group_id()

    # symbol 必須存在 symbols cache(cache 已載入才驗;未載入則放行)
    if store.market.symbols_loaded() and not store.market.has_symbol(payload.symbol):
        raise HTTPException(404, detail={"error": "symbol_not_found", "symbol": payload.symbol})

    # 已在自選 → 409(對齊舊 unique violation 行為)
    if any(it["symbol"] == payload.symbol for it in store.config.list_items(gid)):
        raise HTTPException(409, detail={"error": "already_in_watchlist"})

    store.config.add_item(gid, payload.symbol, note=payload.note)

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
    store = get_local_store()
    gid = _default_group_id()

    store.config.remove_item(gid, symbol)

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
