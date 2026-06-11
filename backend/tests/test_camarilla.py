"""驗 compute_camarilla 8 線數學 + 台股 tick 對齊。"""
from services.camarilla import compute_camarilla


def test_compute_camarilla_reference_values():
    """H=110, L=90, C=100 → rng=20 → 8 線公式驗算。

    H 側 (> 100) tick=0.5; L 側落在 90-100 範圍 tick=0.1。
    各線用自己的 price 決定 tick，不統一用 close 的 tick。

    raw:
      h4 = 100 + 20*1.1/2  = 111.0  → tick 0.5 → 111.0
      h3 = 100 + 20*1.1/4  = 105.5  → tick 0.5 → 105.5
      h2 = 100 + 20*1.1/6  ≈ 103.67 → tick 0.5 → 103.5
      h1 = 100 + 20*1.1/12 ≈ 101.83 → tick 0.5 → 102.0
      l1 = 100 - 20*1.1/12 ≈ 98.17  → tick 0.1 → 98.2
      l2 = 100 - 20*1.1/6  ≈ 96.33  → tick 0.1 → 96.3
      l3 = 100 - 20*1.1/4  = 94.5   → tick 0.1 → 94.5
      l4 = 100 - 20*1.1/2  = 89.0   → tick 0.1 → 89.0
    """
    levels = compute_camarilla(h=110.0, l=90.0, c=100.0)
    assert levels["h4"] == 111.0
    assert levels["h3"] == 105.5
    assert levels["h2"] == 103.5
    assert levels["h1"] == 102.0
    assert levels["l1"] == 98.2
    assert levels["l2"] == 96.3
    assert levels["l3"] == 94.5
    assert levels["l4"] == 89.0


def test_compute_camarilla_low_price_tick():
    """價位 < 50 用 tick 0.05; h4 落在 50-100 用 tick 0.1。

    H=50, L=49.5, C=49.8 → rng=0.5
      h4_raw = 49.8 + 0.5*1.1/2 = 50.075 → tick_size(50.075)=0.1 → round 0.1 → 50.1
      l4_raw = 49.8 - 0.275 = 49.525     → tick_size(49.525)=0.05 → round 0.05 → 49.5
    """
    levels = compute_camarilla(h=50.0, l=49.5, c=49.8)
    assert levels["h4"] == 50.1
    assert levels["l4"] == 49.50


def test_compute_camarilla_high_price_tick():
    """價位 ≥ 1000 用 tick 5.0; l4 落在 500-1000 用 tick 1.0。

    H=1020, L=980, C=1000 → rng=40
      h4_raw = 1000 + 40*1.1/2 = 1022.0 → tick_size(1022)=5 → 1020.0
      l4_raw = 1000 - 22 = 978.0        → tick_size(978)=1  → 978.0
    """
    levels = compute_camarilla(h=1020.0, l=980.0, c=1000.0)
    assert levels["h4"] == 1020.0
    assert levels["l4"] == 978.0


def test_compute_camarilla_zero_range_collapses_to_close():
    """H == L 時 rng=0,所有 level 都 round 到 close 的 tick。"""
    levels = compute_camarilla(h=100.0, l=100.0, c=100.0)
    for key in ("h4", "h3", "h2", "h1", "l1", "l2", "l3", "l4"):
        assert levels[key] == 100.0


"""Service tests — refresh / backfill / cache."""
from datetime import date

import pytest

from services import camarilla as cam_module
from services.local_store import get_local_store


@pytest.mark.asyncio
async def test_service_refresh_caches_levels(local_store_tmp):
    """local_store 有昨日 OHLC → refresh → cache 命中 → has() True、_cache 回值。"""
    get_local_store().market.upsert_daily_ohlc([
        {"symbol": "2330", "date": "2026-05-21", "high": 110.0, "low": 90.0, "close": 100.0}
    ])

    svc = cam_module.CamarillaService()
    await svc.refresh("2330")
    assert "2330" in svc._cache
    levels = svc._cache["2330"]
    assert levels["h4"] == 111.0
    assert levels["l4"] == 89.0
    assert levels["as_of_date"] == "2026-05-21"
    assert levels["prev_close"] == 100.0


@pytest.mark.asyncio
async def test_service_get_uses_shared_daily_ohlc_backfill(monkeypatch, local_store_tmp):
    """get() 走 CDP/Camarilla 共用的 ensure_daily_ohlc(同 symbol 每天只打一次富邦),
    再從 daily_ohlc 重算 — once-per-day / cooldown 行為在 test_cdp_service.py 驗。"""
    ensure_calls = []

    async def fake_ensure(symbol):
        ensure_calls.append(symbol)
        return True

    monkeypatch.setattr(cam_module, "ensure_daily_ohlc", fake_ensure)
    get_local_store().market.upsert_daily_ohlc([
        {"symbol": "2330", "date": "2026-05-21", "high": 110.0, "low": 90.0, "close": 100.0}
    ])

    svc = cam_module.CamarillaService()
    levels = await svc.get("2330")
    assert ensure_calls == ["2330"]
    assert levels is not None and levels["h4"] == 111.0


@pytest.mark.asyncio
async def test_service_get_falls_back_to_stale_ohlc_when_fubon_down(monkeypatch, local_store_tmp):
    """富邦不可用(ensure 回 False)時 get() 仍讀既有 daily_ohlc — stale 但有總比沒有好。"""
    async def fake_ensure(symbol):
        return False

    monkeypatch.setattr(cam_module, "ensure_daily_ohlc", fake_ensure)
    get_local_store().market.upsert_daily_ohlc([
        {"symbol": "2330", "date": "2026-05-20", "high": 110.0, "low": 90.0, "close": 100.0}
    ])

    svc = cam_module.CamarillaService()
    levels = await svc.get("2330")
    assert levels is not None and levels["as_of_date"] == "2026-05-20"


@pytest.mark.asyncio
async def test_service_refresh_missing_row_returns_silently(local_store_tmp):
    """local_store 無此 symbol OHLC → refresh 不 raise、cache 不命中。"""
    # 不 seed local_store，local cache 為空
    svc = cam_module.CamarillaService()
    await svc.refresh("UNKNOWN")
    assert "UNKNOWN" not in svc._cache


def test_get_camarilla_service_singleton():
    """get_camarilla_service() 回相同 instance(module-level singleton)。"""
    cam_module._service = None  # reset for isolation
    a = cam_module.get_camarilla_service()
    b = cam_module.get_camarilla_service()
    assert a is b
