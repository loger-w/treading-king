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
import time
from datetime import date, timedelta
from typing import Literal, TypedDict

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


def limit_up_price(prev_close: float) -> float:
    """台股漲停價 = 昨收 × 1.1,尾數不足一個 tick 捨去(不超過 +10%)。

    tick 以漲停價當下價位的級距為準。用整數「0.1 分」運算避免 float floor 在
    tick 邊界出錯(round_to_tick_tw 的 "down" 對 53.9/0.1=538.999… 會誤捨成 53.8)。
    無漲跌停限制的標的(部分 ETF / 新股首 5 日)價格不會剛好盯在此值,
    策略 1 的鎖死 latch 自然不會誤觸。
    """
    raw = prev_close * 1.1
    tick = tick_size(raw)
    # 0.1 分(deci-cent)整數運算:昨收×1.1 合法地帶半分尾數(9.05×1.1=9.955),
    # 整數「分」會把該被捨去的半分提前四捨五入成 9.96(超出法定 +10%);
    # deci-cent 下輸入是精確整數,round 只殺 float 雜訊
    raw_deci = round(raw * 1000)
    tick_deci = round(tick * 1000)
    floored_deci = (raw_deci // tick_deci) * tick_deci
    return round(floored_deci / 1000.0, 2)


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


# ---- 共用 daily-OHLC backfill（CdpService / CamarillaService 共用） ----
# 兩個 service 都從同一份 daily_ohlc 算各自的 levels，fetch 邏輯抽到這裡共用：
# 同 symbol 每天只打一次富邦 historical API，不因兩個端點各自快取而雙倍消耗額度。

# 回看 30 個日曆天：春節休市加前後週末可超過 10 天（如 2021 年 2/5→2/17），
# historical.candles 是區間查詢，放寬範圍不增加請求數
_LOOKBACK_DAYS = 30
# backfill 失敗後的最短重試間隔 — 暫時性失敗（開盤前富邦還在背景重連、網路抖動）
# 不該把該 symbol 鎖整天；但也不能每個請求都重打造成失敗風暴
_FAIL_RETRY_COOLDOWN_S = 300.0

_backfill_done: dict[str, date] = {}        # symbol -> 當日已「成功」backfill 的日期
_backfill_failed_at: dict[str, float] = {}  # symbol -> 上次失敗的 monotonic 時間戳
_backfill_inflight: dict[str, asyncio.Task] = {}  # 並發呼叫共享同一個進行中的 fetch


async def ensure_daily_ohlc(symbol: str) -> bool:
    """確保 daily_ohlc 有 symbol 的昨日 OHLC，每天最多「成功」打富邦一次。

    成功才標記「今日已完成」；失敗只記 cooldown 時間戳，冷卻後下次呼叫可重試。
    並發呼叫（開盤時 signal_engine refill 與前端請求交錯）await 同一個
    in-flight task，不會對同 symbol 重複打 API、也不會讀到交錯中的 stale 值。
    """
    today = date.today()  # 本地時區（台股 UTC+8，後端在 Taiwan 跑無誤）
    if _backfill_done.get(symbol) == today:
        return True

    task = _backfill_inflight.get(symbol)
    if task is None:
        failed_at = _backfill_failed_at.get(symbol)
        if failed_at is not None and time.monotonic() - failed_at < _FAIL_RETRY_COOLDOWN_S:
            return False
        task = asyncio.create_task(_fetch_daily_ohlc(symbol))
        _backfill_inflight[symbol] = task
    try:
        ok = await task
    finally:
        if _backfill_inflight.get(symbol) is task:
            del _backfill_inflight[symbol]

    if ok:
        _backfill_done[symbol] = today
        _backfill_failed_at.pop(symbol, None)
    else:
        _backfill_failed_at[symbol] = time.monotonic()
    return ok


async def _fetch_daily_ohlc(symbol: str) -> bool:
    """打富邦 historical.candles 拉近期日 OHLC → upsert local daily_ohlc。

    Return True if 有寫入過去交易日資料，False if no data / fubon error。
    Historical API 富邦官方限 60 req/min，用獨立的 historical limiter (1 req/s)。
    """
    from services.fubon_client import FubonStatus, get_fubon
    from services.local_store import get_local_store
    from services.rate_limiter import get_historical_rate_limiter

    fubon = get_fubon()
    if fubon.status != FubonStatus.OK or fubon.sdk is None:
        logger.warning("daily_ohlc.backfill %s: fubon not OK", symbol)
        return False

    today = date.today()
    from_day = today - timedelta(days=_LOOKBACK_DAYS)

    try:
        # Historical 限速 60/min，block 等 token 才打富邦
        await asyncio.to_thread(get_historical_rate_limiter().acquire)
        r = await asyncio.to_thread(
            fubon.sdk.marketdata.rest_client.stock.historical.candles,
            symbol=symbol,
            from_=from_day.isoformat(),
            to=today.isoformat(),
        )
    except Exception as e:
        logger.warning("daily_ohlc.backfill %s: fubon error %s", symbol, e)
        return False

    rows = (r or {}).get("data") or []
    if not rows:
        logger.info("daily_ohlc.backfill %s: no historical data", symbol)
        return False

    # 過濾掉「今日」（不能用今天的 H/L/C 算今天的 levels）；
    # H/L/C 缺欄或非數值的列直接跳過 — daily_ohlc 每檔只留最新一筆，
    # 一筆壞列會覆蓋掉完好的舊列並持久化（寧缺勿錯）
    upserts = []
    for row in rows:
        d = row.get("date")
        if not d or d == today.isoformat():
            continue
        try:
            h, l, c = float(row["high"]), float(row["low"]), float(row["close"])
        except (KeyError, ValueError, TypeError):
            logger.warning("daily_ohlc.backfill %s: bad candle row %s", symbol, row)
            continue
        upserts.append({"symbol": symbol, "date": d, "high": h, "low": l, "close": c})

    if not upserts:
        logger.info("daily_ohlc.backfill %s: only today data (no past)", symbol)
        return False

    get_local_store().market.upsert_daily_ohlc(upserts)
    logger.info("daily_ohlc.backfill %s: %d days OHLC stored", symbol, len(upserts))
    return True


class CdpService:
    """In-memory cache + 從 daily_ohlc 抓昨日 OHLC 算 5 線。

    daily_ohlc 寫入是「lazy backfill」式（watchlist add / CDP route fallback），
    沒有自動每日批次更新。為避免「同一個 symbol 加入後就再也不更新」造成的
    stale state（觀察到 6531 缺 2026-05-12 ⇒ CDP 永遠在算前天 OHLC），
    get() 每次都先走共用的 ensure_daily_ohlc（每天最多成功 backfill 一次）。
    """

    def __init__(self) -> None:
        self._cache: dict[str, CdpLevels] = {}

    async def get(self, symbol: str) -> CdpLevels | None:
        """先確保 daily_ohlc 有今天該有的昨日資料，再從 store 重算。

        如果富邦暫不可用 (network / quota)，fallback 讀既有 daily_ohlc
        (可能是 stale 但有總比沒有好)。
        """
        await ensure_daily_ohlc(symbol)
        await self.refresh(symbol)
        return self._cache.get(symbol)

    async def refresh(self, symbol: str) -> None:
        """從 local MarketCache 抓最近一筆 OHLC → 算 → 進 cache。"""
        from services.local_store import get_local_store

        row = get_local_store().market.get_latest_daily_ohlc(symbol)
        if row is None:
            logger.info("cdp.refresh: no daily_ohlc for %s yet", symbol)
            return
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

    async def backfill_from_fubon(self, symbol: str) -> bool:
        """外部呼叫端（加自選/監聽的背景補資料、route fallback）的 backfill 入口。

        委派給共用的 ensure_daily_ohlc（今日已成功 / 失敗 cooldown 中會直接短路）。
        """
        ok = await ensure_daily_ohlc(symbol)
        await self.refresh(symbol)
        return ok


_service: CdpService | None = None


def get_cdp_service() -> CdpService:
    global _service
    if _service is None:
        _service = CdpService()
    return _service
