"""大漲股排程 — 盤中每 1 分鐘更新 top_gainers_snapshot。

呼叫富邦 `snapshot.movers` 拿漲跌幅排行(server-side filter:type=COMMONSTOCK +
gt/lt 漲跌幅範圍),篩出符合條件的股票寫進 top_gainers_snapshot 表。所有 user
共用同一份(系統書籤「大漲股」的內容)。

篩選條件(per docs/superpowers/specs/2026-05-20-bookmarks-design.md):
  - Server-side: type=COMMONSTOCK(一般股票,排除 ETF / 特別股)
  - Server-side: 4 < changePercent < 9(避免漲停板;Taiwan 漲停 = 10%,留 1% 緩衝
    防 captured 後股價跳到漲停)
  - Client-side: tradeVolume > 3000 張(富邦 movers API 的 tradeVolume 單位是
    「張/lots」,不是「股」— 經實測 2327 vol=22373, close=572, tradeValue≈12.7B 驗證)
  - Client-side: symbol 是 4 位純數字(雙保險,擋權證、可轉債)
  - Client-side: 須在 symbols 表(FK 限制)
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
from datetime import datetime, time as dtime
from typing import Any

from services.fubon_client import FubonStatus, get_fubon
from services.supabase_client import SupabaseStatus, get_supabase

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
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        logger.warning("top_gainers: supabase not ready, skip")
        return {"status": "skipped", "reason": "supabase_unavailable"}

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
        await _replace_snapshot(sb, [])
        return {"status": "ok", "count": 0}

    # 確認在 symbols 表(top_gainers_snapshot 對 symbols 有 FK、不在則 insert 失敗)
    candidate_symbols = [r[0] for r in raw]
    meta_res = await asyncio.to_thread(
        lambda: sb.client.table("symbols")
        .select("symbol")
        .in_("symbol", candidate_symbols)
        .execute()
    )
    known_set = {m["symbol"] for m in (meta_res.data or [])}

    filtered = [r for r in raw if r[0] in known_set]
    filtered.sort(key=lambda r: -r[1])
    top = filtered[:TOP_N]

    rows = [
        {
            "symbol": s, "change_pct": pct, "volume_lots": vol_lots,
            "market": mkt, "rank": i + 1,
        }
        for i, (s, pct, vol_lots, mkt) in enumerate(top)
    ]
    await _replace_snapshot(sb, rows)
    logger.info("top_gainers: refreshed %d entries", len(rows))
    return {"status": "ok", "count": len(rows)}


async def _replace_snapshot(sb, rows: list[dict]) -> None:
    """整批 replace — 先 delete all,再 insert 新批。"""
    try:
        # supabase-py: delete 需要 filter,用 neq("symbol","") 強制 match-all
        await asyncio.to_thread(
            lambda: sb.client.table("top_gainers_snapshot").delete().neq("symbol", "").execute()
        )
        if rows:
            await asyncio.to_thread(
                lambda: sb.client.table("top_gainers_snapshot").insert(rows).execute()
            )
    except Exception as e:
        logger.error("top_gainers: snapshot replace failed: %s", e)


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
