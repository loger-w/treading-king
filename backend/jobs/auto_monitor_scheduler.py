"""自動監聽排程 — 盤中每 1 分鐘篩選熱門股、訂閱 WS、納入 signal engine。

取代原 top_gainers_scheduler。篩選條件:
  - Server-side: type=COMMONSTOCK, 3 < changePercent < 9
  - Client-side: amplitude > 3%, tradeVolume > 3000, 4 位純數字, 在 symbols 快取
  - 滾動只加不減(當天內),上限 100 檔
  - 收盤後退訂 + 清 signal engine auto set

API rate: 每分鐘 2 個 REST call(TSE + OTC),無壓力。
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, time as dtime, timezone
from typing import Any

from services.fubon_client import FubonStatus, get_fubon
from services.fubon_ws import get_ws_pool
from services.local_store import get_local_store
from services.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)

MIN_CHANGE_PCT = 3.0
MAX_CHANGE_PCT = 9.0
MIN_AMP_PCT = 3.0
MIN_VOLUME_LOTS = 3_000
AUTO_MONITOR_CAP = 100
SYMBOL_RE = re.compile(r"^\d{4}$")

MARKET_OPEN = dtime(9, 0)
MARKET_CLOSE = dtime(13, 30)
SCHEDULE_INTERVAL_S = 60.0

AUTO_MONITOR_OWNER = "auto_monitor"

_auto_set: set[str] = set()


def _in_market_hours(now: datetime | None = None) -> bool:
    if now is None:
        now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return MARKET_OPEN <= t < MARKET_CLOSE


def _amplitude_pct(high: float | None, low: float | None) -> float:
    if not high or not low or low <= 0:
        return 0.0
    return (high - low) / low * 100.0


def _passes_screen(item: dict) -> bool:
    symbol = (item.get("symbol") or "").strip()
    pct = item.get("changePercent")
    vol = item.get("tradeVolume")
    high = item.get("highPrice")
    low = item.get("lowPrice")
    if not symbol or pct is None or vol is None:
        return False
    if not (MIN_CHANGE_PCT < pct < MAX_CHANGE_PCT):
        return False
    if _amplitude_pct(high, low) <= MIN_AMP_PCT:
        return False
    if vol <= MIN_VOLUME_LOTS:
        return False
    if not SYMBOL_RE.match(symbol):
        return False
    return True


def _fetch_market_movers(market: str) -> list[dict[str, Any]] | None:
    fubon = get_fubon()
    if fubon.status != FubonStatus.OK or fubon.sdk is None:
        logger.warning("auto_monitor: fubon SDK not ready, skip market=%s", market)
        return None
    try:
        get_rate_limiter().acquire()
        result = fubon.sdk.marketdata.rest_client.stock.snapshot.movers(
            market=market,
            direction="up",
            change="percent",
            type="COMMONSTOCK",
            gt=MIN_CHANGE_PCT,
            lt=MAX_CHANGE_PCT,
        )
        return list(result.get("data") or [])
    except Exception as e:
        logger.warning("auto_monitor: movers(market=%s) failed: %s", market, e)
        return None


async def refresh_auto_monitor() -> dict:
    """執行一次 refresh — 拉漲跌幅榜 + 篩選 + 增量訂閱。"""
    global _auto_set

    if len(_auto_set) >= AUTO_MONITOR_CAP:
        return {"status": "ok", "count": len(_auto_set), "new": 0, "reason": "cap_reached"}

    raw: list[tuple[str, float, float, int, str]] = []
    ok_markets = 0
    for market in ("TSE", "OTC"):
        items = await asyncio.to_thread(_fetch_market_movers, market)
        if items is None:
            continue
        ok_markets += 1
        for it in items:
            if not _passes_screen(it):
                continue
            symbol = it["symbol"].strip()
            raw.append((
                symbol,
                float(it["changePercent"]),
                _amplitude_pct(it.get("highPrice"), it.get("lowPrice")),
                int(it["tradeVolume"]),
                market,
            ))

    if ok_markets == 0:
        logger.warning("auto_monitor: all movers fetches failed, keep previous set")
        return {"status": "error", "count": len(_auto_set), "new": 0}

    store = get_local_store()
    if store.market.symbols_loaded():
        raw = [r for r in raw if store.market.has_symbol(r[0])]

    raw.sort(key=lambda r: -r[1])

    new_symbols: set[str] = set()
    remaining_cap = AUTO_MONITOR_CAP - len(_auto_set)
    for symbol, pct, amp, vol, mkt in raw:
        if symbol in _auto_set:
            continue
        if len(new_symbols) >= remaining_cap:
            break
        new_symbols.add(symbol)

    if not new_symbols:
        _update_snapshot(raw)
        return {"status": "ok", "count": len(_auto_set), "new": 0}

    pool = get_ws_pool()
    failed: set[str] = set()
    for s in new_symbols:
        try:
            await pool.subscribe(s, owner_id=AUTO_MONITOR_OWNER)
        except RuntimeError as e:
            failed.add(s)
            logger.warning("auto_monitor: ws sub %s failed: %s", s, e)
    subscribed = new_symbols - failed

    if subscribed:
        try:
            from services.signal_engine import get_signal_engine
            await get_signal_engine().add_auto_symbols(subscribed)
        except Exception as e:
            logger.warning("auto_monitor: add_auto_symbols failed: %s", e)

    _auto_set |= subscribed

    captured_at = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "symbol": s, "change_pct": pct, "amplitude_pct": amp,
            "volume_lots": vol, "market": mkt, "rank": i + 1,
            "captured_at": captured_at,
        }
        for i, (s, pct, amp, vol, mkt) in enumerate(raw)
        if s in _auto_set
    ]
    store.market.replace_auto_monitor(rows)

    logger.info("auto_monitor: +%d new (total=%d, failed=%d)",
                len(subscribed), len(_auto_set), len(failed))
    return {"status": "ok", "count": len(_auto_set), "new": len(subscribed)}


def _update_snapshot(raw: list[tuple[str, float, float, int, str]]) -> None:
    captured_at = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "symbol": s, "change_pct": pct, "amplitude_pct": amp,
            "volume_lots": vol, "market": mkt, "rank": i + 1,
            "captured_at": captured_at,
        }
        for i, (s, pct, amp, vol, mkt) in enumerate(raw)
        if s in _auto_set
    ]
    get_local_store().market.replace_auto_monitor(rows)


async def _cleanup_after_market() -> None:
    """收盤後退訂 WS + 清 signal engine auto set。snapshot 保留供盤後瀏覽。"""
    global _auto_set
    pool = get_ws_pool()
    for s in list(_auto_set):
        try:
            await pool.unsubscribe(s, owner_id=AUTO_MONITOR_OWNER)
        except Exception as e:
            logger.warning("auto_monitor: ws unsub %s failed: %s", s, e)
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().clear_auto_symbols()
    except Exception as e:
        logger.warning("auto_monitor: clear_auto_symbols failed: %s", e)
    count = len(_auto_set)
    _auto_set = set()
    if count:
        logger.info("auto_monitor: after-market cleanup, unsubscribed %d", count)


async def auto_monitor_loop() -> None:
    """背景 task — 盤中每 1 分鐘 refresh。"""
    logger.info("auto_monitor loop started")
    was_in_market = False
    while True:
        try:
            if _in_market_hours():
                was_in_market = True
                await refresh_auto_monitor()
            elif was_in_market:
                was_in_market = False
                await _cleanup_after_market()
            await asyncio.sleep(SCHEDULE_INTERVAL_S)
        except asyncio.CancelledError:
            logger.info("auto_monitor loop cancelled")
            return
        except Exception as e:
            logger.exception("auto_monitor loop iteration failed: %s", e)
            await asyncio.sleep(SCHEDULE_INTERVAL_S)
