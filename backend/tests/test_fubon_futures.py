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


from services.fubon_futures import filter_active_mxf_symbol, ProductRow


def test_filter_active_mxf_picks_nearest_unexpired():
    # 模擬富邦回傳的 products 清單(精簡欄位)
    today = "2026-05-24"
    products: list[ProductRow] = [
        {"symbol": "MXFD6", "expiry": "2026-04-15"},  # 已過期
        {"symbol": "MXFE6", "expiry": "2026-05-20"},  # 已過期(同月但結算已過)
        {"symbol": "MXFF6", "expiry": "2026-06-17"},  # 未過期、最近
        {"symbol": "MXFG6", "expiry": "2026-07-15"},  # 次月
        {"symbol": "MXFI6", "expiry": "2026-09-16"},  # 季月
        {"symbol": "TXFF6", "expiry": "2026-06-17"},  # 大台,要排除
        {"symbol": "MX1F6", "expiry": "2026-06-17"},  # 微小台,要排除
    ]
    assert filter_active_mxf_symbol(products, today=today) == "MXFF6"


def test_filter_active_mxf_returns_none_if_no_active():
    products: list[ProductRow] = [
        {"symbol": "MXFA5", "expiry": "2025-01-15"},  # 都過期
        {"symbol": "TXFF6", "expiry": "2026-06-17"},  # 大台
    ]
    assert filter_active_mxf_symbol(products, today="2026-05-24") is None


def test_filter_active_mxf_excludes_expiry_equal_today():
    # 結算日當天視為已過期(strict greater-than 語意)
    products: list[ProductRow] = [
        {"symbol": "MXFE6", "expiry": "2026-05-24"},  # 今天結算,排除
        {"symbol": "MXFF6", "expiry": "2026-06-17"},  # 次月,保留
    ]
    assert filter_active_mxf_symbol(products, today="2026-05-24") == "MXFF6"
