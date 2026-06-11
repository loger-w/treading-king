"""Camarilla Pivot 8 線 — 從昨日 OHLC 算 8 個值,盤中為固定值。

公式(Nick Stott 原版):
  rng = H - L
  H4/L4 = C ± rng × 1.1 / 2    ← 突破位
  H3/L3 = C ± rng × 1.1 / 4    ← 反轉位
  H2/L2 = C ± rng × 1.1 / 6
  H1/L1 = C ± rng × 1.1 / 12   ← 最靠近昨收
"""
from __future__ import annotations

import logging
from typing import TypedDict

from services.cdp import ensure_daily_ohlc, round_to_tick_tw
from services.local_store import get_local_store

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

    daily-OHLC 的取得走跟 CdpService 共用的 ensure_daily_ohlc —
    同 symbol 每天只打一次富邦 historical API（CDP / Camarilla 不重複消耗額度）。
    """

    def __init__(self) -> None:
        self._cache: dict[str, CamarillaLevels] = {}

    async def get(self, symbol: str) -> CamarillaLevels | None:
        """先確保 daily_ohlc 有今天該有的昨日資料，再從 store 重算。

        富邦暫不可用時 fallback 讀既有 daily_ohlc（可能 stale 但有總比沒有好）。
        """
        await ensure_daily_ohlc(symbol)
        await self.refresh(symbol)
        return self._cache.get(symbol)

    async def refresh(self, symbol: str) -> None:
        """從 local MarketCache 抓最近一筆 OHLC → 算 → 進 cache。"""
        row = get_local_store().market.get_latest_daily_ohlc(symbol)
        if row is None:
            logger.info("camarilla.refresh: no daily_ohlc for %s yet", symbol)
            return
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

    async def backfill_from_fubon(self, symbol: str) -> bool:
        """外部呼叫端（route fallback）的 backfill 入口 — 委派給共用 ensure_daily_ohlc。"""
        ok = await ensure_daily_ohlc(symbol)
        await self.refresh(symbol)
        return ok


_service: CamarillaService | None = None


def get_camarilla_service() -> CamarillaService:
    global _service
    if _service is None:
        _service = CamarillaService()
    return _service
