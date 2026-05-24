from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.fubon_futures import determine_current_session

TPE = ZoneInfo("Asia/Taipei")


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TPE)


@pytest.mark.parametrize(
    "iso,expected",
    [
        # ---- day session(08:45–13:45)----
        ("2026-05-25T08:45:00", "day"),    # 開盤瞬間
        ("2026-05-25T10:30:00", "day"),    # 日盤中
        ("2026-05-25T13:44:59", "day"),    # 收盤前
        # ---- day → closed 邊界 ----
        ("2026-05-25T13:45:00", "closed"), # 收盤瞬間
        ("2026-05-25T14:30:00", "closed"), # 日盤後休市
        # ---- night session(15:00–翌日 05:00)----
        ("2026-05-25T15:00:00", "night"),  # 夜盤開
        ("2026-05-25T22:00:00", "night"),  # 夜盤中
        ("2026-05-26T01:30:00", "night"),  # 跨日夜盤
        ("2026-05-26T04:59:59", "night"),  # 夜盤收盤前
        # ---- night → closed 邊界 ----
        ("2026-05-26T05:00:00", "closed"), # 夜盤收盤
        ("2026-05-26T07:00:00", "closed"), # 開盤前休市
        # ---- 週五無夜盤(5/29 為週五)----
        ("2026-05-29T13:44:59", "day"),    # 週五日盤
        ("2026-05-29T13:45:00", "closed"), # 週五收盤後
        ("2026-05-29T15:00:00", "closed"), # 週五本應夜盤但無
        ("2026-05-29T22:00:00", "closed"), # 週五本應夜盤但無
        ("2026-05-30T01:00:00", "closed"), # 週六凌晨無夜盤
        # ---- 週六、週日全 closed ----
        ("2026-05-30T10:00:00", "closed"), # 週六
        ("2026-05-31T22:00:00", "closed"), # 週日(夜盤本身不開)
        # ---- 週一凌晨(週日無夜盤,週一 guard 必須攔截)----
        ("2026-06-01T00:00:00", "closed"), # 週一凌晨(週日無夜盤)
        ("2026-06-01T02:30:00", "closed"), # 週一凌晨中段
        # ---- 週一 08:45 開盤 ----
        ("2026-06-01T08:44:59", "closed"), # 週一開盤前
        ("2026-06-01T08:45:00", "day"),    # 週一開盤
    ],
)
def test_determine_current_session(iso: str, expected: str):
    assert determine_current_session(dt(iso)) == expected


from services.fubon_futures import merge_candles, MXFCandleDict


def c(ts: str, **kw) -> MXFCandleDict:
    base: MXFCandleDict = {
        "date": ts, "open": 17000.0, "high": 17010.0, "low": 16990.0,
        "close": 17005.0, "volume": 100, "average": 17000.0,
    }
    base.update(kw)
    return base


def test_merge_candles_orders_night_first():
    # 夜盤 5/24 15:00、5/24 23:00、日盤 5/25 09:00、5/25 12:00
    night = [c("2026-05-24T23:00:00+08:00"), c("2026-05-24T15:00:00+08:00")]
    day = [c("2026-05-25T12:00:00+08:00"), c("2026-05-25T09:00:00+08:00")]
    out = merge_candles(day=day, night=night)
    assert [x["date"] for x in out] == [
        "2026-05-24T15:00:00+08:00",
        "2026-05-24T23:00:00+08:00",
        "2026-05-25T09:00:00+08:00",
        "2026-05-25T12:00:00+08:00",
    ]


def test_merge_candles_dedupes_by_date_keeping_last():
    # 同個 ts 出現在兩段(理論上不該,防呆) — 取後到的(close 不同)
    night = [c("2026-05-25T08:45:00+08:00", close=17000.0)]
    day = [c("2026-05-25T08:45:00+08:00", close=17050.0)]
    out = merge_candles(day=day, night=night)
    assert len(out) == 1
    assert out[0]["close"] == 17050.0  # day(後加)蓋掉 night


def test_merge_candles_empty_inputs():
    assert merge_candles(day=[], night=[]) == []
    assert merge_candles(day=[c("2026-05-25T09:00:00+08:00")], night=[]) == [
        c("2026-05-25T09:00:00+08:00")
    ]
