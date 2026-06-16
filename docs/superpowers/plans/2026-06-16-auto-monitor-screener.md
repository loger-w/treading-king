# Auto-Monitor Screener Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the "大漲股" system bookmark with an auto-monitor scheduler that screens hot stocks (漲幅 3-9%, 振幅 >3%, 量 >3000) → subscribes them to WS → adds them to signal engine evaluation scope for mountain building block detection. Add a new frontend page to display auto-monitored stocks.

**Architecture:** Rewrite `top_gainers_scheduler.py` → `auto_monitor_scheduler.py` with new screening criteria and rolling add-only semantics (cap 100, daily cleanup). Signal engine gets `_auto_monitor_symbols: set` merged into `_load_monitor_symbols()`. New `GET /api/auto_monitor` endpoint + frontend page. Delete all "大漲股" system bookmark code from bookmarks route, market cache, and frontend.

**Tech Stack:** Python 3.12 / FastAPI / Fubon Neo SDK 2.2.8 / React 18 / TypeScript / Tailwind

**Spec:** `docs/superpowers/specs/2026-06-16-auto-monitor-screener-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `backend/jobs/auto_monitor_scheduler.py` | Rewritten scheduler: screen → subscribe → notify engine |
| Create | `backend/routes/auto_monitor.py` | `GET /api/auto_monitor` endpoint |
| Create | `backend/tests/test_auto_monitor_scheduler.py` | Screening logic unit tests |
| Create | `backend/tests/test_signal_engine_auto_monitor.py` | add/clear/merge auto symbols tests |
| Create | `backend/tests/test_auto_monitor_route.py` | Route integration test |
| Create | `frontend/src/pages/AutoMonitor.tsx` | Auto-monitor display page |
| Modify | `backend/services/signal_engine.py` | `_auto_monitor_symbols`, `add_auto_symbols()`, `clear_auto_symbols()`, split `_load_monitor_symbols` |
| Modify | `backend/services/local_store/market_cache.py` | Rename `top_gainers` → `auto_monitor` |
| Modify | `backend/main.py` | Import + router registration + lifespan |
| Modify | `backend/routes/bookmarks.py` | Delete system bookmark code |
| Modify | `backend/tests/test_bookmarks_route.py` | Remove system bookmark assertion |
| Modify | `backend/tests/test_market_cache.py` | Rename test |
| Modify | `frontend/src/components/Sidebar.tsx` | Add `auto_monitor` page entry |
| Modify | `frontend/src/App.tsx` | Mount `AutoMonitor` page |
| Modify | `frontend/src/lib/api.ts` | Remove `source_type`, `change_pct`, `volume_lots`; add auto_monitor API |
| Modify | `frontend/src/components/BookmarksPanel.tsx` | Remove `is_system` partition rendering |
| Modify | `frontend/src/hooks/useBookmarks.ts` | Remove system bookmark filter in reorder |
| Delete | `backend/jobs/top_gainers_scheduler.py` | Replaced by auto_monitor_scheduler |
| Delete | `frontend/src/lib/quote-display.ts` | Only used for 大漲股 fallback |
| Delete | `frontend/src/lib/quote-display.test.ts` | Tests for deleted module |

---

### Task 1: MarketCache — rename top_gainers → auto_monitor

**Files:**
- Modify: `backend/services/local_store/market_cache.py:92-101`
- Modify: `backend/tests/test_market_cache.py:58-63`

- [ ] **Step 1: Update market_cache.py — rename methods and field**

Replace the `top_gainers` section (lines 92-101) in `backend/services/local_store/market_cache.py`:

```python
    # ---- auto_monitor(記憶體,每分鐘更新) ----

    def get_auto_monitor(self) -> list[dict]:
        return list(self._auto_monitor)

    def replace_auto_monitor(self, rows: list[dict]) -> None:
        self._auto_monitor = list(rows)

    def auto_monitor_count(self) -> int:
        return len(self._auto_monitor)
```

Also update `__init__` (line 23): `self._top_gainers: list[dict] = []` → `self._auto_monitor: list[dict] = []`

Update module docstring (lines 1-6): replace `top_gainers(記憶體)` with `auto_monitor(記憶體)`.

- [ ] **Step 2: Update test_market_cache.py — rename test**

Replace test at line 58:

```python
def test_auto_monitor_in_memory(tmp_path):
    mc = MarketCache(tmp_path / "symbols.json", tmp_path / "daily_ohlc.json")
    mc.load()
    mc.replace_auto_monitor([{"symbol": "2330", "change_pct": 5.0, "rank": 1}])
    assert mc.auto_monitor_count() == 1
    assert mc.get_auto_monitor()[0]["symbol"] == "2330"
```

- [ ] **Step 3: Run tests**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_market_cache.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add backend/services/local_store/market_cache.py backend/tests/test_market_cache.py
git commit -m "refactor: rename market_cache top_gainers → auto_monitor"
```

---

### Task 2: Signal Engine — add auto_monitor support

