"""8:25 過夜重連 — fubon relogin + ws pool 重訂閱所有 active symbols。

固定 8:25 觸發（盤前 5 分鐘），給 8:30 集合競價開始時 token 是新的。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dtime, timedelta

from services import alerts
from services.fubon_client import FubonStatus, get_fubon
from services.fubon_ws import get_ws_pool

logger = logging.getLogger(__name__)

OVERNIGHT_HOUR = 8
OVERNIGHT_MINUTE = 25


async def run_overnight_reconnect() -> bool:
    """執行一次過夜重連流程。Return True if success."""
    pool = get_ws_pool()
    fubon = get_fubon()

    logger.info("overnight reconnect starting…")

    # 1. 重 login + init_realtime（重用 fubon_client 的 retry）
    await fubon.init()
    if fubon.status != FubonStatus.OK:
        logger.error("overnight relogin failed: %s", fubon.last_error)
        await alerts.notify_critical(
            "overnight reconnect: fubon relogin failed",
            error=fubon.last_error or "(no detail)",
        )
        return False

    # 2. 重連所有 ws connection + 重訂閱
    try:
        for conn_idx in list(pool._ws_handles.keys()):
            symbols = list(pool._conn_subs.get(conn_idx, set()))
            pool._ws_handles.pop(conn_idx, None)  # discard old
            ws = await pool._ensure_handle(conn_idx)
            if ws is None:
                raise RuntimeError(f"conn[{conn_idx}] ensure_handle None")
            if symbols:
                await asyncio.to_thread(
                    ws.subscribe, {"channel": "trades", "symbols": symbols}
                )
                logger.info("conn[%d] re-subscribed %d symbols", conn_idx, len(symbols))
        logger.info("overnight reconnect OK")
        return True
    except Exception as e:
        logger.error("overnight ws reconnect failed: %s", e)
        await alerts.notify_critical(
            "overnight reconnect: ws re-subscribe failed",
            error=f"{type(e).__name__}: {e}",
        )
        return False


def _next_run_at(now: datetime) -> datetime:
    target = datetime.combine(now.date(), dtime(OVERNIGHT_HOUR, OVERNIGHT_MINUTE))
    if now >= target:
        target = target + timedelta(days=1)
    return target


async def overnight_loop() -> None:
    """背景 task — 每天 8:25 觸發 reconnect。lifespan 啟動時 create_task。"""
    while True:
        now = datetime.now()
        next_run = _next_run_at(now)
        sleep_sec = (next_run - now).total_seconds()
        logger.info("overnight: sleeping %.0fs until %s", sleep_sec, next_run.isoformat())
        try:
            await asyncio.sleep(sleep_sec)
        except asyncio.CancelledError:
            logger.info("overnight loop cancelled")
            return
        await run_overnight_reconnect()
