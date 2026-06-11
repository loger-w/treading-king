"""啟動與匯入共用的訂閱重建。

resync_from_config():
  1. 算 prev_owners(匯入前快照,啟動時為 None)與目前 config 的差集
  2. 只退訂「舊有但新無」、只新訂「新有但舊無」的 (owner, symbol)
     — 重疊不動:全退再重訂會讓 refcount 落 0 → 真打富邦退訂 +
     ring_buffer.discard 清掉最多 30 分鐘 tick 視窗(window 條件全盲),
     還多一段退訂到重訂之間的漏 tick 空窗
  3. signal_engine.refresh_active_signals()
"""
from __future__ import annotations

import logging

from services.fubon_ws import get_ws_pool
from services.local_store import get_local_store
from services.signal_engine import get_signal_engine

logger = logging.getLogger(__name__)


def _desired_owner_map() -> dict[str, set[str]]:
    """目前 config 期望的 owner → symbols(書籤群組 + 監聽清單)。"""
    cfg = get_local_store().config
    desired: dict[str, set[str]] = {}
    for g in cfg.list_groups():
        desired[f"bookmark:{g['id']}"] = {it["symbol"] for it in cfg.list_items(g["id"])}
    desired["monitor_list"] = {m["symbol"] for m in cfg.list_monitor()}
    return desired


async def resync_from_config(prev_owners: dict[str, list[str]] | None = None) -> None:
    pool = get_ws_pool()
    desired = _desired_owner_map()
    prev = {owner: set(syms) for owner, syms in (prev_owners or {}).items()}

    for owner, symbols in prev.items():
        for sym in symbols - desired.get(owner, set()):
            try:
                await pool.unsubscribe(sym, owner)
            except Exception as e:
                logger.warning("resync unsubscribe %s/%s failed: %s", owner, sym, e)

    # 啟動時 prev 為空 = 全部新訂
    for owner, symbols in desired.items():
        for sym in symbols - prev.get(owner, set()):
            try:
                await pool.subscribe(sym, owner_id=owner)
            except RuntimeError as e:
                logger.warning("resync sub %s/%s failed: %s", owner, sym, e)

    await get_signal_engine().refresh_active_signals()


def current_owner_map() -> dict[str, list[str]]:
    """匯入前快照目前 config 的 owner→symbols,供匯入後退訂。"""
    cfg = get_local_store().config
    owners: dict[str, list[str]] = {}
    for g in cfg.list_groups():
        owners[f"bookmark:{g['id']}"] = [it["symbol"] for it in cfg.list_items(g["id"])]
    owners["monitor_list"] = [m["symbol"] for m in cfg.list_monitor()]
    return owners
