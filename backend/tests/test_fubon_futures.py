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


# ---- parse_tickers_response: Fubon futopt.intraday.tickers 回應解析 ----
# 富邦真實欄位用 camelCase(endDate / settlementDate),非 snake_case。
# Schema 參考:https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/tickers.txt

from services.fubon_futures import parse_tickers_response


def test_parse_tickers_response_extracts_symbol_and_endDate():
    raw = {
        "type": "FUTURE",
        "exchange": "TAIFEX",
        "data": [
            {"symbol": "MXFF6", "endDate": "2026-06-17", "settlementDate": "2026-06-17"},
            {"symbol": "MXFG6", "endDate": "2026-07-15", "settlementDate": "2026-07-15"},
        ],
    }
    out = parse_tickers_response(raw)
    assert out == [
        {"symbol": "MXFF6", "expiry": "2026-06-17"},
        {"symbol": "MXFG6", "expiry": "2026-07-15"},
    ]


def test_parse_tickers_response_falls_back_to_settlementDate_when_endDate_missing():
    raw = {"data": [{"symbol": "MXFF6", "settlementDate": "2026-06-17"}]}
    out = parse_tickers_response(raw)
    assert out == [{"symbol": "MXFF6", "expiry": "2026-06-17"}]


def test_parse_tickers_response_skips_rows_without_expiry():
    raw = {
        "data": [
            {"symbol": "MXFF6", "endDate": "2026-06-17"},
            {"symbol": "ORPHAN"},                         # 沒到期日,丟棄
            {"endDate": "2026-07-15"},                    # 沒 symbol,丟棄
            {"symbol": "MXFG6", "endDate": "2026-07-15"},
        ],
    }
    out = parse_tickers_response(raw)
    assert out == [
        {"symbol": "MXFF6", "expiry": "2026-06-17"},
        {"symbol": "MXFG6", "expiry": "2026-07-15"},
    ]


def test_parse_tickers_response_handles_empty_or_missing_data():
    assert parse_tickers_response({}) == []
    assert parse_tickers_response({"data": []}) == []
    assert parse_tickers_response({"data": None}) == []  # null-safe


def test_parse_tickers_response_truncates_datetime_to_date():
    # 若富邦回傳 ISO datetime 而非純 date,只取前 10 字(YYYY-MM-DD)
    raw = {"data": [{"symbol": "MXFF6", "endDate": "2026-06-17T13:45:00+08:00"}]}
    out = parse_tickers_response(raw)
    assert out == [{"symbol": "MXFF6", "expiry": "2026-06-17"}]


# ---- resolve_active_symbol: 夜盤跨午夜的 session 修正 ----
# tickers 不帶 session 預設只回 REGULAR(日盤)清單。夜盤跨午夜後(00:00–08:45)
# 新日期的日盤尚未開盤,REGULAR 為空,必須同時查 AFTERHOURS 才拿得到當前合約 ——
# 否則小台圖整段夜盤拿不到近月 symbol、回 503 mxf_symbol_unavailable。
from types import SimpleNamespace

from services import fubon_futures as ff


class _FakeTickers:
    """記錄每次 tickers 呼叫的 kwargs,並依 session 回不同清單。"""

    def __init__(self, by_session: dict):
        self._by_session = by_session
        self.calls: list[dict] = []

    def tickers(self, **kwargs):
        self.calls.append(kwargs)
        return self._by_session.get(kwargs.get("session"), {"data": []})


def _fake_fubon(intraday: _FakeTickers) -> SimpleNamespace:
    return SimpleNamespace(
        sdk=SimpleNamespace(
            marketdata=SimpleNamespace(
                rest_client=SimpleNamespace(
                    futopt=SimpleNamespace(intraday=intraday)
                )
            )
        )
    )


async def test_resolve_active_symbol_uses_afterhours_when_regular_empty(monkeypatch):
    ff._ACTIVE_SYMBOL_CACHE.clear()
    intraday = _FakeTickers({
        "REGULAR": {"data": []},  # 跨午夜後當日日盤尚未開,REGULAR 空
        "AFTERHOURS": {"data": [
            {"symbol": "MXFF6", "endDate": "2099-06-17"},
            {"symbol": "MXFG6", "endDate": "2099-07-15"},
            {"symbol": "TXFF6", "endDate": "2099-06-17"},  # 大台,須排除
        ]},
    })
    monkeypatch.setattr("services.fubon_client.get_fubon", lambda: _fake_fubon(intraday))
    monkeypatch.setattr(
        "services.rate_limiter.get_rate_limiter",
        lambda: SimpleNamespace(acquire=lambda: None),
    )

    result = await ff.resolve_active_symbol()

    assert result == "MXFF6"
    # 必須真的查過 AFTERHOURS,而不是只查預設 REGULAR
    assert "AFTERHOURS" in {c.get("session") for c in intraday.calls}
