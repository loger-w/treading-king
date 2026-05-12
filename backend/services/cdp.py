"""CDP 5 線 — 從昨日 OHLC 算 5 個值，盤中為固定值。

Plan §Phase 3 §4.5。
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
from datetime import date, timedelta
from typing import Any, TypedDict

logger = logging.getLogger(__name__)


class CdpLevels(TypedDict):
    ah: float
    nh: float
    cdp: float
    nl: float
    al: float
    as_of_date: str  # 昨日 ISO date string


def compute_cdp(o: float, h: float, l: float, c: float) -> dict[str, float]:
    """純函式 — 給 OHLC 算 5 線值。"""
    cdp = (h + l + 2 * c) / 4
    ah = cdp + (h - l)
    nh = 2 * cdp - l
    nl = 2 * cdp - h
    al = cdp - (h - l)
    return {"ah": ah, "nh": nh, "cdp": cdp, "nl": nl, "al": al}


class CdpService:
    """In-memory cache + 從 daily_ohlc 抓昨日 OHLC 算 5 線。"""

    def __init__(self) -> None:
        self._cache: dict[str, CdpLevels] = {}
        self._lock = asyncio.Lock()

    async def get(self, symbol: str) -> CdpLevels | None:
        """回 cache 中的 5 值，沒有就 lazy load 一次。"""
        if symbol in self._cache:
            return self._cache[symbol]
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
            .select("date, open, high, low, close")
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
                float(row["open"]), float(row["high"]),
                float(row["low"]), float(row["close"]),
            )
            self._cache[symbol] = {
                "ah": levels["ah"], "nh": levels["nh"], "cdp": levels["cdp"],
                "nl": levels["nl"], "al": levels["al"],
                "as_of_date": row["date"],
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
        """
        from services.fubon_client import FubonStatus, get_fubon
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
                "open": row.get("open"), "high": row.get("high"),
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


# ----------------------- inline smoke -----------------------

if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"

    def step(n, t): print(f"\n{YELLOW}[Test {n}] {t}{RESET}")
    def ok(m): print(f"{GREEN}  ✓ {m}{RESET}")
    def fail(m): print(f"{RED}  ✗ {m}{RESET}"); sys.exit(1)

    step(1, "compute_cdp(O=2300, H=2320, L=2280, C=2290) — 對 spec 範例")
    r = compute_cdp(2300, 2320, 2280, 2290)
    # CDP = (2320+2280+2*2290)/4 = (2320+2280+4580)/4 = 9180/4 = 2295
    # AH = 2295 + (2320-2280) = 2295 + 40 = 2335
    # NH = 2*2295 - 2280 = 4590 - 2280 = 2310
    # NL = 2*2295 - 2320 = 4590 - 2320 = 2270
    # AL = 2295 - 40 = 2255
    expected = {"ah": 2335, "nh": 2310, "cdp": 2295, "nl": 2270, "al": 2255}
    for k, v in expected.items():
        if abs(r[k] - v) > 0.001:
            fail(f"{k} 不對: 算到 {r[k]}, 預期 {v}")
    ok(f"5 線都對: {r}")

    step(2, "compute_cdp 對極端值不爆")
    r = compute_cdp(0.01, 0.02, 0.01, 0.015)
    if all(isinstance(v, float) for v in r.values()):
        ok("極小值 OK")
    else: fail("type 不對")

    step(3, "ordering — AH > NH > CDP > NL > AL（H>L 時）")
    r = compute_cdp(580, 600, 560, 590)
    if r["ah"] > r["nh"] > r["cdp"] > r["nl"] > r["al"]:
        ok(f"順序正確: {r}")
    else: fail(f"順序錯: {r}")

    print(f"\n{GREEN}All cdp smoke tests passed ✓{RESET}")
