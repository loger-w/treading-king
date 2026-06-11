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

    # 0. 先把舊 ws 收乾淨 — 只 pop 不 disconnect 會讓舊連線繼續推 tick（雙倍行情）
    #    且洩漏富邦每帳號 5 條的連線額度
    await pool.disconnect_all()

    # 1. 重 login + init_realtime（重用 fubon_client 的 retry；內部也會 logout 舊 SDK）
    await fubon.init()
    if fubon.status != FubonStatus.OK:
        logger.error("overnight relogin failed: %s", fubon.last_error)
        await alerts.notify_critical(
            "overnight reconnect: fubon relogin failed",
            error=fubon.last_error or "(no detail)",
        )
        return False

    # 2. 重連 + 重訂閱 — 走 pool 公開方法（持 pool._lock,與 subscribe/_reconnect
    #    不競態），成功會把 CIRCUIT_OPEN/DEGRADED 復位
    try:
        await pool.resubscribe_all()
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
