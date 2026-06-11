"""共用 daily-OHLC backfill(ensure_daily_ohlc)+ CdpService 行為測試。

為何重要:CDP/Camarilla 的 5 線/8 線全靠 daily_ohlc 的昨日 OHLC,
backfill 的「成功才鎖今日 / 失敗可重試 / 壞列不落地」直接決定
訊號引擎整天用的參考價對不對。
"""
import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services import cdp as cdp_module
from services.cdp import ensure_daily_ohlc
from services.local_store import get_local_store


@pytest.fixture(autouse=True)
def reset_backfill_state():
    """ensure_daily_ohlc 的 latch 是 module-level — 測試間必須歸零。"""
    cdp_module._backfill_done.clear()
    cdp_module._backfill_failed_at.clear()
    cdp_module._backfill_inflight.clear()
    yield
    cdp_module._backfill_done.clear()
    cdp_module._backfill_failed_at.clear()
    cdp_module._backfill_inflight.clear()


def _patch_fetch(monkeypatch, results):
    """把 _fetch_daily_ohlc 換成依序回傳 results 的假函式,回呼叫記錄。"""
    calls: list[str] = []

    async def fake_fetch(symbol):
        calls.append(symbol)
        return results[min(len(calls), len(results)) - 1]

    monkeypatch.setattr(cdp_module, "_fetch_daily_ohlc", fake_fetch)
    return calls


@pytest.mark.asyncio
async def test_ensure_success_locks_for_today(monkeypatch):
    """成功後同日第二次呼叫不再打富邦(每天最多成功一次)。"""
    calls = _patch_fetch(monkeypatch, [True])
    assert await ensure_daily_ohlc("2330") is True
    assert await ensure_daily_ohlc("2330") is True
    assert calls == ["2330"]


@pytest.mark.asyncio
async def test_ensure_failure_does_not_lock_whole_day(monkeypatch):
    """失敗不鎖整天:cooldown 內短路不重打,cooldown 過了可重試並成功。

    開盤前富邦還在背景重連時第一發 backfill 必失敗,若失敗也標「今日已試」,
    該 symbol 整天讀 stale OHLC、訊號照錯的參考價靜默觸發。
    """
    calls = _patch_fetch(monkeypatch, [False, True])

    assert await ensure_daily_ohlc("2330") is False
    # cooldown 內:短路、不重打(防失敗風暴)
    assert await ensure_daily_ohlc("2330") is False
    assert calls == ["2330"]

    # cooldown 過了:可重試,這次成功
    cdp_module._backfill_failed_at["2330"] -= cdp_module._FAIL_RETRY_COOLDOWN_S + 1
    assert await ensure_daily_ohlc("2330") is True
    assert calls == ["2330", "2330"]
    # 成功後鎖到明天
    assert await ensure_daily_ohlc("2330") is True
    assert calls == ["2330", "2330"]


@pytest.mark.asyncio
async def test_ensure_retriggers_after_date_rollover(monkeypatch):
    """跨日後要重新 backfill — 「今日已成功」的 latch 不是永不過期。"""
    calls = _patch_fetch(monkeypatch, [True, True])
    await ensure_daily_ohlc("2330")
    # 模擬跨日:昨天成功的標記今天無效
    cdp_module._backfill_done["2330"] = date.today() - timedelta(days=1)
    await ensure_daily_ohlc("2330")
    assert calls == ["2330", "2330"]


@pytest.mark.asyncio
async def test_ensure_concurrent_calls_share_single_fetch(monkeypatch):
    """並發呼叫(開盤時 signal_engine refill 與前端請求交錯)共享同一個
    in-flight fetch — 不重複打富邦、後到者拿到同一個結果而非 stale。"""
    calls: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_fetch(symbol):
        calls.append(symbol)
        started.set()
        await release.wait()
        return True

    monkeypatch.setattr(cdp_module, "_fetch_daily_ohlc", slow_fetch)

    t1 = asyncio.ensure_future(ensure_daily_ohlc("2330"))
    await started.wait()
    t2 = asyncio.ensure_future(ensure_daily_ohlc("2330"))
    await asyncio.sleep(0)  # 讓 t2 跑到 await task
    release.set()
    assert await asyncio.gather(t1, t2) == [True, True]
    assert calls == ["2330"]


