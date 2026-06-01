"""大漲股排程 — 盤中每 1 分鐘更新 top_gainers 記憶體快取。

呼叫富邦 `snapshot.movers` 拿漲跌幅排行(server-side filter:type=COMMONSTOCK +
gt/lt 漲跌幅範圍),篩出符合條件的股票整批 replace 進 market_cache 的 top_gainers
記憶體快取(系統書籤「大漲股」的內容)。

篩選條件(per docs/superpowers/specs/2026-05-20-bookmarks-design.md):
  - Server-side: type=COMMONSTOCK(一般股票,排除 ETF / 特別股)
  - Server-side: 4 < changePercent < 9(避免漲停板;Taiwan 漲停 = 10%,留 1% 緩衝
    防 captured 後股價跳到漲停)
  - Client-side: tradeVolume > 3000 張(富邦 movers API 的 tradeVolume 單位是
    「張/lots」,不是「股」— 經實測 2327 vol=22373, close=572, tradeValue≈12.7B 驗證)
  - Client-side: symbol 是 4 位純數字(雙保險,擋權證、可轉債)
  - Client-side: 須在本機 symbols 快取(快取未載入時不過濾)
  - 依 changePercent desc 取前 50

時間規則:
  - 9:00 - 13:30 盤中跑,每 1 分鐘
  - 8:30-9:00 試撮不跑(跟 signal_engine 一致、避免 indicative tick 帶來雜訊)
  - 週末不跑

rate limit: 每次 2 個 API call(TSE + OTC),每分鐘 2 次 = 完全沒壓力。
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

logger = logging.getLogger(__name__)

# 篩選參數
MIN_CHANGE_PCT = 4.0
MAX_CHANGE_PCT = 9.0   # 排除漲停板 — Taiwan 漲停 = 10%,留 1% 緩衝
MIN_VOLUME_LOTS = 3_000  # 3000 張 — 富邦 movers API tradeVolume 單位是張
TOP_N = 50
SYMBOL_RE = re.compile(r"^\d{4}$")  # 4 位純數字

# 時間規則 — 跟 signal_engine 對齊
MARKET_OPEN  = dtime(9, 0)
MARKET_CLOSE = dtime(13, 30)
SCHEDULE_INTERVAL_S = 60.0

# WS subscription owner — refcount 機制下,同檔股票若同時也在 user 自選,
# unsubscribe 此 owner 不會影響 user 的訂閱。
TOP_GAINERS_OWNER = "system:top_gainers"

# Module-level state — 上一輪 refresh 訂閱的 symbol set,用來 diff
_subscribed_symbols: set[str] = set()


def _in_market_hours(now: datetime | None = None) -> bool:
    """盤中(9:00-13:30,週一到週五)才 refresh。週末 + 試撮 + 盤後皆 false。"""
    if now is None:
        now = datetime.now()
    if now.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    t = now.time()
    return MARKET_OPEN <= t < MARKET_CLOSE


def _fetch_market_movers(market: str) -> list[dict[str, Any]]:
    """同步 wrapper — 呼叫富邦 snapshot.movers,server-side 已過濾 ETF + 漲跌幅範圍。"""
    fubon = get_fubon()
    if fubon.status != FubonStatus.OK or fubon.sdk is None:
        logger.warning("top_gainers: fubon SDK not ready, skip market=%s", market)
        return []
    try:
        result = fubon.sdk.marketdata.rest_client.stock.snapshot.movers(
            market=market,
            direction="up",
            change="percent",
            type="COMMONSTOCK",       # 排除 ETF + 特別股
            gt=MIN_CHANGE_PCT,        # > 4%
            lt=MAX_CHANGE_PCT,        # < 9% 避免接近漲停
        )
        return list(result.get("data") or [])
    except Exception as e:
        logger.warning("top_gainers: movers(market=%s) failed: %s", market, e)
        return []


async def refresh_top_gainers() -> dict:
    """執行一次 refresh — 拉漲跌幅榜 + 過濾 + 整批 replace。"""
    raw: list[tuple[str, float, int, str]] = []  # (symbol, change_pct, volume_lots, market)
    for market in ("TSE", "OTC"):
        items = await asyncio.to_thread(_fetch_market_movers, market)
        for it in items:
            symbol = (it.get("symbol") or "").strip()
            pct = it.get("changePercent")
            vol = it.get("tradeVolume")
            if not symbol or pct is None or vol is None:
                continue
            if vol <= MIN_VOLUME_LOTS:
                continue
            if not SYMBOL_RE.match(symbol):
                continue  # 雙保險:擋權證 / 可轉債(非 4 位純數字)
            raw.append((symbol, float(pct), int(vol), market))

    if not raw:
        logger.info("top_gainers: nothing passes filters")
        # 仍然清空 snapshot(讓前端看到空 state、而不是 stale)
        await _replace_snapshot([])
        await _sync_subscriptions(set())
        return {"status": "ok", "count": 0}

    # 只保留本機 symbols 快取裡有的(擋雜訊 / 未知代號)。
    # 快取尚未載入時保留全部 — 不拿空快取把所有候選砍光。
    store = get_local_store()
    if store.market.symbols_loaded():
        raw = [r for r in raw if store.market.has_symbol(r[0])]

    raw.sort(key=lambda r: -r[1])
    top = raw[:TOP_N]

    captured_at = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "symbol": s, "change_pct": pct, "volume_lots": vol_lots,
            "market": mkt, "rank": i + 1, "captured_at": captured_at,
        }
        for i, (s, pct, vol_lots, mkt) in enumerate(top)
    ]
    await _replace_snapshot(rows)
    await _sync_subscriptions({r["symbol"] for r in rows})
    logger.info("top_gainers: refreshed %d entries", len(rows))
    return {"status": "ok", "count": len(rows)}


async def _sync_subscriptions(new_set: set[str]) -> None:
    """Diff 上輪訂閱 vs 本輪新 set,對 WSPool 增訂 / 退訂。

    refcount 機制保證:若新增 symbol 同時在 user 自選,只是多加一個 owner;
    退訂同理 — 不影響 user 自選的 owner。
    """
    global _subscribed_symbols
    pool = get_ws_pool()
    to_add = new_set - _subscribed_symbols
    to_remove = _subscribed_symbols - new_set
    for s in to_add:
        try:
            await pool.subscribe(s, owner_id=TOP_GAINERS_OWNER)
        except RuntimeError as e:
            logger.warning("top_gainers: ws sub %s failed: %s", s, e)
    for s in to_remove:
        try:
            await pool.unsubscribe(s, owner_id=TOP_GAINERS_OWNER)
        except Exception as e:
            logger.warning("top_gainers: ws unsub %s failed: %s", s, e)
    if to_add or to_remove:
        logger.info("top_gainers: ws sync +%d -%d (total=%d)",
                    len(to_add), len(to_remove), len(new_set))
    _subscribed_symbols = new_set


async def _replace_snapshot(rows: list[dict]) -> None:
    """整批 replace 記憶體快取 — 空 list 即清空,讓前端看到空 state 而非 stale。"""
    get_local_store().market.replace_top_gainers(rows)


async def top_gainers_loop() -> None:
    """背景 task — 盤中每 1 分鐘 refresh。lifespan 啟動時 create_task。"""
    logger.info("top_gainers loop started")
    while True:
        try:
            if _in_market_hours():
                await refresh_top_gainers()
            await asyncio.sleep(SCHEDULE_INTERVAL_S)
        except asyncio.CancelledError:
            logger.info("top_gainers loop cancelled")
            return
        except Exception as e:
            logger.exception("top_gainers loop iteration failed: %s", e)
            await asyncio.sleep(SCHEDULE_INTERVAL_S)
