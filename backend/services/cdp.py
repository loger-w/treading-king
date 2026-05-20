"""CDP 5 線 — 從昨日 OHLC 算 5 個值，盤中為固定值。

公式（台股 / 港股慣例）：
  CDP = (H + L + 2C) / 4
  AH (最高值) = CDP + (H − L)
  NH (近高值) = 2 × CDP − L
  NL (近低值) = 2 × CDP − H
  AL (最低值) = CDP − (H − L)
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, timedelta
from typing import Any, Literal, TypedDict

logger = logging.getLogger(__name__)


# 台股 tick ladder（價格 < upper 時用對應 tick）
_TICK_LADDER = (
    (10.0,           0.01),
    (50.0,           0.05),
    (100.0,          0.10),
    (500.0,          0.50),
    (1000.0,         1.00),
    (float("inf"),   5.00),
)


def tick_size(price: float) -> float:
    """回傳 price 對應的台股最小升降單位。"""
    for upper, tick in _TICK_LADDER:
        if price < upper:
            return tick
    return 5.00  # unreachable


def round_to_tick_tw(price: float, direction: Literal["up", "down", "nearest"]) -> float:
    """對齊台股 tick。

    direction:
      - "up":      向上取（ceil） — 阻力位（AH/NH）
      - "down":    向下取（floor） — 支撐位（NL/AL）
      - "nearest": 取最近（Python round，半值偶數捨入） — 中線（CDP）
    """
    tick = tick_size(price)
    units = price / tick
    if direction == "up":
        rounded = math.ceil(units) * tick
    elif direction == "down":
        rounded = math.floor(units) * tick
    else:  # nearest
        rounded = round(units) * tick
    # 浮點誤差修正（tick 0.05 / 0.1 / 0.5 都會踩到）
    return round(rounded, 2)


class CdpLevels(TypedDict):
    ah: float
    nh: float
    cdp: float
    nl: float
    al: float
    as_of_date: str  # 昨日 ISO date string
    prev_close: float  # 昨日收盤 — 日內漲幅算式分母


def compute_cdp(h: float, l: float, c: float) -> dict[str, float]:
    """純函式 — 給 OHLC 算 5 線值，5 條全部對齊到最近的台股 tick。

    每個價位的 tick 不同（< 10 / 10-50 / 50-100 / 100-500 / 500-1000 / >=1000
    分別是 0.01 / 0.05 / 0.10 / 0.50 / 1 / 5），round_to_tick_tw 內部用
    tick_size(price) 取當下價位的 tick 再 round。

    語意：chart 上看到的 CDP 線數字 = 會 eq 觸發的成交價，1:1 對應。
    """
    cdp_raw = (h + l + 2 * c) / 4
    ah_raw = cdp_raw + (h - l)
    nh_raw = 2 * cdp_raw - l
    nl_raw = 2 * cdp_raw - h
    al_raw = cdp_raw - (h - l)
    return {
        "ah":  round_to_tick_tw(ah_raw,  "nearest"),
        "nh":  round_to_tick_tw(nh_raw,  "nearest"),
        "cdp": round_to_tick_tw(cdp_raw, "nearest"),
        "nl":  round_to_tick_tw(nl_raw,  "nearest"),
        "al":  round_to_tick_tw(al_raw,  "nearest"),
    }


class CdpService:
    """In-memory cache + 從 daily_ohlc 抓昨日 OHLC 算 5 線。

    daily_ohlc 寫入是「lazy backfill」式（watchlist add / CDP route fallback），
    沒有自動每日批次更新。為避免「同一個 symbol 加入後就再也不更新」造成的
    stale state（觀察到 6531 缺 2026-05-12 ⇒ CDP 永遠在算前天 OHLC），
    get() 在每天首次呼叫時自動觸發一次 backfill_from_fubon。
    """

    def __init__(self) -> None:
        self._cache: dict[str, CdpLevels] = {}
        self._lock = asyncio.Lock()
        # 每個 symbol 上次嘗試 backfill 的日期（本地日，per process）
        # 用來確保每天最多 backfill 1 次，避免重複打富邦 historical API
        self._last_backfill_attempt: dict[str, date] = {}

    async def get(self, symbol: str) -> CdpLevels | None:
        """每天首次呼叫自動 backfill 一次，確保 daily_ohlc 最新。

        backfill_from_fubon 是 idempotent upsert + refresh cache，安全重複
        呼叫。如果富邦暫不可用 (network / quota)，fallback 讀既有 daily_ohlc
        (可能是 stale 但有總比沒有好)。
        """
        today = date.today()  # 本地時區（台股 UTC+8，後端在 Taiwan 跑無誤）
        if self._last_backfill_attempt.get(symbol) != today:
            self._last_backfill_attempt[symbol] = today
            await self.backfill_from_fubon(symbol)

        if symbol not in self._cache:
            # 富邦 backfill 失敗 fallback 讀既有 daily_ohlc
            await self.refresh(symbol)

        return self._cache.get(symbol)

    async def refresh(self, symbol: str) -> None:
        """從 daily_ohlc 抓最近一筆 OHLC → 算 → 進 cache。"""
        from services.supabase_client import get_supabase

        sb = get_supabase()
        if sb.client is None:
            logger.warning("cdp.refresh: supabase not ready")
            return

        # 抓最近一筆 daily_ohlc（昨日）
        res = (
            sb.client.table("daily_ohlc")
            .select("date, high, low, close")
            .eq("symbol", symbol)
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            logger.info("cdp.refresh: no daily_ohlc for %s yet", symbol)
            return
        row = rows[0]
        try:
            levels = compute_cdp(
                float(row["high"]), float(row["low"]), float(row["close"]),
            )
            self._cache[symbol] = {
                "ah": levels["ah"], "nh": levels["nh"], "cdp": levels["cdp"],
                "nl": levels["nl"], "al": levels["al"],
                "as_of_date": row["date"],
                "prev_close": float(row["close"]),
            }
            logger.debug("cdp cached %s: %s", symbol, self._cache[symbol])
        except (ValueError, TypeError) as e:
            logger.warning("cdp.refresh %s: bad data %s — %s", symbol, row, e)

    def discard(self, symbol: str) -> None:
        self._cache.pop(symbol, None)

    def has(self, symbol: str) -> bool:
        return symbol in self._cache

    async def backfill_from_fubon(self, symbol: str) -> bool:
        """打富邦 historical.candles 拉昨日 OHLC → INSERT daily_ohlc → refresh cache。

        Return True if successful, False if no data / fubon error。
        Historical API 富邦官方限 60 req/min，用獨立的 historical limiter (1 req/s)。
        """
        from services.fubon_client import FubonStatus, get_fubon
        from services.rate_limiter import get_historical_rate_limiter
        from services.supabase_client import get_supabase

        fubon = get_fubon()
        sb = get_supabase()
        if fubon.status != FubonStatus.OK or fubon.sdk is None:
            logger.warning("cdp.backfill: fubon not OK")
            return False
        if sb.client is None:
            logger.warning("cdp.backfill: supabase not OK")
            return False

        today = date.today()
        last_week = today - timedelta(days=10)  # 抓 10 天範圍，確保至少抓到上個交易日

        try:
            # Historical 限速 60/min，block 等 token 才打富邦
            await asyncio.to_thread(get_historical_rate_limiter().acquire)
            r = await asyncio.to_thread(
                fubon.sdk.marketdata.rest_client.stock.historical.candles,
                symbol=symbol,
                from_=last_week.isoformat(),
                to=today.isoformat(),
            )
        except Exception as e:
            logger.warning("cdp.backfill %s: fubon error %s", symbol, e)
            return False

        rows = (r or {}).get("data") or []
        if not rows:
            logger.info("cdp.backfill %s: no historical data", symbol)
            return False

        # 富邦 historical.candles 預設 desc by date，最新在 index 0；
        # 過濾掉「今日」（不能用今天的 H/L/C 算今天的 CDP）
        upserts = []
        for row in rows:
            d = row.get("date")
            if not d or d == today.isoformat():
                continue
            upserts.append({
                "symbol": symbol, "date": d,
                "high": row.get("high"),
                "low": row.get("low"), "close": row.get("close"),
            })

        if not upserts:
            logger.info("cdp.backfill %s: only today data (no past)", symbol)
            return False

        # upsert 進 daily_ohlc
        try:
            await asyncio.to_thread(
                lambda: sb.client.table("daily_ohlc")
                .upsert(upserts, on_conflict="symbol,date")
                .execute()
            )
        except Exception as e:
            logger.error("cdp.backfill %s: supabase upsert failed: %s", symbol, e)
            return False

        await self.refresh(symbol)
        logger.info("cdp.backfill %s: %d days OHLC stored", symbol, len(upserts))
        return True


_service: CdpService | None = None


def get_cdp_service() -> CdpService:
    global _service
    if _service is None:
        _service = CdpService()
    return _service