def _patch_fubon(monkeypatch, candle_rows):
    """假富邦 SDK:historical.candles 回固定 rows,limiter 不擋。"""
    from services.fubon_client import FubonStatus

    candles = MagicMock(return_value={"data": candle_rows})
    sdk = MagicMock()
    sdk.marketdata.rest_client.stock.historical.candles = candles
    fake_fubon = SimpleNamespace(status=FubonStatus.OK, sdk=sdk)
    monkeypatch.setattr("services.fubon_client.get_fubon", lambda: fake_fubon)
    monkeypatch.setattr(
        "services.rate_limiter.get_historical_rate_limiter",
        lambda: SimpleNamespace(acquire=lambda: None),
    )
    return candles


@pytest.mark.asyncio
async def test_fetch_skips_bad_rows_and_today(monkeypatch, local_store_tmp):
    """H/L/C 缺欄或 None 的列不落地(daily_ohlc 每檔只留最新一筆,
    一筆壞列會覆蓋完好舊列並持久化);今日列也要過濾。"""
    today = date.today()
    d1 = (today - timedelta(days=1)).isoformat()
    d2 = (today - timedelta(days=2)).isoformat()
    candles = _patch_fubon(monkeypatch, [
        {"date": today.isoformat(), "high": 1.0, "low": 1.0, "close": 1.0},  # 今日:不能用
        {"date": d1, "high": None, "low": 90.0, "close": 100.0},             # 壞列:跳過
        {"date": d2, "high": 110.0, "low": 90.0, "close": 100.0},            # 完好
    ])

    assert await cdp_module._fetch_daily_ohlc("2330") is True
    rec = get_local_store().market.get_latest_daily_ohlc("2330")
    assert rec["date"] == d2  # 壞的 d1 沒進來、今日也沒進來
    assert rec["high"] == 110.0

    # 回看窗 30 天:春節休市加前後週末可超過 10 個日曆天
    kwargs = candles.call_args.kwargs
    assert kwargs["from_"] == (today - timedelta(days=cdp_module._LOOKBACK_DAYS)).isoformat()
    assert cdp_module._LOOKBACK_DAYS >= 20


@pytest.mark.asyncio
async def test_fetch_all_rows_bad_returns_false(monkeypatch, local_store_tmp):
    """全部列都壞 → 不落地、回 False(讓 ensure 走失敗 cooldown 而非鎖今日)。"""
    d1 = (date.today() - timedelta(days=1)).isoformat()
    _patch_fubon(monkeypatch, [{"date": d1, "high": None, "low": None, "close": None}])
    assert await cdp_module._fetch_daily_ohlc("2330") is False
    assert get_local_store().market.get_latest_daily_ohlc("2330") is None


@pytest.mark.asyncio
async def test_fetch_fubon_not_ok_returns_false(monkeypatch, local_store_tmp):
    """富邦未登入(背景重連中)→ 回 False,不碰 store。"""
    from services.fubon_client import FubonStatus
    fake_fubon = SimpleNamespace(status=FubonStatus.ERROR, sdk=None)
    monkeypatch.setattr("services.fubon_client.get_fubon", lambda: fake_fubon)
    assert await cdp_module._fetch_daily_ohlc("2330") is False


@pytest.mark.asyncio
async def test_cdp_get_falls_back_to_stale_ohlc_when_fubon_down(monkeypatch, local_store_tmp):
    """backfill 失敗時 get() fallback 讀既有 daily_ohlc — stale 但有總比沒有好。"""
    calls = _patch_fetch(monkeypatch, [False])
    get_local_store().market.upsert_daily_ohlc([
        {"symbol": "2330", "date": "2026-05-20", "high": 110.0, "low": 90.0, "close": 100.0}
    ])
    svc = cdp_module.CdpService()
    levels = await svc.get("2330")
    assert calls == ["2330"]
    assert levels is not None
    assert levels["as_of_date"] == "2026-05-20"
    assert levels["prev_close"] == 100.0