**Files:**
- Modify: `backend/services/signal_engine.py:60-97,169-225,698-717`
- Create: `backend/tests/test_signal_engine_auto_monitor.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_signal_engine_auto_monitor.py`:

```python
"""Signal engine auto-monitor symbol 整合。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.signal_engine import SignalEngine


@pytest.mark.asyncio
async def test_load_monitor_symbols_includes_auto(local_store_tmp):
    """_load_monitor_symbols 回傳 config monitor_list ∪ _auto_monitor_symbols。"""
    local_store_tmp.config.add_monitor("2330")
    engine = SignalEngine()
    engine._auto_monitor_symbols = {"3008"}
    syms = await engine._load_monitor_symbols()
    assert syms == {"2330", "3008"}


@pytest.mark.asyncio
async def test_add_auto_symbols_populates_field_cache(local_store_tmp):
    """add_auto_symbols 後新 symbol 出現在 field_cache。"""
    engine = SignalEngine()

    with patch("services.signal_engine.get_cdp_service") as mock_cdp, \
         patch("services.signal_engine.ma_service") as mock_ma:
        mock_cdp.return_value.get = AsyncMock(return_value={
            "ah": 110, "nh": 105, "cdp": 100, "nl": 95, "al": 90, "prev_close": 99,
        })
        mock_ma.fetch_sma_5_20 = AsyncMock(return_value=(100.0, 98.0))
        await engine.add_auto_symbols({"2330"})

    assert "2330" in engine._field_cache
    assert engine._field_cache["2330"]["cdp"] == 100
    assert "2330" in engine._auto_monitor_symbols


@pytest.mark.asyncio
async def test_add_auto_symbols_skips_already_added(local_store_tmp):
    """已在 auto set 裡的 symbol 不重複載入 field_cache。"""
    engine = SignalEngine()
    engine._auto_monitor_symbols = {"2330"}
    engine._field_cache = {"2330": {"cdp": 100}}

    with patch("services.signal_engine.get_cdp_service") as mock_cdp, \
         patch("services.signal_engine.ma_service") as mock_ma:
        mock_cdp.return_value.get = AsyncMock(return_value=None)
        mock_ma.fetch_sma_5_20 = AsyncMock(return_value=(None, None))
        await engine.add_auto_symbols({"2330"})

    # CDP 沒被重新覆寫（原值 100 不該變）
    assert engine._field_cache["2330"]["cdp"] == 100
    mock_cdp.return_value.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_auto_symbols_evicts_auto_only(local_store_tmp):
    """clear_auto_symbols 只逐出 auto-only 的 symbol,不動手動 monitor 的。"""
    local_store_tmp.config.add_monitor("2330")

    engine = SignalEngine()
    engine._auto_monitor_symbols = {"3008", "2330"}  # 2330 同時在 manual
    engine._field_cache = {"2330": {"cdp": 100}, "3008": {"cdp": 200}}

    await engine.clear_auto_symbols()

    assert "2330" in engine._field_cache    # manual — 保留
    assert "3008" not in engine._field_cache  # auto-only — 逐出
    assert engine._auto_monitor_symbols == set()


@pytest.mark.asyncio
async def test_clear_auto_symbols_idempotent(local_store_tmp):
    """空 auto set 時 clear 不炸。"""
    engine = SignalEngine()
    await engine.clear_auto_symbols()
    assert engine._auto_monitor_symbols == set()


@pytest.mark.asyncio
async def test_daily_reset_clears_auto_symbols():
    """_reset_daily_strategy_state 應清空 _auto_monitor_symbols。"""
    engine = SignalEngine()
    engine._auto_monitor_symbols = {"2330", "3008"}
    engine._reset_daily_strategy_state()
    assert engine._auto_monitor_symbols == set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_signal_engine_auto_monitor.py -v`
Expected: FAIL — `SignalEngine` has no `_auto_monitor_symbols`

- [ ] **Step 3: Implement signal engine changes**

In `backend/services/signal_engine.py`:

**3a. Add `_auto_monitor_symbols` to `__init__` (after line 96):**

```python
        # 自動監聽:scheduler 動態加入、收盤清、不持久化
        self._auto_monitor_symbols: set[str] = set()
```

**3b. Split `_load_monitor_symbols` (replace lines 169-171):**

```python
    async def _load_monitor_symbols(self) -> set[str]:
        """config monitor_list ∪ auto_monitor — 兩來源合併為引擎評估範圍。"""
        return self._load_config_monitor_symbols() | self._auto_monitor_symbols

    def _load_config_monitor_symbols(self) -> set[str]:
        """只讀 config.json 的手動 monitor_list。"""
        return {m["symbol"] for m in get_local_store().config.list_monitor()}
```

Note: `_load_config_monitor_symbols` is sync (only reads in-memory config store). `_load_monitor_symbols` stays `async` for caller compatibility (`_refill_field_cache` awaits it).

**3c. Add `add_auto_symbols` and `clear_auto_symbols` methods (after `health()`, before `_row_to_active`):**

