"""Camarilla Pivot 8 線 — 從昨日 OHLC 算 8 個值,盤中為固定值。

公式(Nick Stott 原版):
  rng = H - L
  H4/L4 = C ± rng × 1.1 / 2    ← 突破位
  H3/L3 = C ± rng × 1.1 / 4    ← 反轉位
  H2/L2 = C ± rng × 1.1 / 6
  H1/L1 = C ± rng × 1.1 / 12   ← 最靠近昨收
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import TypedDict

from services.cdp import round_to_tick_tw
from services.supabase_client import get_supabase

logger = logging.getLogger(__name__)


class CamarillaLevels(TypedDict):
    h4: float
    h3: float
    h2: float
    h1: float
    l1: float
    l2: float
    l3: float
    l4: float
    as_of_date: str
    prev_close: float


def compute_camarilla(h: float, l: float, c: float) -> dict[str, float]:
    """純函式 — 從昨日 H/L/C 算 8 線,全部對齊台股 tick。

    每條線用自己的 price 決定 tick size（不統一用 close 的 tick），
    因此 H 側與 L 側可能落在不同 tick bracket。
    """
    rng = h - l
    raw = {
        "h4": c + rng * 1.1 / 2,
        "h3": c + rng * 1.1 / 4,
        "h2": c + rng * 1.1 / 6,
        "h1": c + rng * 1.1 / 12,
        "l1": c - rng * 1.1 / 12,
        "l2": c - rng * 1.1 / 6,
        "l3": c - rng * 1.1 / 4,
        "l4": c - rng * 1.1 / 2,
    }
    return {k: round_to_tick_tw(v, "nearest") for k, v in raw.items()}


class CamarillaService:
    """In-memory cache + 從 daily_ohlc 抓昨日 OHLC 算 8 線。

    跟 CdpService 同設計:每天首次呼叫 trigger backfill_from_fubon 一次。
    """

    def __init__(self) -> None:
        self._cache: dict[str, CamarillaLevels] = {}
        # _lock kept for future use; current upsert+cache writes are idempotent
        # so concurrent get() calls converge correctly without acquiring
        self._lock = asyncio.Lock()
        self._last_backfill_attempt: dict[str, date] = {}

    async def get(self, symbol: str) -> CamarillaLevels | None:
        today = date.today()
        if self._last_backfill_attempt.get(symbol) != today:
            self._last_backfill_attempt[symbol] = today
            await self.backfill_from_fubon(symbol)

        if symbol not in self._cache:
            await self.refresh(symbol)

        return self._cache.get(symbol)

    async def refresh(self, symbol: str) -> None:
        """從 daily_ohlc 抓最近一筆 OHLC → 算 → 進 cache。"""
        sb = get_supabase()
        if sb.client is None:
            logger.warning("camarilla.refresh: supabase not ready")
            return

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
            logger.info("camarilla.refresh: no daily_ohlc for %s yet", symbol)
            return
        row = rows[0]
        try:
            levels = compute_camarilla(
                float(row["high"]), float(row["low"]), float(row["close"]),
            )
            self._cache[symbol] = {
                "h4": levels["h4"], "h3": levels["h3"], "h2": levels["h2"], "h1": levels["h1"],
                "l1": levels["l1"], "l2": levels["l2"], "l3": levels["l3"], "l4": levels["l4"],
                "as_of_date": row["date"],
                "prev_close": float(row["close"]),
            }
            logger.debug("camarilla cached %s: %s", symbol, self._cache[symbol])
        except (ValueError, TypeError) as e:
            logger.warning("camarilla.refresh %s: bad data %s — %s", symbol, row, e)

    def discard(self, symbol: str) -> None:
        self._cache.pop(symbol, None)

    def has(self, symbol: str) -> bool:
        return symbol in self._cache

    async def backfill_from_fubon(self, symbol: str) -> bool:
        """打富邦 historical.candles 拉昨日 OHLC → upsert daily_ohlc → refresh cache。

        共用既有 historical rate limiter(60 req/min)。
        """
        from services.fubon_client import FubonStatus, get_fubon
        from services.rate_limiter import get_historical_rate_limiter

        fubon = get_fubon()
        sb = get_supabase()
        if fubon.status != FubonStatus.OK or fubon.sdk is None:
            logger.warning("camarilla.backfill: fubon not OK")
            return False
        if sb.client is None:
            logger.warning("camarilla.backfill: supabase not OK")
            return False

        today = date.today()
        last_week = today - timedelta(days=10)

        try:
            await asyncio.to_thread(get_historical_rate_limiter().acquire)
            r = await asyncio.to_thread(
                fubon.sdk.marketdata.rest_client.stock.historical.candles,
                symbol=symbol,
                from_=last_week.isoformat(),
                to=today.isoformat(),
            )
        except Exception as e:
            logger.warning("camarilla.backfill %s: fubon error %s", symbol, e)
            return False

        rows = (r or {}).get("data") or []
        if not rows:
            logger.info("camarilla.backfill %s: no historical data", symbol)
            return False

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
            logger.info("camarilla.backfill %s: only today data (no past)", symbol)
            return False

        try:
            await asyncio.to_thread(
                lambda: sb.client.table("daily_ohlc")
                .upsert(upserts, on_conflict="symbol,date")
                .execute()
            )
        except Exception as e:
            logger.error("camarilla.backfill %s: supabase upsert failed: %s", symbol, e)
            return False

        await self.refresh(symbol)
        logger.info("camarilla.backfill %s: %d days OHLC stored", symbol, len(upserts))
        return True


_service: CamarillaService | None = None


def get_camarilla_service() -> CamarillaService:
    global _service
    if _service is None:
        _service = CamarillaService()
    return _service
