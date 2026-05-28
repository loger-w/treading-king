"""MXF 期貨服務 — symbol 解析、candles 合併、session 邏輯。"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, time, timedelta
from typing import Literal, Optional, TypedDict

logger = logging.getLogger(__name__)

Session = Literal["day", "night", "closed"]


class ProductRow(TypedDict):
    symbol: str
    expiry: str  # "YYYY-MM-DD"


# 精準匹配「MXF + 月碼一碼(A-L)+ 年碼一碼」, 排除 MX1, MXR, TXF 等其他系列
MXF_SYMBOL_RE = re.compile(r"^MXF[A-L][0-9]$")


def filter_active_mxf_symbol(products: list[ProductRow], *, today: str) -> Optional[str]:
    """從 products 清單挑出近月 MXF。

    規則:symbol 嚴格匹配 ^MXF[A-L][0-9]$、expiry > today、取最近一個。
    expiry == today 視為已結算(當日結算後即下市),排除。
    """
    candidates = [
        p for p in products
        if MXF_SYMBOL_RE.match(p["symbol"]) and p["expiry"] > today
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda p: p["expiry"])["symbol"]


def parse_tickers_response(raw: dict) -> list[ProductRow]:
    """從富邦 futopt.intraday.tickers 回應抽出 (symbol, expiry) 對。

    富邦真實欄位名為 camelCase:`endDate`(下市日)、`settlementDate`(最後結算日)。
    台指期貨兩者通常同日。expiry 取 endDate 為主、settlementDate 為輔。
    `expiry[:10]` 截到 date(若是 ISO datetime,只取 YYYY-MM-DD 那 10 字元)。
    Schema 參考:docs/market-data-future/http-api/intraday/tickers.txt
    """
    out: list[ProductRow] = []
    for row in raw.get("data") or []:
        symbol = row.get("symbol")
        expiry = row.get("endDate") or row.get("settlementDate")
        if symbol and expiry:
            out.append({"symbol": symbol, "expiry": expiry[:10]})
    return out


_ACTIVE_SYMBOL_CACHE: dict[str, tuple[str, datetime]] = {}
_ACTIVE_SYMBOL_TTL = timedelta(hours=1)


async def resolve_active_symbol() -> Optional[str]:
    """查富邦近月 MXF symbol,1h cache。

    換月一個月一次,1h TTL 足夠安全。
    回傳 None 代表 SDK 未初始化或找不到符合的 MXF 合約。

    同時查 REGULAR + AFTERHOURS 兩個 session 再合併:tickers 不帶 session 只回
    日盤(REGULAR)清單,而夜盤跨午夜後(00:00 起)新交易日的日盤尚未開盤、
    REGULAR 會是空的,得靠 AFTERHOURS 才拿得到當前可交易合約。
    """
    # lazy import 避免測試環境在 module 載入時拉入 fubon_client 相依鏈
    from services.fubon_client import get_fubon  # noqa: PLC0415

    now = datetime.now()
    cached = _ACTIVE_SYMBOL_CACHE.get("mxf")
    if cached and (now - cached[1]) < _ACTIVE_SYMBOL_TTL:
        return cached[0]

    fubon = get_fubon()
    if fubon.sdk is None:
        logger.warning("Fubon SDK not initialized")
        return None

    # lazy import 與 get_fubon 同理,避免 module 載入時拉入 httpx 相依鏈影響測試
    from services.rate_limiter import get_rate_limiter  # noqa: PLC0415

    async def _fetch_session(session: str) -> list[ProductRow]:
        # 期貨 tickers API:futopt.intraday.tickers(type='FUTURE', exchange='TAIFEX')
        # 回傳欄位是 camelCase:data[].symbol / endDate / settlementDate
        # tickers 的 session 是大寫 REGULAR/AFTERHOURS(candles 端點才用小寫 afterhours)
        try:
            await asyncio.to_thread(get_rate_limiter().acquire)
            raw = await asyncio.to_thread(
                fubon.sdk.marketdata.rest_client.futopt.intraday.tickers,
                type="FUTURE",
                exchange="TAIFEX",
                session=session,
            )
        except Exception as e:
            logger.warning("resolve_active_symbol tickers(session=%s) failed: %s", session, e)
            return []
        return parse_tickers_response(raw)

    regular, afterhours = await asyncio.gather(
        _fetch_session("REGULAR"),
        _fetch_session("AFTERHOURS"),
    )
    # 同一合約兩 session 都會列,dedupe by symbol(expiry 與 session 無關)
    by_symbol: dict[str, ProductRow] = {}
    for p in (*regular, *afterhours):
        by_symbol.setdefault(p["symbol"], p)
    products = list(by_symbol.values())

    today_str = now.strftime("%Y-%m-%d")
    mxf_candidates = [p for p in products if MXF_SYMBOL_RE.match(p["symbol"])]
    logger.info(
        "resolve_active_symbol: %d products (REGULAR=%d, AFTERHOURS=%d), %d MXF candidates: %s",
        len(products),
        len(regular),
        len(afterhours),
        len(mxf_candidates),
        [p["symbol"] for p in mxf_candidates[:6]],
    )

    sym = filter_active_mxf_symbol(products, today=today_str)
    logger.info("resolve_active_symbol(today=%s) → %s", today_str, sym)
    if sym:
        _ACTIVE_SYMBOL_CACHE["mxf"] = (sym, now)
    return sym


class MXFCandleDict(TypedDict):
    date: str       # ISO with tz offset, e.g. "2026-05-25T09:00:00+08:00"
    open: float
    high: float
    low: float
    close: float
    volume: int
    average: float  # 富邦回傳的 VWAP


def merge_candles(*, day: list[MXFCandleDict], night: list[MXFCandleDict]) -> list[MXFCandleDict]:
    """合併日盤 + 夜盤 candles,按 ts 排序(夜盤在前),同 ts 取後到的(day 蓋 night)。"""
    by_date: dict[str, MXFCandleDict] = {}
    for x in night:
        by_date[x["date"]] = x
    for x in day:
        by_date[x["date"]] = x  # day 蓋 night
    return sorted(by_date.values(), key=lambda c: c["date"])

DAY_OPEN = time(8, 45)
DAY_CLOSE = time(13, 45)
NIGHT_OPEN = time(15, 0)
NIGHT_CLOSE = time(5, 0)


def determine_current_session(now: datetime) -> Session:
    """判斷 now(必須帶 tz)屬於哪個 session。

    交易日 D = D-1 15:00 → D 13:45。
    週五日盤後到下週一日盤開盤之間皆 closed(週五無夜盤)。
    """
    weekday = now.weekday()  # Mon=0 ... Sun=6
    t = now.time()

    # 週六(5)整天 closed
    # 週日(6)整天 closed(週日夜盤是「週一交易日」的夜盤,但實務上不開,所以仍 closed)
    if weekday == 5:
        return "closed"
    if weekday == 6:
        return "closed"

    # 週五日盤後到 23:59:59 = closed(週五無夜盤)
    if weekday == 4 and t >= DAY_CLOSE:
        return "closed"

    # 週一凌晨 00:00-05:00 屬於「週日夜盤」— 但週日不開夜盤,所以 closed
    if weekday == 0 and t < DAY_OPEN:
        return "closed"

    # day session
    if DAY_OPEN <= t < DAY_CLOSE:
        return "day"

    # night session
    # 跨日:當日 15:00 ≤ t 或 t < 05:00
    if t >= NIGHT_OPEN or t < NIGHT_CLOSE:
        return "night"

    # 其他(05:00-08:44, 13:45-14:59)= closed
    return "closed"


SUPPORTED_TIMEFRAMES = (1, 5, 10, 15, 30, 60)


async def fetch_candles(symbol: str, timeframe: int) -> list[MXFCandleDict]:
    """拉日盤 + 夜盤 candles 並合併,夜盤在前。

    參數:timeframe ∈ {1, 5, 10, 15, 30, 60}。
    依賴 merge_candles 假設 date 字串可字典序排序 — 後續實測若富邦回傳
    tz 格式不一致(如混 `+0800` 無冒號)需先正規化。
    """
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"unsupported timeframe: {timeframe}")

    # Lazy imports 避免 test env httpx 缺失
    from services.fubon_client import get_fubon  # noqa: PLC0415
    from services.rate_limiter import get_rate_limiter  # noqa: PLC0415

    fubon = get_fubon()
    if fubon.sdk is None:
        return []

    tf_str = str(timeframe)

    async def _fetch(session: Optional[str]) -> list[MXFCandleDict]:
        kwargs: dict = {"symbol": symbol, "timeframe": tf_str}
        if session:
            kwargs["session"] = session
        try:
            await asyncio.to_thread(get_rate_limiter().acquire)
            raw = await asyncio.to_thread(
                fubon.sdk.marketdata.rest_client.futopt.intraday.candles,
                **kwargs,
            )
        except ValueError:
            raise  # 呼叫方 bug 不要吞
        except Exception as e:
            logger.warning("fetch_candles session=%s failed: %s", session, e)
            return []
        return list(raw.get("data") or [])

    day, night = await asyncio.gather(_fetch(None), _fetch("afterhours"))
    return merge_candles(day=day, night=night)