```python
    async def add_auto_symbols(self, symbols: set[str]) -> None:
        """由 auto_monitor_scheduler 呼叫。增量加入 auto 股票 + 載入 field_cache。"""
        new = symbols - self._auto_monitor_symbols
        if not new:
            return
        self._auto_monitor_symbols |= new
        cdp = get_cdp_service()
        for sym in new:
            self._field_cache.setdefault(sym, {})
            levels = await cdp.get(sym)
            if levels:
                d = self._field_cache[sym]
                d["cdp_ah"] = levels["ah"]
                d["cdp_nh"] = levels["nh"]
                d["cdp"] = levels["cdp"]
                d["cdp_nl"] = levels["nl"]
                d["cdp_al"] = levels["al"]
                d["prev_close"] = levels["prev_close"]
            sma_5, sma_20 = await ma_service.fetch_sma_5_20(sym)
            if sma_5 is not None:
                self._field_cache[sym]["sma_5"] = sma_5
            if sma_20 is not None:
                self._field_cache[sym]["sma_20"] = sma_20
        logger.info("auto_monitor: added %d symbols (total=%d)",
                    len(new), len(self._auto_monitor_symbols))

    async def clear_auto_symbols(self) -> None:
        """收盤後呼叫。清 auto set + 逐出 field_cache 中 auto-only 的 entry。"""
        manual = self._load_config_monitor_symbols()
        auto_only = self._auto_monitor_symbols - manual
        for sym in auto_only:
            self._field_cache.pop(sym, None)
        count = len(self._auto_monitor_symbols)
        self._auto_monitor_symbols.clear()
        if count:
            logger.info("auto_monitor: cleared %d symbols", count)
```

**3d. Add `_auto_monitor_symbols.clear()` to `_reset_daily_strategy_state` (after line 715 `self._mountain_state.clear()`):**

```python
        self._auto_monitor_symbols.clear()
```

**3e. Update comment in `_reset_daily_strategy_state` (line 707):**

Change `top_gainers` to `auto_monitor`:

```python
        # 昨日收盤 tick 不該當隔日第一筆的方向參考;順手擋 24/7 長駐下
        # 換股訂閱(auto_monitor / preview)造成的慢速累積
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_signal_engine_auto_monitor.py tests/test_signal_engine_monitor.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/signal_engine.py backend/tests/test_signal_engine_auto_monitor.py
git commit -m "feat(engine): add_auto_symbols / clear_auto_symbols for auto-monitor"
```

---

### Task 3: Auto-monitor scheduler (replace top_gainers)

**Files:**
- Create: `backend/jobs/auto_monitor_scheduler.py`
- Delete: `backend/jobs/top_gainers_scheduler.py`
- Create: `backend/tests/test_auto_monitor_scheduler.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_auto_monitor_scheduler.py`:

