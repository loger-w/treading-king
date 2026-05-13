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


def _tick_size(price: float) -> float:
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
    tick = _tick_size(price)
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


def compute_cdp(o: float, h: float, l: float, c: float) -> dict[str, float]:
    """純函式 — 給 OHLC 算 5 線值，5 條全部對齊到最近的台股 tick。

    每個價位的 tick 不同（< 10 / 10-50 / 50-100 / 100-500 / 500-1000 / >=1000
    分別是 0.01 / 0.05 / 0.10 / 0.50 / 1 / 5），round_to_tick_tw 內部用
    _tick_size(price) 取當下價位的 tick 再 round。

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
    # 這些值剛好都在 tick 5 grid 上 (2335/2310/2295/2270/2255 都是 5 的倍數)，對齊版不影響預期
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

    step(4, "tick rounding — 1000+ 跨越 tick 5 / tick 1 邊界 (all nearest)")
    r = compute_cdp(1000, 1010, 990, 1002)
    # 5 條全部 nearest（不再 ceil/floor）
    # raw cdp = (1010+990+2*1002)/4 = 1001  → nearest tick 5 → 1000
    # raw ah  = 1001 + 20 = 1021             → nearest tick 5 → 1020
    # raw nh  = 2*1001 - 990 = 1012          → nearest tick 5 → 1010
    # raw nl  = 2*1001 - 1010 = 992          → nearest tick 1 → 992
    # raw al  = 1001 - 20 = 981              → nearest tick 1 → 981
    expected = {"ah": 1020, "nh": 1010, "cdp": 1000, "nl": 992, "al": 981}
    for k, v in expected.items():
        if abs(r[k] - v) > 0.001:
            fail(f"{k} 不對: 算到 {r[k]}, 預期 {v}")
    ok(f"tick 對齊 + 跨 band 正確: {r}")

    step(5, "tick rounding — 500-1000 band 用 tick 1 (all nearest)")
    r = compute_cdp(580, 600, 560, 590)
    # raw cdp = (600+560+2*590)/4 = 585      → nearest tick 1 → 585
    # raw ah  = 585 + 40 = 625                → nearest tick 1 → 625
    # raw nh  = 2*585 - 560 = 610             → nearest tick 1 → 610
    # raw nl  = 2*585 - 600 = 570             → nearest tick 1 → 570
    # raw al  = 585 - 40 = 545                → nearest tick 1 → 545
    expected = {"ah": 625, "nh": 610, "cdp": 585, "nl": 570, "al": 545}
    for k, v in expected.items():
        if abs(r[k] - v) > 0.001:
            fail(f"{k} 不對: 算到 {r[k]}, 預期 {v}")
    ok(f"500-1000 band 對齊正確: {r}")

    step(6, "tick rounding helper — direction up/down/nearest 邊界")
    # 1004.5 在 1000+ band → tick 5
    assert round_to_tick_tw(1004.5, "up")      == 1005, "up boundary"
    assert round_to_tick_tw(1004.5, "down")    == 1000, "down boundary"
    assert round_to_tick_tw(1004.5, "nearest") == 1005, "nearest 1004.5 → 1005 (round half up via Python round)"
    # 50 邊界 — 50 < 50 false → 入 50-100 band tick 0.1
    assert round_to_tick_tw(50.05, "nearest") == 50.10 or round_to_tick_tw(50.05, "nearest") == 50.00, \
        "boundary 50 用 tick 0.1（banker round 可 50.0 或 50.1）"
    ok("rounding helper 邊界 OK")

    print(f"\n{GREEN}All cdp smoke tests passed ✓{RESET}")
