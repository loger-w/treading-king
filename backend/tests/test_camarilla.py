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
from unittest.mock import MagicMock

import pytest

from services import camarilla as cam_module


@pytest.mark.asyncio
async def test_service_refresh_caches_levels(monkeypatch):
    """mock daily_ohlc 回一筆 row → refresh → cache 命中 → has() True、_cache 回值。"""
    fake_supabase = MagicMock()
    fake_supabase.client = MagicMock()
    fake_supabase.client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"date": "2026-05-21", "high": 110.0, "low": 90.0, "close": 100.0}
    ]
    monkeypatch.setattr(cam_module, "get_supabase", lambda: fake_supabase)

    svc = cam_module.CamarillaService()
    await svc.refresh("2330")
    assert svc.has("2330")
    levels = svc._cache["2330"]
    assert levels["h4"] == 111.0
    assert levels["l4"] == 89.0
    assert levels["as_of_date"] == "2026-05-21"
    assert levels["prev_close"] == 100.0


@pytest.mark.asyncio
async def test_service_get_triggers_daily_backfill_once(monkeypatch):
    """同一 symbol 同一日,get() 只 trigger backfill 一次。"""
    svc = cam_module.CamarillaService()
    backfill_calls = []

    async def fake_backfill(symbol):
        backfill_calls.append(symbol)
        return True

    svc.backfill_from_fubon = fake_backfill  # type: ignore[method-assign]

    async def fake_refresh(symbol):
        svc._cache[symbol] = {
            "h4": 0, "h3": 0, "h2": 0, "h1": 0,
            "l1": 0, "l2": 0, "l3": 0, "l4": 0,
            "as_of_date": "2026-05-21", "prev_close": 0.0,
        }

    svc.refresh = fake_refresh  # type: ignore[method-assign]

    await svc.get("2330")
    await svc.get("2330")
    assert backfill_calls == ["2330"]


@pytest.mark.asyncio
async def test_service_refresh_missing_row_returns_silently(monkeypatch):
    """daily_ohlc 沒這 symbol 的 row → refresh 不 raise、cache 不命中。"""
    fake_supabase = MagicMock()
    fake_supabase.client = MagicMock()
    fake_supabase.client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
    monkeypatch.setattr(cam_module, "get_supabase", lambda: fake_supabase)

    svc = cam_module.CamarillaService()
    await svc.refresh("UNKNOWN")
    assert not svc.has("UNKNOWN")


def test_get_camarilla_service_singleton():
    """get_camarilla_service() 回相同 instance(module-level singleton)。"""
    a = cam_module.get_camarilla_service()
    b = cam_module.get_camarilla_service()
    assert a is b