```python
"""auto_monitor_scheduler 篩選邏輯。"""
import pytest

from jobs.auto_monitor_scheduler import (
    _amplitude_pct,
    _passes_screen,
    AUTO_MONITOR_CAP,
    MIN_CHANGE_PCT,
    MAX_CHANGE_PCT,
    MIN_AMP_PCT,
    MIN_VOLUME_LOTS,
)


def _make_item(symbol="2330", changePct=5.0, highPrice=105.0, lowPrice=100.0,
               tradeVolume=5000, **kw):
    return {"symbol": symbol, "changePercent": changePct,
            "highPrice": highPrice, "lowPrice": lowPrice,
            "tradeVolume": tradeVolume, **kw}


def test_amplitude_pct_normal():
    assert _amplitude_pct(105.0, 100.0) == pytest.approx(5.0)


def test_amplitude_pct_zero_low():
    assert _amplitude_pct(10.0, 0.0) == 0.0


def test_amplitude_pct_none():
    assert _amplitude_pct(None, 100.0) == 0.0


def test_passes_screen_happy_path():
    item = _make_item(changePct=5.0, highPrice=106.0, lowPrice=100.0, tradeVolume=5000)
    assert _passes_screen(item) is True


def test_fails_screen_low_change_pct():
    item = _make_item(changePct=2.5)
    assert _passes_screen(item) is False


def test_fails_screen_high_change_pct():
    item = _make_item(changePct=9.5)
    assert _passes_screen(item) is False


def test_fails_screen_low_amplitude():
    item = _make_item(highPrice=101.0, lowPrice=100.0)  # amp = 1%
    assert _passes_screen(item) is False


def test_fails_screen_low_volume():
    item = _make_item(tradeVolume=2000)
    assert _passes_screen(item) is False


def test_fails_screen_non_4digit_symbol():
    item = _make_item(symbol="00878")
    assert _passes_screen(item) is False


def test_fails_screen_missing_fields():
    assert _passes_screen({"symbol": "2330"}) is False


def test_cap_is_100():
    assert AUTO_MONITOR_CAP == 100


def test_thresholds():
    assert MIN_CHANGE_PCT == 3.0
    assert MAX_CHANGE_PCT == 9.0
    assert MIN_AMP_PCT == 3.0
    assert MIN_VOLUME_LOTS == 3000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_auto_monitor_scheduler.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create auto_monitor_scheduler.py**

Create `backend/jobs/auto_monitor_scheduler.py`:

```python
"""自動監聽排程 — 盤中每 1 分鐘篩選熱門股、訂閱 WS、納入 signal engine。

取代原 top_gainers_scheduler。篩選條件:
  - Server-side: type=COMMONSTOCK, 3 < changePercent < 9
  - Client-side: amplitude > 3%, tradeVolume > 3000, 4 位純數字, 在 symbols 快取
  - 滾動只加不減(當天內),上限 100 檔
  - 收盤後退訂 + 清 signal engine auto set

API rate: 每分鐘 2 個 REST call(TSE + OTC),無壓力。
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, time as dtime, timezone
from typing import Any

from services.fubon_client import FubonStatus, get_fubon
from services.fubon_ws import get_ws_pool
from services.local_store import get_local_store
from services.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)

MIN_CHANGE_PCT = 3.0
MAX_CHANGE_PCT = 9.0
MIN_AMP_PCT = 3.0
MIN_VOLUME_LOTS = 3_000
AUTO_MONITOR_CAP = 100
SYMBOL_RE = re.compile(r"^\d{4}$")

MARKET_OPEN = dtime(9, 0)
MARKET_CLOSE = dtime(13, 30)
SCHEDULE_INTERVAL_S = 60.0

AUTO_MONITOR_OWNER = "auto_monitor"

_auto_set: set[str] = set()


def _in_market_hours(now: datetime | None = None) -> bool:
    if now is None:
        now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return MARKET_OPEN <= t < MARKET_CLOSE


def _amplitude_pct(high: float | None, low: float | None) -> float:
    if not high or not low or low <= 0:
        return 0.0
    return (high - low) / low * 100.0


def _passes_screen(item: dict) -> bool:
    symbol = (item.get("symbol") or "").strip()
    pct = item.get("changePercent")
    vol = item.get("tradeVolume")
    high = item.get("highPrice")
    low = item.get("lowPrice")
    if not symbol or pct is None or vol is None:
        return False
    if not (MIN_CHANGE_PCT < pct < MAX_CHANGE_PCT):
        return False
    if _amplitude_pct(high, low) <= MIN_AMP_PCT:
        return False
    if vol <= MIN_VOLUME_LOTS:
        return False
    if not SYMBOL_RE.match(symbol):
        return False
    return True


def _fetch_market_movers(market: str) -> list[dict[str, Any]] | None:
    fubon = get_fubon()
    if fubon.status != FubonStatus.OK or fubon.sdk is None:
        logger.warning("auto_monitor: fubon SDK not ready, skip market=%s", market)
        return None
    try:
        get_rate_limiter().acquire()
        result = fubon.sdk.marketdata.rest_client.stock.snapshot.movers(
            market=market,
            direction="up",
            change="percent",
            type="COMMONSTOCK",
            gt=MIN_CHANGE_PCT,
            lt=MAX_CHANGE_PCT,
        )
        return list(result.get("data") or [])
    except Exception as e:
        logger.warning("auto_monitor: movers(market=%s) failed: %s", market, e)
        return None


async def refresh_auto_monitor() -> dict:
    """執行一次 refresh — 拉漲跌幅榜 + 篩選 + 增量訂閱。"""
    global _auto_set

    if len(_auto_set) >= AUTO_MONITOR_CAP:
        return {"status": "ok", "count": len(_auto_set), "new": 0, "reason": "cap_reached"}

    raw: list[tuple[str, float, float, int, str]] = []
    ok_markets = 0
    for market in ("TSE", "OTC"):
        items = await asyncio.to_thread(_fetch_market_movers, market)
        if items is None:
            continue
        ok_markets += 1
        for it in items:
            if not _passes_screen(it):
                continue
            symbol = it["symbol"].strip()
            raw.append((
                symbol,
                float(it["changePercent"]),
                _amplitude_pct(it.get("highPrice"), it.get("lowPrice")),
                int(it["tradeVolume"]),
                market,
            ))

    if ok_markets == 0:
        logger.warning("auto_monitor: all movers fetches failed, keep previous set")
        return {"status": "error", "count": len(_auto_set), "new": 0}

    store = get_local_store()
    if store.market.symbols_loaded():
        raw = [r for r in raw if store.market.has_symbol(r[0])]

    raw.sort(key=lambda r: -r[1])

    new_symbols: set[str] = set()
    remaining_cap = AUTO_MONITOR_CAP - len(_auto_set)
    for symbol, pct, amp, vol, mkt in raw:
        if symbol in _auto_set:
            continue
        if len(new_symbols) >= remaining_cap:
            break
        new_symbols.add(symbol)

    if not new_symbols:
        _update_snapshot(raw)
        return {"status": "ok", "count": len(_auto_set), "new": 0}

    pool = get_ws_pool()
    failed: set[str] = set()
    for s in new_symbols:
        try:
            await pool.subscribe(s, owner_id=AUTO_MONITOR_OWNER)
        except RuntimeError as e:
            failed.add(s)
            logger.warning("auto_monitor: ws sub %s failed: %s", s, e)
    subscribed = new_symbols - failed

    if subscribed:
        try:
            from services.signal_engine import get_signal_engine
            await get_signal_engine().add_auto_symbols(subscribed)
        except Exception as e:
            logger.warning("auto_monitor: add_auto_symbols failed: %s", e)

    _auto_set |= subscribed

    captured_at = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "symbol": s, "change_pct": pct, "amplitude_pct": amp,
            "volume_lots": vol, "market": mkt, "rank": i + 1,
            "captured_at": captured_at,
        }
        for i, (s, pct, amp, vol, mkt) in enumerate(raw)
        if s in _auto_set
    ]
    store.market.replace_auto_monitor(rows)

    logger.info("auto_monitor: +%d new (total=%d, failed=%d)",
                len(subscribed), len(_auto_set), len(failed))
    return {"status": "ok", "count": len(_auto_set), "new": len(subscribed)}


def _update_snapshot(raw: list[tuple[str, float, float, int, str]]) -> None:
    """更新 snapshot（只保留 auto_set 裡的,排序不變）。"""
    captured_at = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "symbol": s, "change_pct": pct, "amplitude_pct": amp,
            "volume_lots": vol, "market": mkt, "rank": i + 1,
            "captured_at": captured_at,
        }
        for i, (s, pct, amp, vol, mkt) in enumerate(raw)
        if s in _auto_set
    ]
    get_local_store().market.replace_auto_monitor(rows)


async def _cleanup_after_market() -> None:
    """收盤後退訂 WS + 清 signal engine auto set。snapshot 保留供盤後瀏覽。"""
    global _auto_set
    pool = get_ws_pool()
    for s in list(_auto_set):
        try:
            await pool.unsubscribe(s, owner_id=AUTO_MONITOR_OWNER)
        except Exception as e:
            logger.warning("auto_monitor: ws unsub %s failed: %s", s, e)
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().clear_auto_symbols()
    except Exception as e:
        logger.warning("auto_monitor: clear_auto_symbols failed: %s", e)
    count = len(_auto_set)
    _auto_set = set()
    if count:
        logger.info("auto_monitor: after-market cleanup, unsubscribed %d", count)


async def auto_monitor_loop() -> None:
    """背景 task — 盤中每 1 分鐘 refresh。"""
    logger.info("auto_monitor loop started")
    was_in_market = False
    while True:
        try:
            if _in_market_hours():
                was_in_market = True
                await refresh_auto_monitor()
            elif was_in_market:
                was_in_market = False
                await _cleanup_after_market()
            await asyncio.sleep(SCHEDULE_INTERVAL_S)
        except asyncio.CancelledError:
            logger.info("auto_monitor loop cancelled")
            return
        except Exception as e:
            logger.exception("auto_monitor loop iteration failed: %s", e)
            await asyncio.sleep(SCHEDULE_INTERVAL_S)
```

- [ ] **Step 4: Delete old scheduler**

Delete `backend/jobs/top_gainers_scheduler.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_auto_monitor_scheduler.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/jobs/auto_monitor_scheduler.py backend/tests/test_auto_monitor_scheduler.py
git rm backend/jobs/top_gainers_scheduler.py
git commit -m "feat: auto_monitor_scheduler replaces top_gainers"
```

---

### Task 4: Auto-monitor API route

**Files:**
- Create: `backend/routes/auto_monitor.py`
- Create: `backend/tests/test_auto_monitor_route.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_auto_monitor_route.py`:

```python
"""GET /api/auto_monitor 回 auto_monitor 快取。"""
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(local_store_tmp):
    from main import app
    return TestClient(app)


def test_get_auto_monitor_empty(client, local_store_tmp):
    r = client.get("/api/auto_monitor")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["count"] == 0


def test_get_auto_monitor_with_data(client, local_store_tmp):
    local_store_tmp.market.replace_symbols([
        {"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True},
    ])
    local_store_tmp.market.replace_auto_monitor([
        {"symbol": "2330", "change_pct": 5.0, "amplitude_pct": 4.2,
         "volume_lots": 8000, "market": "TSE", "rank": 1,
         "captured_at": "2026-06-16T02:00:00Z"},
    ])
    r = client.get("/api/auto_monitor")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["symbol"] == "2330"
    assert item["name"] == "台積電"
    assert item["change_pct"] == 5.0
    assert item["amplitude_pct"] == 4.2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_auto_monitor_route.py -v`
Expected: FAIL — route not found (404)

- [ ] **Step 3: Create route**

Create `backend/routes/auto_monitor.py`:

```python
"""GET /api/auto_monitor — 自動監聽清單(記憶體快取,由排程填充)。"""
from __future__ import annotations

from fastapi import APIRouter

from routes._item_enrich import enrich_item
from services.local_store import get_local_store

router = APIRouter()


@router.get("/api/auto_monitor")
async def list_auto_monitor() -> dict:
    store = get_local_store()
    rows = store.market.get_auto_monitor()
    items = []
    for r in rows:
        base = enrich_item({"symbol": r["symbol"]}, store.market)
        base.update({
            "change_pct": r.get("change_pct"),
            "amplitude_pct": r.get("amplitude_pct"),
            "volume_lots": r.get("volume_lots"),
            "market": r.get("market"),
            "rank": r.get("rank"),
            "captured_at": r.get("captured_at"),
        })
        items.append(base)
    return {"items": items, "count": len(items)}
```

- [ ] **Step 4: Register route in main.py**

In `backend/main.py`:

Add import (line 19 area, inside the `from routes import (...)` block):

```python
from routes import (
    active_signals, auto_monitor as auto_monitor_route, bookmarks, camarilla,
    candles, capital, cdp as cdp_route,
    config_io, ma, monitor_list as monitor_list_route, mxf,
    preview, quote, signals_history, symbols, ws,
)
```

Add router registration (after line 154):

```python
app.include_router(auto_monitor_route.router)
```

Update the scheduler import (line 23):

```python
from jobs.auto_monitor_scheduler import auto_monitor_loop  # noqa: E402
```

Update lifespan (line 84-85):

```python
    # 自動監聽排程 — 盤中每 1 分鐘篩選熱門股 → WS 訂閱 → signal engine
    bg_tasks.append(asyncio.create_task(auto_monitor_loop()))
```

Update the comment on line 77:

```python
    # 系統自動監聽由 auto_monitor_scheduler 在每次 refresh 時自行 sync 訂閱
```

- [ ] **Step 5: Run tests**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_auto_monitor_route.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/routes/auto_monitor.py backend/tests/test_auto_monitor_route.py backend/main.py
git commit -m "feat: GET /api/auto_monitor endpoint + wire scheduler in main"
```

---

### Task 5: Delete system bookmark from backend

**Files:**
- Modify: `backend/routes/bookmarks.py`
- Modify: `backend/tests/test_bookmarks_route.py`

- [ ] **Step 1: Remove system bookmark code from bookmarks.py**

**1a. Remove docstring references (lines 1-16):** Rewrite module docstring to remove all "大漲股" / system bookmark mentions:

```python
"""GET/POST/PATCH/DELETE /api/bookmarks — 書籤群組 + 內含股票 CRUD。

