"""MXF 期貨服務 — symbol 解析、candles 合併、session 邏輯。"""
from __future__ import annotations

from datetime import datetime, time
from typing import Literal, TypedDict

Session = Literal["day", "night", "closed"]


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
