"""GET/POST/PATCH/DELETE /api/bookmarks — 書籤群組 + 內含股票 CRUD。

書籤架構:
  - bookmark_groups:user 自訂書籤 + 系統書籤(user_label = NULL)
  - watchlist_items:書籤 → 股票 (group_id, symbol)
  - top_gainers_snapshot:系統書籤「大漲股」的動態內容(由排程更新)

WS subscribe 用 owner_id = f"bookmark:{group_id}":
  fubon_ws 的 refcount 是 set-of-owner_id,同檔股票在多書籤時、
  pool 自動處理 — 刪一邊不會 unsubscribe(只要還有其他 group_id 的 owner)。

系統書籤(is_system=true):
  - 內容來自 top_gainers_snapshot(不在 watchlist_items)
  - PATCH/DELETE/POST items/DELETE items 一律拒絕(403)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.cdp import get_cdp_service
from services.fubon_ws import get_ws_pool
from services.supabase_client import SupabaseStatus, get_supabase
from services.user_context import get_user_label

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------- helpers ----------

def _ensure_supabase():
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable", "last_error": sb.last_error})
    return sb


def _owner_id(group_id: str) -> str:
    """WSPool owner id naming — 每個書籤獨立 owner,refcount 自動處理多書籤共有。"""
    return f"bookmark:{group_id}"


async def _get_group(sb, group_id: str, *, allow_system: bool = True) -> dict:
    """讀單一 group + 驗證 user 有權看(系統書籤所有人可看,user 書籤要對 label)。"""
    res = await asyncio.to_thread(
        lambda: sb.client.table("bookmark_groups")
        .select("id, user_label, name, sort_order, is_system, source_type")
        .eq("id", group_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(404, detail={"error": "bookmark_not_found"})
    g = rows[0]
    # 系統書籤所有人可看;user 書籤要對 label
    if not g["is_system"] and g["user_label"] != get_user_label():
        raise HTTPException(404, detail={"error": "bookmark_not_found"})  # 不洩漏存在性
    if g["is_system"] and not allow_system:
        raise HTTPException(403, detail={"error": "system_bookmark_readonly"})
    return g


# ---------- 群組 CRUD ----------

class BookmarkCreate(BaseModel):
    name: str = Field(min_length=1, max_length=30)


class BookmarkPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=30)
    sort_order: int | None = None


@router.get("/api/bookmarks")
async def list_bookmarks() -> dict:
    """列出 user 的書籤(含系統書籤),含每個書籤的股票數。"""
    sb = _ensure_supabase()
    label = get_user_label()

    # 撈 user 自己的 + 系統書籤
    res = await asyncio.to_thread(
        lambda: sb.client.table("bookmark_groups")
        .select("id, name, sort_order, is_system, source_type, user_label")
        .or_(f"user_label.eq.{label},user_label.is.null")
        .order("is_system")
        .order("sort_order")
        .execute()
    )
    groups = res.data or []
    group_ids = [g["id"] for g in groups if not g["is_system"]]

    # 一次撈 user 書籤的 item counts
    user_counts: dict[str, int] = {}
    if group_ids:
        items_res = await asyncio.to_thread(
            lambda: sb.client.table("watchlist_items")
            .select("group_id")
            .in_("group_id", group_ids)
            .execute()
        )
        for r in items_res.data or []:
            user_counts[r["group_id"]] = user_counts.get(r["group_id"], 0) + 1

    # 系統書籤 (top_gainers) 從 snapshot 算
    system_counts: dict[str, int] = {}
    for g in groups:
        if g["is_system"] and g["source_type"] == "top_gainers":
            tg_res = await asyncio.to_thread(
                lambda: sb.client.table("top_gainers_snapshot").select("symbol", count="exact").execute()
            )
            system_counts[g["id"]] = tg_res.count or 0

    out = []
    for g in groups:
        cnt = system_counts.get(g["id"], 0) if g["is_system"] else user_counts.get(g["id"], 0)
        out.append({
            "id": g["id"],
            "name": g["name"],
            "sort_order": g["sort_order"],
            "is_system": g["is_system"],
            "source_type": g["source_type"],
            "count": cnt,
        })
    return {"groups": out, "count": len(out)}


@router.post("/api/bookmarks", status_code=201)
async def create_bookmark(payload: BookmarkCreate) -> dict:
    sb = _ensure_supabase()
    label = get_user_label()

    # 算下一個 sort_order
    max_res = await asyncio.to_thread(
        lambda: sb.client.table("bookmark_groups")
        .select("sort_order")
        .eq("user_label", label)
        .order("sort_order", desc=True)
        .limit(1)
        .execute()
    )
    next_order = (max_res.data[0]["sort_order"] + 1) if max_res.data else 1

    try:
        res = await asyncio.to_thread(
            lambda: sb.client.table("bookmark_groups").insert({
                "user_label": label,
                "name": payload.name,
                "sort_order": next_order,
                "is_system": False,
            }).execute()
        )
    except Exception as e:
        # 名稱重複(uniq_bookmark_user_name)
        raise HTTPException(409, detail={"error": "bookmark_name_taken", "detail": str(e)})

    if not res.data:
        raise HTTPException(500, detail={"error": "insert_failed"})
    return res.data[0]


@router.patch("/api/bookmarks/{bid}")
async def update_bookmark(bid: str, payload: BookmarkPatch) -> dict:
    sb = _ensure_supabase()
    await _get_group(sb, bid, allow_system=False)  # 系統書籤擋

    update: dict = {}
    if payload.name is not None:
        update["name"] = payload.name
    if payload.sort_order is not None:
        update["sort_order"] = payload.sort_order
    if not update:
        raise HTTPException(400, detail={"error": "nothing_to_update"})

    try:
        res = await asyncio.to_thread(
            lambda: sb.client.table("bookmark_groups")
            .update(update)
            .eq("id", bid)
            .eq("user_label", get_user_label())
            .execute()
        )
    except Exception as e:
        raise HTTPException(409, detail={"error": "bookmark_name_taken", "detail": str(e)})

    if not res.data:
        raise HTTPException(404, detail={"error": "bookmark_not_found"})
    return res.data[0]


@router.delete("/api/bookmarks/{bid}", status_code=204)
async def delete_bookmark(bid: str) -> None:
    sb = _ensure_supabase()
    await _get_group(sb, bid, allow_system=False)  # 系統書籤擋

    # 撈 items 清單,先 unsubscribe 自己的 owner_id(refcount 機制會處理多書籤共有)
    items_res = await asyncio.to_thread(
        lambda: sb.client.table("watchlist_items")
        .select("symbol")
        .eq("group_id", bid)
        .execute()
    )
    owner = _owner_id(bid)
    for r in items_res.data or []:
        try:
            await get_ws_pool().unsubscribe(r["symbol"], owner_id=owner)
        except Exception as e:
            logger.warning("delete bookmark: ws unsubscribe %s failed: %s", r["symbol"], e)

    # 刪 group 會 cascade 刪 watchlist_items
    await asyncio.to_thread(
        lambda: sb.client.table("bookmark_groups")
        .delete()
        .eq("id", bid)
        .eq("user_label", get_user_label())
        .execute()
    )

    # signal_engine refresh — scope=watchlist 改用「自選」書籤,不影響;但保險 refresh
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("delete bookmark: refresh signal_engine failed: %s", e)
    return None


# ---------- 書籤內股票 ----------

class ItemsAdd(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=200)


class ItemsMove(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=200)
    from_group_id: str
    to_group_ids: list[str] = Field(min_length=1, max_length=20)
    op: Literal["move", "copy"]


@router.get("/api/bookmarks/{bid}/items")
async def list_items(bid: str) -> dict:
    sb = _ensure_supabase()
    g = await _get_group(sb, bid)

    if g["is_system"] and g["source_type"] == "top_gainers":
        # 從 top_gainers_snapshot join symbols
        res = await asyncio.to_thread(
            lambda: sb.client.table("top_gainers_snapshot")
            .select("symbol, change_pct, volume_lots, market, rank, captured_at, symbols(name, is_etf)")
            .order("rank")
            .execute()
        )
        out = []
        for r in res.data or []:
            meta = r.get("symbols") or {}
            out.append({
                "symbol": r["symbol"],
                "name": meta.get("name"),
                "market": r.get("market"),
                "is_etf": meta.get("is_etf"),
                "change_pct": r.get("change_pct"),
                "volume_lots": r.get("volume_lots"),
                "captured_at": r.get("captured_at"),
                "added_at": None,
                "note": None,
            })
        return {"items": out, "count": len(out)}

    # User 書籤 — 從 watchlist_items join symbols
    res = await asyncio.to_thread(
        lambda: sb.client.table("watchlist_items")
        .select("symbol, added_at, note, symbols(name, market, is_etf)")
        .eq("group_id", bid)
        .order("added_at", desc=True)
        .execute()
    )
    out = []
    for r in res.data or []:
        meta = r.get("symbols") or {}
        out.append({
            "symbol": r["symbol"],
            "added_at": r.get("added_at"),
            "note": r.get("note"),
            "name": meta.get("name"),
            "market": meta.get("market"),
            "is_etf": meta.get("is_etf"),
        })
    return {"items": out, "count": len(out)}


@router.post("/api/bookmarks/{bid}/items", status_code=201)
async def add_items(bid: str, payload: ItemsAdd) -> dict:
    """批次加股票。已加入的 symbol 略過(不視為錯誤)。"""
    sb = _ensure_supabase()
    await _get_group(sb, bid, allow_system=False)  # 系統書籤擋

    # 驗 symbols 存在
    valid_res = await asyncio.to_thread(
        lambda: sb.client.table("symbols")
        .select("symbol")
        .in_("symbol", payload.symbols)
        .execute()
    )
    valid_set = {r["symbol"] for r in (valid_res.data or [])}
    bad = [s for s in payload.symbols if s not in valid_set]
    if bad:
        raise HTTPException(404, detail={"error": "symbol_not_found", "symbols": bad})

    # 撈當前已在的 symbol 避免 unique violation
    cur_res = await asyncio.to_thread(
        lambda: sb.client.table("watchlist_items")
        .select("symbol")
        .eq("group_id", bid)
        .in_("symbol", payload.symbols)
        .execute()
    )
    already = {r["symbol"] for r in (cur_res.data or [])}
    to_insert = [s for s in payload.symbols if s not in already]

    if to_insert:
        rows = [{"group_id": bid, "symbol": s} for s in to_insert]
        await asyncio.to_thread(
            lambda: sb.client.table("watchlist_items").insert(rows).execute()
        )

    # WS subscribe (owner_id 用 bookmark:{bid}) + backfill CDP
    owner = _owner_id(bid)
    for s in to_insert:
        try:
            await get_ws_pool().subscribe(s, owner_id=owner)
        except RuntimeError as e:
            logger.warning("add items: ws subscribe %s failed: %s", s, e)
        asyncio.create_task(get_cdp_service().backfill_from_fubon(s))

    # signal_engine refresh
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("add items: refresh signal_engine failed: %s", e)

    return {"added": to_insert, "skipped": list(already), "count": len(to_insert)}


@router.delete("/api/bookmarks/{bid}/items/{symbol}", status_code=204)
async def remove_item(bid: str, symbol: str) -> None:
    sb = _ensure_supabase()
    await _get_group(sb, bid, allow_system=False)  # 系統書籤擋

    await asyncio.to_thread(
        lambda: sb.client.table("watchlist_items")
        .delete()
        .eq("group_id", bid)
        .eq("symbol", symbol)
        .execute()
    )

    # WS unsubscribe(只解除這個書籤的 owner、refcount 自動處理其他書籤)
    try:
        await get_ws_pool().unsubscribe(symbol, owner_id=_owner_id(bid))
    except Exception as e:
        logger.warning("remove item: ws unsubscribe %s failed: %s", symbol, e)

    # 注意:cdp.discard 只有在「user 所有書籤都沒這檔」才該做。
    # 簡化:不在 remove_item 做 discard、留給下次 cache eviction 或重啟。
    # (cdp cache 是 lazy-fill、留著無害)

    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("remove item: refresh signal_engine failed: %s", e)
    return None


@router.patch("/api/bookmarks/items/move")
async def move_items(payload: ItemsMove) -> dict:
    """批次跨書籤搬移 / 複製。"""
    sb = _ensure_supabase()

    # 驗 from + to 都不是系統書籤
    await _get_group(sb, payload.from_group_id, allow_system=False)
    for to_id in payload.to_group_ids:
        await _get_group(sb, to_id, allow_system=False)

    # 加到 to_groups(批次 upsert,unique violation 略過)
    for to_id in payload.to_group_ids:
        cur_res = await asyncio.to_thread(
            lambda tid=to_id: sb.client.table("watchlist_items")
            .select("symbol")
            .eq("group_id", tid)
            .in_("symbol", payload.symbols)
            .execute()
        )
        already = {r["symbol"] for r in (cur_res.data or [])}
        to_insert = [s for s in payload.symbols if s not in already]
        if to_insert:
            rows = [{"group_id": to_id, "symbol": s} for s in to_insert]
            await asyncio.to_thread(
                lambda r=rows: sb.client.table("watchlist_items").insert(r).execute()
            )
        owner_to = _owner_id(to_id)
        for s in to_insert:
            try:
                await get_ws_pool().subscribe(s, owner_id=owner_to)
            except RuntimeError as e:
                logger.warning("move: ws sub %s to %s failed: %s", s, to_id, e)
            asyncio.create_task(get_cdp_service().backfill_from_fubon(s))

    # 如果 op="move",從 from_group 移除(refcount 自動處理同檔多書籤)
    if payload.op == "move":
        await asyncio.to_thread(
            lambda: sb.client.table("watchlist_items")
            .delete()
            .eq("group_id", payload.from_group_id)
            .in_("symbol", payload.symbols)
            .execute()
        )
        owner_from = _owner_id(payload.from_group_id)
        for s in payload.symbols:
            try:
                await get_ws_pool().unsubscribe(s, owner_id=owner_from)
            except Exception as e:
                logger.warning("move: ws unsub %s from %s failed: %s", s, payload.from_group_id, e)

    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("move: refresh signal_engine failed: %s", e)

    return {"status": "ok", "op": payload.op, "symbols": payload.symbols,
            "from_group_id": payload.from_group_id, "to_group_ids": payload.to_group_ids}


# ---------- 大漲股(系統書籤)refresh 端點 ----------

@router.post("/api/bookmarks/top-gainers/refresh")
async def trigger_top_gainers_refresh() -> dict:
    """手動觸發大漲股 refresh,給除錯與測試用。"""
    from jobs.top_gainers_scheduler import refresh_top_gainers
    result = await refresh_top_gainers()
    return result
