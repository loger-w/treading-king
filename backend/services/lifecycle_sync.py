"""啟動與匯入共用的訂閱重建。

resync_from_config():
  1. 退訂 prev_owners 列出的舊訂閱(匯入時用,啟動時 prev_owners 為 None)
  2. 依目前 config 訂閱所有書籤股票(owner=f"bookmark:{gid}")+ 監聽清單(owner="monitor_list")
  3. signal_engine.refresh_active_signals()
"""
from __future__ import annotations

import logging

from services.fubon_ws import get_ws_pool
from services.local_store import get_local_store
from services.signal_engine import get_signal_engine

logger = logging.getLogger(__name__)


async def resync_from_config(prev_owners: dict[str, list[str]] | None = None) -> None:
    pool = get_ws_pool()
    for owner, symbols in (prev_owners or {}).items():
        for sym in symbols:
            try:
                await pool.unsubscribe(sym, owner)
            except Exception as e:
                logger.warning("resync unsubscribe %s/%s failed: %s", owner, sym, e)
    cfg = get_local_store().config
    for g in cfg.list_groups():
        owner = f"bookmark:{g['id']}"
        for it in cfg.list_items(g["id"]):
            try:
                await pool.subscribe(it["symbol"], owner_id=owner)
            except RuntimeError as e:
                logger.warning("resync sub %s failed: %s", it["symbol"], e)
    for m in cfg.list_monitor():
        try:
            await pool.subscribe(m["symbol"], owner_id="monitor_list")
        except RuntimeError as e:
            logger.warning("resync monitor sub %s failed: %s", m["symbol"], e)
    await get_signal_engine().refresh_active_signals()


def current_owner_map() -> dict[str, list[str]]:
    """匯入前快照目前 config 的 owner→symbols,供匯入後退訂。"""
    cfg = get_local_store().config
    owners: dict[str, list[str]] = {}
    for g in cfg.list_groups():
        owners[f"bookmark:{g['id']}"] = [it["symbol"] for it in cfg.list_items(g["id"])]
    owners["monitor_list"] = [m["symbol"] for m in cfg.list_monitor()]
    return owners
