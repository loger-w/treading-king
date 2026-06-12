"""GET/POST/PATCH/DELETE /api/bookmarks — 書籤群組 + 內含股票 CRUD。

書籤架構(本機 JSON 儲存,ConfigStore):
  - bookmark_groups:user 自訂書籤(系統書籤「大漲股」由 route 動態合成,不入檔)
  - watchlist_items:書籤 → 股票 (group_id, symbol)
  - top_gainers:系統書籤「大漲股」的動態內容(記憶體快取,由排程更新)

WS subscribe 用 owner_id = f"bookmark:{group_id}":
  fubon_ws 的 refcount 是 set-of-owner_id,同檔股票在多書籤時、
  pool 自動處理 — 刪一邊不會 unsubscribe(只要還有其他 group_id 的 owner)。

系統書籤(is_system=true):
  - id 固定為 SYSTEM_TOP_GAINERS_ID,內容來自 top_gainers 快取(不在 watchlist_items)
  - PATCH/DELETE/POST items/DELETE items 一律拒絕(403)
  - item 的 name/market/is_etf 由 MarketCache.get_symbol 即時補回
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from routes._item_enrich import enrich_item
from services.cdp import get_cdp_service
from services.fubon_ws import get_ws_pool
from services.local_store import get_local_store

logger = logging.getLogger(__name__)
router = APIRouter()

# 系統書籤「大漲股」的固定 id — 不入檔,由 route 動態合成。
SYSTEM_TOP_GAINERS_ID = "system-top-gainers"
SYSTEM_TOP_GAINERS_NAME = "大漲股"
SYSTEM_TOP_GAINERS_SORT_ORDER = 100


# ---------- helpers ----------

def _owner_id(group_id: str) -> str:
    """WSPool owner id naming — 每個書籤獨立 owner,refcount 自動處理多書籤共有。"""
    return f"bookmark:{group_id}"


# ---------- CDP backfill 佇列 ----------
# 批次加股票若每檔各開 task,backfill 內的 historical limiter(1 req/s)是阻塞式
# 等 token、又包在 to_thread,200 檔會佔滿 asyncio 共用執行緒池,卡住全後端的
# 富邦呼叫。改成單一 worker 序列消化;pending set 同時去重(跨群組複製同檔
# 只 backfill 一次)。worker reference 存模組變數,避免 task 被 GC。
_backfill_queue: asyncio.Queue[str] | None = None
_backfill_pending: set[str] = set()
_backfill_worker: asyncio.Task | None = None


def _enqueue_backfill(symbol: str) -> None:
    global _backfill_queue, _backfill_pending, _backfill_worker
    loop = asyncio.get_running_loop()
    # worker 綁 event loop — 測試等情境每次起新 loop 時,舊 worker 作廢重建
    if (_backfill_worker is None or _backfill_worker.done()
            or _backfill_worker.get_loop() is not loop):
        _backfill_queue = asyncio.Queue()
        _backfill_pending = set()
        _backfill_worker = loop.create_task(_backfill_worker_loop(_backfill_queue))
    if symbol in _backfill_pending:
        return
    _backfill_pending.add(symbol)
    _backfill_queue.put_nowait(symbol)


async def _backfill_worker_loop(queue: asyncio.Queue[str]) -> None:
    while True:
        symbol = await queue.get()
        try:
            await get_cdp_service().backfill_from_fubon(symbol)
        except Exception as e:  # noqa: BLE001 — 單檔失敗不影響佇列後續
            logger.warning("cdp backfill %s failed: %s", symbol, e)
        finally:
            _backfill_pending.discard(symbol)


def _require_user_group(group_id: str) -> dict:
    """取 user 書籤 + 擋系統書籤。系統 id → 403;不存在 → 404。"""
    if group_id == SYSTEM_TOP_GAINERS_ID:
        raise HTTPException(403, detail={"error": "system_bookmark_readonly"})
    g = next((x for x in get_local_store().config.list_groups() if x["id"] == group_id), None)
    if g is None:
        raise HTTPException(404, detail={"error": "bookmark_not_found"})
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
    store = get_local_store()
    groups = store.config.list_groups()
    counts = store.config.item_counts()

    # user 書籤依 sort_order 排序(對齊舊行為:is_system 先、再 sort_order)
    user_sorted = sorted(groups, key=lambda g: g.get("sort_order", 0))
    out = []
    for g in user_sorted:
        out.append({
            "id": g["id"],
            "name": g["name"],
            "sort_order": g.get("sort_order", 0),
            "is_system": False,
            "source_type": g.get("source_type"),
            "count": counts.get(g["id"], 0),
        })

    # 系統書籤「大漲股」放最後(對齊舊 order by is_system)
    out.append({
        "id": SYSTEM_TOP_GAINERS_ID,
        "name": SYSTEM_TOP_GAINERS_NAME,
        "sort_order": SYSTEM_TOP_GAINERS_SORT_ORDER,
        "is_system": True,
        "source_type": "top_gainers",
        "count": store.market.top_gainers_count(),
    })
    return {"groups": out, "count": len(out)}


@router.post("/api/bookmarks", status_code=201)
async def create_bookmark(payload: BookmarkCreate) -> dict:
    store = get_local_store()
    groups = store.config.list_groups()
    if any(g["name"] == payload.name for g in groups):
        raise HTTPException(409, detail={"error": "bookmark_name_taken"})
    next_order = (max((g.get("sort_order", 0) for g in groups), default=0) + 1) if groups else 1
    g = store.config.create_group(payload.name, sort_order=next_order)
    return g


class GroupsReorder(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


# 注意:必須宣告在 PATCH /api/bookmarks/{bid} 之前 — 否則 "reorder" 被當成 {bid}
@router.patch("/api/bookmarks/reorder")
async def reorder_bookmarks(payload: GroupsReorder) -> dict:
    """批次重排 user 書籤群組。ids 必須與現有 user 群組集合完全一致。"""
    if not get_local_store().config.reorder_groups(payload.ids):
        raise HTTPException(400, detail={"error": "groups_mismatch"})
    return {"status": "ok"}


@router.patch("/api/bookmarks/{bid}")
async def update_bookmark(bid: str, payload: BookmarkPatch) -> dict:
    store = get_local_store()
    _require_user_group(bid)  # 系統書籤擋 + 存在性

    if payload.name is None and payload.sort_order is None:
        raise HTTPException(400, detail={"error": "nothing_to_update"})

    if payload.name is not None:
        if any(g["name"] == payload.name and g["id"] != bid
               for g in store.config.list_groups()):
            raise HTTPException(409, detail={"error": "bookmark_name_taken"})

    g = store.config.update_group(bid, name=payload.name, sort_order=payload.sort_order)
    if g is None:
        raise HTTPException(404, detail={"error": "bookmark_not_found"})
    return g


@router.delete("/api/bookmarks/{bid}", status_code=204)
async def delete_bookmark(bid: str) -> None:
    store = get_local_store()
    _require_user_group(bid)  # 系統書籤擋 + 存在性

    # 先 unsubscribe 自己的 owner_id(refcount 機制會處理多書籤共有)
    owner = _owner_id(bid)
    for it in store.config.list_items(bid):
        try:
            await get_ws_pool().unsubscribe(it["symbol"], owner_id=owner)
        except Exception as e:
            logger.warning("delete bookmark: ws unsubscribe %s failed: %s", it["symbol"], e)

    # 刪 group 連帶刪 items(ConfigStore.delete_group 內部處理)
    store.config.delete_group(bid)

    # 不 refresh signal_engine:書籤不在訊號評估範圍(monitor_list),refresh 多餘且慢
    # (會對 monitor 全部 symbol 序列重算 CDP)。
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
    store = get_local_store()

    if bid == SYSTEM_TOP_GAINERS_ID:
        # 從 top_gainers 快取補 symbols metadata;market 仍用大漲股 row 的值(來源較新)
        out = []
        for r in store.market.get_top_gainers():
            out.append({
                **enrich_item({"symbol": r["symbol"]}, store.market),
                "market": r.get("market"),
                "change_pct": r.get("change_pct"),
                "volume_lots": r.get("volume_lots"),
                "captured_at": r.get("captured_at"),
                "added_at": None,
                "note": None,
            })
        return {"items": out, "count": len(out)}

    g = _require_user_group(bid)  # noqa: F841 — 存在性檢查(系統 id 不會到這)

    # User 書籤 — 有 position 的照 position 升冪在前(自訂順序);
    # 舊資料沒有 position(不遷移),fallback added_at 新→舊接在後面
    items = store.config.list_items(bid)
    with_pos = sorted((it for it in items if it.get("position") is not None),
                      key=lambda it: it["position"])
    no_pos = sorted((it for it in items if it.get("position") is None),
                    key=lambda it: it.get("added_at") or "", reverse=True)
    items = with_pos + no_pos
    out = [
        enrich_item(
            {"symbol": it["symbol"], "added_at": it.get("added_at"), "note": it.get("note")},
            store.market,
        )
        for it in items
    ]
    return {"items": out, "count": len(out)}


@router.post("/api/bookmarks/{bid}/items", status_code=201)
async def add_items(bid: str, payload: ItemsAdd) -> dict:
    """批次加股票。已加入的 symbol 略過(不視為錯誤)。"""
    store = get_local_store()
    _require_user_group(bid)  # 系統書籤擋 + 存在性

    # 驗 symbols 存在(cache 已載入才驗;未載入則放行,best-effort)
    if store.market.symbols_loaded():
        bad = [s for s in payload.symbols if not store.market.has_symbol(s)]
        if bad:
            raise HTTPException(404, detail={"error": "symbol_not_found", "symbols": bad})

    # 撈當前已在的 symbol(同 group 不重複)
    already = {it["symbol"] for it in store.config.list_items(bid)
               if it["symbol"] in set(payload.symbols)}
    to_insert = [s for s in payload.symbols if s not in already]

    # WS subscribe (owner_id 用 bookmark:{bid}) + backfill CDP。
    # subscribe 失敗(pool 容量滿)就回滾 store 並回報 failed — 否則書籤
    # 看得到股票、卻整個 session 收不到即時行情(靜默不一致,同 monitor_list
    # 的「訂閱失敗就不寫 store」原則)。
    owner = _owner_id(bid)
    added: list[str] = []
    failed: list[str] = []
    for s in to_insert:
        store.config.add_item(bid, s)
        try:
            await get_ws_pool().subscribe(s, owner_id=owner)
        except RuntimeError as e:
            logger.warning("add items: ws subscribe %s failed: %s", s, e)
            store.config.remove_item(bid, s)
            failed.append(s)
            continue
        added.append(s)
        _enqueue_backfill(s)

    # 不 refresh signal_engine:同 remove_item — 書籤股票不在訊號評估範圍
    # (monitor_list),refresh 只會白白重算 monitor 的 CDP、拖慢加入。
    return {"added": added, "skipped": list(already), "failed": failed, "count": len(added)}


@router.delete("/api/bookmarks/{bid}/items/{symbol}", status_code=204)
async def remove_item(bid: str, symbol: str) -> None:
    store = get_local_store()
    _require_user_group(bid)  # 系統書籤擋 + 存在性

    store.config.remove_item(bid, symbol)

    # WS unsubscribe(只解除這個書籤的 owner、refcount 自動處理其他書籤)
    try:
        await get_ws_pool().unsubscribe(symbol, owner_id=_owner_id(bid))
    except Exception as e:
        logger.warning("remove item: ws unsubscribe %s failed: %s", symbol, e)

    # 注意:cdp.discard 只有在「user 所有書籤都沒這檔」才該做。
    # 簡化:不在 remove_item 做 discard、留給下次 cache eviction 或重啟。
    # (cdp cache 是 lazy-fill、留著無害)

    # 不 refresh signal_engine:訊號評估範圍是 monitor_list,書籤股票增刪不改
    # monitor_list。refresh 會對 monitor 全部 symbol 序列 await cdp.get()(實測
    # 幾秒)→ 純粹拖慢刪除、對訊號毫無影響。
    return None


class ItemsReorder(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=200)


@router.patch("/api/bookmarks/{bid}/items/reorder")
async def reorder_bookmark_items(bid: str, payload: ItemsReorder) -> dict:
    """以 symbols 的順序重排書籤內股票。集合與現況不符回 400(過期請求不蓋資料)。"""
    _require_user_group(bid)  # 系統書籤擋 + 存在性
    if not get_local_store().config.reorder_items(bid, payload.symbols):
        raise HTTPException(400, detail={"error": "items_mismatch"})
    return {"status": "ok"}


@router.patch("/api/bookmarks/items/move")
async def move_items(payload: ItemsMove) -> dict:
    """批次跨書籤搬移 / 複製。"""
    store = get_local_store()

    # 驗 from + to 都不是系統書籤 + 存在
    _require_user_group(payload.from_group_id)
    for to_id in payload.to_group_ids:
        _require_user_group(to_id)

    # 加到 to_groups(同 group 已在則略過)。subscribe 失敗回滾該 group 的寫入
    # 並回報 failed — 同 add_items,避免「股票在書籤裡但沒行情」的靜默不一致。
    failed: list[dict] = []
    ok_symbols: set[str] = set()  # 至少在一個目的群組成功(原本已在也算)
    for to_id in payload.to_group_ids:
        already = {it["symbol"] for it in store.config.list_items(to_id)
                   if it["symbol"] in set(payload.symbols)}
        ok_symbols.update(already)
        to_insert = [s for s in payload.symbols if s not in already]
        owner_to = _owner_id(to_id)
        for s in to_insert:
            store.config.add_item(to_id, s)
            try:
                await get_ws_pool().subscribe(s, owner_id=owner_to)
            except RuntimeError as e:
                logger.warning("move: ws sub %s to %s failed: %s", s, to_id, e)
                store.config.remove_item(to_id, s)
                failed.append({"symbol": s, "group_id": to_id})
                continue
            ok_symbols.add(s)
            _enqueue_backfill(s)

    # 如果 op="move",從 from_group 移除(refcount 自動處理同檔多書籤)。
    # 所有目的群組都失敗的 symbol 留在來源 — 否則兩頭皆空、股票直接消失。
    if payload.op == "move":
        owner_from = _owner_id(payload.from_group_id)
        moved = [s for s in payload.symbols if s in ok_symbols]
        for s in moved:
            store.config.remove_item(payload.from_group_id, s)
        for s in moved:
            try:
                await get_ws_pool().unsubscribe(s, owner_id=owner_from)
            except Exception as e:
                logger.warning("move: ws unsub %s from %s failed: %s", s, payload.from_group_id, e)

    # 不 refresh signal_engine:書籤股票搬移不改 monitor_list,refresh 多餘且慢。
    return {"status": "ok", "op": payload.op, "symbols": payload.symbols,
            "from_group_id": payload.from_group_id, "to_group_ids": payload.to_group_ids,
            "failed": failed}


# ---------- 大漲股(系統書籤)refresh 端點 ----------

@router.post("/api/bookmarks/top-gainers/refresh")
async def trigger_top_gainers_refresh() -> dict:
    """手動觸發大漲股 refresh,給除錯與測試用。"""
    from jobs.top_gainers_scheduler import refresh_top_gainers
    result = await refresh_top_gainers()
    return result