書籤架構(本機 JSON 儲存,ConfigStore):
  - bookmark_groups:user 書籤
  - watchlist_items:書籤 → 股票 (group_id, symbol)

WS subscribe 用 owner_id = f"bookmark:{group_id}":
  fubon_ws 的 refcount 是 set-of-owner_id,同檔股票在多書籤時、
  pool 自動處理 — 刪一邊不會 unsubscribe(只要還有其他 group_id 的 owner)。
"""
```

**1b. Remove constants (lines 34-37):** Delete `SYSTEM_TOP_GAINERS_ID`, `SYSTEM_TOP_GAINERS_NAME`, `SYSTEM_TOP_GAINERS_SORT_ORDER`.

**1c. Simplify `_require_user_group` (lines 83-88):** Remove the system bookmark guard:

```python
def _require_user_group(group_id: str) -> dict:
    """取 user 書籤;不存在 → 404。"""
    g = next((x for x in get_local_store().config.list_groups() if x["id"] == group_id), None)
    if g is None:
        raise HTTPException(404, detail={"error": "group_not_found"})
    return g
```

**1d. Simplify `list_bookmarks` (lines 104-133):** Remove the system bookmark append:

```python
@router.get("/api/bookmarks")
async def list_bookmarks() -> dict:
    store = get_local_store()
    groups = store.config.list_groups()
    counts = store.config.item_counts()
    out = sorted(
        [
            {
                "id": g["id"],
                "name": g["name"],
                "sort_order": g.get("sort_order", 0),
                "is_system": False,
                "count": counts.get(g["id"], 0),
            }
            for g in groups
        ],
        key=lambda g: g["sort_order"],
    )
    return {"groups": out, "count": len(out)}
```

**1e. Simplify `list_items` (lines 215-234):** Remove the `SYSTEM_TOP_GAINERS_ID` branch:

```python
@router.get("/api/bookmarks/{bid}/items")
async def list_items(bid: str) -> dict:
    store = get_local_store()
    g = _require_user_group(bid)  # noqa: F841

    items = store.config.list_items(bid)
    with_pos = sorted((it for it in items if it.get("position") is not None),
                      key=lambda it: it["position"])
    no_pos = sorted((it for it in items if it.get("position") is None),
                    key=lambda it: it.get("added_at") or "", reverse=True)
    items = with_pos + no_pos
    out = [
        enrich_item(
            {"symbol": it["symbol"], "added_at": it.get("added_at"), "note": it.get("note")},
            store.market,
        )
        for it in items
    ]
    return {"items": out, "count": len(out)}
```

**1f. Delete `trigger_top_gainers_refresh` endpoint (lines 388-395):** Remove the entire function and comment block.

- [ ] **Step 2: Fix bookmarks test**

In `backend/tests/test_bookmarks_route.py`, remove the system bookmark assertion (line 24):

```python
    assert any(g["is_system"] and g["source_type"] == "top_gainers" for g in body["groups"])
```

Replace with:

```python
    assert all(not g["is_system"] for g in body["groups"])
```

Also remove `"source_type"` from the key set check if present (line 26). The expected key set becomes:

```python
    assert set(g) == {"id", "name", "sort_order", "is_system", "count"}
```

- [ ] **Step 3: Run tests**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_bookmarks_route.py -v`
Expected: all PASS

Run full backend suite to catch any remaining references:

Run: `cd backend && .venv\Scripts\python -m pytest -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add backend/routes/bookmarks.py backend/tests/test_bookmarks_route.py
git commit -m "refactor: remove 大漲股 system bookmark from bookmarks route"
```

---

### Task 6: Frontend — remove 大漲股, add auto_monitor API

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/BookmarksPanel.tsx`
- Modify: `frontend/src/hooks/useBookmarks.ts`
- Delete: `frontend/src/lib/quote-display.ts`
- Delete: `frontend/src/lib/quote-display.test.ts`

- [ ] **Step 1: Clean api.ts types**

In `frontend/src/lib/api.ts`:

Remove `source_type` from `BookmarkGroup` (line 189):

```typescript
export interface BookmarkGroup {
  id: string;
  name: string;
  sort_order: number;
  is_system: boolean;
  count: number;
}
```

Remove `change_pct`, `volume_lots`, `captured_at` from `BookmarkItem` (lines 198-203):

```typescript
export interface BookmarkItem extends WatchlistRow {}
```

Add `AutoMonitorItem` and `AutoMonitorResponse` interfaces (after `BookmarkItemsResponse`):

```typescript
export interface AutoMonitorItem {
  symbol: string;
  name: string | null;
  market: string | null;
  is_etf: boolean;
  change_pct: number | null;
  amplitude_pct: number | null;
  volume_lots: number | null;
  rank: number | null;
  captured_at: string | null;
}

export interface AutoMonitorResponse {
  items: AutoMonitorItem[];
  count: number;
}
```

Add API method in the `api` object — find the bookmarks section and add after it:

```typescript
  autoMonitor: {
    list: () => get<AutoMonitorResponse>("/api/auto_monitor"),
  },
```

- [ ] **Step 2: Remove quote-display files**

Delete `frontend/src/lib/quote-display.ts` and `frontend/src/lib/quote-display.test.ts`.

Search for `resolveDisplayChangePct` imports in the codebase and remove them. If any component uses it, replace with direct `quote?.changePct ?? null` (the 大漲股 fallback is no longer needed).

- [ ] **Step 3: Simplify BookmarksPanel.tsx — remove is_system partition**

In `frontend/src/components/BookmarksPanel.tsx`:

Replace the `is_system` section (lines 215-224):

```tsx
          {groups.filter((g) => g.is_system).map((g) => (
            <SidebarItem
              key={g.id}
              label={g.name}
              count={g.count}
              selected={selectedGroupId === g.id}
              system
              onClick={() => pickGroup(g.id)}
            />
          ))}
```

Delete these lines entirely — no more system bookmark rendering.

Also remove any `is_system` checks in the empty state hints (lines 288-292):

```tsx
              emptyHint="這個書籤還沒有股票 — 上方搜尋加入第一檔"
```

Remove `isSystem={!!selectedGroup?.is_system}` prop (line 288) — pass nothing or always false.

- [ ] **Step 4: Simplify useBookmarks.ts — remove system bookmark filter**

In `frontend/src/hooks/useBookmarks.ts` (line 52):

```typescript
    const system = groups.filter((g) => g.is_system);
```

Remove this line. The reorderGroups function should just reorder all groups (no system filter):

```typescript
    setGroups([
      ...ids.flatMap((i, idx) => {
        const g = byId.get(i);
        return g ? [{ ...g, sort_order: idx }] : [];
      }),
    ]);
```

- [ ] **Step 5: Run frontend type check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 6: Run frontend tests**

Run: `cd frontend && npx vitest run`
Expected: all PASS (quote-display tests are deleted, nothing should reference them)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/components/BookmarksPanel.tsx frontend/src/hooks/useBookmarks.ts
git rm frontend/src/lib/quote-display.ts frontend/src/lib/quote-display.test.ts
git commit -m "refactor(frontend): remove 大漲股 system bookmark, add auto_monitor API types"
```

---

### Task 7: Frontend — AutoMonitor page + Sidebar entry

**Files:**
- Create: `frontend/src/pages/AutoMonitor.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Add Sidebar entry**

In `frontend/src/components/Sidebar.tsx`:

Update the `Page` type (line 3):

```typescript
export type Page = 'monitor' | 'auto_monitor' | 'mxf_backtest' | 'index_board';
```

Add nav item (after `monitor`, before `mxf_backtest` in `NAV_ITEMS`):

```typescript
  {
    id: 'auto_monitor',
    label: '自動監聽',
    iconPath: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2m-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zM17.9 17.39c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39',
  },
```

- [ ] **Step 2: Create AutoMonitor page**

Create `frontend/src/pages/AutoMonitor.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { api, type AutoMonitorItem } from "../lib/api";
import { useTickStore } from "../hooks/useTickStore";

const POLL_INTERVAL = 30_000;

export function AutoMonitor({ active = true }: { active?: boolean }) {
  const [items, setItems] = useState<AutoMonitorItem[]>([]);
  const [loading, setLoading] = useState(true);
  const ticks = useTickStore();

  const refresh = useCallback(async () => {
    try {
      const r = await api.autoMonitor.list();
      setItems(r.items);
    } catch (e) {
      console.warn("auto_monitor refresh failed:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!active) return;
    const id = setInterval(refresh, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [active, refresh]);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-ink-muted text-sm">
        載入中…
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-ink-muted text-sm">
        尚無符合條件的股票（盤中自動篩選）
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <header className="px-4 py-3 border-b border-line flex items-center justify-between shrink-0">
        <h2 className="text-sm font-medium text-ink">
          自動監聽
          <span className="ml-2 text-ink-muted font-normal">{items.length} 檔</span>
        </h2>
      </header>

      <div className="overflow-y-auto scroll-editorial flex-1">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-bg-deep z-10">
            <tr className="text-ink-muted text-xs border-b border-line">
              <th className="text-left px-4 py-2 font-normal w-16">#</th>
              <th className="text-left px-2 py-2 font-normal">代號</th>
              <th className="text-left px-2 py-2 font-normal">名稱</th>
              <th className="text-right px-2 py-2 font-normal">現價</th>
              <th className="text-right px-2 py-2 font-normal">漲跌%</th>
              <th className="text-right px-2 py-2 font-normal">振幅%</th>
              <th className="text-right px-4 py-2 font-normal">成交量(張)</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const tick = ticks[item.symbol];
              const price = tick?.price ?? null;
              const changePct = tick?.changePct ?? item.change_pct;
              const color =
                changePct != null && changePct > 0
                  ? "text-bull"
                  : changePct != null && changePct < 0
                    ? "text-bear"
                    : "text-ink";
              return (
                <tr
                  key={item.symbol}
                  className="border-b border-line/50 hover:bg-bg-surface transition-colors"
                >
                  <td className="px-4 py-2 text-ink-muted">{item.rank}</td>
                  <td className="px-2 py-2 font-mono">{item.symbol}</td>
                  <td className="px-2 py-2 text-ink-muted">{item.name ?? "—"}</td>
                  <td className={`px-2 py-2 text-right font-mono ${color}`}>
                    {price != null ? price.toFixed(2) : "—"}
                  </td>
                  <td className={`px-2 py-2 text-right font-mono ${color}`}>
                    {changePct != null ? `${changePct > 0 ? "+" : ""}${changePct.toFixed(2)}%` : "—"}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-ink-muted">
                    {item.amplitude_pct != null ? `${item.amplitude_pct.toFixed(1)}%` : "—"}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-ink-muted px-4">
                    {item.volume_lots != null ? item.volume_lots.toLocaleString() : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Wire into App.tsx**

In `frontend/src/App.tsx`:

Add import:

```typescript
import { AutoMonitor } from './pages/AutoMonitor';
```

Add page rendering (after the `monitor` div, before `mxf_backtest`):

```tsx
        <div hidden={page !== 'auto_monitor'} className="h-full">
          <AutoMonitor active={page === 'auto_monitor'} />
        </div>
```

- [ ] **Step 4: Run type check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 5: Verify in browser**

Run the dev server (`.\start.ps1`) and verify:
1. Sidebar shows "自動監聽" entry
2. Clicking it shows the auto-monitor page
3. During non-market hours it shows "尚無符合條件的股票"
4. Bookmarks panel no longer shows "大漲股" system bookmark
5. Other bookmarks work normally (create, add items, reorder)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/AutoMonitor.tsx frontend/src/components/Sidebar.tsx frontend/src/App.tsx
git commit -m "feat(frontend): auto-monitor page + sidebar entry"
```

---

### Task 8: Full integration verification

- [ ] **Step 1: Run full backend test suite**

Run: `cd backend && .venv\Scripts\python -m pytest -v`
Expected: all PASS. Watch specifically for:
- No `top_gainers` import errors
- No `SYSTEM_TOP_GAINERS_ID` references
- `test_bookmarks_route` passes with new assertions

- [ ] **Step 2: Run full frontend checks**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: no type errors, all tests pass.

- [ ] **Step 3: Grep for stale references**

Run: `grep -rn "top_gainers\|SYSTEM_TOP_GAINERS\|大漲股" backend/ frontend/src/ --include="*.py" --include="*.ts" --include="*.tsx"`

Expected: zero hits (or only in spec/plan docs). Fix any remaining references.

- [ ] **Step 4: Final commit if any fixes**

```bash
git add -A
git commit -m "chore: clean stale top_gainers references"
```
